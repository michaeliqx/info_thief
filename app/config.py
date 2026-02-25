from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from app.env_utils import load_local_env
from app.models import Settings, SourceConfig

_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


def _resolve_env_string(value: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        env_name = match.group(1)
        default = match.group(2)
        env_value = os.getenv(env_name)
        if env_value:
            return env_value
        return default or ""

    return _ENV_PATTERN.sub(_replace, value)


def _resolve_env_value(value: Any) -> Any:
    if isinstance(value, str):
        return _resolve_env_string(value)
    if isinstance(value, dict):
        return {k: _resolve_env_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_value(v) for v in value]
    return value


def _load_yaml(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return _resolve_env_value(data)


def load_settings(path: str = "config/settings.yaml") -> Settings:
    load_local_env(path)
    raw = _load_yaml(path)
    settings = Settings(**raw)
    Path(settings.archives_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    return settings


def load_sources(path: str = "config/sources.yaml") -> list[SourceConfig]:
    load_local_env(path)
    raw = _load_yaml(path)
    sources = raw.get("sources", [])
    return [SourceConfig(**item) for item in sources if item.get("enabled", True)]
