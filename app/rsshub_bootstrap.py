from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from app.models import SourceConfig

logger = logging.getLogger(__name__)


def _detect_local_rsshub(sources: list[SourceConfig]) -> tuple[str, int] | None:
    for source in sources:
        parsed = urlsplit(source.url)
        host = (parsed.hostname or "").strip().lower()
        if host not in {"127.0.0.1", "localhost", "0.0.0.0"}:
            continue
        if not (parsed.path.startswith("/huxiu/") or parsed.path.startswith("/freewechat/")):
            continue
        port = parsed.port or 80
        return host, port
    return None


def ensure_rsshub_for_sources(sources: list[SourceConfig]) -> None:
    target = _detect_local_rsshub(sources)
    if target is None:
        return

    script = Path("scripts/rsshub/ensure_rsshub.sh")
    if not script.exists():
        logger.warning("RSSHub local source detected but ensure script missing: %s", script)
        return

    host, port = target
    env = os.environ.copy()
    env.setdefault("RSSHUB_HOST", host)
    env.setdefault("RSSHUB_PORT", str(port))

    try:
        subprocess.run([str(script)], check=True, env=env)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to ensure RSSHub for local sources: %s", exc)
