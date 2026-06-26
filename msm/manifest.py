"""Mod manifest — TOML-backed registry of installed mods with metadata
from Modrinth/CurseForge, used for sync, removal, and client-pack generation."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import tomllib
import tomli_w

from .providers import Side, Provider, UnifiedMod

logger = logging.getLogger(__name__)

MANIFEST_VERSION = 1


@dataclass
class ManifestDependency:
    project_id: str
    name: str
    dependency_type: str


@dataclass
class ManifestEntry:
    name: str
    slug: str
    project_id: str
    provider: str
    filename: str
    client_side: str
    server_side: str
    game_versions: list[str]
    loaders: list[str]
    version_number: str = ""
    version_id: str = ""
    download_url: str = ""
    sha1: str = ""
    sha512: str = ""
    size: int = 0
    categories: list[str] = field(default_factory=list)
    dependencies: list[ManifestDependency] = field(default_factory=list)

    @property
    def side(self) -> str:
        server_ok = self.server_side in ("required", "optional")
        client_ok = self.client_side in ("required", "optional")
        if server_ok and client_ok:
            return "both"
        if server_ok:
            return "server"
        if client_ok:
            return "client"
        return "unknown"

    @property
    def is_client_compatible(self) -> bool:
        return self.client_side in ("required", "optional")

    @property
    def is_server_compatible(self) -> bool:
        return self.server_side in ("required", "optional")

    def is_client_compatible_with_overrides(self, overrides: dict[str, str] | None = None) -> bool:
        if overrides:
            override = overrides.get(self.slug) or overrides.get(self.name.lower())
            if override == "both":
                return True
            if override == "client":
                return True
            if override == "server":
                return False
        return self.is_client_compatible

    def is_server_only_with_overrides(self, overrides: dict[str, str] | None = None) -> bool:
        if overrides:
            override = overrides.get(self.slug) or overrides.get(self.name.lower())
            if override == "server":
                return True
            if override == "client":
                return False
            if override == "both":
                return False
        return self.is_server_compatible and not self.is_client_compatible

    def should_sync_to_client(
        self,
        overrides: dict[str, str] | None = None,
        server_only_denylist: set[str] | None = None,
        exclude_server_only: bool = False,
    ) -> bool:
        """Decide whether this mod should be copied to the client pack.

        Default policy is "include unless explicitly excluded": a missing
        content/dependency mod breaks joins, while an extra client mod is
        harmless. Server-only mods are copied too unless ``exclude_server_only``
        is enabled or they are listed in ``server_only_denylist``.

        Resolution order:
        1. Explicit side override (``server`` -> skip, ``client``/``both`` -> keep).
        2. ``server_only_denylist`` match -> skip.
        3. Modrinth/CF says client is required/optional -> keep.
        4. ``exclude_server_only=True`` and mod is server-only -> skip.
        5. Otherwise -> keep.
        """
        keys = {self.slug.lower(), self.name.lower(), self.filename.lower()}

        if overrides:
            for key in keys:
                override = overrides.get(key)
                if override == "client" or override == "both":
                    return True
                if override == "server":
                    return False

        if server_only_denylist and any(key in server_only_denylist for key in keys):
            return False

        if self.client_side in ("required", "optional"):
            return True

        if exclude_server_only and self.client_side == "unsupported" and self.server_side in ("required", "optional"):
            return False

        return True

    @classmethod
    def from_unified_mod(
        cls,
        unified: UnifiedMod,
        filename: str,
        download_url: str = "",
        version_number: str = "",
        version_id: str = "",
        sha1: str = "",
        sha512: str = "",
        size: int = 0,
        dependencies: list[ManifestDependency] | None = None,
    ) -> ManifestEntry:
        return cls(
            name=unified.name,
            slug=unified.slug,
            project_id=unified.project_id,
            provider=unified.provider,
            filename=filename,
            client_side=unified.client_side,
            server_side=unified.server_side,
            game_versions=unified.game_versions,
            loaders=unified.categories,
            version_number=version_number,
            version_id=version_id,
            download_url=download_url,
            sha1=sha1,
            sha512=sha512,
            size=size,
            categories=unified.categories,
            dependencies=dependencies or [],
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "slug": self.slug,
            "project_id": self.project_id,
            "provider": self.provider,
            "filename": self.filename,
            "client_side": self.client_side,
            "server_side": self.server_side,
            "side": self.side,
            "game_versions": self.game_versions,
            "loaders": self.loaders,
            "version_number": self.version_number,
            "version_id": self.version_id,
            "download_url": self.download_url,
            "sha1": self.sha1,
            "sha512": self.sha512,
            "size": self.size,
            "categories": self.categories,
            "dependencies": [
                {"project_id": d.project_id, "name": d.name, "dependency_type": d.dependency_type}
                for d in self.dependencies
            ],
        }

    @classmethod
    def from_dict(cls, d: dict) -> ManifestEntry:
        return cls(
            name=d.get("name", ""),
            slug=d.get("slug", ""),
            project_id=d.get("project_id", ""),
            provider=d.get("provider", "unknown"),
            filename=d.get("filename", ""),
            client_side=d.get("client_side", "unknown"),
            server_side=d.get("server_side", "unknown"),
            game_versions=d.get("game_versions", []),
            loaders=d.get("loaders", []),
            version_number=d.get("version_number", ""),
            version_id=d.get("version_id", ""),
            download_url=d.get("download_url", ""),
            sha1=d.get("sha1", ""),
            sha512=d.get("sha512", ""),
            size=d.get("size", 0),
            categories=d.get("categories", []),
            dependencies=[
                ManifestDependency(
                    project_id=dep.get("project_id", ""),
                    name=dep.get("name", ""),
                    dependency_type=dep.get("dependency_type", "required"),
                )
                for dep in d.get("dependencies", [])
            ],
        )


class Manifest:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.entries: dict[str, ManifestEntry] = {}
        self.updated_at: str = ""

    def load(self) -> None:
        if not self.path.is_file():
            logger.info("No existing manifest at %s, starting fresh.", self.path)
            self.entries = {}
            return

        with open(self.path, "rb") as f:
            data = tomllib.load(f)

        meta = data.get("manifest", {})
        if meta.get("version", 0) != MANIFEST_VERSION:
            logger.warning("Manifest version mismatch, re-scan recommended.")

        self.updated_at = meta.get("updated_at", "")

        for entry_data in data.get("mods", []):
            entry = ManifestEntry.from_dict(entry_data)
            self.entries[entry.filename] = entry

        logger.info("Loaded %d entries from manifest.", len(self.entries))

    def save(self) -> None:
        self.updated_at = datetime.now(timezone.utc).isoformat()

        mods_list = [e.to_dict() for e in self.entries.values()]

        doc = {
            "manifest": {
                "version": MANIFEST_VERSION,
                "updated_at": self.updated_at,
            },
            "mods": mods_list,
        }

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "wb") as f:
            tomli_w.dump(doc, f)

        logger.info("Saved %d entries to %s", len(self.entries), self.path)

    def add(self, entry: ManifestEntry) -> None:
        self.entries[entry.filename] = entry

    def remove(self, filename: str) -> ManifestEntry | None:
        return self.entries.pop(filename, None)

    def get(self, filename: str) -> ManifestEntry | None:
        return self.entries.get(filename)

    def get_by_project_id(self, project_id: str) -> ManifestEntry | None:
        for entry in self.entries.values():
            if entry.project_id == project_id:
                return entry
        return None

    def get_by_slug(self, slug: str) -> ManifestEntry | None:
        for entry in self.entries.values():
            if entry.slug.lower() == slug.lower():
                return entry
        return None

    def all(self) -> list[ManifestEntry]:
        return list(self.entries.values())

    def client_compatible(self, side_overrides: dict[str, str] | None = None) -> list[ManifestEntry]:
        return [e for e in self.entries.values() if e.is_client_compatible_with_overrides(side_overrides)]

    def server_side_only(self, side_overrides: dict[str, str] | None = None) -> list[ManifestEntry]:
        return [e for e in self.entries.values() if e.is_server_only_with_overrides(side_overrides)]


def hash_file(path: Path) -> tuple[str, str]:
    sha1 = hashlib.sha1()
    sha512 = hashlib.sha512()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            sha1.update(chunk)
            sha512.update(chunk)
    return sha1.hexdigest(), sha512.hexdigest()
