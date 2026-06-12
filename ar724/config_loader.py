"""Config loader — safety policy, model profiles, role routing.

PRD §14.1, §14.2, §15.1. Loads YAML configs from the controller's config/
directory at startup. Provides a single read-only interface; mutations go
through the CLI (which writes the YAML and sends SIGHUP).
"""

from __future__ import annotations

import os
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_DEFAULT_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

_lock = threading.RLock()
_overrides: dict[str, dict[str, Any]] = {}


def config_dir() -> Path:
    """Return the active config directory (env override > default)."""
    env = os.environ.get("AR724_CONFIG_DIR")
    return Path(env) if env else _DEFAULT_CONFIG_DIR


def _load_yaml(name: str) -> dict[str, Any]:
    path = config_dir() / name
    if not path.exists():
        return {}
    with path.open() as f:
        return yaml.safe_load(f) or {}


@lru_cache(maxsize=8)
def get_safety_policy() -> dict[str, Any]:
    """Load and cache safety_policy.yaml. Override via env AR724_SAFETY_PATH."""
    env = os.environ.get("AR724_SAFETY_PATH")
    if env:
        with Path(env).open() as f:
            return yaml.safe_load(f) or {}
    return _load_yaml("safety_policy.yaml")


def get_model_profiles() -> dict[str, dict[str, Any]]:
    """Return {profile_name: profile_dict} from model_profiles.yaml."""
    data = _load_yaml("model_profiles.yaml")
    return data.get("profiles", {})


def get_role_routing() -> dict[str, dict[str, Any]]:
    """Return {role_id: routing_dict} from role_routing.yaml."""
    data = _load_yaml("role_routing.yaml")
    return data.get("routing", {})


def get_mcp_allowlist() -> list[dict[str, Any]]:
    """Return the MCP allowlist from safety_policy.yaml."""
    policy = get_safety_policy()
    return policy.get("mcp_allowlist", [])


def get_loop_config(loop_config_path: Path | None = None) -> dict[str, Any]:
    """Load .ares/loop_config.json. Path defaults to AR724_LOOP_CONFIG env or .ares/loop_config.json."""
    if loop_config_path is None:
        env = os.environ.get("AR724_LOOP_CONFIG")
        loop_config_path = Path(env) if env else Path(".ares/loop_config.json")
    if not loop_config_path.exists():
        return {}
    with loop_config_path.open() as f:
        return json_load(f)


def json_load(path: Path) -> dict[str, Any]:
    import json
    with path.open() as f:
        return json.load(f)


def write_loop_config(data: dict[str, Any], loop_config_path: Path | None = None) -> None:
    """Write .ares/loop_config.json atomically."""
    import json
    from .db import atomic_write
    if loop_config_path is None:
        env = os.environ.get("AR724_LOOP_CONFIG")
        loop_config_path = Path(env) if env else Path(".ares/loop_config.json")
    loop_config_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(loop_config_path, json.dumps(data, indent=2, sort_keys=True))
