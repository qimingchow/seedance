"""模块 3 · 人脸素材入库系统。

流程：选择文件 → 校验格式 → 上传到 TOS → 注册 Ark 资产 → Active 后写入资产组。
资产组配置支持：从现有列表选择 / 手动输入 Ark GroupId / 创建新组。
"""
import uuid

import streamlit as st

import ark_assets
import asset_store
import review
import tos_client
import validation
from auth import current_user

MAX_FILES = 50

user = current_user()
ark_configured = ark_assets.is_configured()
ark_capability_unavailable = bool(st.session_state.get("ark_asset_capability_unavailable"))
ark_ready = ark_configured and not ark_capability_unavailable


def _group_label(group: dict) -> str:
    remote = group.get("ark_group_id") or "未同步"
    return f"{group['name']} · 本地 #{group['id']} · Ark {remote}"


def _refresh_groups() -> list[dict]:
    return asset_store.list_groups_with_counts()


def _switch_to_local_asset_mode(message: str) -> None:
    st.session_state["ark_asset_capability_unavailable"] = True
    st.session_state["face_notice"] = (
        "warning",
        f"{message} 已切换为本地 TOS 入库模式；如需 asset://AssetId，请先在火山方舟开通 AIGC 资产能力。",
    )
    st.rerun()


st.title("👤 人脸素材入库系统")
if ark_ready:
    st.info("流程：选择文件 → 校验格式 → 上传到 TOS → 注册火山 Ark 资产 → Active 后入库资产组", icon="🧭")
else:
    st.info("流程：选择文件 → 校验格式 → 上传到 TOS → 本地入库 → 管理员审核后可在创作场引用", icon="🧭")
notice = st.session_state.pop("face_notice", None)
if notice:
    level, message = notice
    if level == "success":
        st.success(message)
    else:
        st.warning(message)

# ---- 侧边栏：校验规则 ----
with st.sidebar:
    st.markdown("### 📋 校验规则")
    st.caption(
        f"**图片** (≤{validation.IMG_MAX_MB}MB)：jpg/png/webp/bmp/tiff/gif/heic/heif；"
        f"{validation.IMG_MIN_PX}~{validation.IMG_MAX_PX}px；宽高比 {validation.IMG_ASPECT_MIN}~{validation.IMG_ASPECT_MAX}"
    )
    st.caption(
        f"**视频** (≤{validation.VID_MAX_MB}MB)：mp4/mov；{validation.VID_MIN_S}~{validation.VID_MAX_S}s；"
        f"{validation.VID_FPS_MIN}~{validation.VID_FPS_MAX}fps；{validation.VID_RES_MIN}p~{validation.VID_RES_MAX}p"
    )
    st.markdown("### 连接状态")
    st.caption(f"TOS：{'可用' if tos_client.is_configured() else '未配置'}")
    st.caption(f"Ark 资产 OpenAPI：{'可用' if ark_ready else '未配置、未启用或账号未开通'}")
    if not tos_client.is_configured():
        st.warning("TOS 未配置：可校验文件，但无法上传入库。", icon="⚠️")
    if not ark_ready:
        st.info("Ark 资产 OpenAPI 未配置时，会保留原本的本地 pending 审核流程。", icon="ℹ️")
    if ark_configured and ark_capability_unavailable:
        st.warning("当前账号未开通 Ark AIGC 资产能力，本会话已切换成本地 TOS 入库模式。", icon="⚠️")
        if st.button("重新尝试 Ark 资产同步", width="stretch"):
            st.session_state.pop("ark_asset_capability_unavailable", None)
            st.rerun()

# ---- 第一步：选择文件 ----
st.subheader("第一步：选择文件")
files = st.file_uploader(
    f"选择人脸素材（图片或视频，可多选，最多 {MAX_FILES} 个）",
    accept_multiple_files=True,
    type=sorted(validation.IMAGE_EXTS | validation.VIDEO_EXTS),
)
if files and len(files) > MAX_FILES:
    st.error(f"一次最多提交 {MAX_FILES} 个文件，当前选择了 {len(files)} 个。")

if files:
    st.caption(f"已选择 {len(files)} 个文件")
    preview_cols = st.columns(5)
    for idx, uploaded in enumerate(files[:10]):
        with preview_cols[idx % 5]:
            if validation.classify(uploaded.name) == "image":
                st.image(uploaded.getvalue(), width="stretch")
            else:
                st.caption(uploaded.name)
            st.caption(uploaded.name)
    if len(files) > 10:
        st.caption(f"还有 {len(files) - 10} 个文件将在提交时处理。")

# ---- 第二步：资产组配置 ----
st.subheader("第二步：资产组配置")
groups = _refresh_groups()
if ark_ready:
    if st.button("同步火山资产组列表", type="secondary"):
        try:
            remote_groups = ark_assets.list_asset_groups()
            count = asset_store.sync_remote_groups(remote_groups)
            st.cache_data.clear()
            st.session_state["face_notice"] = (
                "success",
                f"已同步 {count} 个火山 Ark 资产组。",
            )
            st.rerun()
        except ark_assets.ArkAssetError as exc:
            if ark_assets.is_subscription_required(exc):
                _switch_to_local_asset_mode(ark_assets.capability_hint(exc))
            st.error(f"同步资产组失败：{exc}")
            hint = ark_assets.capability_hint(exc)
            if hint:
                st.info(hint)

group_name_to_id = {g["name"]: g["id"] for g in groups}
group_labels = {_group_label(g): g["id"] for g in groups}
mode_options = ["从现有列表中选择", "创建新组"]
if ark_ready:
    mode_options.insert(1, "手动输入 Ark GroupId")
mode = st.radio("组操作", mode_options, horizontal=True)

target_group_id = None
manual_ark_group_id = ""
manual_group_name = ""
new_group_name = ""
new_group_desc = ""
if mode == "从现有列表中选择":
    if groups:
        sel = st.selectbox("选择资产组", list(group_labels.keys()))
        target_group_id = group_labels.get(sel)
    else:
        st.caption("还没有资产组，请切换到「创建新组」。")
elif mode == "手动输入 Ark GroupId":
    manual_ark_group_id = st.text_input("Ark GroupId")
    manual_group_name = st.text_input("本地显示名称（可选）")
    if not ark_ready:
        st.warning("手动输入 Ark GroupId 需要先配置 Ark 资产 OpenAPI。")
else:
    new_group_name = st.text_input("新资产组名称")
    new_group_desc = st.text_input("描述（可选）")
    clean_group_name = new_group_name.strip()
    group_already_exists = bool(clean_group_name and clean_group_name in group_name_to_id)
    create_group_clicked = st.button(
        "创建资产组",
        type="secondary",
        disabled=not clean_group_name or group_already_exists,
    )
    if group_already_exists:
        st.info(f"资产组「{clean_group_name}」已存在，可切换到「从现有列表中选择」使用。")
    if create_group_clicked:
        group_id = None
        try:
            group_id = asset_store.create_group(clean_group_name, new_group_desc)
        except asset_store.AssetError as e:
            if ark_assets.is_subscription_required(e):
                try:
                    group_id = asset_store.create_group(
                        clean_group_name,
                        new_group_desc,
                        sync_remote=False,
                    )
                except asset_store.AssetError as local_error:
                    st.error(str(local_error))
                    group_id = None
                if group_id:
                    _switch_to_local_asset_mode(ark_assets.capability_hint(e))
            else:
                st.error(str(e))
        if group_id:
            st.session_state["face_notice"] = (
                "success",
                f"已创建资产组 #{group_id}：{clean_group_name}。",
            )
            st.cache_data.clear()
            st.rerun()
    st.caption("也可以选择文件后直接点击下方批量提交，系统会自动创建该组并入库。")

# ---- 提交 ----
submit_label = "上传并注册 Ark 资产" if ark_ready else "批量提交并开始本地审核"
submit = st.button(f"🚀 {submit_label}", type="primary", disabled=not files)

if submit:
    if files and len(files) > MAX_FILES:
        st.error(f"一次最多提交 {MAX_FILES} 个文件，请减少后重试。")
        st.stop()

    # 解析目标资产组
    try:
        if mode == "创建新组":
            clean_group_name = new_group_name.strip()
            if not clean_group_name:
                st.error("请填写新资产组名称")
                st.stop()
            target_group_id = group_name_to_id.get(clean_group_name)
            if target_group_id is None:
                try:
                    target_group_id = asset_store.create_group(clean_group_name, new_group_desc)
                except asset_store.AssetError as e:
                    if not ark_assets.is_subscription_required(e):
                        raise
                    target_group_id = asset_store.create_group(
                        clean_group_name,
                        new_group_desc,
                        sync_remote=False,
                    )
                    st.session_state["ark_asset_capability_unavailable"] = True
                    ark_ready = False
        elif mode == "手动输入 Ark GroupId":
            clean_ark_group_id = manual_ark_group_id.strip()
            if not clean_ark_group_id:
                st.error("请填写 Ark GroupId")
                st.stop()
            if not ark_ready:
                st.error("Ark 资产 OpenAPI 未配置，无法使用远端 GroupId。")
                st.stop()
            remote_group = asset_store.get_group_by_ark_id(clean_ark_group_id)
            if remote_group:
                target_group_id = remote_group["id"]
            else:
                local_name = manual_group_name.strip() or f"ark_{clean_ark_group_id[-8:]}"
                target_group_id = asset_store.upsert_remote_group(
                    local_name,
                    clean_ark_group_id,
                    "手动输入的火山 Ark 资产组",
                )
        elif target_group_id is None:
            st.error("请先选择或创建资产组")
            st.stop()
    except asset_store.AssetError as e:
        st.error(str(e))
        st.stop()

    try:
        ark_group_id = asset_store.ensure_remote_group(target_group_id) if ark_ready else ""
    except asset_store.AssetError as e:
        if ark_assets.is_subscription_required(e):
            st.session_state["ark_asset_capability_unavailable"] = True
            ark_group_id = ""
            st.warning(
                "当前账号未开通 Ark AIGC 资产能力，本次提交将按本地 TOS 审核模式继续。",
                icon="⚠️",
            )
            st.info(ark_assets.capability_hint(e))
        else:
            st.error(str(e))
            st.stop()

    results = []
    bar = st.progress(0.0, text="开始处理…")
    for i, f in enumerate(files):
        data = f.getvalue()
        kind, ok, msg = validation.validate(data, f.name)
        if not ok:
            results.append((f.name, "❌ 校验失败", msg))
        elif not tos_client.is_configured():
            results.append((f.name, "⚠️ 已校验·未上传", f"{msg}；TOS 未配置"))
        else:
            key = f"face/{target_group_id}/{uuid.uuid4().hex}_{f.name}"
            try:
                url = tos_client.upload_bytes(key, data, content_type=getattr(f, "type", None))
                source_url = tos_client.access_url(key)
                if ark_group_id:
                    active = ark_assets.submit_and_wait(
                        ark_group_id,
                        source_url,
                        asset_name=f.name,
                        asset_type="Image" if kind == "image" else "Video",
                    )
                    asset_id = asset_store.add_asset(
                        target_group_id,
                        asset_type=kind,
                        tos_url=url,
                        tos_key=key,
                        filename=f.name,
                        size_bytes=len(data),
                        review_status="approved",
                        ark_asset_id=str(active.get("id") or ""),
                        ark_status=str(active.get("status") or "Active"),
                        ark_url=str(active.get("url") or source_url),
                        uploaded_by=(user.id if user else None),
                    )
                    results.append(
                        (
                            f.name,
                            "✅ Ark 已激活",
                            f"{msg}；本地素材 #{asset_id}；Ark AssetId：{active.get('id')}",
                        )
                    )
                else:
                    status = review.submit_for_review(
                        {"filename": f.name, "type": kind, "tos_url": url}
                    )
                    asset_store.add_asset(
                        target_group_id,
                        asset_type=kind,
                        tos_url=url,
                        tos_key=key,
                        filename=f.name,
                        size_bytes=len(data),
                        review_status=status,
                        uploaded_by=(user.id if user else None),
                    )
                    results.append((f.name, "✅ 已入库", f"{msg}；审核状态：{status}"))
            except ark_assets.ArkAssetError as e:
                if ark_assets.is_subscription_required(e):
                    st.session_state["ark_asset_capability_unavailable"] = True
                    ark_group_id = ""
                    status = review.submit_for_review(
                        {"filename": f.name, "type": kind, "tos_url": url}
                    )
                    asset_id = asset_store.add_asset(
                        target_group_id,
                        asset_type=kind or "image",
                        tos_url=url,
                        tos_key=key,
                        filename=f.name,
                        size_bytes=len(data),
                        review_status=status,
                        ark_status="SubscriptionRequired",
                        ark_error=str(e),
                        uploaded_by=(user.id if user else None),
                    )
                    results.append(
                        (
                            f.name,
                            "⚠️ 已转本地入库",
                            f"本地素材 #{asset_id}；审核状态：{status}；Ark 资产能力未开通",
                        )
                    )
                else:
                    asset_id = asset_store.add_asset(
                        target_group_id,
                        asset_type=kind or "image",
                        tos_url=tos_client.access_url(key),
                        tos_key=key,
                        filename=f.name,
                        size_bytes=len(data),
                        review_status="rejected",
                        ark_status="Failed",
                        ark_error=str(e),
                        uploaded_by=(user.id if user else None),
                    )
                    results.append((f.name, "❌ Ark 注册失败", f"本地记录 #{asset_id}；{e}"))
            except Exception as e:
                results.append((f.name, "❌ 上传失败", str(e)))
        bar.progress((i + 1) / len(files), text=f"已处理 {i + 1}/{len(files)}")

    st.subheader("提交结果")
    ok_n = sum(1 for _, s, _ in results if s.startswith("✅") or s.startswith("⚠️ 已转本地入库"))
    pending_n = sum(1 for _, _, detail in results if "审核状态：pending" in detail)
    if ok_n:
        st.cache_data.clear()
    st.write(f"成功入库 **{ok_n}** / {len(results)} 个文件")
    for name, label, detail in results:
        st.write(f"{label} **{name}** — {detail}")
    if pending_n:
        st.warning(
            f"{pending_n} 个素材当前为待审核状态。管理员在「管理后台 → 素材审核」批准后，"
            "它们才会进入创作场快速资产库和 Asset ID 引用。",
            icon="⏳",
        )
    if ark_ready:
        st.caption("Ark 返回 Active 后，素材会直接进入已批准状态，可在创作场作为资产引用。")
    else:
        st.caption(
            "入库素材默认为待审核（pending）。管理员在「管理后台 → 素材审核」批准后，"
            "图片即可在创作场「选用首帧」中引用。"
        )
