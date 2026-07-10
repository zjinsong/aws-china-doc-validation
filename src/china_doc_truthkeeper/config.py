from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_path: Path
    aws_region: str
    qwen_api_key: str | None
    qwen_base_url: str
    qwen_model: str


def get_settings() -> Settings:
    return Settings(
        database_path=Path(os.getenv("TRUTHKEEPER_DB_PATH", "data/truthkeeper.db")),
        aws_region=os.getenv("AWS_DEFAULT_REGION", "cn-north-1"),
        qwen_api_key=os.getenv("QWEN_API_KEY") or None,
        qwen_base_url=os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/"),
        qwen_model=os.getenv("QWEN_MODEL", "qwen3-235b-vl"),
    )
