"""创建首个管理员（运行一次）：

    python seed_admin.py

管理员初始额度设为 350,000,000 tokens（对应 Seedance 账号总池，可在后台调整）。
"""
from getpass import getpass

from sqlalchemy import select

from auth import hash_password
from database import get_session, init_db
from models import User

ACCOUNT_TOTAL_TOKENS = 350_000_000


def main() -> None:
    init_db()
    username = input("管理员用户名: ").strip()
    if not username:
        print("用户名不能为空")
        return
    password = getpass("管理员密码: ").strip()
    if not password:
        print("密码不能为空")
        return

    with get_session() as session:
        if session.scalar(select(User).where(User.username == username)):
            print("该用户名已存在")
            return
        session.add(
            User(
                username=username,
                password_hash=hash_password(password),
                role="admin",
                token_quota=ACCOUNT_TOTAL_TOKENS,
            )
        )
    print(f"✅ 管理员 {username} 已创建（初始额度 {ACCOUNT_TOTAL_TOKENS:,} tokens）")
    print("现在运行：streamlit run app.py")


if __name__ == "__main__":
    main()
