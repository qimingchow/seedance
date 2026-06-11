"""火山引擎 TOS 轻量封装。

为了让模块 2 在未安装 tos 包 / 未配置凭证时也能正常运行，
这里对 tos 采用「惰性导入」，并提供 is_configured() 供调用方判断。
模块 3（人脸素材入库）会用到 upload_bytes 做真实上传。
"""
from __future__ import annotations

from config import settings


def is_configured() -> bool:
    """凭证齐全且 tos 包可导入时才算可用。"""
    if not (settings.TOS_ACCESS_KEY and settings.TOS_SECRET_KEY and settings.TOS_BUCKET):
        return False
    try:
        import tos  # noqa: F401
    except Exception:
        return False
    return True


def _client():
    import tos

    return tos.TosClientV2(
        settings.TOS_ACCESS_KEY,
        settings.TOS_SECRET_KEY,
        settings.TOS_ENDPOINT,
        settings.TOS_REGION,
    )


def public_url(key: str) -> str:
    """对象的访问 URL（按你的桶访问策略，可能需要改成签名 URL）。"""
    host = settings.TOS_ENDPOINT
    return f"https://{settings.TOS_BUCKET}.{host}/{key}"


def upload_bytes(key: str, data: bytes, content_type: str | None = None) -> str:
    """上传字节流到 TOS，返回访问 URL。"""
    if not is_configured():
        raise RuntimeError("TOS 未配置或 tos 包未安装")
    client = _client()
    client.put_object(settings.TOS_BUCKET, key, content=data)
    return public_url(key)


def delete_object(key: str) -> bool:
    """删除对象。未配置或失败时返回 False（best-effort）。"""
    if not key or not is_configured():
        return False
    try:
        _client().delete_object(settings.TOS_BUCKET, key)
        return True
    except Exception:
        return False
