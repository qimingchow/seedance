"""模块 3 · 人脸素材入库系统。

流程：选择文件 → 校验格式 → 上传到 TOS → 提交审核 → 写入资产组（入库为 pending）。
资产组配置支持：从现有列表选择 / 手动输入 ID / 创建新组。
"""
import uuid

import streamlit as st

import asset_store
import review
import tos_client
import validation
from auth import current_user

user = current_user()

st.title("👤 人脸素材入库系统")
st.info("流程：选择文件 → 校验格式 → 上传到 TOS → 提交火山引擎审核 → 入库资产组", icon="🧭")
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
    if not tos_client.is_configured():
        st.warning("TOS 未配置：可校验文件，但无法上传入库。", icon="⚠️")

# ---- 第一步：选择文件 ----
st.subheader("第一步：选择文件")
files = st.file_uploader(
    "选择人脸素材（图片或视频，可多选）",
    accept_multiple_files=True,
    type=sorted(validation.IMAGE_EXTS | validation.VIDEO_EXTS),
)

# ---- 第二步：资产组配置 ----
st.subheader("第二步：资产组配置")
groups = asset_store.list_groups_with_counts()
group_name_to_id = {g["name"]: g["id"] for g in groups}
mode = st.radio("组操作", ["从现有列表中选择", "手动输入 ID", "创建新组"], horizontal=True)

target_group_id = None
new_group_name = ""
new_group_desc = ""
if mode == "从现有列表中选择":
    if groups:
        sel = st.selectbox("选择资产组", list(group_name_to_id.keys()))
        target_group_id = group_name_to_id.get(sel)
    else:
        st.caption("还没有资产组，请切换到「创建新组」。")
elif mode == "手动输入 ID":
    target_group_id = int(st.number_input("资产组 ID", min_value=1, step=1, value=1))
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
        try:
            group_id = asset_store.create_group(clean_group_name, new_group_desc)
            st.session_state["face_notice"] = (
                "success",
                f"已创建资产组 #{group_id}：{clean_group_name}。现在可切换到「从现有列表中选择」使用。",
            )
            st.rerun()
        except asset_store.AssetError as e:
            st.error(str(e))
    st.caption("也可以选择文件后直接点击下方批量提交，系统会自动创建该组并入库。")

# ---- 提交 ----
submit = st.button("🚀 批量提交并开始审核", type="primary", disabled=not files)

if submit:
    # 解析目标资产组
    try:
        if mode == "创建新组":
            clean_group_name = new_group_name.strip()
            if not clean_group_name:
                st.error("请填写新资产组名称")
                st.stop()
            target_group_id = group_name_to_id.get(clean_group_name)
            if target_group_id is None:
                target_group_id = asset_store.create_group(clean_group_name, new_group_desc)
        elif mode == "手动输入 ID":
            if not any(g["id"] == target_group_id for g in groups):
                st.error(f"资产组 ID {target_group_id} 不存在")
                st.stop()
        elif target_group_id is None:
            st.error("请先选择或创建资产组")
            st.stop()
    except asset_store.AssetError as e:
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
            try:
                key = f"face/{target_group_id}/{uuid.uuid4().hex}_{f.name}"
                url = tos_client.upload_bytes(key, data, content_type=getattr(f, "type", None))
                status = review.submit_for_review(
                    {"filename": f.name, "type": kind, "tos_url": url}
                )
                asset_store.add_asset(
                    target_group_id, asset_type=kind, tos_url=url, tos_key=key,
                    filename=f.name, size_bytes=len(data), review_status=status,
                    uploaded_by=(user.id if user else None),
                )
                results.append((f.name, "✅ 已入库", f"{msg}；审核状态：{status}"))
            except Exception as e:
                results.append((f.name, "❌ 上传失败", str(e)))
        bar.progress((i + 1) / len(files), text=f"已处理 {i + 1}/{len(files)}")

    st.subheader("提交结果")
    ok_n = sum(1 for _, s, _ in results if s.startswith("✅"))
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
    st.caption(
        "入库素材默认为待审核（pending）。管理员在「管理后台 → 素材审核」批准后，"
        "图片即可在创作场「选用首帧」中引用。"
    )
