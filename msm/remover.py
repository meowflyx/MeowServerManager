"""Mod removal — removes mods from server and client directories."""

from __future__ import annotations

import logging
from pathlib import Path

from .providers import Side

logger = logging.getLogger(__name__)


def remove_mod(
    name_or_pattern: str,
    server_mods_dir: str,
    client_mods_dir: str,
    side: Side = Side.SERVER,
) -> list[str]:
    removed: list[str] = []

    def _scan_and_remove(directory: str) -> list[str]:
        dir_path = Path(directory)
        if not dir_path.is_dir():
            logger.warning("Directory not found: %s", dir_path)
            return []

        local_removed: list[str] = []
        for file_path in dir_path.glob("*.jar"):
            if name_or_pattern.lower() in file_path.name.lower():
                try:
                    file_path.unlink()
                    local_removed.append(str(file_path))
                    logger.info("Removed %s", file_path)
                except OSError as exc:
                    logger.error("Failed to remove %s: %s", file_path, exc)
        return local_removed

    if side in (Side.SERVER, Side.BOTH):
        removed.extend(_scan_and_remove(server_mods_dir))
    if side in (Side.CLIENT, Side.BOTH):
        removed.extend(_scan_and_remove(client_mods_dir))

    return removed


def list_mods(server_mods_dir: str, client_mods_dir: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}

    for label, directory in [("server", server_mods_dir), ("client", client_mods_dir)]:
        dir_path = Path(directory)
        if dir_path.is_dir():
            result[label] = sorted(
                f.name for f in dir_path.glob("*.jar") if f.is_file()
            )
        else:
            result[label] = []

    return result
