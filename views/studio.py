"""模块 1 · 视频创作场（创作场）。

流程：填剧本 + 选参数 -> 额度硬预检 -> 调用 Seedance 生成 -> 轮询进度
     -> 成功后按「实际计费 token」扣减额度、写入历史 -> 展示视频。
下方提供搜索历史与创作灵感库（一键复用提示词）。
"""
import json
import time

import streamlit as st
from sqlalchemy import select

import pricing
import seedance
import asset_store
from database import get_session
from models import Generation, User
from quota import can_afford, consume_tokens

uid = st.session_state["user_id"]

st.title("🎬 Seedance Studio 创作场")

# ---------------- 侧边栏：创作参数 ----------------
with st.sidebar:
    st.markdown("### ⚙️ 创作参数")
    model_label = st.radio(
        "模型版本", ["Seedance 2.0 (高品质)", "Seedance 2.0 fast (高性能)"]
    )
    model_key = "pro" if "高品质" in model_label else "fast"

    ca, cb = st.columns(2)
    ratio = ca.selectbox("宽高比", ["9:16", "16:9", "1:1", "4:3", "3:4", "21:9"], index=0)
    resolution = cb.selectbox("分辨率", ["480p", "720p", "1080p"], index=1)

    smart = st.checkbox("💡 智能时长（由 AI 决定）")
    duration = st.slider("视频时长（秒）", 3, 12, 5, disabled=smart)
    fps = st.select_slider("帧率", options=[24, 30, 60], value=24)
    seed = st.number_input("随机种子 (Seed)", min_value=-1, value=-1, step=1)
    camerafixed = st.checkbox("固定镜头", value=False)

# ---------------- 主区：剧本 + 生成 ----------------
prompt = st.text_area(
    "视频剧本 (Prompt)", height=160, key="studio_prompt",
    placeholder="描述你想要的画面...",
)
with st.expander("🖼️ 从资产库选用首帧（图生视频）"):
    ref_imgs = asset_store.list_image_assets_for_reference()
    if ref_imgs:
        labels = {f"#{r['id']} {r['filename'] or '(无名)'}": r["tos_url"] for r in ref_imgs}
        pick = st.selectbox("选择已审核通过的图片素材", ["（不选）"] + list(labels.keys()))
        if pick != "（不选）" and st.button("用作首帧"):
            st.session_state["ref_image_url"] = labels[pick]
            st.rerun()
    else:
        st.caption("资产库暂无已审核图片素材。可在「资产库全览」登记，或经「人脸素材提交」入库。")

image_url = st.text_input(
    "首帧图 URL（可选，填则做图生视频）", key="ref_image_url",
    placeholder="https:// … 留空为文生视频",
)

# 费用估算（智能时长按保守值 10s 估算预检）
est_duration = 10 if smart else duration
est_tokens = pricing.estimate_tokens(resolution, ratio, est_duration, fps)
est_cost = pricing.tokens_to_yuan(est_tokens)

with get_session() as s:
    me = s.get(User, uid)
    my_quota, my_used, my_remaining = me.token_quota, me.token_used, me.token_remaining

pct = (my_used / my_quota) if my_quota else 0.0
st.progress(
    min(pct, 1.0),
    text=f"🎟️ 我的额度：剩余 {my_remaining:,} / {my_quota:,} tokens　|　"
    f"本次预估 ≈ {est_tokens:,} tokens（¥{est_cost:.4f}）",
)

go = st.button(
    "🚀 开始渲染", type="primary", use_container_width=False,
    disabled=not prompt.strip(),
)

if go:
    ok, remaining = can_afford(uid, est_tokens)
    if not ok:
        st.error(
            f"额度不足：本次预估约需 {est_tokens:,} tokens，你仅剩 {remaining:,}。"
            "请联系管理员追加额度。"
        )
        st.stop()

    params = {
        "ratio": ratio, "resolution": resolution,
        "duration": (None if smart else duration), "fps": fps,
        "seed": seed, "smart": smart, "camerafixed": camerafixed,
    }
    with get_session() as s:
        g = Generation(
            user_id=uid, prompt=prompt.strip(), model=model_label,
            params_json=json.dumps(params, ensure_ascii=False), status="pending",
        )
        s.add(g)
        s.flush()
        gen_id = g.id

    result_video = ""
    try:
        with st.status("正在提交到 Seedance…", expanded=True) as status:
            task_id = seedance.create_task(
                model_key, prompt.strip(),
                ratio=ratio, resolution=resolution,
                duration=(None if smart else duration), fps=fps,
                seed=(None if seed < 0 else int(seed)),
                image_url=(image_url.strip() or None), camerafixed=camerafixed,
            )
            with get_session() as s:
                g = s.get(Generation, gen_id)
                g.task_id = task_id
                g.status = "running"
            status.update(label=f"任务已提交（{task_id}），生成中…")

            final, video_url, tokens = "failed", "", 0
            for _ in range(120):  # 每 5s 轮询一次，最多约 10 分钟
                time.sleep(5)
                data = seedance.get_task(task_id)
                stt, vurl, tk = seedance.parse_result(data)
                if stt in seedance.TERMINAL:
                    final, video_url, tokens = stt, vurl, tk
                    break
                status.update(label=f"生成中… 当前状态：{stt or 'running'}")

            if final == seedance.SUCCEEDED:
                billed = tokens or est_tokens  # 实际计费优先，兜底用估算
                consume_tokens(uid, billed, note=f"生成 #{gen_id} ({task_id})")
                cost = pricing.tokens_to_yuan(billed)
                with get_session() as s:
                    g = s.get(Generation, gen_id)
                    g.status, g.video_url = "succeeded", video_url
                    g.tokens_used, g.cost_yuan = billed, cost
                result_video = video_url
                status.update(
                    label=f"✅ 完成！实际消耗 {billed:,} tokens（¥{cost:.4f}）",
                    state="complete",
                )
            else:
                with get_session() as s:
                    s.get(Generation, gen_id).status = "failed"
                status.update(label=f"❌ 生成失败（状态：{final}）", state="error")

    except seedance.SeedanceError as e:
        with get_session() as s:
            s.get(Generation, gen_id).status = "failed"
        st.error(str(e))

    if result_video:
        st.video(result_video)
    with get_session() as s:
        latest = s.get(User, uid)
        st.caption(f"🎟️ 当前剩余额度：{latest.token_remaining:,} tokens")

# ---------------- 历史与灵感库 ----------------
st.divider()
st.subheader("🔍 搜索历史")
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
            "id": g.id, "prompt": g.prompt, "model": g.model, "status": g.status,
            "tokens": g.tokens_used, "cost": g.cost_yuan, "task_id": g.task_id,
            "video_url": g.video_url,
            "time": g.created_at.strftime("%Y-%m-%d %H:%M"),
        }
        for g in gens
    ]

if keyword.strip():
    k = keyword.strip().lower()
    records = [
        r for r in records
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
                if r["status"] == "succeeded":
                    st.caption(f"💰 ¥{r['cost']:.4f} · {r['tokens']:,} tokens")
                st.write(r["prompt"][:80] + ("…" if len(r["prompt"]) > 80 else ""))
                if r["video_url"]:
                    st.video(r["video_url"])
                if st.button("♻️ 复用提示词", key=f"reuse_{r['id']}", use_container_width=True):
                    st.session_state["studio_prompt"] = r["prompt"]
                    st.rerun()
