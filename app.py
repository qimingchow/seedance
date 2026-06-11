"""Seedance Studio 入口。

负责：建表 → 登录校验 → 侧边栏（身份/额度/登出）→ 按角色生成导航。
普通成员看到「创作场 / 资产库 / 人脸素材提交」；管理员额外看到「管理后台」。
"""
import streamlit as st

from auth import current_user, logout, require_login
from database import init_db

st.set_page_config(page_title="Seedance Studio", page_icon="🎬", layout="wide")

init_db()
require_login()

user = current_user()
if user is None:  # 会话异常兜底
    logout()

with st.sidebar:
    role_label = "管理员" if user.role == "admin" else "成员"
    st.caption(f"👤 {user.username} · {role_label}")
    if user.role == "member":
        pct = (user.token_used / user.token_quota) if user.token_quota else 0.0
        st.caption(f"🎟️ 剩余 {user.token_remaining:,} / {user.token_quota:,} tokens")
        st.progress(min(pct, 1.0))
    if st.button("退出登录", use_container_width=True):
        logout()

creation_pages = [
    st.Page("views/studio.py", title="创作场", icon=":material/movie:", default=True),
    st.Page("views/assets.py", title="资产库全览", icon=":material/photo_library:"),
    st.Page("views/face.py", title="人脸素材提交", icon=":material/face:"),
]

nav: dict[str, list] = {"创作": creation_pages}
if user.role == "admin":
    nav["管理"] = [st.Page("views/admin.py", title="管理后台", icon=":material/shield:")]

st.navigation(nav).run()
