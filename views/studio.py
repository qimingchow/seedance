"""模块 1 · 视频创作场。

支持 Seedance 2.0 多模态创作：文字、参考图片、参考视频、参考音频。
提交前做成员额度预检，完成后按接口返回 token 扣减并写入历史。
"""
import json
import uuid

import streamlit as st
from sqlalchemy import select

import asset_store
import pricing
import seedance
import task_runner
import tos_client
from database import get_session
from models import Generation, User
from quota import (
    QuotaError,
    account_managed_total,
    account_remaining,
    can_afford,
    reserve_tokens,
)

uid = st.session_state["user_id"]


def _split_lines(raw: str) -> list[str]:
    """按行/逗号拆 URL，保留顺序并去重。"""
    seen: set[str] = set()
    urls: list[str] = []
    for part in (raw or "").replace(",", "\n").splitlines():
        value = part.strip()
        if value and value not in seen:
            urls.append(value)
            seen.add(value)
    return urls


def _parse_asset_ids(raw: str) -> list[int]:
    ids: list[int] = []
    for part in (raw or "").replace(",", "\n").splitlines():
        token = part.strip().lstrip("#")
        if token.isdigit():
            ids.append(int(token))
    return list(dict.fromkeys(ids))


def _parse_direct_asset_refs(raw: str) -> list[str]:
    """解析手动输入的 Ark AssetId，统一转为 Seedance 支持的 asset:// 引用。"""
    refs: list[str] = []
    seen: set[str] = set()
    for part in (raw or "").replace(",", "\n").splitlines():
        token = part.strip()
        if not token or token.lstrip("#").isdigit():
            continue
        if token.startswith("asset://"):
            ref = token
        else:
            ref = f"asset://{token}"
        if ref not in seen:
            refs.append(ref)
            seen.add(ref)
    return refs


def _asset_reference(asset: dict) -> str:
    ark_asset_id = (asset.get("ark_asset_id") or "").strip()
    if ark_asset_id:
        return f"asset://{ark_asset_id}"
    return asset["tos_url"]


def _append_state_url(key: str, url: str) -> None:
    current = _split_lines(st.session_state.get(key, ""))
    if url and url not in current:
        current.append(url)
    st.session_state[key] = "\n".join(current)


def _queue_state_url(key: str, url: str) -> None:
    pending_key = f"_pending_{key}"
    pending = st.session_state.get(pending_key, [])
    if isinstance(pending, str):
        pending = [pending]
    if url and url not in pending:
        pending.append(url)
    st.session_state[pending_key] = pending


def _consume_queued_state_urls(key: str) -> None:
    pending_key = f"_pending_{key}"
    pending = st.session_state.pop(pending_key, [])
    if isinstance(pending, str):
        pending = [pending]
    for url in pending:
        _append_state_url(key, url)


def _upload_files(files, *, folder: str) -> list[str]:
    if not files:
        return []
    if not tos_client.is_configured():
        st.error("TOS 未配置，无法上传本地素材。请先配置 TOS，或改用公网 URL。")
        st.stop()

    urls: list[str] = []
    for f in files:
        key = f"studio/{uid}/{folder}/{uuid.uuid4().hex}_{f.name}"
        try:
            urls.append(
                tos_client.upload_bytes(
                    key,
                    f.getvalue(),
                    content_type=getattr(f, "type", None),
                )
            )
        except Exception as exc:
            st.error(f"上传素材失败：{f.name} · {exc}")
            st.stop()
    return urls


if st.session_state.get("ref_image_url"):
    _queue_state_url("reference_image_urls", st.session_state["ref_image_url"])
    st.session_state.pop("ref_image_url", None)
_consume_queued_state_urls("reference_image_urls")

st.title("🎬 Seedance Studio 创作场")

# ---------------- 侧边栏：创作参数 ----------------
with st.sidebar:
    st.markdown("### ⚙️ 创作参数")
    model_options = seedance.model_options()
    if not model_options:
        st.error("未启用任何 Seedance 模型。请在 .env 至少启用 SEEDANCE_ENABLE_PRO=true。")
        st.stop()
    model_label_to_key = {item["label"]: item["key"] for item in model_options}
    model_labels = list(model_label_to_key.keys())
    default_key = seedance.default_model_key()
    default_index = next(
        (i for i, item in enumerate(model_options) if item["key"] == default_key),
        0,
    )
    model_label = st.radio("模型版本", model_labels, index=default_index)
    model_key = model_label_to_key[model_label]
    if "fast" not in model_label_to_key:
        st.caption("Seedance 2.0 fast 未启用；开通 fast 资源包后可在 .env 打开。")

    ca, cb = st.columns(2)
    ratio = ca.selectbox("宽高比", ["9:16", "16:9", "1:1", "4:3", "3:4", "21:9"], index=0)
    resolution = cb.selectbox("分辨率", ["480p", "720p", "1080p"], index=1)

    duration = st.slider("视频时长（秒）", 4, 15, 5)
    smart = st.checkbox("💡 智能时长（由 AI 决定）")
    seed = st.number_input("🎲 随机种子 (Seed)", min_value=-1, value=-1, step=1)
    fps = 24
    st.caption("当前 Seedance 2.0 模型限制为 24fps。")

    st.markdown("### 🛠️ 功能开关")
    generate_audio = st.checkbox("同步生成音频", value=True)
    return_last_frame = st.checkbox("返回视频尾帧")
    watermark = st.checkbox("开启视频水印")
    web_search = st.checkbox("联网搜索增强")
    st.caption("尾帧与联网搜索的接口字段待确认，当前会记录到任务参数中。")

    st.markdown("### 🖼️ 多模态素材")
    first_last_mode = st.checkbox("开启首尾帧模式")
    reference_video_urls_raw = st.text_area(
        "参考视频 URL（每行一个）",
        height=80,
        placeholder="https://...mp4",
    )
    uploaded_videos = st.file_uploader(
        "上传参考视频（支持多选）",
        accept_multiple_files=True,
        type=["mp4", "mov", "webm"],
    )
    asset_ids_raw = st.text_area(
        "输入本地素材 ID 或 Ark AssetId（每行一个）",
        height=80,
        placeholder="例如：\n12\nasset-xxxx\nasset://asset-yyyy",
    )
    reference_image_urls_raw = st.text_area(
        "参考图片 URL / asset://Ark AssetId（每行一个）",
        key="reference_image_urls",
        height=100,
        placeholder="https://...\nasset://asset-xxxx",
    )
    uploaded_images = st.file_uploader(
        "上传参考图（支持多选）",
        accept_multiple_files=True,
        type=["jpg", "jpeg", "png", "webp", "bmp", "tif", "tiff", "gif"],
    )

    st.markdown("### 🎵 音频素材")
    reference_audio_urls_raw = st.text_area(
        "参考音频 URL（每行一个）",
        height=80,
        placeholder="https://...mp3",
    )
    uploaded_audio = st.file_uploader(
        "上传音频素材（支持多选）",
        accept_multiple_files=True,
        type=["mp3", "wav", "m4a"],
    )

# ---------------- 主区：剧本 + 快速资产库 ----------------
prompt = st.text_area(
    "视频剧本 (Prompt)",
    height=160,
    key="studio_prompt",
    placeholder="描述你想要的画面...",
)
has_prompt = bool(prompt.strip())

with st.expander("🖼️ 快速访问私域资产库"):
    ref_imgs = asset_store.list_image_assets_for_reference()
    if ref_imgs:
        labels = {
            (
                f"#{r['id']} {r['filename'] or '(无名)'}"
                + (f" · Ark {r['ark_asset_id']}" if r.get("ark_asset_id") else "")
            ): _asset_reference(r)
            for r in ref_imgs
        }
        pick = st.selectbox("选择已审核通过的图片素材", ["（不选）"] + list(labels.keys()))
        if pick != "（不选）" and st.button("加入参考图"):
            _queue_state_url("reference_image_urls", labels[pick])
            st.rerun()
    else:
        pending_images = asset_store.count_assets_by_status("pending", asset_type="image")
        if pending_images:
            st.caption(
                f"资产库暂无已审核图片素材；当前有 {pending_images} 个图片素材待审核，"
                "管理员批准后可在这里选择。"
            )
        else:
            st.caption("资产库暂无已审核图片素材。")

# 费用估算（智能时长按保守值 10s 估算预检）
est_duration = 10 if smart else duration
est_video_urls = _split_lines(reference_video_urls_raw)
est_input_duration = pricing.estimate_reference_video_seconds(
    est_duration,
    len(est_video_urls) + len(uploaded_videos or []),
)
est_tokens = pricing.estimate_tokens(
    resolution,
    ratio,
    est_duration,
    fps,
    input_duration=est_input_duration,
)
est_cost = pricing.tokens_to_yuan(
    est_tokens,
    has_video_input=bool(est_video_urls or uploaded_videos),
)

with get_session() as s:
    me = s.get(User, uid)
    is_admin = me.role == "admin"
    my_quota = me.token_quota
    my_used = me.token_used
    my_reserved = me.token_reserved
    my_remaining = me.token_remaining
    account_free = account_remaining()
    account_total = account_managed_total()

if is_admin:
    quota_used_for_bar = max(account_total - account_free, 0)
    pct = (quota_used_for_bar / account_total) if account_total else 0.0
    if has_prompt:
        afford_text = "账号池可覆盖" if account_free >= est_tokens else f"账号可用 {account_free:,}"
        quota_text = (
            f"🎟️ 本次预估 ≈ {est_tokens:,} tokens（¥{est_cost:.4f}）　|　{afford_text}"
        )
    else:
        quota_text = "🎟️ 填写剧本后显示本次预估；账号额度总览见左侧栏。"
else:
    pct = ((my_used + my_reserved) / my_quota) if my_quota else 0.0
    quota_parts = [
        f"🎟️ 我的额度：剩余 {my_remaining:,} / {my_quota:,} tokens",
        f"运行中预占 {my_reserved:,}",
        f"账号可用 {account_free:,}",
    ]
    if has_prompt:
        quota_parts.append(f"本次预估 ≈ {est_tokens:,} tokens（¥{est_cost:.4f}）")
    else:
        quota_parts.append("填写剧本后显示本次预估")
    quota_text = "　|　".join(quota_parts)
st.progress(
    min(pct, 1.0),
    text=quota_text,
)
if account_free <= 0:
    st.warning("账号资源包额度基准尚未设置，或已无可用额度。请管理员先在管理后台设置真实资源包 token 总额。")

go = st.button("🚀 开始渲染", type="primary", disabled=not has_prompt)

if go:
    image_urls = _split_lines(reference_image_urls_raw)
    video_urls = _split_lines(reference_video_urls_raw)
    audio_urls = _split_lines(reference_audio_urls_raw)

    asset_ids = _parse_asset_ids(asset_ids_raw)
    direct_asset_refs = _parse_direct_asset_refs(asset_ids_raw)
    missing_asset_ids: list[int] = []
    image_urls.extend(direct_asset_refs)
    if asset_ids:
        approved_assets = asset_store.list_assets_by_ids(asset_ids)
        found_ids = {a["id"] for a in approved_assets}
        missing_asset_ids = [i for i in asset_ids if i not in found_ids]
        for asset in approved_assets:
            if asset["type"] == "image":
                image_urls.append(_asset_reference(asset))
            elif asset["type"] == "video":
                video_urls.append(_asset_reference(asset))

    preupload_video_count = len(video_urls) + len(uploaded_videos or [])
    preupload_input_duration = pricing.estimate_reference_video_seconds(
        10 if smart else duration,
        preupload_video_count,
    )
    preupload_est_tokens = pricing.estimate_tokens(
        resolution,
        ratio,
        10 if smart else duration,
        fps,
        input_duration=preupload_input_duration,
    )
    ok, remaining = can_afford(uid, preupload_est_tokens)
    if not ok:
        st.error(
            f"额度不足：本次预估约需 {preupload_est_tokens:,} tokens，你仅剩 {remaining:,}。"
            "请联系管理员追加额度。"
        )
        st.stop()

    image_urls.extend(_upload_files(uploaded_images, folder="images"))
    video_urls.extend(_upload_files(uploaded_videos, folder="videos"))
    audio_urls.extend(_upload_files(uploaded_audio, folder="audio"))
    image_urls = _split_lines("\n".join(image_urls))
    video_urls = _split_lines("\n".join(video_urls))
    audio_urls = _split_lines("\n".join(audio_urls))

    final_est_duration = 10 if smart else duration
    final_input_duration = pricing.estimate_reference_video_seconds(
        final_est_duration,
        len(video_urls),
    )
    final_est_tokens = pricing.estimate_tokens(
        resolution,
        ratio,
        final_est_duration,
        fps,
        input_duration=final_input_duration,
    )
    final_est_cost = pricing.tokens_to_yuan(
        final_est_tokens,
        has_video_input=bool(video_urls),
    )
    ok, remaining = can_afford(uid, final_est_tokens)
    if not ok:
        st.error(
            f"额度不足：本次预估约需 {final_est_tokens:,} tokens，你仅剩 {remaining:,}。"
            "请联系管理员追加额度。"
        )
        st.stop()

    if missing_asset_ids:
        st.warning(f"以下 Asset ID 不存在或未审核通过，已跳过：{missing_asset_ids}")
    if first_last_mode and len(image_urls) < 2:
        st.warning("已开启首尾帧模式，但参考图片少于 2 张。建议至少提供首帧和尾帧两张图。")

    params = {
        "model_key": model_key,
        "ratio": ratio,
        "resolution": resolution,
        "duration": (None if smart else duration),
        "fps": fps,
        "seed": seed,
        "smart": smart,
        "generate_audio": generate_audio,
        "return_last_frame": return_last_frame,
        "watermark": watermark,
        "web_search": web_search,
        "first_last_mode": first_last_mode,
        "asset_ids": asset_ids,
        "direct_asset_refs": direct_asset_refs,
        "image_urls": image_urls,
        "video_urls": video_urls,
        "audio_urls": audio_urls,
    }
    request_payload = seedance.build_payload(
        model_key,
        prompt.strip(),
        ratio=ratio,
        resolution=resolution,
        duration=(None if smart else duration),
        seed=(None if seed < 0 else int(seed)),
        image_urls=image_urls,
        video_urls=video_urls,
        audio_urls=audio_urls,
        generate_audio=generate_audio,
        watermark=watermark,
    )
    try:
        reserve_tokens(uid, final_est_tokens, note="提交生成任务预占")
    except QuotaError as e:
        st.error(str(e))
        st.stop()

    with get_session() as s:
        g = Generation(
            user_id=uid,
            prompt=prompt.strip(),
            model=model_label,
            params_json=json.dumps(params, ensure_ascii=False),
            request_json=json.dumps(request_payload, ensure_ascii=False),
            estimated_tokens=final_est_tokens,
            reserved_tokens=final_est_tokens,
            status="queued",
        )
        s.add(g)
        s.flush()
        gen_id = g.id

    task_runner.submit_generation(gen_id)
    st.success(
        f"任务 #{gen_id} 已进入后台队列，预占 {final_est_tokens:,} tokens"
        f"（约 ¥{final_est_cost:.4f}）。"
    )
    st.rerun()

# ---------------- 历史与灵感库 ----------------
st.divider()
hc1, hc2 = st.columns([3, 1])
hc1.subheader("🔍 搜索历史")
if hc2.button("刷新历史", width="stretch"):
    st.rerun()
keyword = st.text_input("提示词 / 任务 ID / 日期", placeholder="输入关键字后过滤…")

with get_session() as s:
    gens = s.scalars(
        select(Generation)
        .where(Generation.user_id == uid)
        .order_by(Generation.created_at.desc())
        .limit(60)
    ).all()
    records = [
        {
            "id": g.id,
            "prompt": g.prompt,
            "model": g.model,
            "status": g.status,
            "tokens": g.tokens_used,
            "cost": g.cost_yuan,
            "task_id": g.task_id,
            "video_url": tos_client.access_url_for_stored_url(
                g.video_url, g.video_tos_key
            ),
            "audio_url": tos_client.access_url_for_stored_url(
                g.audio_url, g.audio_tos_key
            ),
            "last_frame_url": tos_client.access_url_for_stored_url(
                g.last_frame_url, g.last_frame_tos_key
            ),
            "error_message": g.error_message,
            "reserved_tokens": g.reserved_tokens,
            "time": g.created_at.strftime("%Y-%m-%d %H:%M"),
        }
        for g in gens
    ]

if keyword.strip():
    k = keyword.strip().lower()
    records = [
        r
        for r in records
        if k in r["prompt"].lower() or k in r["task_id"].lower() or k in r["time"]
    ]

st.subheader("🎨 创作灵感库")
if not records:
    st.info("还没有创作记录，填好剧本点「开始渲染」试试。")
else:
    cols = st.columns(3)
    for i, r in enumerate(records):
        with cols[i % 3]:
            with st.container(border=True):
                st.caption(f"#{r['id']} · {r['time']} · `{r['status']}`")
                if r["task_id"]:
                    st.caption(f"Task ID: `{r['task_id']}`")
                if r["status"] == "succeeded":
                    st.caption(f"💰 ¥{r['cost']:.4f} · {r['tokens']:,} tokens")
                elif r["status"] in {"queued", "running", "pending"}:
                    st.caption(f"⏳ 运行中 · 预占 {r['reserved_tokens']:,} tokens")
                elif r["status"] == "failed" and r["error_message"]:
                    st.caption(f"失败原因：{r['error_message'][:80]}")
                    hint = seedance.error_hint_from_text(r["error_message"])
                    if hint:
                        st.caption(f"处理建议：{hint[:120]}")
                st.write(r["prompt"][:80] + ("…" if len(r["prompt"]) > 80 else ""))
                if r["video_url"]:
                    st.video(r["video_url"])
                if r["audio_url"]:
                    st.audio(r["audio_url"])
                if r["last_frame_url"]:
                    st.image(r["last_frame_url"], caption="尾帧", width="stretch")
                if st.button("♻️ 复用提示词", key=f"reuse_{r['id']}", width="stretch"):
                    st.session_state["studio_prompt"] = r["prompt"]
                    st.rerun()
