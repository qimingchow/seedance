"""资产库数据访问层（纯 DB 函数，不依赖 Streamlit）。

页面层会用 @st.cache_data 包一层做「多用户共享缓存」。
"""
from typing import Any

from sqlalchemy import func, select

import ark_assets
import tos_client
from database import get_session
from models import Asset, AssetGroup


class AssetError(Exception):
    pass


def _asset_url(asset: Asset) -> str:
    """优先用 tos_key 生成当前配置下的编码 URL，兼容旧数据中的原始 URL。"""
    return tos_client.access_url(asset.tos_key) if asset.tos_key else asset.tos_url


def _remote_value(item: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if value:
            return str(value)
    return ""


# ---------- 资产组 ----------

def list_groups_with_counts() -> list[dict]:
    """所有资产组 + 各自素材数量。"""
    with get_session() as session:
        rows = session.execute(
            select(
                AssetGroup.id,
                AssetGroup.name,
                AssetGroup.ark_group_id,
                AssetGroup.description,
                func.count(Asset.id).label("asset_count"),
            )
            .outerjoin(Asset, Asset.group_id == AssetGroup.id)
            .group_by(
                AssetGroup.id,
                AssetGroup.name,
                AssetGroup.ark_group_id,
                AssetGroup.description,
                AssetGroup.created_at,
            )
            .order_by(AssetGroup.created_at.desc())
        ).all()
        return [dict(r._mapping) for r in rows]


def create_group(
    name: str,
    description: str = "",
    *,
    ark_group_id: str = "",
    sync_remote: bool = True,
) -> int:
    name = (name or "").strip()
    description = (description or "").strip()
    ark_group_id = (ark_group_id or "").strip()
    if not name:
        raise AssetError("资产组名称不能为空")
    with get_session() as session:
        if session.scalar(select(AssetGroup).where(AssetGroup.name == name)):
            raise AssetError("同名资产组已存在")

    if not ark_group_id and sync_remote and ark_assets.is_configured():
        try:
            ark_group_id = ark_assets.create_asset_group(name, description)
        except ark_assets.ArkAssetError as exc:
            raise AssetError(f"创建火山资产组失败：{exc}") from exc

    with get_session() as session:
        if session.scalar(select(AssetGroup).where(AssetGroup.name == name)):
            raise AssetError("同名资产组已存在")
        group = AssetGroup(
            name=name,
            description=description,
            ark_group_id=ark_group_id,
        )
        session.add(group)
        session.flush()
        return group.id


def get_group(group_id: int) -> dict | None:
    with get_session() as session:
        group = session.get(AssetGroup, group_id)
        if not group:
            return None
        return {
            "id": group.id,
            "name": group.name,
            "description": group.description,
            "ark_group_id": group.ark_group_id,
        }


def get_group_by_ark_id(ark_group_id: str) -> dict | None:
    ark_group_id = (ark_group_id or "").strip()
    if not ark_group_id:
        return None
    with get_session() as session:
        group = session.scalar(
            select(AssetGroup).where(AssetGroup.ark_group_id == ark_group_id)
        )
        if not group:
            return None
        return {
            "id": group.id,
            "name": group.name,
            "description": group.description,
            "ark_group_id": group.ark_group_id,
        }


def ensure_remote_group(group_id: int) -> str:
    """确保本地资产组存在 Ark GroupId；未配置 Ark 时返回空字符串。"""
    with get_session() as session:
        group = session.get(AssetGroup, group_id)
        if not group:
            raise AssetError("资产组不存在")
        if group.ark_group_id:
            return group.ark_group_id
        name = group.name
        description = group.description

    if not ark_assets.is_configured():
        return ""

    try:
        ark_group_id = ark_assets.create_asset_group(name, description)
    except ark_assets.ArkAssetError as exc:
        raise AssetError(f"同步火山资产组失败：{exc}") from exc

    with get_session() as session:
        group = session.get(AssetGroup, group_id)
        if not group:
            raise AssetError("资产组不存在")
        group.ark_group_id = ark_group_id
    return ark_group_id


def upsert_remote_group(name: str, ark_group_id: str, description: str = "") -> int:
    """把火山 Ark 资产组映射到本地分组，返回本地 group_id。"""
    ark_group_id = (ark_group_id or "").strip()
    if not ark_group_id:
        raise AssetError("Ark GroupId 不能为空")
    name = (name or "").strip() or f"ark_{ark_group_id[-8:]}"
    description = (description or "").strip()
    with get_session() as session:
        group = session.scalar(
            select(AssetGroup).where(AssetGroup.ark_group_id == ark_group_id)
        )
        if group:
            if not group.name:
                group.name = name
            if description and not group.description:
                group.description = description
            session.flush()
            return group.id

        group = session.scalar(select(AssetGroup).where(AssetGroup.name == name))
        if group:
            group.ark_group_id = ark_group_id
            if description and not group.description:
                group.description = description
            session.flush()
            return group.id

        group = AssetGroup(name=name, description=description, ark_group_id=ark_group_id)
        session.add(group)
        session.flush()
        return group.id


def sync_remote_groups(remote_groups: list[dict[str, Any]]) -> int:
    count = 0
    for item in remote_groups:
        if not isinstance(item, dict):
            continue
        ark_group_id = _remote_value(item, "Id", "GroupId", "id", "group_id")
        if not ark_group_id:
            continue
        name = _remote_value(item, "Name", "name") or f"ark_{ark_group_id[-8:]}"
        description = _remote_value(item, "Description", "description")
        upsert_remote_group(name, ark_group_id, description)
        count += 1
    return count


def upsert_remote_asset(group_id: int, item: dict[str, Any]) -> int:
    """把火山 Ark 资产映射到本地素材，返回本地 asset_id。"""
    ark_asset_id = _remote_value(item, "Id", "AssetId", "id", "asset_id")
    if not ark_asset_id:
        raise AssetError("Ark AssetId 不能为空")
    asset_type_raw = _remote_value(item, "AssetType", "asset_type").lower()
    asset_type = "video" if "video" in asset_type_raw else "image"
    status = _remote_value(item, "Status", "status")
    status_lower = status.lower()
    review_status = "approved" if status_lower == "active" else "pending"
    if status_lower == "failed":
        review_status = "rejected"
    url = _remote_value(item, "URL", "Url", "url")
    name = _remote_value(item, "Name", "name") or ark_asset_id
    error = _remote_value(item, "Error", "ErrorMessage", "error", "error_message")

    with get_session() as session:
        if not session.get(AssetGroup, group_id):
            raise AssetError("资产组不存在")
        asset = session.scalar(select(Asset).where(Asset.ark_asset_id == ark_asset_id))
        if asset:
            asset.group_id = group_id
            asset.type = asset_type
            if url:
                asset.tos_url = url
                asset.ark_url = url
            asset.filename = name
            asset.review_status = review_status
            asset.ark_status = status
            asset.ark_error = error
            session.flush()
            return asset.id
        asset = Asset(
            group_id=group_id,
            type=asset_type,
            tos_url=url,
            tos_key="",
            filename=name,
            review_status=review_status,
            ark_asset_id=ark_asset_id,
            ark_status=status,
            ark_url=url,
            ark_error=error,
        )
        session.add(asset)
        session.flush()
        return asset.id


def sync_remote_assets_for_group(group_id: int, remote_assets: list[dict[str, Any]]) -> int:
    count = 0
    for item in remote_assets:
        if not isinstance(item, dict):
            continue
        upsert_remote_asset(group_id, item)
        count += 1
    return count


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
                "tos_url": _asset_url(a),
                "tos_key": a.tos_key,
                "filename": a.filename,
                "size_bytes": a.size_bytes,
                "review_status": a.review_status,
                "ark_asset_id": a.ark_asset_id,
                "ark_status": a.ark_status,
                "ark_url": a.ark_url,
                "ark_error": a.ark_error,
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
    ark_asset_id: str = "",
    ark_status: str = "",
    ark_url: str = "",
    ark_error: str = "",
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
            group_id=group_id,
            type=asset_type,
            tos_url=tos_url.strip(),
            tos_key=tos_key.strip(),
            filename=filename.strip(),
            size_bytes=int(size_bytes),
            review_status=review_status,
            ark_asset_id=ark_asset_id.strip(),
            ark_status=ark_status.strip(),
            ark_url=ark_url.strip(),
            ark_error=ark_error.strip(),
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
        return [
            {
                "id": a.id,
                "tos_url": _asset_url(a),
                "filename": a.filename,
                "group_id": a.group_id,
                "ark_asset_id": a.ark_asset_id,
                "ark_status": a.ark_status,
            }
            for a in rows
        ]


def list_assets_by_ids(asset_ids: list[int]) -> list[dict]:
    """按 ID 返回已审核通过的素材，供创作场引用。"""
    clean_ids = list(dict.fromkeys(int(i) for i in asset_ids if int(i) > 0))
    if not clean_ids:
        return []
    with get_session() as session:
        rows = session.scalars(
            select(Asset)
            .where(Asset.id.in_(clean_ids), Asset.review_status == "approved")
            .order_by(Asset.created_at.desc())
        ).all()
        return [
            {
                "id": a.id,
                "type": a.type,
                "tos_url": _asset_url(a),
                "filename": a.filename,
                "group_id": a.group_id,
                "ark_asset_id": a.ark_asset_id,
                "ark_status": a.ark_status,
            }
            for a in rows
        ]


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
                "id": a.id,
                "group_id": a.group_id,
                "type": a.type,
                "tos_url": _asset_url(a),
                "filename": a.filename,
                "review_status": a.review_status,
                "ark_asset_id": a.ark_asset_id,
                "ark_status": a.ark_status,
                "ark_url": a.ark_url,
                "ark_error": a.ark_error,
                "created_at": a.created_at.strftime("%Y-%m-%d %H:%M"),
            }
            for a in rows
        ]


def count_assets_by_status(status: str, asset_type: str | None = None) -> int:
    """统计某审核状态的素材数，用于页面提示。"""
    with get_session() as session:
        stmt = select(func.count(Asset.id)).where(Asset.review_status == status)
        if asset_type:
            stmt = stmt.where(Asset.type == asset_type)
        return int(session.scalar(stmt) or 0)


def set_review_status(asset_id: int, status: str) -> None:
    if status not in ("pending", "approved", "rejected"):
        raise AssetError("非法审核状态")
    with get_session() as session:
        asset = session.get(Asset, asset_id)
        if not asset:
            raise AssetError("素材不存在")
        asset.review_status = status
