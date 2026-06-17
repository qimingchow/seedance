"""数据库连接与会话管理。"""
from contextlib import contextmanager

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from config import settings
from models import Base

_connect_args = (
    {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}
)

engine = create_engine(
    settings.DATABASE_URL,
    echo=False,
    future=True,
    pool_pre_ping=True,
    connect_args=_connect_args,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    """建表并补齐新增列（轻量幂等迁移）。"""
    Base.metadata.create_all(engine)
    _migrate_missing_columns()


def _migrate_missing_columns() -> None:
    """为既有 SQLite/PostgreSQL 表补齐本项目新增的简单列。

    这是内部工具的轻量迁移方案；未来多环境部署建议改用 Alembic。
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    wanted = {
        "users": {
            "token_reserved": "BIGINT DEFAULT 0 NOT NULL",
        },
        "generations": {
            "audio_url": "TEXT DEFAULT '' NOT NULL",
            "last_frame_url": "TEXT DEFAULT '' NOT NULL",
            "video_tos_key": "VARCHAR(512) DEFAULT '' NOT NULL",
            "audio_tos_key": "VARCHAR(512) DEFAULT '' NOT NULL",
            "last_frame_tos_key": "VARCHAR(512) DEFAULT '' NOT NULL",
            "request_json": "TEXT DEFAULT '{}' NOT NULL",
            "response_json": "TEXT DEFAULT '{}' NOT NULL",
            "error_message": "TEXT DEFAULT '' NOT NULL",
            "estimated_tokens": "BIGINT DEFAULT 0 NOT NULL",
            "reserved_tokens": "BIGINT DEFAULT 0 NOT NULL",
            "started_at": "DATETIME",
            "finished_at": "DATETIME",
        },
    }
    with engine.begin() as conn:
        for table, columns in wanted.items():
            if table not in existing_tables:
                continue
            existing_columns = {c["name"] for c in inspector.get_columns(table)}
            for name, ddl in columns.items():
                if name not in existing_columns:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


@contextmanager
def get_session():
    """事务性会话：正常提交，异常回滚。"""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
