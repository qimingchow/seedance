"""创建首个管理员（运行一次）：

    python seed_admin.py

管理员账号不参与成员额度池；资源包总额请在管理后台设置。
"""
from getpass import getpass
import os

from sqlalchemy import select

from auth import hash_password
from database import get_session, init_db
from models import User


def main() -> None:
    init_db()
    username = os.getenv("ADMIN_USERNAME", "").strip() or input("管理员用户名: ").strip()
    if not username:
        print("用户名不能为空")
        return
    password = os.getenv("ADMIN_PASSWORD", "").strip() or getpass("管理员密码: ").strip()
    if not password:
        print("密码不能为空")
        return
    quota = int(os.getenv("ADMIN_TOKEN_QUOTA", "0") or 0)

    with get_session() as session:
        if session.scalar(select(User).where(User.username == username)):
            print("该用户名已存在")
            return
        session.add(
            User(
                username=username,
                password_hash=hash_password(password),
                role="admin",
                token_quota=quota,
            )
        )
    print(f"✅ 管理员 {username} 已创建（管理员个人额度 {quota:,} tokens）")
    print("现在运行：streamlit run app.py")


if __name__ == "__main__":
    main()
