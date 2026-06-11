"""Seedance（火山方舟 Ark）视频生成客户端。

官方接口（直连火山）：
  创建：POST {ARK_BASE_URL}/contents/generations/tasks
  查询：GET  {ARK_BASE_URL}/contents/generations/tasks/{id}
  鉴权：Header  Authorization: Bearer <ARK_API_KEY>

参数以 flag 形式拼在 content 的 text 里：
  --ratio 9:16  --resolution 720p  --duration 5  --fps 24  --seed 123  --camerafixed false
图生视频再追加一个 image_url 元素（首帧）。
"""
from __future__ import annotations

import requests

from config import settings

# 模型档位 -> 实际模型/接入点 ID（在 .env 配置；从火山控制台获取）
MODELS = {
    "pro": settings.SEEDANCE_MODEL_PRO,    # Seedance 2.0 高品质
    "fast": settings.SEEDANCE_MODEL_FAST,  # Seedance 2.0 fast
}

# 终态
SUCCEEDED = "succeeded"
FAILED = "failed"
TERMINAL = {SUCCEEDED, FAILED, "cancelled", "canceled"}

_TIMEOUT = 30


class SeedanceError(Exception):
    pass


def _headers() -> dict:
    if not settings.ARK_API_KEY:
        raise SeedanceError("未配置 ARK_API_KEY，请在 .env 填写火山方舟 API Key")
    return {
        "Authorization": f"Bearer {settings.ARK_API_KEY}",
        "Content-Type": "application/json",
    }


def build_prompt(
    text: str,
    *,
    ratio: str,
    resolution: str,
    duration: int | None = None,
    fps: int | None = None,
    seed: int | None = None,
    camerafixed: bool | None = None,
) -> str:
    """把提示词 + 参数拼成火山要求的 text。duration 为 None 时交给模型自动决定（智能时长）。"""
    flags = [f"--ratio {ratio}", f"--resolution {resolution}"]
    if duration:
        flags.append(f"--duration {duration}")
    if fps:
        flags.append(f"--fps {fps}")
    if seed is not None and seed >= 0:
        flags.append(f"--seed {seed}")
    if camerafixed is not None:
        flags.append(f"--camerafixed {'true' if camerafixed else 'false'}")
    return f"{text.strip()} " + " ".join(flags)


def create_task(
    model_key: str,
    prompt_text: str,
    *,
    ratio: str,
    resolution: str,
    duration: int | None = None,
    fps: int | None = None,
    seed: int | None = None,
    image_url: str | None = None,
    camerafixed: bool | None = None,
) -> str:
    """创建视频生成任务，返回 task_id。"""
    model = MODELS.get(model_key)
    if not model:
        raise SeedanceError(f"未配置模型档位「{model_key}」，请在 .env 设置 SEEDANCE_MODEL_*")

    content: list[dict] = [
        {
            "type": "text",
            "text": build_prompt(
                prompt_text, ratio=ratio, resolution=resolution,
                duration=duration, fps=fps, seed=seed, camerafixed=camerafixed,
            ),
        }
    ]
    if image_url:
        content.append({"type": "image_url", "image_url": {"url": image_url}})

    resp = requests.post(
        f"{settings.ARK_BASE_URL}/contents/generations/tasks",
        headers=_headers(),
        json={"model": model, "content": content},
        timeout=_TIMEOUT,
    )
    if resp.status_code >= 400:
        raise SeedanceError(f"创建任务失败（{resp.status_code}）：{resp.text}")
    data = resp.json()
    task_id = data.get("id")
    if not task_id:
        raise SeedanceError(f"创建任务返回缺少 id：{data}")
    return task_id


def get_task(task_id: str) -> dict:
    """查询任务原始 JSON。"""
    resp = requests.get(
        f"{settings.ARK_BASE_URL}/contents/generations/tasks/{task_id}",
        headers=_headers(),
        timeout=_TIMEOUT,
    )
    if resp.status_code >= 400:
        raise SeedanceError(f"查询任务失败（{resp.status_code}）：{resp.text}")
    return resp.json()


def parse_result(task_json: dict) -> tuple[str, str, int]:
    """从任务 JSON 解析出 (status, video_url, total_tokens)。

    做了容错：不同版本字段位置可能略有差异，拿真实响应跑一次即可确认。
    """
    status = (task_json.get("status") or "").lower()

    video_url = ""
    content = task_json.get("content")
    if isinstance(content, dict):
        video_url = content.get("video_url") or content.get("url") or ""
    elif isinstance(content, list):  # 兼容数组形态
        for item in content:
            if isinstance(item, dict) and (item.get("video_url") or item.get("url")):
                video_url = item.get("video_url") or item.get("url")
                break

    usage = task_json.get("usage") or {}
    total_tokens = (
        usage.get("total_tokens")
        or usage.get("completion_tokens")
        or usage.get("output_tokens")
        or 0
    )
    return status, video_url, int(total_tokens)
