"""后台执行 Seedance 生成任务。

Streamlit 页面负责创建 Generation 记录并提交到这里；后台线程负责调用 Ark、
轮询任务、结算额度并写回数据库。
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from sqlalchemy import select

import pricing
import seedance
import tos_client
from database import get_session
from models import Generation
from ops import get_logger
from quota import release_reserved_tokens, settle_reserved_tokens_in_session

_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="seedance-task")
_RUNNING: set[int] = set()
logger = get_logger(__name__)


def _has_video_input(payload: dict) -> bool:
    return any(item.get("type") == "video_url" for item in payload.get("content", []))


def _mirror_outputs(generation_id: int, parsed: dict) -> dict[str, str]:
    """把 Ark 签名输出转存到自己的 TOS。失败时保留原 URL。"""
    output = {
        "video_url": parsed["video_url"],
        "audio_url": parsed["audio_url"],
        "last_frame_url": parsed["last_frame_url"],
        "video_tos_key": "",
        "audio_tos_key": "",
        "last_frame_tos_key": "",
    }
    if not tos_client.is_configured():
        return output

    try:
        if output["video_url"]:
            key = f"outputs/{generation_id}/video.mp4"
            output["video_url"] = tos_client.mirror_url(
                key,
                output["video_url"],
                content_type="video/mp4",
            )
            output["video_tos_key"] = key
        if output["audio_url"]:
            key = f"outputs/{generation_id}/audio.mp3"
            output["audio_url"] = tos_client.mirror_url(
                key,
                output["audio_url"],
                content_type="audio/mpeg",
            )
            output["audio_tos_key"] = key
        if output["last_frame_url"]:
            key = f"outputs/{generation_id}/last_frame.jpg"
            output["last_frame_url"] = tos_client.mirror_url(
                key,
                output["last_frame_url"],
                content_type="image/jpeg",
            )
            output["last_frame_tos_key"] = key
    except Exception as exc:
        logger.warning("generation %s mirror outputs failed: %s", generation_id, exc)
        return {
            "video_url": parsed["video_url"],
            "audio_url": parsed["audio_url"],
            "last_frame_url": parsed["last_frame_url"],
            "video_tos_key": "",
            "audio_tos_key": "",
            "last_frame_tos_key": "",
        }
    return output


def submit_generation(generation_id: int) -> None:
    """把生成记录交给后台线程执行。"""
    if generation_id in _RUNNING:
        return
    _RUNNING.add(generation_id)
    logger.info("generation %s submitted to local executor", generation_id)
    _EXECUTOR.submit(_run_generation, generation_id)


def resume_unfinished_generations(limit: int = 20) -> None:
    """应用启动/重载时接管未完成任务。"""
    with get_session() as session:
        ids = session.scalars(
            select(Generation.id)
            .where(Generation.status.in_(["queued", "pending", "running"]))
            .order_by(Generation.created_at.asc())
            .limit(limit)
        ).all()
    for generation_id in ids:
        submit_generation(generation_id)


def _set_failed(generation_id: int, message: str, response: dict | None = None) -> None:
    logger.warning("generation %s failed: %s", generation_id, message)
    with get_session() as session:
        gen = session.get(Generation, generation_id)
        if not gen:
            return
        gen.status = "failed"
        gen.error_message = message[:4000]
        gen.finished_at = datetime.utcnow()
        if response is not None:
            gen.response_json = json.dumps(response, ensure_ascii=False)
        reserved = gen.reserved_tokens
        user_id = gen.user_id
    if reserved:
        release_reserved_tokens(user_id, reserved, note=f"生成 #{generation_id} 失败释放")


def _set_needs_settlement(
    generation_id: int, message: str, response: dict | None = None
) -> None:
    logger.warning("generation %s needs settlement: %s", generation_id, message)
    with get_session() as session:
        gen = session.get(Generation, generation_id)
        if not gen:
            return
        gen.status = "needs_settlement"
        gen.error_message = message[:4000]
        gen.finished_at = datetime.utcnow()
        if response is not None:
            gen.response_json = json.dumps(response, ensure_ascii=False)


def _run_generation(generation_id: int) -> None:
    submitted_to_ark = False
    task_id = ""
    final_response: dict | None = None
    try:
        logger.info("generation %s started", generation_id)
        with get_session() as session:
            gen = session.get(Generation, generation_id)
            if not gen or gen.status not in {"queued", "pending", "running"}:
                return
            gen.status = "running"
            if gen.started_at is None:
                gen.started_at = datetime.utcnow()
            task_id = gen.task_id
            request_payload = json.loads(gen.request_json or "{}")
            has_video_input = _has_video_input(request_payload)
        submitted_to_ark = bool(task_id)

        if task_id:
            create_response = {}
        else:
            try:
                create_response = seedance.submit_task(request_payload)
            except Exception as exc:
                _set_failed(generation_id, str(exc))
                return

            task_id = create_response.get("id") or ""
            if not task_id:
                _set_failed(generation_id, f"创建任务返回缺少 id：{create_response}", create_response)
                return
            submitted_to_ark = True
            logger.info("generation %s created ark task %s", generation_id, task_id)

            with get_session() as session:
                gen = session.get(Generation, generation_id)
                if not gen:
                    return
                gen.task_id = task_id
                gen.response_json = json.dumps(create_response, ensure_ascii=False)

        final_response = create_response
        parsed = seedance.parse_task_result(create_response)
        timed_out = True
        for _ in range(120):
            if parsed["status"] in seedance.TERMINAL:
                timed_out = False
                break
            time.sleep(5)
            try:
                final_response = seedance.get_task(task_id)
            except Exception as exc:
                _set_needs_settlement(
                    generation_id,
                    f"任务已提交到 Ark，但查询任务失败：{exc}。"
                    "为避免漏扣真实消耗，系统保留预占额度；请稍后用 Task ID 查询真实状态后人工结算。",
                    final_response,
                )
                return
            parsed = seedance.parse_task_result(final_response)
            with get_session() as session:
                gen = session.get(Generation, generation_id)
                if gen:
                    gen.response_json = json.dumps(final_response, ensure_ascii=False)

        with get_session() as session:
            gen = session.get(Generation, generation_id)
            if not gen:
                return
            reserved = gen.reserved_tokens
            estimated = gen.estimated_tokens
            user_id = gen.user_id

        if parsed["status"] == seedance.SUCCEEDED:
            billed = parsed["total_tokens"]
            if not billed:
                message = (
                    "任务已成功，但火山响应缺少 usage.total_tokens。"
                    "为避免本地额度与火山实际计费不一致，系统保留预占额度，"
                    "请管理员用 Task ID 在火山控制台/API 查询真实 tokens 后人工处理。"
                )
                logger.error("generation %s missing usage.total_tokens task=%s", generation_id, task_id)
                output = _mirror_outputs(generation_id, parsed)
                _set_needs_settlement(generation_id, message, final_response)
                with get_session() as session:
                    gen = session.get(Generation, generation_id)
                    if gen:
                        gen.video_url = output["video_url"]
                        gen.audio_url = output["audio_url"]
                        gen.last_frame_url = output["last_frame_url"]
                        gen.video_tos_key = output["video_tos_key"]
                        gen.audio_tos_key = output["audio_tos_key"]
                        gen.last_frame_tos_key = output["last_frame_tos_key"]
                return
            output = _mirror_outputs(generation_id, parsed)
            with get_session() as session:
                gen = session.get(Generation, generation_id)
                if gen:
                    settle_reserved_tokens_in_session(
                        session,
                        user_id,
                        reserved,
                        billed,
                        note=f"生成 #{generation_id} ({task_id})",
                    )
                    gen.status = "succeeded"
                    gen.video_url = output["video_url"]
                    gen.audio_url = output["audio_url"]
                    gen.last_frame_url = output["last_frame_url"]
                    gen.video_tos_key = output["video_tos_key"]
                    gen.audio_tos_key = output["audio_tos_key"]
                    gen.last_frame_tos_key = output["last_frame_tos_key"]
                    gen.tokens_used = billed
                    gen.cost_yuan = pricing.tokens_to_yuan(
                        billed, has_video_input=has_video_input
                    )
                    gen.response_json = json.dumps(final_response, ensure_ascii=False)
                    gen.finished_at = datetime.utcnow()
            logger.info(
                "generation %s succeeded task=%s billed_tokens=%s",
                generation_id,
                task_id,
                billed,
            )
        else:
            message = parsed["error_message"]
            if not message:
                if timed_out:
                    message = (
                        "任务轮询超时，未在约 10 分钟内进入终态。"
                        "为避免漏扣真实消耗，系统保留预占额度；请稍后用 Task ID 手动查询后人工结算。"
                    )
                elif parsed["status"]:
                    message = f"任务未成功结束，状态：{parsed['status']}"
                else:
                    message = (
                        "任务状态未知。"
                        "为避免漏扣真实消耗，系统保留预占额度；请稍后用 Task ID 手动查询后人工结算。"
                    )
            if parsed.get("error_code"):
                message = f"[{parsed['error_code']}] {message}"
            if timed_out or parsed["status"] in {"", "unknown"}:
                _set_needs_settlement(generation_id, message, final_response)
            else:
                _set_failed(generation_id, message, final_response)
    except Exception as exc:
        with get_session() as session:
            gen = session.get(Generation, generation_id)
            current_status = gen.status if gen else ""
        if current_status == seedance.SUCCEEDED:
            logger.error("generation %s raised after success: %s", generation_id, exc)
        elif submitted_to_ark:
            _set_needs_settlement(
                generation_id,
                f"任务已提交到 Ark，但本地后台发生异常：{exc}。"
                "为避免漏扣真实消耗，系统保留预占额度；请用 Task ID 查询后人工结算。",
                final_response,
            )
        else:
            _set_failed(generation_id, f"本地后台任务异常：{exc}")
    finally:
        _RUNNING.discard(generation_id)
