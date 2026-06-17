"""认证与权限。

- 密码用 bcrypt 加盐哈希
- 登录态存在 st.session_state
- require_login() / require_admin() 做页面级访问控制
"""
import bcrypt
import streamlit as st
from sqlalchemy import select

from database import get_session
from models import User


def hash_password(raw: str) -> str:
    return bcrypt.hashpw(raw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(raw.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def _authenticate(username: str, password: str) -> User | None:
    with get_session() as session:
        user = session.scalar(select(User).where(User.username == username))
        if user and user.status == "active" and verify_password(password, user.password_hash):
            session.expunge(user)
            return user
    return None


def _login_form() -> None:
    st.markdown("### 🔐 登录 Seedance Studio")
    with st.form("login_form"):
        username = st.text_input("用户名")
        password = st.text_input("密码", type="password")
        submitted = st.form_submit_button("登录", type="primary", width="stretch")
    if submitted:
        user = _authenticate(username.strip(), password)
        if user:
            st.session_state["user_id"] = user.id
            st.session_state["username"] = user.username
            st.session_state["role"] = user.role
            st.rerun()
        else:
            st.error("用户名或密码错误，或账号已被禁用。")


def require_login() -> None:
    """未登录则显示登录表单并中断后续渲染。"""
    if "user_id" not in st.session_state:
        _login_form()
        st.stop()
    user = current_user()
    if not user or user.status != "active":
        for key in ("user_id", "username", "role"):
            st.session_state.pop(key, None)
        st.warning("账号不存在或已被停用，请重新登录。")
        _login_form()
        st.stop()


def current_user() -> User | None:
    """返回当前登录用户（已与会话分离的对象）。"""
    if "user_id" not in st.session_state:
        return None
    with get_session() as session:
        user = session.get(User, st.session_state["user_id"])
        if user:
            session.expunge(user)
        return user


def require_admin() -> None:
    """仅管理员可继续，否则中断。"""
    if st.session_state.get("role") != "admin":
        st.error("⛔ 仅管理员可访问此页面。")
        st.stop()


def logout() -> None:
    for key in ("user_id", "username", "role"):
        st.session_state.pop(key, None)
    st.rerun()
