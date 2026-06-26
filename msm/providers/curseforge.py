"""CurseForge Core API v1 client.

Requires an API key (x-api-key header).
Minecraft gameId = 432.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

CURSEFORGE_API = "https://api.curseforge.com/v1"
MINECRAFT_GAME_ID = 432

LOADER_TYPE_MAP: dict[str, int] = {
    "forge": 1,
    "cauldron": 2,
    "liteloader": 3,
    "fabric": 4,
    "quilt": 5,
    "neoforge": 6,
}

LOADER_TYPE_REVERSE: dict[int, str] = {v: k for k, v in LOADER_TYPE_MAP.items()}


@dataclass
class CurseForgeMod:
    mod_id: int
    name: str
    slug: str
    summary: str
    downloads: int
    date_created: str
    date_modified: str
    date_released: str
    game_versions: list[str]
    categories: list[dict[str, Any]]
    authors: list[dict[str, Any]]
    logo_url: str | None


@dataclass
class CurseForgeFile:
    file_id: int
    mod_id: int
    file_name: str
    display_name: str
    download_url: str | None
    file_size: int
    game_versions: list[str]
    release_type: int
    dependencies: list[CurseForgeFileDependency] = field(default_factory=list)
    is_server_pack: bool = False


@dataclass
class CurseForgeFileDependency:
    mod_id: int
    file_id: int | None
    relation_type: int


class CurseForgeClient:
    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("CurseForge API key is required")

        self.api_key = api_key
        self._client = httpx.Client(
            base_url=CURSEFORGE_API,
            headers={
                "x-api-key": api_key,
                "Accept": "application/json",
            },
            timeout=30.0,
        )

    def close(self) -> None:
        self._client.close()

    def _game_id(self) -> int:
        return MINECRAFT_GAME_ID

    def search(
        self,
        query: str,
        loader: str | None = None,
        game_version: str | None = None,
        sort_field: str = "Featured",
        sort_order: str = "desc",
        index: int = 0,
        page_size: int = 20,
    ) -> tuple[list[CurseForgeMod], int]:
        params: dict[str, Any] = {
            "gameId": self._game_id(),
            "searchFilter": query,
            "sortField": sort_field,
            "sortOrder": sort_order,
            "index": index,
            "pageSize": page_size,
        }
        if game_version:
            params["gameVersion"] = game_version
        if loader:
            loader_id = LOADER_TYPE_MAP.get(loader.lower())
            if loader_id:
                params["modLoaderType"] = loader_id

        logger.debug("CurseForge search: %s", params)
        resp = self._client.get("/mods/search", params=params)
        resp.raise_for_status()
        data = resp.json()

        mods = []
        for m in data.get("data", []):
            mods.append(CurseForgeMod(
                mod_id=m["id"],
                name=m["name"],
                slug=m["slug"],
                summary=m.get("summary", ""),
                downloads=m.get("downloadCount", 0),
                date_created=m.get("dateCreated", ""),
                date_modified=m.get("dateModified", ""),
                date_released=m.get("dateReleased", ""),
                game_versions=[],
                categories=m.get("categories", []),
                authors=m.get("authors", []),
                logo_url=m.get("logo", {}).get("url") if m.get("logo") else None,
            ))
        return mods, data.get("pagination", {}).get("totalCount", 0)

    def get_mod(self, mod_id: int) -> CurseForgeMod:
        resp = self._client.get(f"/mods/{mod_id}")
        resp.raise_for_status()
        m = resp.json()["data"]
        return CurseForgeMod(
            mod_id=m["id"],
            name=m["name"],
            slug=m["slug"],
            summary=m.get("summary", ""),
            downloads=m.get("downloadCount", 0),
            date_created=m.get("dateCreated", ""),
            date_modified=m.get("dateModified", ""),
            date_released=m.get("dateReleased", ""),
            game_versions=[],
            categories=m.get("categories", []),
            authors=m.get("authors", []),
            logo_url=m.get("logo", {}).get("url") if m.get("logo") else None,
        )

    def get_mod_files(
        self,
        mod_id: int,
        game_version: str | None = None,
        loader: str | None = None,
        index: int = 0,
        page_size: int = 50,
    ) -> tuple[list[CurseForgeFile], int]:
        params: dict[str, Any] = {
            "index": index,
            "pageSize": page_size,
        }
        if game_version:
            params["gameVersion"] = game_version
        if loader:
            loader_id = LOADER_TYPE_MAP.get(loader.lower())
            if loader_id:
                params["modLoaderType"] = loader_id

        resp = self._client.get(f"/mods/{mod_id}/files", params=params)
        resp.raise_for_status()
        data = resp.json()

        files = []
        for f in data.get("data", []):
            files.append(CurseForgeFile(
                file_id=f["id"],
                mod_id=f["modId"],
                file_name=f.get("fileName", ""),
                display_name=f.get("displayName", ""),
                download_url=None,
                file_size=f.get("fileLength", 0),
                game_versions=f.get("gameVersions", []),
                release_type=f.get("releaseType", 1),
                dependencies=[
                    CurseForgeFileDependency(
                        mod_id=d["modId"],
                        file_id=d.get("fileId"),
                        relation_type=d["relationType"],
                    )
                    for d in f.get("dependencies", [])
                ],
                is_server_pack=f.get("isServerPack", False),
            ))
        return files, data.get("pagination", {}).get("totalCount", 0)

    def get_file_download_url(self, mod_id: int, file_id: int) -> str:
        resp = self._client.get(f"/mods/{mod_id}/files/{file_id}/download-url")
        resp.raise_for_status()
        return resp.json()["data"]

    def download_file(self, url: str, dest_path: str) -> None:
        logger.info("Downloading %s -> %s", url, dest_path)
        with self._client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(dest_path, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=8192):
                    f.write(chunk)
        logger.info("Downloaded %s", dest_path)
