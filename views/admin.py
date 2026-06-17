"""管理后台（仅管理员）。

三个标签页：
1. 成员与额度 —— 新增成员、追加/重设 token 额度
2. 用量统计   —— 各成员已用额度、生成数、累计费用 + 柱状图
3. 内容审核   —— 按成员查看创作记录与视频
"""
import json
from datetime import datetime

import pandas as pd
import streamlit as st
from sqlalchemy import select

import asset_store
import pricing
import seedance
import tos_client
from auth import hash_password, require_admin
from config import settings
from database import get_session
from models import Generation, QuotaTransaction, User
from quota import (
    QuotaError,
    account_managed_total,
    account_overrun,
    account_remaining,
    account_reserved_sum,
    account_used_sum,
    adjust_quota,
    allocate_tokens,
    allocated_sum,
    get_account_external_used,
    get_account_total,
    release_reserved_tokens_in_session,
    set_account_quota_baseline,
    settle_reserved_tokens_in_session,
    usage_summary,
)

require_admin()
admin_id = st.session_state["user_id"]


def _set_user_status(user_id: int, status: str) -> None:
    with get_session() as session:
        user = session.get(User, user_id)
        if user and user.role == "member":
            user.status = status


def _reset_member_password(user_id: int, password: str) -> None:
    with get_session() as session:
        user = session.get(User, user_id)
        if user and user.role == "member":
            user.password_hash = hash_password(password)


def _generation_has_video_input(request_json: str) -> bool:
    try:
        payload = json.loads(request_json or "{}")
    except json.JSONDecodeError:
        return False
    return any(item.get("type") == "video_url" for item in payload.get("content", []))


def _manual_settle_generation(generation_id: int, actual_tokens: int) -> None:
    if actual_tokens <= 0:
        raise QuotaError("真实消耗 tokens 必须大于 0")
    with get_session() as session:
        gen = session.get(Generation, generation_id)
        if not gen:
            raise QuotaError("生成记录不存在")
        if gen.status != "needs_settlement":
            raise QuotaError("只有待人工结算的任务可以手动结算")
        if gen.tokens_used:
            raise QuotaError("该任务已有实际消耗记录，请勿重复结算")
        user_id = gen.user_id
        reserved = gen.reserved_tokens
        has_video_input = _generation_has_video_input(gen.request_json)

    with get_session() as session:
        gen = session.get(Generation, generation_id)
        if gen:
            settle_reserved_tokens_in_session(
                session,
                user_id,
                reserved,
                actual_tokens,
                note=f"管理员人工结算生成 #{generation_id}",
                operator_id=admin_id,
            )
            gen.status = "succeeded"
            gen.tokens_used = actual_tokens
            gen.cost_yuan = pricing.tokens_to_yuan(
                actual_tokens,
                has_video_input=has_video_input,
            )
            gen.error_message = ""
            gen.finished_at = datetime.utcnow()


def _manual_release_unbilled_generation(generation_id: int) -> None:
    with get_session() as session:
        gen = session.get(Generation, generation_id)
        if not gen:
            raise QuotaError("生成记录不存在")
        if gen.status != "needs_settlement":
            raise QuotaError("只有待人工结算的任务可以释放预占")
        if gen.tokens_used:
            raise QuotaError("该任务已有实际消耗记录，请勿释放")
        release_reserved_tokens_in_session(
            session,
            gen.user_id,
            gen.reserved_tokens,
            note=f"管理员确认未计费，释放生成 #{generation_id} 预占",
            operator_id=admin_id,
        )
        gen.status = "failed"
        gen.error_message = "管理员确认该任务未产生火山计费，已释放预占额度。"
        gen.finished_at = datetime.utcnow()


def _quota_step(free_tokens: int) -> int:
    if free_tokens <= 100_000:
        return 1_000
    if free_tokens <= 1_000_000:
        return 10_000
    if free_tokens <= 10_000_000:
        return 100_000
    return 1_000_000


st.title("🛡️ 管理后台")
tab_members, tab_usage, tab_review, tab_assets, tab_ops = st.tabs(
    ["👥 成员与额度", "📊 用量统计", "🔎 内容审核", "🧑‍⚖️ 素材审核", "🧰 运维"]
)

# ---------- 1. 成员与额度 ----------
with tab_members:
    # 账号总额 + 已分配 + 已用 概览（硬约束的基准）
    total = get_account_total()
    external_used = get_account_external_used()
    managed_total = account_managed_total()
    allocated = allocated_sum()
    used = account_used_sum()
    reserved = account_reserved_sum()
    account_free = account_remaining()
    overrun = account_overrun()
    free_to_allocate = max(managed_total - allocated, 0)

    st.subheader("账号额度总览")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("资源包总额", f"{total:,}")
    m2.metric("期初/外部已消耗", f"{external_used:,}")
    m3.metric("本应用可管控", f"{managed_total:,}")
    m4.metric("账号可用", f"{account_free:,}")
    m5, m6, m7, m8 = st.columns(4)
    m5.metric("已分配给成员", f"{allocated:,}")
    m6.metric("实际已消耗", f"{used:,}")
    m7.metric("运行中预占", f"{reserved:,}")
    m8.metric("账号超额", f"{overrun:,}")
    st.progress(
        min(allocated / managed_total, 1.0) if managed_total else 0.0,
        text=f"已分配 {allocated:,} / 本应用可管控 {managed_total:,}",
    )
    if not managed_total:
        st.error("请先设置真实资源包 token 总额，否则无法安全分配额度。")
    if allocated > managed_total:
        st.warning("成员额度之和已超过本应用可管控额度，请调整资源包基准或下调成员额度。")
    if overrun:
        st.error(
            f"本地账本显示已消耗+预占超过本应用可管控额度 {overrun:,} tokens。"
            "请核对资源包基准、外部消耗，以及是否存在待人工结算任务。"
        )

    with st.expander("⚙️ 设置资源包额度基准", expanded=not managed_total):
        c_total, c_used = st.columns(2)
        new_total = c_total.number_input(
            "资源包 token 总额", min_value=0, value=int(total), step=1_000_000
        )
        new_external_used = c_used.number_input(
            "期初/外部已消耗 tokens",
            min_value=0,
            value=int(external_used),
            step=1_000_000,
        )
        st.caption(
            "请从火山控制台的资源包/用量页面填写真实 token。"
            "如果资源包已有历史消耗，填入期初/外部已消耗；本应用只会把剩余可管控部分分配给成员。"
        )
        if st.button("保存资源包基准"):
            try:
                set_account_quota_baseline(int(new_total), int(new_external_used))
                st.success("已更新资源包额度基准")
                st.rerun()
            except QuotaError as e:
                st.error(str(e))

    st.divider()
    st.subheader("新增成员")
    with st.form("add_member", clear_on_submit=True):
        c1, c2, c3 = st.columns([2, 2, 2])
        new_name = c1.text_input("用户名")
        new_pw = c2.text_input("初始密码", type="password")
        init_quota = c3.number_input(
            "初始额度 (tokens)",
            min_value=0,
            max_value=int(free_to_allocate),
            value=0,
            step=_quota_step(free_to_allocate),
        )
        st.caption(f"当前最多可分配 {free_to_allocate:,} tokens；也可以先创建 0 额度成员，之后再追加。")
        if st.form_submit_button("创建成员", type="primary"):
            name = new_name.strip()
            if not name or not new_pw:
                st.error("用户名和密码不能为空")
            elif managed_total <= 0:
                st.error("请先设置真实资源包 token 总额。")
            elif int(init_quota) > free_to_allocate:
                st.error(
                    f"超出可分配额度：当前最多还能分配 {free_to_allocate:,} tokens。"
                )
            else:
                created = False
                with get_session() as session:
                    exists = session.scalar(select(User).where(User.username == name))
                    if exists:
                        st.error("用户名已存在")
                    else:
                        user = User(
                            username=name,
                            password_hash=hash_password(new_pw),
                            role="member",
                            token_quota=int(init_quota),
                        )
                        session.add(user)
                        session.flush()
                        if int(init_quota) > 0:
                            session.add(
                                QuotaTransaction(
                                    user_id=user.id,
                                    operator_id=admin_id,
                                    type="allocate",
                                    tokens=int(init_quota),
                                    note="创建成员初始额度",
                                )
                            )
                        created = True
                if created:
                    st.success(f"已创建成员 {name}")
                    st.rerun()

    st.subheader("成员额度管理")
    rows = [r for r in usage_summary() if r["role"] != "admin"]
    if not rows:
        st.info("还没有成员，先在上方创建一个。")
    for r in rows:
        used, quota, reserved = r["token_used"], r["token_quota"], r["token_reserved"]
        remaining = max(quota - used - reserved, 0)
        member_overrun = max(used + reserved - quota, 0)
        pct = ((used + reserved) / quota) if quota else 0.0
        status_label = "启用" if r["status"] == "active" else "停用"
        with st.expander(
            f"👤 {r['username']} · {status_label} · 已用 {used:,} / {quota:,} tokens"
        ):
            st.progress(
                min(pct, 1.0),
                text=(
                    f"可用 {remaining:,} tokens · 运行中预占 {reserved:,} · "
                    f"累计费用 ¥{r['total_cost']:.4f}"
                ),
            )
            if member_overrun:
                st.error(
                    f"该成员已超出分配额度 {member_overrun:,} tokens。"
                    "系统会阻止继续提交新任务，请重设额度或等待人工核对。"
                )
            col_add, col_set = st.columns(2)
            with col_add:
                add_step = _quota_step(free_to_allocate)
                add_amt = st.number_input(
                    "追加额度",
                    min_value=0,
                    max_value=int(free_to_allocate),
                    value=0,
                    step=add_step,
                    key=f"add_{r['id']}",
                )
                if st.button("追加", key=f"addbtn_{r['id']}", width="stretch"):
                    try:
                        allocate_tokens(r["id"], int(add_amt), admin_id, note="管理员追加")
                        st.rerun()
                    except QuotaError as e:
                        st.error(str(e))
            with col_set:
                set_max = int(quota + free_to_allocate)
                set_amt = st.number_input(
                    "重设总额度",
                    min_value=0,
                    max_value=set_max,
                    value=int(min(quota, set_max)),
                    step=_quota_step(set_max),
                    key=f"set_{r['id']}",
                )
                if st.button("重设", key=f"setbtn_{r['id']}", width="stretch"):
                    try:
                        adjust_quota(r["id"], int(set_amt), admin_id, note="管理员重设")
                        st.rerun()
                    except QuotaError as e:
                        st.error(str(e))
            st.divider()
            col_status, col_reset = st.columns(2)
            with col_status:
                if r["status"] == "active":
                    if st.button("停用账号", key=f"disable_{r['id']}", width="stretch"):
                        _set_user_status(r["id"], "disabled")
                        st.rerun()
                else:
                    if st.button("启用账号", key=f"enable_{r['id']}", width="stretch"):
                        _set_user_status(r["id"], "active")
                        st.rerun()
            with col_reset:
                new_password = st.text_input(
                    "重置密码", type="password", key=f"pw_{r['id']}", placeholder="输入新密码"
                )
                if st.button("保存新密码", key=f"pwbtn_{r['id']}", width="stretch"):
                    if not new_password:
                        st.error("新密码不能为空")
                    else:
                        _reset_member_password(r["id"], new_password)
                        st.success("已重置密码")

    with st.expander("最近额度流水"):
        with get_session() as session:
            users = session.scalars(select(User)).all()
            user_map = {u.id: u.username for u in users}
            txs = session.scalars(
                select(QuotaTransaction)
                .order_by(QuotaTransaction.created_at.desc())
                .limit(200)
            ).all()
            tx_rows = [
                {
                    "时间": t.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                    "用户": user_map.get(t.user_id, str(t.user_id)),
                    "操作人": user_map.get(t.operator_id, "系统") if t.operator_id else "系统",
                    "类型": t.type,
                    "tokens": t.tokens,
                    "备注": t.note,
                }
                for t in txs
            ]
        if tx_rows:
            tx_df = pd.DataFrame(tx_rows)
            st.download_button(
                "下载额度流水 CSV",
                tx_df.to_csv(index=False).encode("utf-8-sig"),
                file_name="quota_transactions.csv",
                mime="text/csv",
            )
            st.dataframe(tx_df, width="stretch", hide_index=True)
        else:
            st.caption("暂无额度流水。")

# ---------- 2. 用量统计 ----------
with tab_usage:
    st.subheader("各成员用量")
    rows = [r for r in usage_summary() if r["role"] != "admin"]
    if rows:
        df = pd.DataFrame(rows)
        df["剩余"] = (df["token_quota"] - df["token_used"]).clip(lower=0)
        df["可用"] = (df["token_quota"] - df["token_used"] - df["token_reserved"]).clip(lower=0)
        df["超额"] = (df["token_used"] + df["token_reserved"] - df["token_quota"]).clip(lower=0)
        df = df.rename(
            columns={
                "username": "用户名",
                "role": "角色",
                "status": "状态",
                "token_quota": "分配额度",
                "token_used": "已用",
                "token_reserved": "预占",
                "gen_count": "生成数",
                "total_cost": "累计费用(¥)",
            }
        )
        st.dataframe(
            df[
                [
                    "用户名",
                    "角色",
                    "状态",
                    "分配额度",
                    "已用",
                    "预占",
                    "可用",
                    "超额",
                    "生成数",
                    "累计费用(¥)",
                ]
            ],
            width="stretch",
            hide_index=True,
        )
        st.download_button(
            "下载用量统计 CSV",
            df.to_csv(index=False).encode("utf-8-sig"),
            file_name="usage_summary.csv",
            mime="text/csv",
        )
        members = df[df["角色"] == "member"]
        if not members.empty:
            st.bar_chart(members.set_index("用户名")["已用"])
    else:
        st.info("暂无数据。")

# ---------- 3. 内容审核 ----------
with tab_review:
    st.subheader("创作内容审核")
    with get_session() as session:
        users = session.scalars(select(User)).all()
        name_map = {u.id: u.username for u in users}

    selected = st.selectbox("筛选成员", options=["全部"] + list(name_map.values()))

    with get_session() as session:
        query = select(Generation).order_by(Generation.created_at.desc()).limit(50)
        if selected != "全部":
            uid = next(k for k, v in name_map.items() if v == selected)
            query = (
                select(Generation)
                .where(Generation.user_id == uid)
                .order_by(Generation.created_at.desc())
                .limit(50)
            )
        gens = session.scalars(query).all()
        records = [
            {
                "id": g.id,
                "user": name_map.get(g.user_id, str(g.user_id)),
                "prompt": g.prompt,
                "status": g.status,
                "tokens": g.tokens_used,
                "estimated_tokens": g.estimated_tokens,
                "reserved_tokens": g.reserved_tokens,
                "token_delta": g.tokens_used - g.estimated_tokens if g.tokens_used else 0,
                "cost": g.cost_yuan,
                "time": g.created_at.strftime("%Y-%m-%d %H:%M"),
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
                "task_id": g.task_id,
                "request_json": g.request_json,
                "response_json": g.response_json,
            }
            for g in gens
        ]

    if not records:
        st.info("暂无创作记录（视频生成将在第二步「模块 1 创作场」接入）。")
    else:
        review_df = pd.DataFrame(
            [
                {
                    "ID": r["id"],
                    "用户": r["user"],
                    "状态": r["status"],
                    "Task ID": r["task_id"],
                    "tokens": r["tokens"],
                    "预估 tokens": r["estimated_tokens"],
                    "预占 tokens": r["reserved_tokens"],
                    "实际-预估": r["token_delta"],
                    "费用": r["cost"],
                    "时间": r["time"],
                    "提示词": r["prompt"],
                    "失败原因": r["error_message"],
                    "视频": r["video_url"],
                }
                for r in records
            ]
        )
        st.download_button(
            "下载创作记录 CSV",
            review_df.to_csv(index=False).encode("utf-8-sig"),
            file_name="generation_records.csv",
            mime="text/csv",
        )
    for rec in records:
        with st.container(border=True):
            st.markdown(
                f"**{rec['user']}** · {rec['time']} · `{rec['status']}` · "
                f"{rec['tokens']:,} tokens · ¥{rec['cost']:.4f}"
            )
            if rec["status"] == "succeeded":
                delta = rec["tokens"] - rec["estimated_tokens"]
                st.caption(
                    f"预估 {rec['estimated_tokens']:,} · 预占 {rec['reserved_tokens']:,} · "
                    f"实际-预估 {delta:+,}"
                )
                if rec["tokens"] > rec["reserved_tokens"]:
                    st.warning(
                        f"本次实际消耗超过预占 {rec['tokens'] - rec['reserved_tokens']:,} tokens，"
                        "账本已按真实消耗结算。"
                    )
            if rec["status"] == "needs_settlement":
                st.warning(rec["error_message"] or "该任务需要人工结算真实 token 消耗。")
                with st.form(f"manual_settle_{rec['id']}"):
                    actual_tokens = st.number_input(
                        "火山真实消耗 tokens",
                        min_value=1,
                        value=max(int(rec["reserved_tokens"]), 1),
                        step=1_000,
                        key=f"actual_tokens_{rec['id']}",
                    )
                    if st.form_submit_button("按真实 tokens 结算"):
                        try:
                            _manual_settle_generation(rec["id"], int(actual_tokens))
                            st.success("已按真实 tokens 完成人工结算")
                            st.rerun()
                        except QuotaError as e:
                            st.error(str(e))
                if st.button("确认火山未计费，释放预占", key=f"release_unbilled_{rec['id']}"):
                    try:
                        _manual_release_unbilled_generation(rec["id"])
                        st.success("已释放预占额度")
                        st.rerun()
                    except QuotaError as e:
                        st.error(str(e))
            if rec["prompt"]:
                st.caption(rec["prompt"][:120])
            if rec["task_id"]:
                st.caption(f"Task ID: `{rec['task_id']}`")
            if rec["video_url"]:
                st.video(rec["video_url"])
            if rec["audio_url"]:
                st.audio(rec["audio_url"])
            if rec["last_frame_url"]:
                st.image(rec["last_frame_url"], caption="尾帧", width="stretch")
            if rec["status"] == "failed" and rec["error_message"]:
                st.error(rec["error_message"])
                hint = seedance.error_hint_from_text(rec["error_message"])
                if hint:
                    st.info(f"处理建议：{hint}")
            with st.expander("请求 / 响应 JSON"):
                st.code(rec["request_json"] or "{}", language="json")
                st.code(rec["response_json"] or "{}", language="json")

# ---------- 4. 素材审核 ----------
with tab_assets:
    st.subheader("待审核素材")
    st.caption("人脸素材入库默认为待审核（pending）。批准后图片才能在创作场「选用首帧」中引用。")
    pending = asset_store.list_assets_by_status("pending", limit=60)
    if not pending:
        st.success("没有待审核素材。")
    else:
        st.write(f"共 **{len(pending)}** 个待审核：")
        cols = st.columns(4)
        for i, a in enumerate(pending):
            with cols[i % 4]:
                with st.container(border=True):
                    if a["type"] == "image":
                        st.image(a["tos_url"], width="stretch")
                    else:
                        st.video(a["tos_url"])
                    st.caption(f"#{a['id']} · {a['filename'] or a['type']} · {a['created_at']}")
                    bc1, bc2 = st.columns(2)
                    if bc1.button("✅ 批准", key=f"appr_{a['id']}", width="stretch"):
                        asset_store.set_review_status(a["id"], "approved")
                        st.cache_data.clear()
                        st.rerun()
                    if bc2.button("❌ 驳回", key=f"rej_{a['id']}", width="stretch"):
                        asset_store.set_review_status(a["id"], "rejected")
                        st.cache_data.clear()
                        st.rerun()

    with st.expander("查看已驳回素材"):
        rejected = asset_store.list_assets_by_status("rejected", limit=40)
        if not rejected:
            st.caption("无")
        for a in rejected:
            rc1, rc2 = st.columns([4, 1])
            rc1.write(f"#{a['id']} · {a['filename'] or a['type']} · {a['created_at']}")
            if rc2.button("恢复待审", key=f"restore_{a['id']}"):
                asset_store.set_review_status(a["id"], "pending")
                st.cache_data.clear()
                st.rerun()

# ---------- 5. 运维 ----------
with tab_ops:
    st.subheader("运行状态")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("TOS 配置", "可用" if tos_client.is_configured() else "未配置")
    c2.metric("账号可用 tokens", f"{account_remaining():,}")
    c3.metric("运行中预占", f"{account_reserved_sum():,}")
    c4.metric("TOS URL 模式", settings.TOS_URL_MODE or "public")

    st.subheader("最近日志")
    log_path = "logs/seedance.log"
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()[-300:]
        log_text = "".join(lines)
        st.code(log_text or "暂无日志", language="text")
        if log_text:
            st.download_button(
                "下载日志",
                log_text.encode("utf-8"),
                file_name="seedance.log",
                mime="text/plain",
            )
    except FileNotFoundError:
        st.caption("暂无日志文件。")
