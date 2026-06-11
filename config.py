"""集中读取环境变量配置。"""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    # 数据库
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///seedance_studio.db")

    # 火山方舟 Ark（Seedance）—— 第二步使用
    ARK_API_KEY: str = os.getenv("ARK_API_KEY", "")
    ARK_BASE_URL: str = os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
    SEEDANCE_MODEL_PRO: str = os.getenv("SEEDANCE_MODEL_PRO", "")
    SEEDANCE_MODEL_FAST: str = os.getenv("SEEDANCE_MODEL_FAST", "")

    # 火山引擎 TOS —— 第二、三步使用
    TOS_ACCESS_KEY: str = os.getenv("TOS_ACCESS_KEY", "")
    TOS_SECRET_KEY: str = os.getenv("TOS_SECRET_KEY", "")
    TOS_ENDPOINT: str = os.getenv("TOS_ENDPOINT", "tos-cn-beijing.volces.com")
    TOS_REGION: str = os.getenv("TOS_REGION", "cn-beijing")
    TOS_BUCKET: str = os.getenv("TOS_BUCKET", "")

    # 1 token = 多少元（用于展示费用）
    TOKEN_UNIT_PRICE_YUAN: float = float(os.getenv("TOKEN_UNIT_PRICE_YUAN", "0") or 0)


settings = Settings()
