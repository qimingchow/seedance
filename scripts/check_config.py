"""检查本地配置是否足够启动服务，不打印任何密钥。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings
from database import init_db
import tos_client
import seedance


def _mask(value: str) -> str:
    if not value:
        return "未配置"
    if len(value) <= 8:
        return "已配置"
    return f"{value[:4]}...{value[-4:]}"


def main() -> None:
    init_db()
    print("DATABASE_URL:", settings.DATABASE_URL)
    print("ACCOUNT_TOTAL_TOKENS:", settings.ACCOUNT_TOTAL_TOKENS)
    print("ACCOUNT_EXTERNAL_USED_TOKENS:", settings.ACCOUNT_EXTERNAL_USED_TOKENS)
    print("ARK_API_KEY:", _mask(settings.ARK_API_KEY))
    print("ARK_BASE_URL:", settings.ARK_BASE_URL)
    print("SEEDANCE_MODEL_PRO:", settings.SEEDANCE_MODEL_PRO)
    print("SEEDANCE_MODEL_FAST:", settings.SEEDANCE_MODEL_FAST)
    print("SEEDANCE_DEFAULT_MODEL_KEY:", settings.SEEDANCE_DEFAULT_MODEL_KEY)
    print("SEEDANCE_ENABLE_PRO:", settings.SEEDANCE_ENABLE_PRO)
    print("SEEDANCE_ENABLE_FAST:", settings.SEEDANCE_ENABLE_FAST)
    print("SEEDANCE_ENABLED_MODELS:", ",".join(seedance.enabled_model_keys()) or "无")
    print("TOS_BUCKET:", settings.TOS_BUCKET or "未配置")
    print("TOS_ENDPOINT:", settings.TOS_ENDPOINT)
    print("TOS_CONFIGURED:", tos_client.is_configured())
    print("TOKEN_UNIT_PRICE_WITH_VIDEO_YUAN:", settings.TOKEN_UNIT_PRICE_WITH_VIDEO_YUAN)
    print("TOKEN_UNIT_PRICE_NO_VIDEO_YUAN:", settings.TOKEN_UNIT_PRICE_NO_VIDEO_YUAN)
    print("TOKEN_ESTIMATE_SAFETY_MULTIPLIER:", settings.TOKEN_ESTIMATE_SAFETY_MULTIPLIER)
    print("DB_INIT:", "OK")


if __name__ == "__main__":
    main()
