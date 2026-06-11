"""资产库数据访问层（纯 DB 函数，不依赖 Streamlit）。

页面层会用 @st.cache_data 包一层做「多用户共享缓存」。
"""
from sqlalchemy import func, select

from database import get_session
from models import Asset, AssetGroup


class AssetError(Exception):
    pass


# ---------- 资产组 ----------

def list_groups_with_counts() -> list[dict]:
    """所有资产组 + 各自素材数量。"""
    with get_session() as session:
        rows = session.execute(
            select(
                AssetGroup.id,
                AssetGroup.name,
                AssetGroup.description,
                func.count(Asset.id).label("asset_count"),
            )
            .outerjoin(Asset, Asset.group_id == AssetGroup.id)
            .group_by(AssetGroup.id)
            .order_by(AssetGroup.created_at.desc())
        ).all()
        return [dict(r._mapping) for r in rows]


def create_group(name: str, description: str = "") -> int:
    name = (name or "").strip()
    if not name:
        raise AssetError("资产组名称不能为空")
    with get_session() as session:
        if session.scalar(select(AssetGroup).where(AssetGroup.name == name)):
            raise AssetError("同名资产组已存在")
        group = AssetGroup(name=name, description=description.strip())
        session.add(group)
        session.flush()
        return group.id


def rename_group(group_id: int, new_name: str) -> None:
    new_name = (new_name or "").strip()
    if not new_name:
        raise AssetError("名称不能为空")
    with get_session() as session:
        clash = session.scalar(
            select(AssetGroup).where(AssetGroup.name == new_name, AssetGroup.id != group_id)
        )
        if clash:
            raise AssetError("同名资产组已存在")
        group = session.get(AssetGroup, group_id)
        if not group:
            raise AssetError("资产组不存在")
        group.name = new_name


def delete_group(group_id: int) -> list[str]:
    """删除资产组（连同组内素材记录）。返回被删素材的 tos_key 列表，供调用方清理 TOS。"""
    with get_session() as session:
        group = session.get(AssetGroup, group_id)
        if not group:
            raise AssetError("资产组不存在")
        keys = [a.tos_key for a in group.assets if a.tos_key]
        session.delete(group)
        return keys


# ---------- 素材 ----------

def list_assets(group_id: int) -> list[dict]:
    with get_session() as session:
        rows = session.scalars(
            select(Asset).where(Asset.group_id == group_id).order_by(Asset.created_at.desc())
        ).all()
        return [
            {
                "id": a.id,
                "type": a.type,
                "tos_url": a.tos_url,
                "tos_key": a.tos_key,
                "filename": a.filename,
                "size_bytes": a.size_bytes,
                "review_status": a.review_status,
                "created_at": a.created_at.strftime("%Y-%m-%d %H:%M"),
            }
            for a in rows
        ]


def add_asset(
    group_id: int,
    *,
    asset_type: str,
    tos_url: str,
    tos_key: str = "",
    filename: str = "",
    size_bytes: int = 0,
    review_status: str = "approved",
    uploaded_by: int | None = None,
) -> int:
    """新增一条素材记录（模块 3 上传成功后调用；本步骤也用于手动登记）。"""
    if asset_type not in ("image", "video"):
        raise AssetError("素材类型必须是 image 或 video")
    if not tos_url.strip():
        raise AssetError("素材地址不能为空")
    with get_session() as session:
        if not session.get(AssetGroup, group_id):
            raise AssetError("资产组不存在")
        asset = Asset(
            group_id=group_id, type=asset_type, tos_url=tos_url.strip(),
            tos_key=tos_key.strip(), filename=filename.strip(),
            size_bytes=int(size_bytes), review_status=review_status,
            uploaded_by=uploaded_by,
        )
        session.add(asset)
        session.flush()
        return asset.id


def delete_asset(asset_id: int) -> str:
    """删除一条素材记录，返回其 tos_key（供调用方清理 TOS）。"""
    with get_session() as session:
        asset = session.get(Asset, asset_id)
        if not asset:
            raise AssetError("素材不存在")
        key = asset.tos_key
        session.delete(asset)
        return key


def list_image_assets_for_reference(limit: int = 200) -> list[dict]:
    """供创作场「选用首帧」：已审核通过的图片素材。"""
    with get_session() as session:
        rows = session.scalars(
            select(Asset)
            .where(Asset.type == "image", Asset.review_status == "approved")
            .order_by(Asset.created_at.desc())
            .limit(limit)
        ).all()
        return [{"id": a.id, "tos_url": a.tos_url, "filename": a.filename, "group_id": a.group_id} for a in rows]


def list_assets_by_status(status: str, limit: int = 200) -> list[dict]:
    """按审核状态列出素材（供管理员审核队列）。"""
    with get_session() as session:
        rows = session.scalars(
            select(Asset)
            .where(Asset.review_status == status)
            .order_by(Asset.created_at.desc())
            .limit(limit)
        ).all()
        return [
            {
                "id": a.id, "group_id": a.group_id, "type": a.type,
                "tos_url": a.tos_url, "filename": a.filename,
                "review_status": a.review_status,
                "created_at": a.created_at.strftime("%Y-%m-%d %H:%M"),
            }
            for a in rows
        ]


def set_review_status(asset_id: int, status: str) -> None:
    if status not in ("pending", "approved", "rejected"):
        raise AssetError("非法审核状态")
    with get_session() as session:
        asset = session.get(Asset, asset_id)
        if not asset:
            raise AssetError("素材不存在")
        asset.review_status = status
