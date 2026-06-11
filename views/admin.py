"""管理后台（仅管理员）。

三个标签页：
1. 成员与额度 —— 新增成员、追加/重设 token 额度
2. 用量统计   —— 各成员已用额度、生成数、累计费用 + 柱状图
3. 内容审核   —— 按成员查看创作记录与视频
"""
import pandas as pd
import streamlit as st
from sqlalchemy import select

import asset_store
import tos_client
from auth import hash_password, require_admin
from database import get_session
from models import Generation, User
from quota import (
    QuotaError,
    account_used_sum,
    adjust_quota,
    allocate_tokens,
    allocated_sum,
    get_account_total,
    set_account_total,
    usage_summary,
)

require_admin()
admin_id = st.session_state["user_id"]

st.title("🛡️ 管理后台")
tab_members, tab_usage, tab_review, tab_assets = st.tabs(
    ["👥 成员与额度", "📊 用量统计", "🔎 内容审核", "🧑‍⚖️ 素材审核"]
)

# ---------- 1. 成员与额度 ----------
with tab_members:
    # 账号总额 + 已分配 + 已用 概览（硬约束的基准）
    total = get_account_total()
    allocated = allocated_sum()
    used = account_used_sum()
    free_to_allocate = max(total - allocated, 0)

    st.subheader("账号额度总览")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("账号总额", f"{total:,}")
    m2.metric("已分配给成员", f"{allocated:,}")
    m3.metric("可再分配", f"{free_to_allocate:,}")
    m4.metric("实际已消耗", f"{used:,}")
    st.progress(min(allocated / total, 1.0) if total else 0.0,
                text=f"已分配 {allocated:,} / 账号总额 {total:,}")
    if allocated > total:
        st.warning("成员额度之和已超过账号总额，请上调账号总额或下调成员额度。")

    with st.expander("⚙️ 调整账号总额（资源包大小 / 自设上限）"):
        new_total = st.number_input(
            "账号 token 总额", min_value=0, value=int(total), step=50_000_000
        )
        st.caption(
            "火山大模型为后付费、按小时结算，没有可实时调用的硬剩余额度。"
            "此处填你购买的资源包大小或自定上限，作为分配与消耗的硬闸门基准。"
        )
        if st.button("保存账号总额"):
            try:
                set_account_total(int(new_total))
                st.success("已更新账号总额")
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
            "初始额度 (tokens)", min_value=0, value=min(50_000_000, free_to_allocate),
            step=10_000_000,
        )
        if st.form_submit_button("创建成员", type="primary"):
            name = new_name.strip()
            if not name or not new_pw:
                st.error("用户名和密码不能为空")
            elif int(init_quota) > free_to_allocate:
                st.error(
                    f"超出账号总额：当前最多还能分配 {free_to_allocate:,} tokens。"
                )
            else:
                with get_session() as session:
                    exists = session.scalar(select(User).where(User.username == name))
                    if exists:
                        st.error("用户名已存在")
                    else:
                        session.add(
                            User(
                                username=name,
                                password_hash=hash_password(new_pw),
                                role="member",
                                token_quota=int(init_quota),
                            )
                        )
                        st.success(f"已创建成员 {name}")
                        st.rerun()

    st.subheader("成员额度管理")
    rows = [r for r in usage_summary() if r["role"] != "admin"]
    if not rows:
        st.info("还没有成员，先在上方创建一个。")
    for r in rows:
        used, quota = r["token_used"], r["token_quota"]
        remaining = max(quota - used, 0)
        pct = (used / quota) if quota else 0.0
        with st.expander(f"👤 {r['username']} · 已用 {used:,} / {quota:,} tokens"):
            st.progress(
                min(pct, 1.0),
                text=f"剩余 {remaining:,} tokens · 累计费用 ¥{r['total_cost']:.4f}",
            )
            col_add, col_set = st.columns(2)
            with col_add:
                add_amt = st.number_input(
                    "追加额度", min_value=0, value=10_000_000, step=10_000_000,
                    key=f"add_{r['id']}",
                )
                if st.button("追加", key=f"addbtn_{r['id']}", use_container_width=True):
                    try:
                        allocate_tokens(r["id"], int(add_amt), admin_id, note="管理员追加")
                        st.rerun()
                    except QuotaError as e:
                        st.error(str(e))
            with col_set:
                set_amt = st.number_input(
                    "重设总额度", min_value=0, value=int(quota), step=10_000_000,
                    key=f"set_{r['id']}",
                )
                if st.button("重设", key=f"setbtn_{r['id']}", use_container_width=True):
                    try:
                        adjust_quota(r["id"], int(set_amt), admin_id, note="管理员重设")
                        st.rerun()
                    except QuotaError as e:
                        st.error(str(e))

# ---------- 2. 用量统计 ----------
with tab_usage:
    st.subheader("各成员用量")
    rows = usage_summary()
    if rows:
        df = pd.DataFrame(rows)
        df["剩余"] = (df["token_quota"] - df["token_used"]).clip(lower=0)
        df = df.rename(
            columns={
                "username": "用户名",
                "role": "角色",
                "token_quota": "分配额度",
                "token_used": "已用",
                "gen_count": "生成数",
                "total_cost": "累计费用(¥)",
            }
        )
        st.dataframe(
            df[["用户名", "角色", "分配额度", "已用", "剩余", "生成数", "累计费用(¥)"]],
            use_container_width=True,
            hide_index=True,
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
        members = session.scalars(select(User).where(User.role == "member")).all()
        name_map = {u.id: u.username for u in members}

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
                "user": name_map.get(g.user_id, str(g.user_id)),
                "prompt": g.prompt,
                "status": g.status,
                "tokens": g.tokens_used,
                "cost": g.cost_yuan,
                "time": g.created_at.strftime("%Y-%m-%d %H:%M"),
                "video_url": g.video_url,
            }
            for g in gens
        ]

    if not records:
        st.info("暂无创作记录（视频生成将在第二步「模块 1 创作场」接入）。")
    for rec in records:
        with st.container(border=True):
            st.markdown(
                f"**{rec['user']}** · {rec['time']} · `{rec['status']}` · "
                f"{rec['tokens']:,} tokens · ¥{rec['cost']:.4f}"
            )
            if rec["prompt"]:
                st.caption(rec["prompt"][:120])
            if rec["video_url"]:
                st.video(rec["video_url"])

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
                        st.image(a["tos_url"], use_container_width=True)
                    else:
                        st.video(a["tos_url"])
                    st.caption(f"#{a['id']} · {a['filename'] or a['type']} · {a['created_at']}")
                    bc1, bc2 = st.columns(2)
                    if bc1.button("✅ 批准", key=f"appr_{a['id']}", use_container_width=True):
                        asset_store.set_review_status(a["id"], "approved")
                        st.rerun()
                    if bc2.button("❌ 驳回", key=f"rej_{a['id']}", use_container_width=True):
                        asset_store.set_review_status(a["id"], "rejected")
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
                st.rerun()
