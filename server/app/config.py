"""运行时配置：全部来自环境变量，便于 Docker 部署。"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # 未安装时退化为纯环境变量
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()


class Settings:
    def __init__(self) -> None:
        # LLM
        self.llm_api_key: str = os.getenv("LLM_API_KEY", "")
        self.llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
        self.llm_model: str = os.getenv("LLM_MODEL", "deepseek-chat")

        # 定位
        self.min_confidence: float = float(os.getenv("MIN_CONFIDENCE", "0.60"))
        self.locate_samples: int = int(os.getenv("LOCATE_SAMPLES", "1"))

        # 存储
        self.data_dir: Path = Path(os.getenv("DATA_DIR", "./data"))
        self.max_upload_mb: int = int(os.getenv("MAX_UPLOAD_MB", "8"))

        # 截图保留天数（定稿 §6.6：7 天自动清理）
        self.retention_days: int = int(os.getenv("RETENTION_DAYS", "7"))

    @property
    def screenshots_dir(self) -> Path:
        return self.data_dir / "screenshots"


settings = Settings()
