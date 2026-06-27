"""CLI commands for MeowServerManager."""

from __future__ import annotations

import json
import logging
import re
import shutil
import sys
from pathlib import Path

import click

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from .config import (
    load_config, save_config, get_active_profile_name,
    get_profile, list_profiles, create_profile,
    delete_profile, set_active_profile_name,
)
from .manifest import Manifest, ManifestEntry, ManifestDependency, hash_file
from .providers import Provider, Side
from .providers.modrinth import ModrinthClient, ModrinthProject, ModrinthVersion
from .providers.curseforge import CurseForgeClient
from .downloader import download_mod
from .remover import remove_mod, list_mods
from .server import ServerManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("msm")


def _side_label(client_side: str, server_side: str) -> str:
    server_ok = server_side in ("required", "optional")
    client_ok = client_side in ("required", "optional")
    if server_ok and client_ok:
        return "Server+Client"
    if server_ok:
        return "Server"
    if client_ok:
        return "Client"
    return f"client={client_side}, server={server_side}"


def _load_manifest(config: dict) -> Manifest:
    profile_name = get_active_profile_name(config)
    manifest_name = f"{profile_name}_manifest.toml"
    mf = Manifest(Path(manifest_name))
    mf.load()
    return mf


def _resolve_profile_dirs(ctx: click.Context) -> dict:
    config = ctx.obj["config"]
    profile = get_profile(config)
    base_path = Path(ctx.obj.get("config_path", "config.toml")).parent.resolve()
    return {
        "mods_dir": str((base_path / profile.get("mods_dir", "../mods")).resolve()),
        "client_mods_dir": str((base_path / profile.get("client_mods_dir", "../client_mods")).resolve()),
        "loader": profile.get("loader", "neoforge"),
        "game_version": profile.get("game_version", "1.21.1"),
        "run_script": profile.get("run_script", "run.sh"),
        "server_dir": str((base_path / profile.get("server_dir", "..")).resolve()),
    }


def _fill_manifest_entry_from_modrinth(
    mr: ModrinthClient,
    filename: str,
    sha1_hash: str,
) -> ManifestEntry | None:
    try:
        version = mr.get_version_from_hash(sha1_hash, algorithm="sha1")
    except Exception:
        logger.debug("No Modrinth match for hash %s (%s)", sha1_hash[:12], filename)
        return None
    project = mr.get_project(version.project_id)
    primary = [f for f in version.files if f.primary]
    best_file = primary[0] if primary else version.files[0]
    return ManifestEntry(
        name=project.title,
        slug=project.slug,
        project_id=project.project_id,
        provider="modrinth",
        filename=filename,
        client_side=project.client_side,
        server_side=project.server_side,
        game_versions=version.game_versions,
        loaders=version.loaders,
        version_number=version.version_number,
        version_id=version.version_id,
        download_url=best_file.url,
        sha1=best_file.sha1,
        sha512=best_file.sha512,
        size=best_file.size,
        categories=project.categories,
        dependencies=[
            ManifestDependency(
                project_id=d.project_id or "",
                name=d.file_name or "",
                dependency_type=d.dependency_type,
            )
            for d in version.dependencies
        ],
    )


def _manifest_entry_from_modrinth_version(
    project: ModrinthProject,
    version: ModrinthVersion,
    filename: str,
    sha1: str,
    sha512: str,
    size: int,
) -> ManifestEntry:
    """Build a ManifestEntry from a Modrinth project/version."""
    primary = [f for f in version.files if f.primary]
    best_file = primary[0] if primary else version.files[0]
    return ManifestEntry(
        name=project.title,
        slug=project.slug,
        project_id=project.project_id,
        provider="modrinth",
        filename=filename,
        client_side=project.client_side,
        server_side=project.server_side,
        game_versions=version.game_versions,
        loaders=version.loaders,
        version_number=version.version_number,
        version_id=version.version_id,
        download_url=best_file.url,
        sha1=best_file.sha1,
        sha512=best_file.sha512,
        size=size,
        categories=project.categories,
        dependencies=[
            ManifestDependency(
                project_id=d.project_id or "",
                name=d.file_name or "",
                dependency_type=d.dependency_type,
            )
            for d in version.dependencies
        ],
    )


def _search_queries_from_filename(filename: str) -> list[str]:
    """Generate possible Modrinth slugs from a local JAR filename."""
    name = filename.removesuffix(".jar")
    queries = [name]

    # Drop a trailing loader/version chunk like "-neoforge-1.2.3".
    slug_guess = re.sub(r"-(?:neoforge|forge|fabric|quilt|mc|minecraft)(?:[.-].*)?$", "", name, flags=re.IGNORECASE)
    if slug_guess and slug_guess != name:
        queries.append(slug_guess)

    # Pure alphabetic slug up to the first version-looking token.
    pure = re.sub(r"-[0-9].*$", "", name)
    if pure and pure != name and pure not in queries:
        queries.append(pure)

    return queries


def _identify_unknown_mod_by_filename(
    mr: ModrinthClient,
    filename: str,
    sha1_hash: str,
    sha512_hash: str,
    size: int,
    loader: str,
    game_version: str,
) -> ManifestEntry | None:
    """Try to find a Modrinth project for a JAR whose hash is not indexed."""
    queries = _search_queries_from_filename(filename)
    logger.debug("Trying to identify %r via queries: %s", filename, queries)

    for query in queries:
        try:
            hits, _ = mr.search(
                query,
                loader=loader,
                game_version=game_version,
                index="relevance",
                limit=10,
            )
        except Exception as exc:
            logger.debug("Search failed for %r: %s", query, exc)
            continue

        for project in hits:
            if project.slug != query.lower() and query.lower() not in project.title.lower():
                continue

            try:
                versions = mr.get_project_versions(
                    project.project_id,
                    loaders=[loader],
                    game_versions=[game_version],
                )
            except Exception as exc:
                logger.debug("Could not fetch versions for %s: %s", project.slug, exc)
                continue

            for version in versions:
                for vfile in version.files:
                    if vfile.filename.lower() == filename.lower():
                        entry = _manifest_entry_from_modrinth_version(
                            project, version, filename, sha1_hash, sha512_hash, size
                        )
                        if vfile.sha1 == sha1_hash:
                            logger.debug("Exact hash match found via filename search: %s", filename)
                        else:
                            logger.warning(
                                "Filename match for %s on Modrinth, but hash differs. "
                                "Using metadata anyway; verify manually if unsure.",
                                filename,
                            )
                        return entry

    return None


@click.group()
@click.option("--config", "-c", "config_path", default=None, help="Path to config.toml")
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
@click.pass_context
def cli(ctx: click.Context, config_path: str | None, verbose: bool) -> None:
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    try:
        ctx.ensure_object(dict)
        ctx.obj["config"] = load_config(config_path)
        ctx.obj["config_path"] = config_path or "config.toml"
    except FileNotFoundError:
        click.echo("Config file not found. Run from MSM/ directory or use --config.", err=True)
        ctx.obj["config"] = {}
        ctx.obj["config_path"] = "config.toml"


@cli.group()
def profile() -> None:
    """Manage modpack profiles (different servers, loaders, versions)."""


@profile.command("list")
@click.pass_context
def profile_list(ctx: click.Context) -> None:
    """List all profiles."""
    config = ctx.obj["config"]
    active = get_active_profile_name(config)
    for name in list_profiles(config):
        marker = " *" if name == active else ""
        p = get_profile(config, name)
        click.echo(f"  [{name}]{marker}")
        click.echo(f"      Loader: {p.get('loader', '?')}  |  MC: {p.get('game_version', '?')}")
        click.echo(f"      Mods: {p.get('mods_dir', '?')}  |  Server: {p.get('server_dir', '?')}")


@profile.command("create")
@click.argument("name")
@click.option("--loader", "-l", default="neoforge", help="Mod loader")
@click.option("--version", "-g", "game_version", default="1.21.1", help="Minecraft version")
@click.option("--mods-dir", "-m", default="../mods", help="Mods directory")
@click.option("--server-dir", "-s", default="..", help="Server root directory")
@click.pass_context
def profile_create(ctx: click.Context, name: str, loader: str, game_version: str, mods_dir: str, server_dir: str) -> None:
    """Create a new profile."""
    config = ctx.obj["config"]
    if name in list_profiles(config):
        click.echo(f"Profile '{name}' already exists.", err=True)
        return
    create_profile(config, name, {
        "mods_dir": mods_dir,
        "client_mods_dir": "../client_mods",
        "loader": loader,
        "game_version": game_version,
        "run_script": "../run.sh",
        "server_dir": server_dir,
    })
    save_config(config)
    click.echo(f"Profile '{name}' created (loader={loader}, mc={game_version}).")


@profile.command("use")
@click.argument("name")
@click.pass_context
def profile_use(ctx: click.Context, name: str) -> None:
    """Switch to a different profile."""
    config = ctx.obj["config"]
    if name not in list_profiles(config):
        click.echo(f"Profile '{name}' not found. Use 'msm profile list'.", err=True)
        return
    set_active_profile_name(config, name)
    save_config(config)
    click.echo(f"Switched to profile '{name}'.")


@profile.command("delete")
@click.argument("name")
@click.pass_context
def profile_delete(ctx: click.Context, name: str) -> None:
    """Delete a profile."""
    config = ctx.obj["config"]
    if not delete_profile(config, name):
        click.echo(f"Profile '{name}' not found.", err=True)
        return
    save_config(config)
    click.echo(f"Profile '{name}' deleted. Active: {get_active_profile_name(config)}")


@profile.command("show")
@click.pass_context
def profile_show(ctx: click.Context) -> None:
    """Show active profile details."""
    config = ctx.obj["config"]
    active = get_active_profile_name(config)
    p = get_profile(config)
    click.echo(f"Active profile: {active}")
    click.echo(f"  Loader:       {p.get('loader')}")
    click.echo(f"  Game version: {p.get('game_version')}")
    click.echo(f"  Mods dir:     {p.get('mods_dir')}")
    click.echo(f"  Client dir:   {p.get('client_mods_dir')}")
    click.echo(f"  Server dir:   {p.get('server_dir')}")
    click.echo(f"  Run script:   {p.get('run_script')}")


@cli.group()
def side() -> None:
    """Override mod side classification for sync-clients."""


@side.command("set")
@click.argument("slug")
@click.argument("value", type=click.Choice(["client", "server", "both"]))
@click.pass_context
def side_set(ctx: click.Context, slug: str, value: str) -> None:
    """Override a mod's side (e.g. 'msm side set lithostitched both')."""
    config = ctx.obj["config"]
    config.setdefault("side_overrides", {})[slug.lower()] = value
    save_config(config)
    click.echo(f"Side override: {slug} -> {value}")


@side.command("unset")
@click.argument("slug")
@click.pass_context
def side_unset(ctx: click.Context, slug: str) -> None:
    """Remove a side override."""
    config = ctx.obj["config"]
    overrides = config.get("side_overrides", {})
    if slug.lower() in overrides:
        del overrides[slug.lower()]
        save_config(config)
        click.echo(f"Side override removed: {slug}")
    else:
        click.echo(f"No override found for: {slug}")


@side.command("list")
@click.pass_context
def side_list(ctx: click.Context) -> None:
    """List all side overrides."""
    config = ctx.obj["config"]
    overrides = config.get("side_overrides", {})
    if overrides:
        for slug, side_val in overrides.items():
            click.echo(f"  {slug} -> {side_val}")
    else:
        click.echo("No side overrides configured.")


@cli.command()
@click.argument("query")
@click.option("--provider", "-p", type=click.Choice(["modrinth", "curseforge"]), default="modrinth", help="Mod platform")
@click.option("--loader", "-l", default=None, help="Mod loader (neoforge, forge, fabric)")
@click.option("--version", "-g", "game_version", default=None, help="Minecraft version")
@click.option("--sort", "-s", default="relevance", type=click.Choice(["relevance", "downloads", "follows", "newest", "updated"]), help="Sort order")
@click.option("--limit", "-n", default=10, help="Max results")
@click.pass_context
def search(ctx: click.Context, query: str, provider: str, loader: str | None, game_version: str | None, sort: str, limit: int) -> None:
    """Search for mods on Modrinth or CurseForge."""
    config = ctx.obj["config"]
    profile = _resolve_profile_dirs(ctx)
    resolved_loader = loader or profile["loader"]
    resolved_version = game_version or profile["game_version"]

    index_map = {
        "relevance": "relevance", "downloads": "downloads",
        "follows": "follows", "newest": "newest", "updated": "updated",
    }

    if provider == "curseforge":
        cf_key = config.get("apis", {}).get("curseforge_api_key", "")
        if not cf_key:
            click.echo("CurseForge API key not set in config.", err=True)
            return
        cf = CurseForgeClient(api_key=cf_key)
        try:
            results, total = cf.search(query, loader=resolved_loader, game_version=resolved_version, sort_field="Popularity" if sort == "downloads" else "Featured", page_size=limit)
            click.echo(f"Found {total} results on CurseForge.\n")
            for mod in results:
                click.echo(f"  [{mod.mod_id}] {mod.name}")
                click.echo(f"      Downloads: {mod.downloads:,}  |  {mod.summary[:80]}")
                click.echo(f"      URL: https://curseforge.com/minecraft/mc-mods/{mod.slug}\n")
        finally:
            cf.close()
        return

    mr = ModrinthClient(user_agent=config.get("modrinth", {}).get("user_agent", "MSM/1.0.0"))
    try:
        results, total = mr.search(query, loader=resolved_loader, game_version=resolved_version, index=index_map.get(sort, "relevance"), limit=limit)
        click.echo(f"Found {total} results on Modrinth.\n")
        for mod in results:
            side_label = _side_label(mod.client_side, mod.server_side)
            click.echo(f"  [{mod.project_id}] {mod.title}")
            click.echo(f"      Downloads: {mod.downloads:,}  |  Follows: {mod.follows:,}")
            click.echo(f"      Side: {side_label}  |  {mod.description[:80]}")
            click.echo(f"      URL: https://modrinth.com/mod/{mod.slug}\n")
    finally:
        mr.close()


@cli.command()
@click.argument("name_or_id")
@click.option("--provider", "-p", type=click.Choice(["modrinth", "curseforge"]), default="modrinth", help="Mod platform")
@click.option("--side", "-S", type=click.Choice(["client", "server", "both"]), default=None, help="Override auto-detected install target")
@click.option("--loader", "-l", default=None, help="Mod loader")
@click.option("--version", "-g", "game_version", default=None, help="Minecraft version")
@click.option("--no-deps", is_flag=True, help="Skip dependency resolution")
@click.pass_context
def install(ctx: click.Context, name_or_id: str, provider: str, side: str | None, loader: str | None, game_version: str | None, no_deps: bool) -> None:
    """Download and install a mod (and its dependencies). Side is auto-detected from mod metadata."""
    config = ctx.obj["config"]
    resolved_side = Side(side) if side else None
    if no_deps:
        config = dict(config)
        config.setdefault("download", {})["auto_resolve_deps"] = False

    try:
        profile = _resolve_profile_dirs(ctx)
        result = download_mod(provider=Provider(provider), query_or_id=name_or_id, config=config, side=resolved_side, loader=loader or profile["loader"], game_version=game_version or profile["game_version"], server_mods_dir=profile["mods_dir"], client_mods_dir=profile["client_mods_dir"])
        side_label = result.detected_side.value.upper()
        if resolved_side is not None:
            side_label += " (forced via --side)"
        else:
            side_label += " (auto-detected)"
        click.echo(f"Install side: {side_label}")
        click.echo(f"Downloaded {len(result.paths)} file(s):")
        for path in result.paths:
            click.echo(f"  {path}")
        manifest = _load_manifest(config)
        for entry in result.entries:
            manifest.add(entry)
        manifest.save()
        click.echo(f"Manifest updated: {len(result.entries)} entries written.")
    except ValueError as exc:
        click.echo(f"Error: {exc}", err=True)
    except Exception as exc:
        logger.exception("Install failed")
        click.echo(f"Error: {exc}", err=True)


@cli.command()
@click.argument("name")
@click.option("--side", "-S", type=click.Choice(["client", "server", "both"]), default="server", help="Which directories to remove from")
@click.pass_context
def remove(ctx: click.Context, name: str, side: str) -> None:
    """Remove a mod by name pattern from server/client directories and update manifest."""
    config = ctx.obj["config"]
    profile = _resolve_profile_dirs(ctx)
    resolved_side = Side(side)

    removed = remove_mod(name, profile["mods_dir"], profile["client_mods_dir"], resolved_side)
    if removed:
        click.echo(f"Removed {len(removed)} file(s):")
        for path in removed:
            click.echo(f"  {path}")
        manifest = _load_manifest(config)
        for path in removed:
            filename = Path(path).name
            if manifest.remove(filename):
                click.echo(f"  Manifest entry removed: {filename}")
        manifest.save()
    else:
        click.echo(f"No mods matching '{name}' found.")


@cli.command()
@click.pass_context
def list(ctx: click.Context) -> None:
    """List installed mods in server and client directories."""
    config = ctx.obj["config"]
    profile = _resolve_profile_dirs(ctx)
    active = get_active_profile_name(config)
    click.echo(f"Profile: {active}\n")

    mods = list_mods(profile["mods_dir"], profile["client_mods_dir"])
    for label, files in mods.items():
        click.echo(f"[{label.upper()} MODS] ({len(files)} files)")
        for f in files:
            click.echo(f"  {f}")


@cli.group()
def server() -> None:
    """Manage the Minecraft server process."""


@server.command("start")
@click.pass_context
def server_start(ctx: click.Context) -> None:
    config = ctx.obj["config"]
    profile = _resolve_profile_dirs(ctx)
    mgr = ServerManager(server_dir=profile["server_dir"], run_script=profile["run_script"])
    try:
        pid = mgr.start()
        click.echo(f"Server started with PID {pid}")
    except (FileNotFoundError, PermissionError) as exc:
        click.echo(f"Error: {exc}", err=True)


@server.command("stop")
@click.pass_context
def server_stop(ctx: click.Context) -> None:
    config = ctx.obj["config"]
    profile = _resolve_profile_dirs(ctx)
    mgr = ServerManager(server_dir=profile["server_dir"], run_script=profile["run_script"])
    mgr.stop()
    click.echo("Server stopped.")


@server.command("restart")
@click.pass_context
def server_restart(ctx: click.Context) -> None:
    config = ctx.obj["config"]
    profile = _resolve_profile_dirs(ctx)
    mgr = ServerManager(server_dir=profile["server_dir"], run_script=profile["run_script"])
    pid = mgr.restart()
    click.echo(f"Server restarted with PID {pid}")


@server.command("status")
@click.pass_context
def server_status(ctx: click.Context) -> None:
    config = ctx.obj["config"]
    profile = _resolve_profile_dirs(ctx)
    mgr = ServerManager(server_dir=profile["server_dir"], run_script=profile["run_script"])
    info = mgr.status()
    click.echo(json.dumps(info, indent=2, default=str))


@cli.command()
@click.argument("name_or_id")
@click.option("--provider", "-p", type=click.Choice(["modrinth", "curseforge"]), default="modrinth", help="Mod platform")
@click.pass_context
def info(ctx: click.Context, name_or_id: str, provider: str) -> None:
    """Show detailed info about a mod."""
    config = ctx.obj["config"]
    if provider == "curseforge":
        cf_key = config.get("apis", {}).get("curseforge_api_key", "")
        if not cf_key:
            click.echo("CurseForge API key not set.", err=True)
            return
        client = CurseForgeClient(api_key=cf_key)
        try:
            mod = client.get_mod(int(name_or_id))
            click.echo(f"Name:      {mod.name}")
            click.echo(f"Slug:      {mod.slug}")
            click.echo(f"Downloads: {mod.downloads:,}")
            click.echo(f"Summary:   {mod.summary}")
            click.echo(f"Created:   {mod.date_created}")
            click.echo(f"URL:       https://curseforge.com/minecraft/mc-mods/{mod.slug}")
        finally:
            client.close()
        return
    mr = ModrinthClient(user_agent=config.get("modrinth", {}).get("user_agent", "MSM/1.0.0"))
    try:
        mod = mr.get_project(name_or_id)
        click.echo(f"Name:      {mod.title}")
        click.echo(f"Slug:      {mod.slug}")
        click.echo(f"Downloads: {mod.downloads:,}")
        click.echo(f"Follows:   {mod.follows:,}")
        click.echo(f"Client:    {mod.client_side}")
        click.echo(f"Server:    {mod.server_side}")
        click.echo(f"License:   {mod.license_id}")
        click.echo(f"Versions:  {', '.join(mod.versions[:10])}")
        click.echo(f"Categories:{', '.join(mod.categories)}")
        click.echo(f"Created:   {mod.date_created}")
        click.echo(f"Updated:   {mod.date_modified}")
        click.echo(f"URL:       https://modrinth.com/mod/{mod.slug}")
        click.echo(f"\nDescription: {mod.description[:500]}")
    finally:
        mr.close()


@cli.command("scan")
@click.option("--mods-dir", "-d", default=None, help="Override mods directory for scan")
@click.option("--refresh", is_flag=True, help="Re-identify existing entries with unknown/missing metadata")
@click.pass_context
def scan_cmd(ctx: click.Context, mods_dir: str | None, refresh: bool) -> None:
    """Scan existing mods/ directory and build/update the manifest.

    Hashes each JAR, looks it up on Modrinth, records metadata.
    Use --refresh to re-attempt identification of mods previously marked unknown.
    """
    config = ctx.obj["config"]
    profile = _resolve_profile_dirs(ctx)
    resolved_dir = Path(mods_dir or profile["mods_dir"]).resolve()

    if not resolved_dir.is_dir():
        click.echo(f"Directory not found: {resolved_dir}", err=True)
        return

    jar_files = [p for p in resolved_dir.glob("*.jar")]
    if not jar_files:
        click.echo(f"No .jar files found in {resolved_dir}")
        return

    click.echo(f"Scanning {len(jar_files)} JARs in {resolved_dir}...\n")

    mr = ModrinthClient(user_agent=config.get("modrinth", {}).get("user_agent", "MSM/1.0.0"))
    manifest = _load_manifest(config)

    try:
        for jar_path in jar_files:
            sha1_hash, sha512_hash = hash_file(jar_path)
            filename = jar_path.name
            click.echo(f"  {filename} ... ", nl=False)

            existing = manifest.get(filename)
            if existing and not (refresh and (existing.provider == "unknown" or existing.client_side == "unknown")):
                click.echo("already in manifest, skipped.")
                continue

            entry = _fill_manifest_entry_from_modrinth(mr, filename, sha1_hash)
            if entry is None:
                entry = _identify_unknown_mod_by_filename(
                    mr, filename, sha1_hash, sha512_hash,
                    jar_path.stat().st_size, profile["loader"], profile["game_version"],
                )
                if entry is None:
                    entry = ManifestEntry(
                        name=filename.replace(".jar", ""), slug="", project_id="",
                        provider="unknown", filename=filename,
                        client_side="unknown", server_side="unknown",
                        game_versions=[], loaders=[],
                        sha1=sha1_hash, sha512=sha512_hash,
                        size=jar_path.stat().st_size,
                    )
                    click.echo(f"unknown (hash={sha1_hash[:12]})")
                else:
                    click.echo(f"{entry.name} [{entry.version_number}] side={entry.side} (by filename)")
            else:
                click.echo(f"{entry.name} [{entry.version_number}] side={entry.side}")
            manifest.add(entry)

        manifest.save()
        click.echo(f"\nManifest saved: {len(manifest.entries)} mods recorded.")
    finally:
        mr.close()


@cli.command("sync-clients")
@click.option("--dry-run", is_flag=True, help="Show what would be copied without copying")
@click.pass_context
def sync_clients(ctx: click.Context, dry_run: bool) -> None:
    """Copy client-compatible mods from mods/ to client_mods/.

    The default policy is "safe": mods are copied unless they are explicitly
    classified as server-only. Unknown mods are included by default, because a
    missing content/dependency mod breaks joins, while an extra client mod is
    harmless.
    """
    config = ctx.obj["config"]
    profile = _resolve_profile_dirs(ctx)
    mods_dir = Path(profile["mods_dir"]).resolve()
    client_dir = Path(profile["client_mods_dir"]).resolve()

    manifest = _load_manifest(config)
    side_overrides = config.get("side_overrides", {})
    sync_cfg = config.get("sync", {})
    exclude_server_only = sync_cfg.get("exclude_server_only", False)
    server_only_denylist = {s.lower() for s in sync_cfg.get("server_only", [])}

    if not manifest.entries:
        click.echo("Manifest is empty. Run 'msm scan' first.", err=True)
        return

    client_mods: list[ManifestEntry] = []
    skipped: list[ManifestEntry] = []
    unknown_included: list[ManifestEntry] = []

    for entry in manifest.entries.values():
        if entry.should_sync_to_client(side_overrides, server_only_denylist, exclude_server_only):
            if entry.client_side == "unknown":
                unknown_included.append(entry)
            client_mods.append(entry)
        else:
            skipped.append(entry)

    click.echo(f"Mods to copy to client: {len(client_mods)}")
    if unknown_included:
        click.echo(f"  (including {len(unknown_included)} with unknown side)")
    click.echo(f"Skipped (server-only/excluded): {len(skipped)}\n")

    if dry_run:
        click.echo("[DRY RUN] Would copy:")
        for entry in client_mods:
            dst = client_dir / entry.filename
            exists = "EXISTS" if dst.exists() else "NEW"
            marker = " [unknown side]" if entry in unknown_included else ""
            click.echo(f"  {exists}: {entry.filename} ({entry.name}){marker}")
        return

    client_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    sync_skipped = 0

    for entry in client_mods:
        src = mods_dir / entry.filename
        dst = client_dir / entry.filename
        if not src.is_file():
            click.echo(f"  SKIP: source not found: {entry.filename}")
            sync_skipped += 1
            continue
        if dst.is_file():
            src_hash, _ = hash_file(src)
            dst_hash, _ = hash_file(dst)
            if src_hash == dst_hash:
                click.echo(f"  IDENTICAL: {entry.filename}")
                sync_skipped += 1
                continue
        shutil.copy2(src, dst)
        click.echo(f"  COPY: {entry.filename}")
        copied += 1

    click.echo(f"\nCopied: {copied}, Skipped: {sync_skipped}")


if __name__ == "__main__":
    cli()
