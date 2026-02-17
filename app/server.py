from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException

from app.config import load_settings
from app.feishu import handle_feishu_event
from app.logging_utils import setup_logging
from app.pipeline import run_daily_pipeline

app = FastAPI(title="AI Daily Brief Backend", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/run-today")
def run_today() -> dict:
    settings = load_settings()
    brief = run_daily_pipeline(push=False)
    date_str = brief.date.isoformat()
    return {
        "title": brief.title,
        "date": date_str,
        "items": len(brief.items),
        "archives": {
            "markdown": str(Path(settings.archives_dir) / f"{date_str}.md"),
            "json": str(Path(settings.archives_dir) / f"{date_str}.json"),
        },
    }


@app.get("/latest")
def latest() -> dict:
    settings = load_settings()
    archives_dir = Path(settings.archives_dir)
    files = sorted(archives_dir.glob("*.json"), reverse=True)
    if not files:
        raise HTTPException(status_code=404, detail="No archives found")

    latest_file = files[0]
    data = json.loads(latest_file.read_text(encoding="utf-8"))
    return {
        "file": str(latest_file),
        "brief": data,
    }


@app.post("/feishu/events")
def feishu_events(payload: dict, background_tasks: BackgroundTasks) -> dict:
    settings = load_settings()
    result = handle_feishu_event(payload, settings, background_tasks)
    if result.get("ok") is False:
        raise HTTPException(status_code=403, detail=result.get("error", "forbidden"))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Daily Brief backend service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    settings = load_settings()
    setup_logging(settings.log_level)

    feishu_gateway = None
    if settings.feishu_enabled and settings.feishu_connection_mode == "websocket":
        from app.feishu_ws import FeishuLongConnectionGateway

        feishu_gateway = FeishuLongConnectionGateway(settings_path="config/settings.yaml")
        feishu_gateway.start_in_background()

    try:
        uvicorn.run("app.server:app", host=args.host, port=args.port, reload=False)
    finally:
        if feishu_gateway is not None:
            feishu_gateway.stop()


if __name__ == "__main__":
    main()
