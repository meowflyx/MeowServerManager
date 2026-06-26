"""Download orchestrator — resolves deps, determines side routing,
downloads mods to server/client directories."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from .providers import (
    Provider,
    Side,
    UnifiedMod,
    DownloadTarget,
    create_client,
    _map_modrinth_project,
    _map_curseforge_mod,
)
from .providers.modrinth import ModrinthClient, ModrinthVersion
from .providers.curseforge import CurseForgeClient, CurseForgeFile
from .resolver import resolve_dependencies_modrinth, resolve_dependencies_curseforge
from .manifest import ManifestEntry, ManifestDependency, hash_file

logger = logging.getLogger(__name__)


@dataclass
class DownloadResult:
    paths: list[str]
    entries: list[ManifestEntry]
    detected_side: Side


def _detect_side(project: UnifiedMod) -> Side:
    """Auto-detect installation side from mod metadata.

    When metadata is unavailable, default to BOTH. An extra mod on the client
    is harmless, while a missing dependency/content mod breaks joins.
    """
    server_ok = project.server_side in ("required", "optional")
    client_ok = project.client_side in ("required", "optional")

    if server_ok and client_ok:
        return Side.BOTH
    if server_ok:
        return Side.SERVER
    if client_ok:
        return Side.CLIENT
    return Side.BOTH


def _effective_side(project: UnifiedMod, user_override: Side | None) -> Side:
    if user_override is not None:
        return user_override
    return _detect_side(project)


def _should_install_to(effective: Side, target: Side) -> bool:
    if effective == Side.BOTH:
        return True
    return effective == target


def _determine_targets(
    version: ModrinthVersion,
    project: UnifiedMod,
    base_server_dir: str,
    base_client_dir: str,
    effective_side: Side,
) -> list[DownloadTarget]:
    targets: list[DownloadTarget] = []
    primary = [f for f in version.files if f.primary]
    files_to_download = primary if primary else version.files

    for f in files_to_download:
        if _should_install_to(effective_side, Side.SERVER):
            targets.append(DownloadTarget(
                url=f.url,
                filename=f.filename,
                dest_dir=base_server_dir,
                side=Side.SERVER,
                project_name=project.name,
                size=f.size,
            ))
        if _should_install_to(effective_side, Side.CLIENT):
            targets.append(DownloadTarget(
                url=f.url,
                filename=f.filename,
                dest_dir=base_client_dir,
                side=Side.CLIENT,
                project_name=project.name,
                size=f.size,
            ))
    return targets


def _download_single(client: ModrinthClient | CurseForgeClient, target: DownloadTarget) -> str:
    dest_path = Path(target.dest_dir) / target.filename
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    if isinstance(client, ModrinthClient):
        client.download_file(target.url, str(dest_path))
    elif isinstance(client, CurseForgeClient):
        client.download_file(target.url, str(dest_path))

    return str(dest_path)


def download_mod(
    provider: Provider,
    query_or_id: str,
    config: dict,
    side: Side | None = None,
    loader: str | None = None,
    game_version: str | None = None,
    server_mods_dir: str = "../mods",
    client_mods_dir: str = "../client_mods",
) -> DownloadResult:
    """Download a mod and its dependencies.

    Returns DownloadResult with paths, manifest entries, and detected side.
    """
    resolved_loader = loader or "neoforge"
    resolved_version = game_version or "1.21.1"
    max_workers = config.get("download", {}).get("concurrent_downloads", 5)
    auto_deps = config.get("download", {}).get("auto_resolve_deps", True)

    client = create_client(provider, config)
    downloaded: list[str] = []
    all_targets: list[DownloadTarget] = []
    manifest_entries: list[ManifestEntry] = []
    detected_side: Side | None = None

    try:
        if isinstance(client, ModrinthClient):
            project = client.get_project(query_or_id)
            unified = _map_modrinth_project(project)
            effective = _effective_side(unified, side)
            detected_side = _detect_side(unified)

            versions = client.get_project_versions(
                query_or_id,
                loaders=[resolved_loader],
                game_versions=[resolved_version],
            )
            if not versions:
                raise ValueError(f"No versions found for {resolved_loader}/{resolved_version}")

            best = versions[0]
            primary = [f for f in best.files if f.primary]
            best_file = primary[0] if primary else best.files[0]

            targets = _determine_targets(
                best, unified, server_mods_dir, client_mods_dir, effective
            )
            all_targets.extend(targets)

            manifest_entries.append(ManifestEntry(
                name=unified.name,
                slug=unified.slug,
                project_id=unified.project_id,
                provider="modrinth",
                filename=best_file.filename,
                client_side=unified.client_side,
                server_side=unified.server_side,
                game_versions=best.game_versions,
                loaders=best.loaders,
                version_number=best.version_number,
                version_id=best.version_id,
                download_url=best_file.url,
                sha1=best_file.sha1,
                sha512=best_file.sha512,
                size=best_file.size,
                categories=unified.categories,
                dependencies=[
                    ManifestDependency(
                        project_id=d.project_id or "",
                        name=d.file_name or "",
                        dependency_type=d.dependency_type,
                    )
                    for d in best.dependencies
                ],
            ))

            if auto_deps:
                deps = resolve_dependencies_modrinth(
                    client, best, resolved_version, resolved_loader
                )
                for dep_project_id, dep_version_id in deps:
                    dep_version = client.get_version(dep_version_id)
                    dep_project = client.get_project(dep_project_id)
                    dep_unified = _map_modrinth_project(dep_project)
                    dep_effective = _effective_side(dep_unified, None)
                    dep_targets = _determine_targets(
                        dep_version, dep_unified, server_mods_dir, client_mods_dir, dep_effective
                    )
                    all_targets.extend(dep_targets)

                    dep_primary = [f for f in dep_version.files if f.primary]
                    dep_file = dep_primary[0] if dep_primary else dep_version.files[0]
                    manifest_entries.append(ManifestEntry(
                        name=dep_unified.name,
                        slug=dep_unified.slug,
                        project_id=dep_unified.project_id,
                        provider="modrinth",
                        filename=dep_file.filename,
                        client_side=dep_unified.client_side,
                        server_side=dep_unified.server_side,
                        game_versions=dep_version.game_versions,
                        loaders=dep_version.loaders,
                        version_number=dep_version.version_number,
                        version_id=dep_version.version_id,
                        download_url=dep_file.url,
                        sha1=dep_file.sha1,
                        sha512=dep_file.sha512,
                        size=dep_file.size,
                        categories=dep_unified.categories,
                        dependencies=[
                            ManifestDependency(
                                project_id=d.project_id or "",
                                name=d.file_name or "",
                                dependency_type=d.dependency_type,
                            )
                            for d in dep_version.dependencies
                        ],
                    ))

        elif isinstance(client, CurseForgeClient):
            mod_id = int(query_or_id)
            try:
                cf_mod = client.get_mod(mod_id)
            except Exception:
                results, _ = client.search(query_or_id, loader=resolved_loader)
                if not results:
                    raise ValueError(f"CurseForge mod not found: {query_or_id}")
                cf_mod = results[0]

            unified = _map_curseforge_mod(cf_mod)

            files, _ = client.get_mod_files(
                cf_mod.mod_id,
                game_version=resolved_version,
                loader=resolved_loader,
            )
            if not files:
                raise ValueError(f"No files found for {resolved_loader}/{resolved_version}")

            best_file = files[0]
            download_url = client.get_file_download_url(cf_mod.mod_id, best_file.file_id)

            if side is not None:
                effective = side
                detected_side = side
            else:
                effective = Side.BOTH
                detected_side = Side.BOTH

            if _should_install_to(effective, Side.SERVER):
                all_targets.append(DownloadTarget(
                    url=download_url,
                    filename=best_file.file_name,
                    dest_dir=server_mods_dir,
                    side=Side.SERVER,
                    project_name=unified.name,
                    size=best_file.file_size,
                ))
            if _should_install_to(effective, Side.CLIENT):
                all_targets.append(DownloadTarget(
                    url=download_url,
                    filename=best_file.file_name,
                    dest_dir=client_mods_dir,
                    side=Side.CLIENT,
                    project_name=unified.name,
                    size=best_file.file_size,
                ))

            manifest_entries.append(ManifestEntry(
                name=unified.name,
                slug=unified.slug,
                project_id=unified.project_id,
                provider="curseforge",
                filename=best_file.file_name,
                client_side=unified.client_side,
                server_side=unified.server_side,
                game_versions=best_file.game_versions,
                loaders=[],
                version_number="",
                version_id=str(best_file.file_id),
                download_url=download_url,
                sha1="",
                sha512="",
                size=best_file.file_size,
                categories=unified.categories,
            ))

            if auto_deps:
                deps = resolve_dependencies_curseforge(
                    client, best_file, resolved_version, resolved_loader
                )
                for dep_mod_id, dep_file_id in deps:
                    dep_files, _ = client.get_mod_files(
                        dep_mod_id,
                        game_version=resolved_version,
                        loader=resolved_loader,
                    )
                    if dep_files:
                        dep_file = dep_files[0]
                        dep_url = client.get_file_download_url(dep_mod_id, dep_file.file_id)
                        dep_effective = Side.BOTH
                        if _should_install_to(dep_effective, Side.SERVER):
                            all_targets.append(DownloadTarget(
                                url=dep_url,
                                filename=dep_file.file_name,
                                dest_dir=server_mods_dir,
                                side=Side.SERVER,
                                project_name=f"dep-{dep_mod_id}",
                                size=dep_file.file_size,
                            ))
                        manifest_entries.append(ManifestEntry(
                            name=f"cf-dep-{dep_mod_id}",
                            slug="",
                            project_id=str(dep_mod_id),
                            provider="curseforge",
                            filename=dep_file.file_name,
                            client_side="unknown",
                            server_side="unknown",
                            game_versions=dep_file.game_versions,
                            loaders=[],
                            version_number="",
                            version_id=str(dep_file.file_id),
                            download_url=dep_url,
                            sha1="",
                            sha512="",
                            size=dep_file.file_size,
                            categories=[],
                        ))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_download_single, client, t): t
                for t in all_targets
            }
            for future in as_completed(futures):
                try:
                    path = future.result()
                    downloaded.append(path)
                except Exception as exc:
                    target = futures[future]
                    logger.error("Failed to download %s: %s", target.filename, exc)

    finally:
        client.close()

    # Compute actual hashes for downloaded files
    for entry in manifest_entries:
        for path in downloaded:
            if Path(path).name == entry.filename:
                try:
                    entry.sha1, entry.sha512 = hash_file(Path(path))
                except Exception:
                    pass
                break

    return DownloadResult(
        paths=downloaded,
        entries=manifest_entries,
        detected_side=detected_side or Side.SERVER,
    )
