"""数据库模型。

三张核心表：
- User                用户（含角色 role 与 token 子额度）
- Generation          每一次视频生成记录（属于某个 user）
- QuotaTransaction    额度变动流水（分配 / 消耗 / 调整），用于审计
"""
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="member")   # 'admin' | 'member'
    token_quota: Mapped[int] = mapped_column(BigInteger, default=0)    # 已分配的总额度
    token_used: Mapped[int] = mapped_column(BigInteger, default=0)     # 已消耗
    token_reserved: Mapped[int] = mapped_column(BigInteger, default=0) # 运行中任务预占额度
    status: Mapped[str] = mapped_column(String(16), default="active")  # 'active' | 'disabled'
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    generations: Mapped[list["Generation"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def token_remaining(self) -> int:
        return max(self.token_quota - self.token_used - self.token_reserved, 0)


class Generation(Base):
    __tablename__ = "generations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    prompt: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    params_json: Mapped[str] = mapped_column(Text, default="{}")   # 宽高比/分辨率/时长/seed 等
    task_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    video_url: Mapped[str] = mapped_column(Text, default="")
    audio_url: Mapped[str] = mapped_column(Text, default="")
    last_frame_url: Mapped[str] = mapped_column(Text, default="")
    cover_url: Mapped[str] = mapped_column(Text, default="")
    request_json: Mapped[str] = mapped_column(Text, default="{}")
    response_json: Mapped[str] = mapped_column(Text, default="{}")
    error_message: Mapped[str] = mapped_column(Text, default="")
    estimated_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    reserved_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    tokens_used: Mapped[int] = mapped_column(BigInteger, default=0)
    cost_yuan: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|running|succeeded|failed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship(back_populates="generations")


class QuotaTransaction(Base):
    __tablename__ = "quota_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)       # 受影响的成员
    operator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)  # 操作者（消耗时为空）
    type: Mapped[str] = mapped_column(String(16))   # 'allocate' | 'consume' | 'adjust'
    tokens: Mapped[int] = mapped_column(BigInteger)  # 正=增加额度，负=消耗
    note: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class AppSetting(Base):
    """全局键值配置，例如账号 token 总额上限。"""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255), default="")


class AssetGroup(Base):
    """资产组（素材分组）。素材主要由「人脸素材提交」上传后归入。"""

    __tablename__ = "asset_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    description: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    assets: Mapped[list["Asset"]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )


class Asset(Base):
    """组内素材（图片或视频），存放于火山 TOS。"""

    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("asset_groups.id"), index=True)
    type: Mapped[str] = mapped_column(String(8), default="image")   # 'image' | 'video'
    tos_url: Mapped[str] = mapped_column(Text, default="")          # 可访问地址（用于预览/引用）
    tos_key: Mapped[str] = mapped_column(String(512), default="")   # 桶内对象 key（用于删除）
    filename: Mapped[str] = mapped_column(String(255), default="")
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    review_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|approved|rejected
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    group: Mapped["AssetGroup"] = relationship(back_populates="assets")
