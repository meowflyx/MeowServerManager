"""Dependency resolver — resolves mod dependencies recursively,
filtering by loader (neoforge/forge/fabric) and game version.

Both Modrinth and CurseForge resolvers walk the full dependency tree
(direct + transitive) using a BFS queue guarded by a visited set, so
cycles are impossible and every reachable required dependency is
collected exactly once.
"""

from __future__ import annotations

import logging
from collections import deque

from .providers import Provider
from .providers.modrinth import ModrinthClient, ModrinthDependency, ModrinthVersion
from .providers.curseforge import CurseForgeClient, CurseForgeFileDependency, CurseForgeFile

logger = logging.getLogger(__name__)

RELATION_REQUIRED = 3
RELATION_EMBEDDED = 6  # noqa: E221


def _loader_filter(loader: str) -> str:
    """Normalize loader names accepted by the Modrinth API."""
    normalized = loader.lower()
    if normalized in ("neoforge", "neo"):
        return "neoforge"
    return normalized


def _resolve_modrinth_project_id(client: ModrinthClient, dep: ModrinthDependency) -> str | None:
    """Recover a project_id for a dependency that only carries a version_id.

    Modrinth occasionally omits ``project_id`` on dependencies pointing at
    unlisted or removed projects. Feeding the version_id into the
    ``/project/{id|slug}/version`` endpoint silently 404s and drops the
    whole sub-tree, so we fetch the version first and read its project_id.
    """
    if dep.project_id:
        return dep.project_id
    if not dep.version_id:
        return None
    try:
        ref = client.get_version(dep.version_id)
    except Exception:
        logger.warning(
            "Could not resolve project_id from version %s", dep.version_id, exc_info=True
        )
        return None
    return ref.project_id


def resolve_dependencies_modrinth(
    client: ModrinthClient,
    version: ModrinthVersion,
    game_version: str,
    loader: str,
    visited: set[str] | None = None,
) -> list[tuple[str, str]]:
    """Resolve Modrinth dependencies recursively (direct + transitive).

    Returns a de-duplicated list of (project_id, version_id) tuples in
    BFS order. Each project is resolved at most once.
    """
    if visited is None:
        visited = set()

    resolved: list[tuple[str, str]] = []
    queue: deque[tuple[ModrinthDependency, int]] = deque()

    for dep in version.dependencies:
        if dep.dependency_type in ("required",):
            queue.append((dep, 1))

    target_loader = _loader_filter(loader)

    while queue:
        dep, depth = queue.popleft()

        dep_project_id = _resolve_modrinth_project_id(client, dep)
        if not dep_project_id:
            logger.warning("Skipping dep without project_id/version_id (depth %d)", depth)
            continue
        if dep_project_id in visited:
            continue
        visited.add(dep_project_id)

        label = "direct" if depth == 1 else "transitive"
        logger.info("Resolving %s dep %s (depth %d)", label, dep_project_id, depth)

        try:
            dep_versions = client.get_project_versions(
                dep_project_id,
                loaders=[target_loader],
                game_versions=[game_version],
            )
        except Exception:
            logger.warning("Failed to fetch versions for dep %s", dep_project_id, exc_info=True)
            continue

        strict = [
            v for v in dep_versions
            if target_loader in v.loaders and game_version in v.game_versions
        ]
        if strict:
            compatible = strict
        elif dep_versions:
            logger.warning(
                "Dep %s: no exact %s/%s match, falling back to closest available",
                dep_project_id, target_loader, game_version,
            )
            compatible = dep_versions
        else:
            logger.warning("No compatible version found for dep %s", dep_project_id)
            continue

        best = compatible[0]
        resolved.append((best.project_id, best.version_id))

        for sub_dep in best.dependencies:
            if sub_dep.dependency_type in ("required",):
                queue.append((sub_dep, depth + 1))

    logger.info("Modrinth dependency resolution: %d mods (incl. transitive)", len(resolved))
    return resolved


def resolve_dependencies_curseforge(
    client: CurseForgeClient,
    cf_file: CurseForgeFile,
    game_version: str,
    loader: str,
    visited: set[int] | None = None,
) -> list[tuple[int, int | None]]:
    """Resolve CurseForge dependencies recursively (direct + transitive).

    Returns a de-duplicated list of (mod_id, file_id or None) tuples in
    BFS order. Each mod is resolved at most once.
    """
    if visited is None:
        visited = set()

    resolved: list[tuple[int, int | None]] = []
    queue: deque[tuple[CurseForgeFileDependency, int]] = deque()

    for dep in cf_file.dependencies:
        if dep.relation_type == RELATION_REQUIRED:
            queue.append((dep, 1))

    while queue:
        dep, depth = queue.popleft()
        if dep.mod_id in visited:
            continue
        visited.add(dep.mod_id)

        label = "direct" if depth == 1 else "transitive"
        logger.info("Resolving %s CF dep mod_id=%d (depth %d)", label, dep.mod_id, depth)

        try:
            dep_files, _ = client.get_mod_files(
                dep.mod_id,
                game_version=game_version,
                loader=loader,
            )
        except Exception:
            logger.warning("Failed to fetch files for CF dep %d", dep.mod_id, exc_info=True)
            continue

        if not dep_files:
            logger.warning("No files found for CF dep %d", dep.mod_id)
            continue

        best = dep_files[0]
        resolved.append((dep.mod_id, best.file_id))

        for sub_dep in best.dependencies:
            if sub_dep.relation_type == RELATION_REQUIRED:
                queue.append((sub_dep, depth + 1))

    logger.info("CurseForge dependency resolution: %d mods (incl. transitive)", len(resolved))
    return resolved
