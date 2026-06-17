"""Seedance（火山方舟 Ark）视频生成客户端。

官方接口（直连火山）：
  创建：POST {ARK_BASE_URL}/contents/generations/tasks
  查询：GET  {ARK_BASE_URL}/contents/generations/tasks/{id}
  鉴权：Header  Authorization: Bearer <ARK_API_KEY>

Seedance 2.0 的多模态创作以顶层 JSON 参数 + content 数组提交：
  content: text + reference_image/reference_video/reference_audio
  generate_audio / ratio / duration / watermark 等参数放在顶层。
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

import requests

from config import settings
from ops import get_logger

# 模型档位 -> 实际模型/接入点 ID（在 .env 配置；从火山控制台获取）
MODELS = {
    "pro": settings.SEEDANCE_MODEL_PRO,    # Seedance 2.0 高品质
    "fast": settings.SEEDANCE_MODEL_FAST,  # Seedance 2.0 fast
}
MODEL_LABELS = {
    "pro": "Seedance 2.0 (高品质)",
    "fast": "Seedance 2.0 fast (高性能)",
}
MODEL_ENABLED = {
    "pro": settings.SEEDANCE_ENABLE_PRO,
    "fast": settings.SEEDANCE_ENABLE_FAST,
}

# 终态
SUCCEEDED = "succeeded"
FAILED = "failed"
TERMINAL = {SUCCEEDED, FAILED, "cancelled", "canceled"}

_TIMEOUT = 30
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_RETRY_DELAYS = (1, 2, 4)
_REQUEST_ID_RE = re.compile(r"request id:\s*([A-Za-z0-9_-]+)", re.IGNORECASE)

logger = get_logger(__name__)


class SeedanceError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str = "",
        request_id: str = "",
        response: Any = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.request_id = request_id
        self.response = response


def enabled_model_keys() -> list[str]:
    """Return enabled model keys in UI order."""
    return [key for key in ("pro", "fast") if MODEL_ENABLED.get(key) and MODELS.get(key)]


def default_model_key() -> str:
    """Return the configured default model key, falling back to the first enabled model."""
    configured = settings.SEEDANCE_DEFAULT_MODEL_KEY
    if configured in MODELS and MODEL_ENABLED.get(configured):
        return configured
    enabled = enabled_model_keys()
    return enabled[0] if enabled else configured


def model_options() -> list[dict[str, str]]:
    """Return enabled model options for Streamlit controls."""
    return [
        {"key": key, "label": MODEL_LABELS[key], "model": MODELS[key]}
        for key in enabled_model_keys()
    ]


def model_key_for_model_id(model_id: str) -> str:
    """Return the configured model key for a concrete Ark model id."""
    for key, configured_model_id in MODELS.items():
        if model_id == configured_model_id:
            return key
    return ""


def validate_payload_model_enabled(payload: dict[str, Any]) -> None:
    """Block disabled or unknown model ids before a direct submit."""
    model_id = str(payload.get("model") or "")
    if not model_id:
        raise SeedanceError("创建任务缺少 model")
    model_key = model_key_for_model_id(model_id)
    if not model_key:
        enabled = ", ".join(MODELS[key] for key in enabled_model_keys()) or "无"
        raise SeedanceError(
            f"请求 model「{model_id}」不在当前已启用模型列表中。已启用：{enabled}"
        )
    if not MODEL_ENABLED.get(model_key):
        raise SeedanceError(
            f"模型档位「{MODEL_LABELS.get(model_key, model_key)}」当前未启用，"
            "已在本地拦截，未提交到 Ark。"
        )


def _headers() -> dict:
    if not settings.ARK_API_KEY:
        raise SeedanceError("未配置 ARK_API_KEY，请在 .env 填写火山方舟 API Key")
    return {
        "Authorization": f"Bearer {settings.ARK_API_KEY}",
        "Content-Type": "application/json",
    }


def _extract_error(text: str) -> tuple[str, str, str, Any]:
    """Return (code, message, request_id, raw_json) from an Ark error body."""
    code = ""
    message = text.strip()
    request_id = ""
    raw: Any = None
    try:
        raw = json.loads(text)
    except Exception:
        match = _REQUEST_ID_RE.search(text)
        return code, message, match.group(1) if match else "", raw

    error = raw.get("error") if isinstance(raw, dict) else None
    if isinstance(error, dict):
        code = str(error.get("code") or "")
        message = str(error.get("message") or error.get("msg") or message)
        request_id = str(error.get("request_id") or error.get("requestId") or "")
    elif isinstance(raw, dict):
        code = str(raw.get("code") or "")
        message = str(raw.get("message") or raw.get("msg") or message)
        request_id = str(raw.get("request_id") or raw.get("requestId") or "")

    if not request_id:
        match = _REQUEST_ID_RE.search(message)
        if match:
            request_id = match.group(1)
            message = message[: match.start()].rstrip(" .。")
    return code, message, request_id, raw


def _error_hint(code: str, message: str) -> str:
    lower = message.lower()
    hints = {
        "AccountOverdueError": "火山账号存在逾期/欠费余额，请先到火山引擎费用中心处理账单后重试。",
        "SetLimitExceeded": "触发模型体验限额或 Safe Experience Mode，请到火山方舟模型详情/开通管理页调整限额后重试。",
        "AuthenticationError": "请检查 ARK_API_KEY 是否来自当前账号、是否完整、是否仍有效。",
        "InvalidAuthenticationToken": "请检查 ARK_API_KEY 是否来自当前账号、是否完整、是否仍有效。",
        "AccessDenied": "当前 API Key 或账号没有该模型/地域的调用权限，请在火山方舟开通管理中确认。",
        "InvalidParameter": "请求参数不被 Ark 接受，请对照火山方舟 API Explorer 校准字段。",
    }
    if code in hints:
        return hints[code]
    if "overdue" in lower:
        return hints["AccountOverdueError"]
    if "safe experience mode" in lower or "limit" in lower:
        return hints["SetLimitExceeded"]
    return ""


def _format_http_error(action: str, status_code: int, text: str) -> SeedanceError:
    code, message, request_id, raw = _extract_error(text)
    prefix = f"{action}失败（{status_code}）"
    if code:
        prefix += f" [{code}]"
    detail = f"{prefix}：{message}" if message else prefix
    hint = _error_hint(code, message)
    if hint:
        detail += f"。处理建议：{hint}"
    if request_id:
        detail += f"（Request ID: {request_id}）"
    return SeedanceError(
        detail,
        status_code=status_code,
        code=code,
        request_id=request_id,
        response=raw,
    )


def _request_json(
    method: str,
    path: str,
    *,
    action: str,
    payload: dict[str, Any] | None = None,
    retry: bool = False,
) -> dict[str, Any]:
    url = f"{settings.ARK_BASE_URL.rstrip('/')}{path}"
    attempts = len(_RETRY_DELAYS) + 1 if retry else 1
    last_error: Exception | None = None

    for attempt in range(attempts):
        try:
            resp = requests.request(
                method,
                url,
                headers=_headers(),
                json=payload,
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            last_error = exc
            if attempt < attempts - 1:
                delay = _RETRY_DELAYS[attempt]
                logger.warning("%s request error, retrying in %ss: %s", action, delay, exc)
                time.sleep(delay)
                continue
            raise SeedanceError(f"{action}请求异常：{exc}") from exc

        if resp.status_code in _RETRY_STATUSES and attempt < attempts - 1:
            delay = _RETRY_DELAYS[attempt]
            logger.warning(
                "%s returned %s, retrying in %ss", action, resp.status_code, delay
            )
            time.sleep(delay)
            continue

        if resp.status_code >= 400:
            error = _format_http_error(action, resp.status_code, resp.text)
            logger.warning("%s", error)
            raise error

        try:
            return resp.json()
        except ValueError as exc:
            raise SeedanceError(f"{action}返回非 JSON：{resp.text[:500]}") from exc

    raise SeedanceError(f"{action}请求失败：{last_error}")


def _clean_urls(urls: list[str] | tuple[str, ...] | None) -> list[str]:
    return [u.strip() for u in (urls or []) if u and u.strip()]


def build_content(
    text: str,
    *,
    image_urls: list[str] | tuple[str, ...] | None = None,
    video_urls: list[str] | tuple[str, ...] | None = None,
    audio_urls: list[str] | tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """构造 Seedance 2.0 content 数组。"""
    content: list[dict[str, Any]] = [{"type": "text", "text": text.strip()}]
    for url in _clean_urls(image_urls):
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": url},
                "role": "reference_image",
            }
        )
    for url in _clean_urls(video_urls):
        content.append(
            {
                "type": "video_url",
                "video_url": {"url": url},
                "role": "reference_video",
            }
        )
    for url in _clean_urls(audio_urls):
        content.append(
            {
                "type": "audio_url",
                "audio_url": {"url": url},
                "role": "reference_audio",
            }
        )
    return content


def build_payload(
    model_key: str,
    prompt_text: str,
    *,
    ratio: str,
    resolution: str | None = None,
    duration: int | None = None,
    seed: int | None = None,
    image_urls: list[str] | tuple[str, ...] | None = None,
    video_urls: list[str] | tuple[str, ...] | None = None,
    audio_urls: list[str] | tuple[str, ...] | None = None,
    generate_audio: bool = False,
    watermark: bool = False,
    extra_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造创建任务 payload，便于页面层预览和测试。"""
    model = MODELS.get(model_key)
    if not model:
        raise SeedanceError(f"未配置模型档位「{model_key}」，请在 .env 设置 SEEDANCE_MODEL_*")
    if not MODEL_ENABLED.get(model_key):
        raise SeedanceError(
            f"模型档位「{MODEL_LABELS.get(model_key, model_key)}」当前未启用。"
            f"如果已开通对应资源包，请在 .env 设置 SEEDANCE_ENABLE_{model_key.upper()}=true。"
        )

    payload: dict[str, Any] = {
        "model": model,
        "content": build_content(
            prompt_text,
            image_urls=image_urls,
            video_urls=video_urls,
            audio_urls=audio_urls,
        ),
        "generate_audio": bool(generate_audio),
        "ratio": ratio,
        "watermark": bool(watermark),
    }
    if duration:
        payload["duration"] = int(duration)
    if resolution:
        payload["resolution"] = resolution
    if seed is not None and seed >= 0:
        payload["seed"] = int(seed)
    if extra_payload:
        payload.update(extra_payload)
    return payload


def create_task(
    model_key: str,
    prompt_text: str,
    *,
    ratio: str,
    resolution: str,
    duration: int | None = None,
    seed: int | None = None,
    image_urls: list[str] | tuple[str, ...] | None = None,
    video_urls: list[str] | tuple[str, ...] | None = None,
    audio_urls: list[str] | tuple[str, ...] | None = None,
    generate_audio: bool = False,
    watermark: bool = False,
    extra_payload: dict[str, Any] | None = None,
) -> str:
    """创建视频生成任务，返回 task_id。"""
    payload = build_payload(
        model_key,
        prompt_text,
        ratio=ratio,
        resolution=resolution,
        duration=duration,
        seed=seed,
        image_urls=image_urls,
        video_urls=video_urls,
        audio_urls=audio_urls,
        generate_audio=generate_audio,
        watermark=watermark,
        extra_payload=extra_payload,
    )
    data = submit_task(payload)
    task_id = data.get("id")
    if not task_id:
        raise SeedanceError(f"创建任务返回缺少 id：{data}")
    return task_id


def submit_task(payload: dict[str, Any]) -> dict[str, Any]:
    """提交已经构造好的任务 payload，返回原始 JSON。"""
    validate_payload_model_enabled(payload)

    # 创建任务不是幂等操作，不自动重试，避免网络抖动后重复扣费。
    return _request_json(
        "POST",
        "/contents/generations/tasks",
        action="创建任务",
        payload=payload,
        retry=False,
    )


def get_task(task_id: str) -> dict:
    """查询任务原始 JSON。"""
    return _request_json(
        "GET",
        f"/contents/generations/tasks/{task_id}",
        action="查询任务",
        retry=True,
    )


def _url_from(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("url") or value.get("uri") or ""
    return ""


def _looks_like_video_url(url: str) -> bool:
    lower = url.lower().split("?", 1)[0]
    return lower.endswith((".mp4", ".mov", ".m3u8", ".webm"))


def _find_video_url(value: Any) -> str:
    if isinstance(value, dict):
        role = str(value.get("role") or "").lower()
        if role.startswith("reference"):
            return ""

        for key in ("video_url", "output_video_url", "result_video_url"):
            found = _url_from(value.get(key))
            if found and (key != "url" or _looks_like_video_url(found)):
                return found

        item_type = str(value.get("type") or "").lower()
        if "video" in item_type:
            found = _url_from(value.get("url"))
            if found:
                return found
            for key in ("video", "file"):
                found = _url_from(value.get(key))
                if found:
                    return found

        for key in ("result", "output", "outputs", "content", "data"):
            found = _find_video_url(value.get(key))
            if found:
                return found

        for child in value.values():
            found = _find_video_url(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_video_url(child)
            if found:
                return found
    return ""


def _find_url_by_keys(value: Any, keys: tuple[str, ...]) -> str:
    if isinstance(value, dict):
        role = str(value.get("role") or "").lower()
        if role.startswith("reference"):
            return ""
        for key in keys:
            found = _url_from(value.get(key))
            if found:
                return found
        for key in ("result", "output", "outputs", "content", "data"):
            found = _find_url_by_keys(value.get(key), keys)
            if found:
                return found
        for child in value.values():
            found = _find_url_by_keys(child, keys)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_url_by_keys(child, keys)
            if found:
                return found
    return ""


def parse_task_result(task_json: dict) -> dict[str, Any]:
    """解析任务状态、媒体产物、计费与错误信息。

    已用真实响应校准：
      content.video_url
      usage.total_tokens / usage.completion_tokens
      framespersecond
    """
    usage = task_json.get("usage") or {}
    total_tokens = (
        usage.get("total_tokens")
        or usage.get("completion_tokens")
        or usage.get("output_tokens")
        or 0
    )
    error_code = ""
    error = task_json.get("error") or task_json.get("message") or task_json.get("reason") or ""
    if isinstance(error, dict):
        error_code = str(error.get("code") or "")
        error = error.get("message") or error.get("msg") or str(error)
    return {
        "status": (task_json.get("status") or "").lower(),
        "video_url": _find_video_url(task_json),
        "audio_url": _find_url_by_keys(task_json, ("audio_url", "output_audio_url")),
        "last_frame_url": _find_url_by_keys(
            task_json,
            ("last_frame_url", "tail_frame_url", "end_frame_url", "cover_url"),
        ),
        "total_tokens": int(total_tokens),
        "seed": task_json.get("seed"),
        "resolution": task_json.get("resolution") or "",
        "ratio": task_json.get("ratio") or "",
        "duration": task_json.get("duration") or 0,
        "framespersecond": task_json.get("framespersecond") or task_json.get("fps") or 0,
        "error_code": error_code,
        "error_message": str(error or ""),
    }


def parse_result(task_json: dict) -> tuple[str, str, int]:
    """从任务 JSON 解析出 (status, video_url, total_tokens)。

    做了容错：不同版本字段位置可能略有差异，拿真实响应跑一次即可确认。
    """
    result = parse_task_result(task_json)
    return result["status"], result["video_url"], result["total_tokens"]
