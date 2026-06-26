"""Modrinth API v2 client for read/search/download operations.

No API key required for read operations.
Rate limit: 300 requests/minute.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

MODRINTH_API = "https://api.modrinth.com/v2"


@dataclass
class ModrinthProject:
    project_id: str
    slug: str
    title: str
    description: str
    categories: list[str]
    client_side: str
    server_side: str
    downloads: int
    icon_url: str | None
    versions: list[str]
    follows: int
    date_created: str
    date_modified: str
    latest_version: str | None
    license_id: str

    def is_server_compatible(self) -> bool:
        return self.server_side in ("required", "optional")

    def is_client_compatible(self) -> bool:
        return self.client_side in ("required", "optional")


@dataclass
class ModrinthVersionFile:
    filename: str
    url: str
    size: int
    sha1: str
    sha512: str
    primary: bool


@dataclass
class ModrinthDependency:
    project_id: str | None
    version_id: str | None
    file_name: str | None
    dependency_type: str


@dataclass
class ModrinthVersion:
    version_id: str
    project_id: str
    name: str
    version_number: str
    game_versions: list[str]
    loaders: list[str]
    version_type: str
    downloads: int
    date_published: str
    files: list[ModrinthVersionFile]
    dependencies: list[ModrinthDependency] = field(default_factory=list)


class ModrinthClient:
    def __init__(self, user_agent: str = "MeowServerManager/1.0.0") -> None:
        self.user_agent = user_agent
        self._client = httpx.Client(
            base_url=MODRINTH_API,
            headers={"User-Agent": user_agent},
            timeout=30.0,
        )

    def close(self) -> None:
        self._client.close()

    def search(
        self,
        query: str,
        loader: str | None = None,
        game_version: str | None = None,
        client_side: str | None = None,
        server_side: str | None = None,
        sort: str = "relevance",
        index: str = "downloads",
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[ModrinthProject], int]:
        facets: list[list[str]] = []
        if loader:
            facets.append([f"categories:{loader}"])
        if game_version:
            facets.append([f"versions:{game_version}"])
        if client_side:
            facets.append([f"client_side:{client_side}"])
        if server_side:
            facets.append([f"server_side:{server_side}"])
        facets.append(['project_type:mod'])

        facets_json = str(facets).replace("'", '"')

        params: dict[str, Any] = {
            "query": query,
            "facets": facets_json,
            "index": index,
            "limit": limit,
            "offset": offset,
        }

        logger.debug("Modrinth search: %s", params)
        resp = self._client.get("/search", params=params)
        resp.raise_for_status()
        data = resp.json()

        projects = [
            ModrinthProject(
                project_id=h["project_id"],
                slug=h["slug"],
                title=h["title"],
                description=h["description"],
                categories=h.get("categories", []),
                client_side=h.get("client_side", "unknown"),
                server_side=h.get("server_side", "unknown"),
                downloads=h["downloads"],
                icon_url=h.get("icon_url"),
                versions=h["versions"],
                follows=h["follows"],
                date_created=h.get("date_created", ""),
                date_modified=h.get("date_modified", ""),
                latest_version=h.get("latest_version"),
                license_id=h.get("license", ""),
            )
            for h in data["hits"]
        ]
        return projects, data["total_hits"]

    def get_project(self, project_id: str) -> ModrinthProject:
        resp = self._client.get(f"/project/{project_id}")
        resp.raise_for_status()
        p = resp.json()
        return ModrinthProject(
            project_id=p["id"],
            slug=p["slug"],
            title=p["title"],
            description=p["description"],
            categories=p.get("categories", []),
            client_side=p.get("client_side", "unknown"),
            server_side=p.get("server_side", "unknown"),
            downloads=p["downloads"],
            icon_url=p.get("icon_url"),
            versions=p["versions"],
            follows=p["followers"],
            date_created=p.get("published", ""),
            date_modified=p.get("updated", ""),
            latest_version=None,
            license_id=p.get("license", {}).get("id", "") if p.get("license") else "",
        )

    def get_project_versions(
        self,
        project_id: str,
        loaders: list[str] | None = None,
        game_versions: list[str] | None = None,
    ) -> list[ModrinthVersion]:
        params: dict[str, Any] = {}
        if loaders:
            params["loaders"] = str(loaders).replace("'", '"')
        if game_versions:
            params["game_versions"] = str(game_versions).replace("'", '"')

        resp = self._client.get(f"/project/{project_id}/version", params=params)
        resp.raise_for_status()
        versions_data = resp.json()

        versions = []
        for v in versions_data:
            versions.append(ModrinthVersion(
                version_id=v["id"],
                project_id=v["project_id"],
                name=v["name"],
                version_number=v["version_number"],
                game_versions=v.get("game_versions", []),
                loaders=v.get("loaders", []),
                version_type=v.get("version_type", "release"),
                downloads=v["downloads"],
                date_published=v["date_published"],
                files=[
                    ModrinthVersionFile(
                        filename=f["filename"],
                        url=f["url"],
                        size=f["size"],
                        sha1=f["hashes"].get("sha1", ""),
                        sha512=f["hashes"].get("sha512", ""),
                        primary=f.get("primary", False),
                    )
                    for f in v.get("files", [])
                ],
                dependencies=[
                    ModrinthDependency(
                        project_id=d.get("project_id"),
                        version_id=d.get("version_id"),
                        file_name=d.get("file_name"),
                        dependency_type=d.get("dependency_type", "required"),
                    )
                    for d in v.get("dependencies", [])
                ],
            ))
        return versions

    def get_version(self, version_id: str) -> ModrinthVersion:
        resp = self._client.get(f"/version/{version_id}")
        resp.raise_for_status()
        v = resp.json()
        return ModrinthVersion(
            version_id=v["id"],
            project_id=v["project_id"],
            name=v["name"],
            version_number=v["version_number"],
            game_versions=v.get("game_versions", []),
            loaders=v.get("loaders", []),
            version_type=v.get("version_type", "release"),
            downloads=v["downloads"],
            date_published=v["date_published"],
            files=[
                ModrinthVersionFile(
                    filename=f["filename"],
                    url=f["url"],
                    size=f["size"],
                    sha1=f["hashes"].get("sha1", ""),
                    sha512=f["hashes"].get("sha512", ""),
                    primary=f.get("primary", False),
                )
                for f in v.get("files", [])
            ],
            dependencies=[
                ModrinthDependency(
                    project_id=d.get("project_id"),
                    version_id=d.get("version_id"),
                    file_name=d.get("file_name"),
                    dependency_type=d.get("dependency_type", "required"),
                )
                for d in v.get("dependencies", [])
            ],
        )

    def get_project_dependencies(self, project_id: str) -> dict[str, Any]:
        resp = self._client.get(f"/project/{project_id}/dependencies")
        resp.raise_for_status()
        return resp.json()

    def get_version_from_hash(self, sha1_hash: str, algorithm: str = "sha1") -> ModrinthVersion:
        resp = self._client.get(f"/version_file/{sha1_hash}", params={"algorithm": algorithm})
        resp.raise_for_status()
        v = resp.json()
        return ModrinthVersion(
            version_id=v["id"],
            project_id=v["project_id"],
            name=v["name"],
            version_number=v["version_number"],
            game_versions=v.get("game_versions", []),
            loaders=v.get("loaders", []),
            version_type=v.get("version_type", "release"),
            downloads=v["downloads"],
            date_published=v["date_published"],
            files=[
                ModrinthVersionFile(
                    filename=f["filename"],
                    url=f["url"],
                    size=f["size"],
                    sha1=f["hashes"].get("sha1", ""),
                    sha512=f["hashes"].get("sha512", ""),
                    primary=f.get("primary", False),
                )
                for f in v.get("files", [])
            ],
            dependencies=[
                ModrinthDependency(
                    project_id=d.get("project_id"),
                    version_id=d.get("version_id"),
                    file_name=d.get("file_name"),
                    dependency_type=d.get("dependency_type", "required"),
                )
                for d in v.get("dependencies", [])
            ],
        )

    def get_loaders(self) -> list[dict[str, Any]]:
        resp = self._client.get("/tag/loader")
        resp.raise_for_status()
        return resp.json()

    def get_game_versions(self) -> list[str]:
        resp = self._client.get("/tag/game_version")
        resp.raise_for_status()
        data = resp.json()
        return [v["version"] for v in data]

    def download_file(self, url: str, dest_path: str) -> None:
        logger.info("Downloading %s -> %s", url, dest_path)
        with self._client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(dest_path, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=8192):
                    f.write(chunk)
        logger.info("Downloaded %s", dest_path)
