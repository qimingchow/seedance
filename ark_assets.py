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
    pass


def is_configured() -> bool:
    return bool(
        settings.ARK_ASSET_SYNC_ENABLED
        and settings.ARK_OPENAPI_ACCESS_KEY
        and settings.ARK_OPENAPI_SECRET_KEY
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
        raise ArkAssetError("Ark 资产 OpenAPI 未配置或未启用")

    body = json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":"))
    now = dt.datetime.utcnow()
    x_date = now.strftime("%Y%m%dT%H%M%SZ")
    short_date = x_date[:8]
    content_hash = _hash_sha256(body)
    query = {
        "Action": action,
        "Version": settings.ARK_OPENAPI_VERSION,
    }
    canonical_request = "\n".join(
        [
            "POST",
            PATH,
            _norm_query(query),
            "\n".join(
                [
                    f"content-type:{CONTENT_TYPE}",
                    f"host:{settings.ARK_OPENAPI_HOST}",
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
        [short_date, settings.ARK_OPENAPI_REGION, SERVICE, "request"]
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
        settings.ARK_OPENAPI_SECRET_KEY.encode("utf-8"), short_date
    )
    key_region = _hmac_sha256(key_date, settings.ARK_OPENAPI_REGION)
    key_service = _hmac_sha256(key_region, SERVICE)
    key_signing = _hmac_sha256(key_service, "request")
    signature = _hmac_sha256(key_signing, string_to_sign).hex()

    headers = {
        "Host": settings.ARK_OPENAPI_HOST,
        "X-Content-Sha256": content_hash,
        "X-Date": x_date,
        "Content-Type": CONTENT_TYPE,
        "Authorization": (
            "HMAC-SHA256 "
            f"Credential={settings.ARK_OPENAPI_ACCESS_KEY}/{credential_scope}, "
            "SignedHeaders=content-type;host;x-content-sha256;x-date, "
            f"Signature={signature}"
        ),
    }
    url = f"https://{settings.ARK_OPENAPI_HOST}{PATH}"
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
        raise ArkAssetError(f"{prefix}：{message}")
    if not isinstance(data, dict):
        raise ArkAssetError(f"{action} 返回非对象 JSON：{response.text[:500]}")
    return data


def _with_project(payload: dict[str, Any]) -> dict[str, Any]:
    if settings.ARK_ASSET_PROJECT_NAME:
        payload = dict(payload)
        payload["ProjectName"] = settings.ARK_ASSET_PROJECT_NAME
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
    deadline = time.time() + settings.ARK_ASSET_POLL_TIMEOUT_SECONDS
    interval = max(settings.ARK_ASSET_POLL_INTERVAL_SECONDS, 1)
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
