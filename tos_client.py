"""火山引擎 TOS 轻量封装。

为了让模块 2 在未安装 tos 包 / 未配置凭证时也能正常运行，
这里对 tos 采用「惰性导入」，并提供 is_configured() 供调用方判断。
模块 3（人脸素材入库）会用到 upload_bytes 做真实上传。
"""
from __future__ import annotations

import requests
from urllib.parse import quote, unquote, urlsplit

from config import settings
from ops import get_logger

logger = get_logger(__name__)


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


def _upload_acl():
    """按配置返回对象上传 ACL。默认不覆盖桶策略。"""
    import tos

    acl_map = {
        "default": None,
        "": None,
        "private": tos.ACLType.ACL_Private,
        "public-read": tos.ACLType.ACL_Public_Read,
        "public": tos.ACLType.ACL_Public_Read,
    }
    value = settings.TOS_UPLOAD_ACL
    if value not in acl_map:
        raise RuntimeError(
            "TOS_UPLOAD_ACL 只支持 default、private、public-read"
        )
    return acl_map[value]


def public_url(key: str) -> str:
    """对象的访问 URL（按你的桶访问策略，可能需要改成签名 URL）。"""
    host = settings.TOS_ENDPOINT
    encoded_key = quote(key, safe="/")
    return f"https://{settings.TOS_BUCKET}.{host}/{encoded_key}"


def signed_url(key: str, expires: int | None = None) -> str:
    """生成临时签名 GET URL，用于桶公共读不可用时给浏览器/Ark 读取。"""
    if not is_configured():
        return public_url(key)
    import tos

    ttl = int(expires or settings.TOS_SIGNED_URL_EXPIRES_SECONDS)
    ttl = max(60, min(ttl, 604800))
    output = _client().pre_signed_url(
        tos.HttpMethodType.Http_Method_Get,
        settings.TOS_BUCKET,
        key,
        expires=ttl,
    )
    return output.signed_url


def access_url(key: str) -> str:
    """素材读取 URL。public 模式走匿名访问，signed 模式走临时签名。"""
    if settings.TOS_URL_MODE == "signed":
        return signed_url(key)
    return public_url(key)


def object_key_from_url(url: str) -> str:
    """从本桶公开 URL 中还原对象 key；非本桶 URL 返回空字符串。"""
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    expected_host = f"{settings.TOS_BUCKET}.{settings.TOS_ENDPOINT}"
    if parts.netloc != expected_host:
        return ""
    return unquote(parts.path.lstrip("/"))


def access_url_for_stored_url(url: str, key: str = "") -> str:
    """把数据库里保存的 TOS URL/key 转为当前配置下可访问的 URL。"""
    object_key = key or object_key_from_url(url)
    if object_key:
        return access_url(object_key)
    return url


def upload_bytes(key: str, data: bytes, content_type: str | None = None) -> str:
    """上传字节流到 TOS，返回访问 URL。"""
    if not is_configured():
        raise RuntimeError("TOS 未配置或 tos 包未安装")
    client = _client()
    client.put_object(
        settings.TOS_BUCKET,
        key,
        content=data,
        content_type=content_type,
        acl=_upload_acl(),
    )
    logger.info("uploaded tos://%s/%s bytes=%s", settings.TOS_BUCKET, key, len(data))
    return public_url(key)


def mirror_url(key: str, source_url: str, content_type: str | None = None) -> str:
    """下载一个远程 URL 并转存到自己的 TOS 桶。"""
    if not is_configured():
        raise RuntimeError("TOS 未配置或 tos 包未安装")
    resp = requests.get(source_url, timeout=120)
    if resp.status_code >= 400:
        raise RuntimeError(f"下载远程文件失败（{resp.status_code}）：{resp.text[:200]}")
    return upload_bytes(
        key,
        resp.content,
        content_type=content_type or resp.headers.get("content-type"),
    )


def delete_object(key: str) -> bool:
    """删除对象。未配置或失败时返回 False（best-effort）。"""
    if not key or not is_configured():
        return False
    try:
        _client().delete_object(settings.TOS_BUCKET, key)
        logger.info("deleted tos://%s/%s", settings.TOS_BUCKET, key)
        return True
    except Exception as exc:
        logger.warning("delete tos://%s/%s failed: %s", settings.TOS_BUCKET, key, exc)
        return False
