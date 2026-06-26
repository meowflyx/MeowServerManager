"""Dependency resolver — resolves mod dependencies recursively,
filtering by loader (neoforge/forge/fabric) and game version."""

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
    normalized = loader.lower()
    if normalized in ("neoforge", "neo"):
        return "neoforge"
    return normalized


def resolve_dependencies_modrinth(
    client: ModrinthClient,
    version: ModrinthVersion,
    game_version: str,
    loader: str,
    visited: set[str] | None = None,
) -> list[tuple[str, str]]:
    """Resolve Modrinth dependencies recursively.

    Returns list of (project_id, version_id).
    """
    if visited is None:
        visited = set()

    resolved: list[tuple[str, str]] = []
    queue: deque[ModrinthDependency] = deque()

    for dep in version.dependencies:
        if dep.dependency_type in ("required",):
            queue.append(dep)

    target_loader = _loader_filter(loader)

    while queue:
        dep = queue.popleft()
        dep_project_id = dep.project_id or dep.version_id
        if not dep_project_id:
            continue
        if dep_project_id in visited:
            continue
        visited.add(dep_project_id)

        logger.debug("Resolving dep %s", dep_project_id)

        try:
            dep_versions = client.get_project_versions(
                dep_project_id,
                loaders=[target_loader],
                game_versions=[game_version],
            )
        except Exception:
            logger.warning("Failed to fetch versions for dep %s", dep_project_id, exc_info=True)
            continue

        compatible = [
            v for v in dep_versions
            if target_loader in v.loaders and game_version in v.game_versions
        ]
        if not compatible:
            compatible = dep_versions

        if not compatible:
            logger.warning("No compatible version found for dep %s", dep_project_id)
            continue

        best = compatible[0]
        resolved.append((best.project_id, best.version_id))

        for sub_dep in best.dependencies:
            if sub_dep.dependency_type in ("required",):
                queue.append(sub_dep)

    return resolved


def resolve_dependencies_curseforge(
    client: CurseForgeClient,
    cf_file: CurseForgeFile,
    game_version: str,
    loader: str,
    visited: set[int] | None = None,
) -> list[tuple[int, int | None]]:
    """Resolve CurseForge dependencies recursively.

    Returns list of (mod_id, file_id or None).
    """
    if visited is None:
        visited = set()

    resolved: list[tuple[int, int | None]] = []
    queue: deque[CurseForgeFileDependency] = deque()

    for dep in cf_file.dependencies:
        if dep.relation_type == RELATION_REQUIRED:
            queue.append(dep)

    while queue:
        dep = queue.popleft()
        if dep.mod_id in visited:
            continue
        visited.add(dep.mod_id)

        logger.debug("Resolving CF dep mod_id=%d", dep.mod_id)

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
                queue.append(sub_dep)

    return resolved
