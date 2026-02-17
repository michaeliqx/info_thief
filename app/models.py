from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


class Perspective(str, Enum):
    PRODUCT = "product"
    TECHNOLOGY = "technology"
    INDUSTRY = "industry"


class RawItem(BaseModel):
    source_name: str
    source_weight: float = 1.0
    url: str
    title: str
    content: str = ""
    published_at: Optional[datetime] = None
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    tags: list[str] = Field(default_factory=list)


class NormalizedItem(BaseModel):
    item_id: str
    source_name: str
    source_weight: float
    url: str
    canonical_url: str
    title: str
    content: str
    published_at: Optional[datetime]
    discovered_at: datetime
    language: Literal["zh", "en", "mixed", "unknown"]
    tags: list[str] = Field(default_factory=list)


class ClassifiedItem(NormalizedItem):
    perspective: Perspective
    classification_source: Literal["rule", "llm", "fallback"] = "rule"


class RankedItem(ClassifiedItem):
    score: float
    rank_reason: str


class BriefItem(BaseModel):
    perspective: Perspective
    title: str
    key_points: list[str]
    source_name: str
    url: str
    score: float


class DailyBrief(BaseModel):
    date: date
    title: str
    intro: str
    items: list[BriefItem]
    observations: list[str]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SourceConfig(BaseModel):
    name: str
    type: Literal["rss", "html"]
    url: str
    article_selector: Optional[str] = None
    link_pattern: Optional[str] = None
    weight: float = 1.0
    enabled: bool = True
    tags: list[str] = Field(default_factory=list)


class Settings(BaseModel):
    timezone: str
    schedule_time: str
    collector_trigger_time: str = "09:20"
    item_min: int = 8
    item_max: int = 12
    mix_min_each: int = 2
    max_items_per_source: int = 2
    llm_provider: Literal["openai", "volcengine"] = "volcengine"
    llm_model: str = "doubao-seed-1-8-251228"
    volcengine_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    ark_api_key: str = ""
    push_enabled: bool = False
    wechat_webhook: str = ""
    feishu_enabled: bool = False
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_verification_token: str = ""
    feishu_encrypt_key: str = ""
    feishu_base_url: str = "https://open.feishu.cn"
    feishu_connection_mode: Literal["webhook", "websocket"] = "webhook"
    feishu_ws_reconnect_seconds: int = 5
    feishu_push_targets: list[str] = Field(default_factory=list)
    feishu_receive_id_type: Literal["chat_id", "open_id"] = "chat_id"
    feishu_allow_from: list[str] = Field(default_factory=list)
    feishu_require_mention: bool = True
    openai_api_key: str = ""
    request_timeout_seconds: int = 15
    db_path: str = "data/state.db"
    archives_dir: str = "archives"
    log_level: str = "INFO"
