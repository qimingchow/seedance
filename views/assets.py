"""模块 2 · 私域资产库全览。

- 浏览：资产组列表 + 组内素材（图片/视频）
- 缓存：组列表 5 分钟、组内素材 10 分钟，多用户共享（@st.cache_data 服务端共享）
- 懒加载：展开资产组后点「加载素材」按需拉取；分页每页 20
- 管理：新建/重命名/删除资产组、登记/删除素材
- 引用：图片素材可一键「用作创作场首帧」跳到创作场
"""
import math

import streamlit as st

import ark_assets
import asset_store
import tos_client
from auth import current_user

PAGE = 20
user = current_user()


# ---------------- 多用户共享缓存 ----------------
@st.cache_data(ttl=300, show_spinner=False)
def cached_groups():
    return asset_store.list_groups_with_counts()


@st.cache_data(ttl=600, show_spinner=False)
def cached_assets(group_id: int):
    return asset_store.list_assets(group_id)


def _bust_cache():
    cached_groups.clear()
    cached_assets.clear()


# ---------------- 侧边栏：缓存策略 ----------------
with st.sidebar:
    st.markdown("### 🗂️ 缓存策略")
    st.caption(
        "· 资产组列表：5 分钟\n\n"
        "· 组内素材：10 分钟\n\n"
        "· 多用户共享同一缓存\n\n"
        "· 每页 20 个素材"
    )
    if st.button("🔄 刷新所有资产缓存", width="stretch"):
        st.cache_data.clear()
        st.rerun()
    if not tos_client.is_configured():
        st.info("TOS 未配置：删除素材只移除记录，不会删 TOS 对象。", icon="ℹ️")
    ark_capability_unavailable = bool(st.session_state.get("ark_asset_capability_unavailable"))
    ark_ready = ark_assets.is_configured() and not ark_capability_unavailable
    st.caption(f"Ark 资产 OpenAPI：{'可用' if ark_ready else '未配置、未启用或账号未开通'}")
    if ark_assets.is_configured() and ark_capability_unavailable:
        st.warning("当前账号未开通 Ark AIGC 资产能力，资产库会使用本地 TOS 记录。", icon="⚠️")
        if st.button("重新尝试 Ark 资产同步", width="stretch"):
            st.session_state.pop("ark_asset_capability_unavailable", None)
            st.rerun()

# ---------------- 页头 + 新建组 ----------------
st.title("🖼️ 私域资产库全览")
notice = st.session_state.pop("asset_notice", None)
if notice:
    level, message = notice
    if level == "success":
        st.success(message)
    else:
        st.warning(message)
groups = cached_groups()
st.write(f"当前共 **{len(groups)}** 个资产组，展开后点「加载素材」按需拉取。")
st.caption("💡 素材主要通过「人脸素材提交」上传入库（模块 3）。下面也可手动登记 TOS 地址用于测试。")

with st.expander("➕ 新建资产组"):
    c1, c2 = st.columns([2, 3])
    g_name = c1.text_input("资产组名称", key="new_group_name")
    g_desc = c2.text_input("描述（可选）", key="new_group_desc")
    clean_group_name = g_name.strip()
    existing_group_names = {g["name"] for g in groups}
    group_already_exists = bool(clean_group_name and clean_group_name in existing_group_names)
    if group_already_exists:
        st.info(f"资产组「{clean_group_name}」已存在。")
    if st.button("创建资产组", type="primary", disabled=not clean_group_name or group_already_exists):
        group_id = None
        try:
            group_id = asset_store.create_group(clean_group_name, g_desc)
        except asset_store.AssetError as e:
            if ark_assets.is_subscription_required(e):
                try:
                    group_id = asset_store.create_group(
                        clean_group_name,
                        g_desc,
                        sync_remote=False,
                    )
                except asset_store.AssetError as local_error:
                    st.error(str(local_error))
                else:
                    st.session_state["ark_asset_capability_unavailable"] = True
                    st.session_state["asset_notice"] = (
                        "warning",
                        f"已创建本地资产组 #{group_id}：{clean_group_name}。{ark_assets.capability_hint(e)}",
                    )
                    _bust_cache()
                    st.rerun()
            else:
                st.error(str(e))
        if group_id:
            _bust_cache()
            st.session_state["asset_notice"] = (
                "success",
                f"已创建资产组 #{group_id}：{clean_group_name}",
            )
            st.rerun()

# ---------------- 资产组分页 ----------------
if not groups:
    st.info("还没有资产组，先在上面新建一个。")
    st.stop()

pages = max(math.ceil(len(groups) / PAGE), 1)
gpage = st.number_input("资产组页码", min_value=1, max_value=pages, value=1, key="group_page")
shown_groups = groups[(gpage - 1) * PAGE: gpage * PAGE]

for g in shown_groups:
    gid = g["id"]
    with st.expander(f"📁 {g['name']} （{g['asset_count']} 个素材）"):
        st.caption(f"Ark GroupId：{g.get('ark_group_id') or '未同步'}")
        # --- 组管理：重命名 / 删除 ---
        mc1, mc2, mc3 = st.columns([3, 1, 2])
        rename_val = mc1.text_input("重命名", value=g["name"], key=f"rn_{gid}", label_visibility="collapsed")
        if mc2.button("重命名", key=f"rnbtn_{gid}"):
            try:
                asset_store.rename_group(gid, rename_val)
                _bust_cache()
                st.rerun()
            except asset_store.AssetError as e:
                st.error(str(e))
        confirm_del = mc3.checkbox("确认删除整组", key=f"cfd_{gid}")
        if mc3.button("🗑️ 删除资产组", key=f"delg_{gid}", disabled=not confirm_del):
            try:
                keys = asset_store.delete_group(gid)
                for k in keys:
                    tos_client.delete_object(k)
                _bust_cache()
                st.success("已删除资产组")
                st.rerun()
            except asset_store.AssetError as e:
                st.error(str(e))

        st.divider()

        # --- 懒加载：点「加载素材」才拉取并渲染 ---
        loaded_key = f"loaded_{gid}"
        lc1, lc2, lc3 = st.columns([1, 1, 1])
        if lc1.button("📥 加载素材", key=f"load_{gid}"):
            st.session_state[loaded_key] = True
        if lc2.button("🔄 刷新本组缓存", key=f"refresh_{gid}"):
            cached_assets.clear()
            cached_groups.clear()
            st.session_state[loaded_key] = True
            st.rerun()
        if lc3.button(
            "同步火山素材",
            key=f"sync_remote_{gid}",
            disabled=not (ark_ready and g.get("ark_group_id")),
        ):
            try:
                remote_assets = ark_assets.list_assets_in_group(g["ark_group_id"])
                count = asset_store.sync_remote_assets_for_group(gid, remote_assets)
                _bust_cache()
                st.session_state[loaded_key] = True
                st.success(f"已同步 {count} 个火山素材")
                st.rerun()
            except (ark_assets.ArkAssetError, asset_store.AssetError) as e:
                st.error(str(e))
                if ark_assets.is_subscription_required(e):
                    st.session_state["ark_asset_capability_unavailable"] = True
                    st.info(ark_assets.capability_hint(e))

        if st.session_state.get(loaded_key):
            assets = cached_assets(gid)
            if not assets:
                st.caption("（本组暂无素材）")
            else:
                apages = max(math.ceil(len(assets) / PAGE), 1)
                apage = st.number_input(
                    "素材页码", min_value=1, max_value=apages, value=1, key=f"apage_{gid}"
                )
                page_assets = assets[(apage - 1) * PAGE: apage * PAGE]
                cols = st.columns(4)
                for i, a in enumerate(page_assets):
                    with cols[i % 4]:
                        if a["type"] == "image":
                            st.image(a["tos_url"], width="stretch")
                        else:
                            st.video(a["tos_url"])
                        badge = {"approved": "✅", "pending": "⏳", "rejected": "❌"}.get(
                            a["review_status"], ""
                        )
                        status_text = {
                            "approved": "已批准",
                            "pending": "待审核",
                            "rejected": "已驳回",
                        }.get(a["review_status"], a["review_status"])
                        ark_text = f" · Ark {a['ark_asset_id']}" if a.get("ark_asset_id") else ""
                        st.caption(
                            f"{badge} #{a['id']} · {status_text}{ark_text} · "
                            f"{a['filename'] or a['type']}"
                        )
                        if a.get("ark_status") and a.get("ark_status") != "Active":
                            st.caption(f"Ark 状态：{a['ark_status']}")
                        if a.get("ark_error"):
                            st.caption(f"Ark 错误：{a['ark_error'][:80]}")
                        st.markdown(f"[打开原文件]({a['tos_url']})")
                        if a["type"] == "image":
                            approved = a["review_status"] == "approved"
                            if st.button(
                                "🎬 用作首帧" if approved else "待审核，批准后可用",
                                key=f"ref_{a['id']}",
                                width="stretch",
                                disabled=not approved,
                            ):
                                st.session_state["ref_image_url"] = a["tos_url"]
                                st.switch_page("views/studio.py")
                        if st.button("🗑️ 删除", key=f"dela_{a['id']}", width="stretch"):
                            try:
                                key = asset_store.delete_asset(a["id"])
                                tos_client.delete_object(key)
                                _bust_cache()
                                st.rerun()
                            except asset_store.AssetError as e:
                                st.error(str(e))

        # --- 手动登记素材（测试用；正式入库走模块 3）---
        with st.popover("➕ 手动登记素材（TOS 地址）"):
            a_type = st.selectbox("类型", ["image", "video"], key=f"at_{gid}")
            a_url = st.text_input("TOS 访问地址", key=f"au_{gid}", placeholder="https://…")
            a_name = st.text_input("文件名（可选）", key=f"an_{gid}")
            if st.button("登记", key=f"addab_{gid}"):
                try:
                    asset_store.add_asset(
                        gid, asset_type=a_type, tos_url=a_url, filename=a_name,
                        review_status="approved",
                        uploaded_by=(user.id if user else None),
                    )
                    _bust_cache()
                    st.session_state[loaded_key] = True
                    st.success("已登记素材")
                    st.rerun()
                except asset_store.AssetError as e:
                    st.error(str(e))
