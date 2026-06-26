"""Configuration loader with profile support for MSM."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import tomllib
import tomli_w

DEFAULT_PROFILE = "default"


def _default_profile() -> dict[str, Any]:
    return {
        "mods_dir": "../mods",
        "client_mods_dir": "../client_mods",
        "loader": "neoforge",
        "game_version": "1.21.1",
        "run_script": "../run.sh",
        "server_dir": "..",
    }


def load_config(config_path: str | None = None) -> dict[str, Any]:
    if config_path is None:
        config_path = os.getenv("MSM_CONFIG", "config.toml")

    config_file = Path(config_path)
    if not config_file.is_file():
        raise FileNotFoundError(f"Config file not found: {config_file}")

    with open(config_file, "rb") as f:
        config = tomllib.load(f)

    cf_key = os.getenv("CF_API_KEY", "")
    if cf_key:
        config.setdefault("apis", {})["curseforge_api_key"] = cf_key

    return config


def save_config(config: dict[str, Any], config_path: str | None = None) -> None:
    if config_path is None:
        config_path = os.getenv("MSM_CONFIG", "config.toml")

    with open(config_path, "wb") as f:
        tomli_w.dump(config, f)


def get_active_profile_name(config: dict[str, Any]) -> str:
    return config.get("active", {}).get("profile", DEFAULT_PROFILE)


def set_active_profile_name(config: dict[str, Any], name: str) -> None:
    config.setdefault("active", {})["profile"] = name


def get_profile(config: dict[str, Any], name: str | None = None) -> dict[str, Any]:
    profile_name = name or get_active_profile_name(config)
    profiles = config.get("profiles", {})
    return profiles.get(profile_name, _default_profile())


def list_profiles(config: dict[str, Any]) -> list[str]:
    return list(config.get("profiles", {}).keys())


def create_profile(config: dict[str, Any], name: str, settings: dict[str, Any] | None = None) -> None:
    config.setdefault("profiles", {})[name] = settings or _default_profile()


def delete_profile(config: dict[str, Any], name: str) -> bool:
    profiles = config.get("profiles", {})
    if name not in profiles:
        return False
    del profiles[name]
    active = get_active_profile_name(config)
    if active == name:
        remaining = list(profiles.keys())
        set_active_profile_name(config, remaining[0] if remaining else DEFAULT_PROFILE)
        if DEFAULT_PROFILE not in profiles:
            profiles[DEFAULT_PROFILE] = _default_profile()
    return True


def get_global_config(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "apis": config.get("apis", {}),
        "modrinth": config.get("modrinth", {}),
        "download": config.get("download", {}),
    }
