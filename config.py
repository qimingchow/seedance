"""集中读取环境变量配置。"""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw.strip())


@dataclass(frozen=True)
class Settings:
    # 数据库
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///seedance_studio.db")
    ACCOUNT_TOTAL_TOKENS: int = _env_int("ACCOUNT_TOTAL_TOKENS", 0)
    ACCOUNT_EXTERNAL_USED_TOKENS: int = _env_int("ACCOUNT_EXTERNAL_USED_TOKENS", 0)

    # 火山方舟 Ark（Seedance）—— 第二步使用
    ARK_API_KEY: str = os.getenv("ARK_API_KEY", "")
    ARK_BASE_URL: str = os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
    SEEDANCE_MODEL_PRO: str = os.getenv(
        "SEEDANCE_MODEL_PRO", "doubao-seedance-2-0-260128"
    )
    SEEDANCE_MODEL_FAST: str = os.getenv(
        "SEEDANCE_MODEL_FAST", "doubao-seedance-2-0-fast-260128"
    )
    SEEDANCE_DEFAULT_MODEL_KEY: str = (
        os.getenv("SEEDANCE_DEFAULT_MODEL_KEY", "pro").strip().lower() or "pro"
    )
    SEEDANCE_ENABLE_PRO: bool = _env_bool("SEEDANCE_ENABLE_PRO", True)
    SEEDANCE_ENABLE_FAST: bool = _env_bool("SEEDANCE_ENABLE_FAST", False)

    # 火山引擎 TOS —— 第二、三步使用
    TOS_ACCESS_KEY: str = os.getenv("TOS_ACCESS_KEY", "")
    TOS_SECRET_KEY: str = os.getenv("TOS_SECRET_KEY", "")
    TOS_ENDPOINT: str = os.getenv("TOS_ENDPOINT", "tos-cn-beijing.volces.com")
    TOS_REGION: str = os.getenv("TOS_REGION", "cn-beijing")
    TOS_BUCKET: str = os.getenv("TOS_BUCKET", "")
    TOS_URL_MODE: str = os.getenv("TOS_URL_MODE", "public").strip().lower()
    TOS_SIGNED_URL_EXPIRES_SECONDS: int = _env_int(
        "TOS_SIGNED_URL_EXPIRES_SECONDS", 604800
    )
    TOS_UPLOAD_ACL: str = os.getenv("TOS_UPLOAD_ACL", "default").strip().lower()

    # 1 token = 多少元（用于展示费用）
    TOKEN_UNIT_PRICE_YUAN: float = float(os.getenv("TOKEN_UNIT_PRICE_YUAN", "0") or 0)
    TOKEN_UNIT_PRICE_WITH_VIDEO_YUAN: float = float(
        os.getenv("TOKEN_UNIT_PRICE_WITH_VIDEO_YUAN", os.getenv("TOKEN_UNIT_PRICE_YUAN", "0")) or 0
    )
    TOKEN_UNIT_PRICE_NO_VIDEO_YUAN: float = float(
        os.getenv("TOKEN_UNIT_PRICE_NO_VIDEO_YUAN", os.getenv("TOKEN_UNIT_PRICE_YUAN", "0")) or 0
    )
    TOKEN_ESTIMATE_SAFETY_MULTIPLIER: float = float(
        os.getenv("TOKEN_ESTIMATE_SAFETY_MULTIPLIER", "1.10") or 1.0
    )


settings = Settings()
