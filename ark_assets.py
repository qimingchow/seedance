"""火山方舟 OpenAPI 资产组 / 素材资产客户端。

参考旧版 Seedance Studio 的做法：
- CreateAssetGroup 创建 AIGC 资产组
- ListAssetGroups 拉取资产组
- CreateAsset 把 TOS 签名 URL 注册为 Ark 素材
- GetAsset 轮询到 Active 后得到可用于模型侧引用的资产 ID/URL

凭证从 .env 读取，不在代码里硬编码 AK/SK。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import quote

import requests

from config import settings
from ops import get_logger

logger = get_logger(__name__)

SERVICE = "ark"
CONTENT_TYPE = "application/json"
PATH = "/"


class ArkAssetError(Exception):
    def __init__(self, message: str, *, code: str = "", status_code: int | None = None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def is_subscription_required(exc: BaseException) -> bool:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current and id(current) not in seen:
        seen.add(id(current))
        message = str(current)
        if (
            getattr(current, "code", "") == "SubscriptionRequired"
            or "SubscriptionRequired" in message
            or "AIGC asset capability is not available" in message
        ):
            return True
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
    return False


def capability_hint(exc: BaseException | None = None) -> str:
    if exc and is_subscription_required(exc):
        return (
            "当前火山账号/套餐未开通 Ark AIGC 资产能力，所以无法调用资产组和素材资产 OpenAPI。"
            "需要在火山方舟开通该能力后，才能使用 asset://AssetId 方式引用真人素材。"
            "未开通前可以把 ARK_ASSET_SYNC_ENABLED=false，先使用本地 TOS 入库和人工审核流程。"
        )
    return ""


def _setting(name: str, default: Any = "") -> Any:
    return getattr(settings, name, default)


def _ark_access_key() -> str:
    return str(_setting("ARK_OPENAPI_ACCESS_KEY", _setting("TOS_ACCESS_KEY", "")) or "")


def _ark_secret_key() -> str:
    return str(_setting("ARK_OPENAPI_SECRET_KEY", _setting("TOS_SECRET_KEY", "")) or "")


def _ark_host() -> str:
    return str(_setting("ARK_OPENAPI_HOST", "ark.cn-beijing.volcengineapi.com") or "")


def _ark_region() -> str:
    return str(_setting("ARK_OPENAPI_REGION", "cn-beijing") or "")


def _ark_version() -> str:
    return str(_setting("ARK_OPENAPI_VERSION", "2024-01-01") or "")


def _ark_project_name() -> str:
    return str(_setting("ARK_ASSET_PROJECT_NAME", "") or "").strip()


def _ark_poll_interval_seconds() -> int:
    return max(int(_setting("ARK_ASSET_POLL_INTERVAL_SECONDS", 3) or 3), 1)


def _ark_poll_timeout_seconds() -> int:
    return max(int(_setting("ARK_ASSET_POLL_TIMEOUT_SECONDS", 120) or 120), 1)


def is_configured() -> bool:
    return bool(
        bool(_setting("ARK_ASSET_SYNC_ENABLED", True))
        and _ark_access_key()
        and _ark_secret_key()
    )


def _norm_query(params: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in sorted(params.keys()):
        value = params[key]
        if isinstance(value, list):
            for item in value:
                parts.append(
                    f"{quote(key, safe='-_.~')}={quote(str(item), safe='-_.~')}"
                )
        else:
            parts.append(
                f"{quote(key, safe='-_.~')}={quote(str(value), safe='-_.~')}"
            )
    return "&".join(parts).replace("+", "%20")


def _hmac_sha256(key: bytes, content: str) -> bytes:
    return hmac.new(key, content.encode("utf-8"), hashlib.sha256).digest()


def _hash_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _extract_nested(data: Any, *keys: str) -> str:
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
        if current is None:
            return ""
    if isinstance(current, str):
        return current
    return "" if current is None else str(current)


def _request(action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if not is_configured():
        raise ArkAssetError("Ark 资产 OpenAPI 未配置或未启用", code="NotConfigured")

    body = json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":"))
    now = dt.datetime.utcnow()
    x_date = now.strftime("%Y%m%dT%H%M%SZ")
    short_date = x_date[:8]
    content_hash = _hash_sha256(body)
    query = {
        "Action": action,
        "Version": _ark_version(),
    }
    host = _ark_host()
    region = _ark_region()
    access_key = _ark_access_key()
    secret_key = _ark_secret_key()
    canonical_request = "\n".join(
        [
            "POST",
            PATH,
            _norm_query(query),
            "\n".join(
                [
                    f"content-type:{CONTENT_TYPE}",
                    f"host:{host}",
                    f"x-content-sha256:{content_hash}",
                    f"x-date:{x_date}",
                ]
            ),
            "",
            "content-type;host;x-content-sha256;x-date",
            content_hash,
        ]
    )
    credential_scope = "/".join(
        [short_date, region, SERVICE, "request"]
    )
    string_to_sign = "\n".join(
        [
            "HMAC-SHA256",
            x_date,
            credential_scope,
            _hash_sha256(canonical_request),
        ]
    )
    key_date = _hmac_sha256(
        secret_key.encode("utf-8"), short_date
    )
    key_region = _hmac_sha256(key_date, region)
    key_service = _hmac_sha256(key_region, SERVICE)
    key_signing = _hmac_sha256(key_service, "request")
    signature = _hmac_sha256(key_signing, string_to_sign).hex()

    headers = {
        "Host": host,
        "X-Content-Sha256": content_hash,
        "X-Date": x_date,
        "Content-Type": CONTENT_TYPE,
        "Authorization": (
            "HMAC-SHA256 "
            f"Credential={access_key}/{credential_scope}, "
            "SignedHeaders=content-type;host;x-content-sha256;x-date, "
            f"Signature={signature}"
        ),
    }
    url = f"https://{host}{PATH}"
    try:
        response = requests.post(
            url,
            headers=headers,
            params=query,
            data=body,
            timeout=30,
        )
    except requests.RequestException as exc:
        raise ArkAssetError(f"{action} 请求异常：{exc}") from exc

    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code >= 400:
        message = (
            _extract_nested(data, "ResponseMetadata", "Error", "Message")
            or _extract_nested(data, "error", "message")
            or response.text[:500]
        )
        code = (
            _extract_nested(data, "ResponseMetadata", "Error", "Code")
            or _extract_nested(data, "error", "code")
        )
        prefix = f"{action} 失败（HTTP {response.status_code}）"
        if code:
            prefix += f" [{code}]"
        raise ArkAssetError(
            f"{prefix}：{message}",
            code=code,
            status_code=response.status_code,
        )
    if not isinstance(data, dict):
        raise ArkAssetError(f"{action} 返回非对象 JSON：{response.text[:500]}")
    return data


def _with_project(payload: dict[str, Any]) -> dict[str, Any]:
    project_name = _ark_project_name()
    if project_name:
        payload = dict(payload)
        payload["ProjectName"] = project_name
    return payload


def create_asset_group(name: str, description: str = "") -> str:
    payload = _with_project(
        {
            "Name": name,
            "Description": description,
            "GroupType": "AIGC",
        }
    )
    data = _request("CreateAssetGroup", payload)
    group_id = (
        _extract_nested(data, "Result", "Id")
        or _extract_nested(data, "Result", "GroupId")
        or _extract_nested(data, "Id")
        or _extract_nested(data, "GroupId")
    )
    if not group_id:
        raise ArkAssetError(f"CreateAssetGroup 返回缺少 GroupId：{data}")
    logger.info("created ark asset group %s (%s)", group_id, name)
    return group_id


def list_asset_groups(name_filter: str = "", page_size: int = 50) -> list[dict[str, Any]]:
    filter_data: dict[str, Any] = {"GroupType": "AIGC"}
    if name_filter:
        filter_data["Name"] = name_filter
    payload = _with_project(
        {
            "Filter": filter_data,
            "PageNumber": 1,
            "PageSize": page_size,
        }
    )
    data = _request("ListAssetGroups", payload)
    items = data.get("Items") or data.get("Result", {}).get("Items") or []
    return items if isinstance(items, list) else []


def list_assets_in_group(
    group_id: str,
    *,
    page_number: int = 1,
    page_size: int = 100,
) -> list[dict[str, Any]]:
    payload = _with_project(
        {
            "Filter": {
                "GroupIds": [group_id],
                "GroupType": "AIGC",
            },
            "PageNumber": page_number,
            "PageSize": page_size,
        }
    )
    data = _request("ListAssets", payload)
    items = data.get("Items") or data.get("Result", {}).get("Items") or []
    return items if isinstance(items, list) else []


def create_asset(
    group_id: str,
    file_url: str,
    *,
    asset_name: str,
    asset_type: str = "Image",
) -> str:
    payload = _with_project(
        {
            "GroupId": group_id,
            "URL": file_url,
            "AssetType": asset_type,
            "Name": asset_name,
        }
    )
    data = _request("CreateAsset", payload)
    asset_id = (
        _extract_nested(data, "Result", "Id")
        or _extract_nested(data, "Result", "AssetId")
        or _extract_nested(data, "Id")
        or _extract_nested(data, "AssetId")
    )
    if not asset_id:
        raise ArkAssetError(f"CreateAsset 返回缺少 AssetId：{data}")
    logger.info("created ark asset %s in group %s", asset_id, group_id)
    return asset_id


def get_asset(asset_id: str) -> dict[str, Any]:
    data = _request("GetAsset", {"Id": asset_id})
    status = _extract_nested(data, "Result", "Status") or _extract_nested(data, "Status")
    url = _extract_nested(data, "Result", "URL") or _extract_nested(data, "URL")
    error = _extract_nested(data, "Result", "Error") or _extract_nested(data, "Error")
    return {
        "id": asset_id,
        "status": status,
        "url": url,
        "error": error,
        "raw": data,
    }


def wait_for_asset_active(asset_id: str) -> dict[str, Any]:
    deadline = time.time() + _ark_poll_timeout_seconds()
    interval = _ark_poll_interval_seconds()
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = get_asset(asset_id)
        status = str(last.get("status") or "")
        if status.lower() == "active":
            return last
        if status.lower() == "failed":
            raise ArkAssetError(last.get("error") or f"Ark 资产 {asset_id} 处理失败")
        time.sleep(interval)
    raise ArkAssetError(
        f"Ark 资产 {asset_id} 轮询超时，最后状态：{last.get('status') or 'unknown'}"
    )


def submit_and_wait(
    group_id: str,
    file_url: str,
    *,
    asset_name: str,
    asset_type: str = "Image",
) -> dict[str, Any]:
    asset_id = create_asset(
        group_id,
        file_url,
        asset_name=asset_name,
        asset_type=asset_type,
    )
    active = wait_for_asset_active(asset_id)
    active["id"] = asset_id
    return active
