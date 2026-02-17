from __future__ import annotations

import argparse
import logging
import threading
import time
from typing import Any

from app.config import load_settings
from app.feishu import handle_feishu_event
from app.logging_utils import setup_logging
from app.models import Settings

logger = logging.getLogger(__name__)

try:
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

    FEISHU_SDK_AVAILABLE = True
except ImportError:
    FEISHU_SDK_AVAILABLE = False
    lark = None  # type: ignore[assignment]
    P2ImMessageReceiveV1 = Any  # type: ignore[misc,assignment]


class _ThreadBackgroundTasks:
    def add_task(self, func, *args, **kwargs) -> None:
        thread = threading.Thread(target=func, args=args, kwargs=kwargs, daemon=True)
        thread.start()


class FeishuLongConnectionGateway:
    def __init__(self, settings_path: str = "config/settings.yaml") -> None:
        self.settings_path = settings_path
        self.settings: Settings = load_settings(settings_path)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._ws_client: Any | None = None

    def _build_message_payload(self, data: P2ImMessageReceiveV1) -> dict:
        event = getattr(data, "event", None)
        if event is None:
            return {}

        sender = getattr(event, "sender", None)
        sender_id = getattr(sender, "sender_id", None)
        message = getattr(event, "message", None)
        if message is None:
            return {}

        mentions_payload: list[dict] = []
        for mention in (getattr(message, "mentions", None) or []):
            mention_id = getattr(mention, "id", None)
            mentions_payload.append(
                {
                    "name": str(getattr(mention, "name", "")),
                    "id": {
                        "open_id": getattr(mention_id, "open_id", None),
                        "user_id": getattr(mention_id, "user_id", None),
                        "union_id": getattr(mention_id, "union_id", None),
                    },
                }
            )

        return {
            "header": {
                "event_id": str(getattr(message, "message_id", "")),
                "event_type": "im.message.receive_v1",
                "token": self.settings.feishu_verification_token,
            },
            "event": {
                "sender": {
                    "sender_id": {
                        "open_id": getattr(sender_id, "open_id", None),
                        "user_id": getattr(sender_id, "user_id", None),
                        "union_id": getattr(sender_id, "union_id", None),
                    }
                },
                "message": {
                    "message_id": str(getattr(message, "message_id", "")),
                    "chat_id": str(getattr(message, "chat_id", "")),
                    "chat_type": str(getattr(message, "chat_type", "")),
                    "message_type": str(getattr(message, "message_type", "")),
                    "content": str(getattr(message, "content", "")),
                    "mentions": mentions_payload,
                },
            },
        }

    def _on_message_sync(self, data: P2ImMessageReceiveV1) -> None:
        payload = self._build_message_payload(data)
        if not payload:
            return
        result = handle_feishu_event(payload, self.settings, _ThreadBackgroundTasks())
        if result.get("ok") is False:
            logger.warning("Feishu message rejected: %s", result)

    def _build_event_handler(self) -> Any:
        builder = lark.EventDispatcherHandler.builder(
            self.settings.feishu_encrypt_key or "",
            self.settings.feishu_verification_token or "",
        ).register_p2_im_message_receive_v1(self._on_message_sync)

        # Some tenants emit p2p chat lifecycle events even when business logic only
        # needs message events. Register no-op handlers when SDK provides them.
        noop = lambda _data: None
        for method_name in (
            "register_p2_im_chat_access_event_bot_p2p_chat_entered_v1",
            "register_p2_im_chat_access_event_bot_p2p_chat_created_v1",
            "register_p2_im_chat_access_event_bot_p2p_chat_create_v1",
        ):
            if hasattr(builder, method_name):
                builder = getattr(builder, method_name)(noop)
        return builder.build()

    def _run_forever(self) -> None:
        if not FEISHU_SDK_AVAILABLE:
            raise RuntimeError("Missing dependency lark-oapi, please install project dependencies first")

        if not self.settings.feishu_app_id or not self.settings.feishu_app_secret:
            raise RuntimeError("Missing feishu_app_id or feishu_app_secret")

        reconnect_seconds = max(1, int(self.settings.feishu_ws_reconnect_seconds))
        logger.info("Feishu WebSocket long connection starting")
        while not self._stop_event.is_set():
            self._ws_client = lark.ws.Client(
                self.settings.feishu_app_id,
                self.settings.feishu_app_secret,
                event_handler=self._build_event_handler(),
                log_level=lark.LogLevel.INFO,
            )
            try:
                self._ws_client.start()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Feishu WebSocket disconnected: %s", exc)
            finally:
                self._ws_client = None
            if not self._stop_event.is_set():
                time.sleep(reconnect_seconds)

    def _run_with_guard(self) -> None:
        try:
            self._run_forever()
        except Exception:  # noqa: BLE001
            logger.exception("Feishu WebSocket gateway failed to start")

    def start_in_background(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_with_guard, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._ws_client is not None:
            try:
                self._ws_client.stop()
            except Exception:  # noqa: BLE001
                logger.exception("Failed to stop Feishu WebSocket client")
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)


def main() -> None:
    parser = argparse.ArgumentParser(description="Feishu WebSocket long-connection gateway")
    parser.add_argument("--settings", default="config/settings.yaml")
    args = parser.parse_args()

    settings = load_settings(args.settings)
    setup_logging(settings.log_level)

    gateway = FeishuLongConnectionGateway(args.settings)
    logger.info("Gateway running in foreground, press Ctrl+C to stop")
    try:
        gateway._run_forever()
    except KeyboardInterrupt:
        logger.info("Stopping Feishu WebSocket gateway")
        gateway.stop()


if __name__ == "__main__":
    main()
