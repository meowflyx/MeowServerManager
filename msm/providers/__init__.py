"""Provider registry — unified interface for Modrinth and CurseForge."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .modrinth import ModrinthClient, ModrinthProject, ModrinthVersion
from .curseforge import CurseForgeClient, CurseForgeMod, CurseForgeFile

logger = logging.getLogger(__name__)


class Side(str, Enum):
    CLIENT = "client"
    SERVER = "server"
    BOTH = "both"


class Provider(str, Enum):
    MODRINTH = "modrinth"
    CURSEFORGE = "curseforge"


@dataclass
class UnifiedMod:
    provider: str
    project_id: str
    slug: str
    name: str
    summary: str
    downloads: int
    categories: list[str]
    client_side: str
    server_side: str
    game_versions: list[str]
    icon_url: str | None
    raw: Any = field(default=None, repr=False)

    def is_server_compatible(self) -> bool:
        return self.server_side in ("required", "optional")

    def is_client_compatible(self) -> bool:
        return self.client_side in ("required", "optional")

    def matches_side(self, side: Side) -> bool:
        if side == Side.SERVER:
            return self.is_server_compatible()
        if side == Side.CLIENT:
            return self.is_client_compatible()
        return self.is_server_compatible() or self.is_client_compatible()


@dataclass
class DownloadTarget:
    url: str
    filename: str
    dest_dir: str
    side: Side
    project_name: str
    size: int
    sha1: str = ""
    sha512: str = ""


def _map_modrinth_project(p: ModrinthProject) -> UnifiedMod:
    return UnifiedMod(
        provider="modrinth",
        project_id=p.project_id,
        slug=p.slug,
        name=p.title,
        summary=p.description,
        downloads=p.downloads,
        categories=p.categories,
        client_side=p.client_side,
        server_side=p.server_side,
        game_versions=p.versions,
        icon_url=p.icon_url,
        raw=p,
    )


def _map_curseforge_mod(m: CurseForgeMod) -> UnifiedMod:
    category_names = [c.get("name", "") for c in m.categories]
    return UnifiedMod(
        provider="curseforge",
        project_id=str(m.mod_id),
        slug=m.slug,
        name=m.name,
        summary=m.summary,
        downloads=m.downloads,
        categories=category_names,
        client_side="unknown",
        server_side="unknown",
        game_versions=m.game_versions,
        icon_url=m.logo_url,
        raw=m,
    )


def create_client(provider: Provider, config: dict[str, Any]) -> ModrinthClient | CurseForgeClient:
    if provider == Provider.MODRINTH:
        user_agent = config.get("modrinth", {}).get("user_agent", "MeowServerManager/1.0.0")
        return ModrinthClient(user_agent=user_agent)
    if provider == Provider.CURSEFORGE:
        api_key = config.get("apis", {}).get("curseforge_api_key", "")
        return CurseForgeClient(api_key=api_key)
    raise ValueError(f"Unknown provider: {provider}")
