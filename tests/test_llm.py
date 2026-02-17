from app.llm import VolcengineLLMClient, _extract_response_text
from app.models import Settings
from app.pipeline import _build_llm_client


def test_extract_response_text_prefers_output_text() -> None:
    class _Resp:
        output_text = "连接成功"

    assert _extract_response_text(_Resp()) == "连接成功"


def test_extract_response_text_from_output_content() -> None:
    response = {
        "output": [
            {
                "content": [
                    {"type": "output_text", "text": "第一行"},
                    {"type": "output_text", "text": "第二行"},
                ]
            }
        ]
    }
    assert _extract_response_text(response) == "第一行\n第二行"


def test_build_llm_client_uses_volcengine() -> None:
    settings = Settings(
        timezone="Asia/Shanghai",
        schedule_time="09:30",
        llm_provider="volcengine",
        llm_model="doubao-seed-1-8-251228",
        volcengine_base_url="https://ark.cn-beijing.volces.com/api/v3",
        ark_api_key="test-key",
    )

    client = _build_llm_client(settings)
    assert isinstance(client, VolcengineLLMClient)
