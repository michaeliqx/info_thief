from __future__ import annotations

from app.config import load_settings
from app.llm import VolcengineLLMClient
from app.logging_utils import setup_logging


def main() -> None:
    settings = load_settings()
    setup_logging(settings.log_level)

    if not settings.ark_api_key:
        raise RuntimeError("缺少 ARK_API_KEY，请先 export ARK_API_KEY='你的key'")

    client = VolcengineLLMClient(
        api_key=settings.ark_api_key,
        base_url=settings.volcengine_base_url,
        model=settings.llm_model,
    )

    ping_text = client.ping()
    print("[Doubao Ping]", ping_text)

    points = client.summarize_item(
        title="测试：大模型接入验证",
        content="请将这段话总结为2-4个关键点，用于验证模型接入链路。",
        source_name="system",
        url="https://example.com",
    )
    print("[Doubao Summary]", points)


if __name__ == "__main__":
    main()
