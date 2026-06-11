"""额度治理：分配 / 调整 / 校验 / 消耗 / 统计。

注意：Seedance 账号只有一个真实 token 池。这里的 token_quota 是
管理员给每个成员划出的「逻辑子预算」，用于约束和统计，并非物理隔离。
"""
from sqlalchemy import func, select

from database import get_session
from models import AppSetting, Generation, QuotaTransaction, User

# 账号 token 总额上限（资源包/自设上限）。可由管理员在后台修改。
DEFAULT_ACCOUNT_TOTAL = 350_000_000
_ACCOUNT_TOTAL_KEY = "account_total_tokens"


class QuotaError(Exception):
    pass


# ---------- 账号级配置与硬约束 ----------

def get_account_total() -> int:
    with get_session() as session:
        row = session.get(AppSetting, _ACCOUNT_TOTAL_KEY)
        if row and row.value.isdigit():
            return int(row.value)
        return DEFAULT_ACCOUNT_TOTAL


def set_account_total(tokens: int) -> None:
    if tokens < 0:
        raise QuotaError("账号总额不能为负")
    with get_session() as session:
        row = session.get(AppSetting, _ACCOUNT_TOTAL_KEY)
        if row:
            row.value = str(int(tokens))
        else:
            session.add(AppSetting(key=_ACCOUNT_TOTAL_KEY, value=str(int(tokens))))


def allocated_sum(exclude_user_id: int | None = None) -> int:
    """所有成员已分配额度之和（可排除某个成员，便于「重设」时计算）。"""
    with get_session() as session:
        stmt = select(func.coalesce(func.sum(User.token_quota), 0)).where(User.role == "member")
        if exclude_user_id is not None:
            stmt = stmt.where(User.id != exclude_user_id)
        return int(session.scalar(stmt) or 0)


def account_used_sum() -> int:
    """所有成员已消耗 token 之和（本应用作为唯一消费方，等同账号真实消耗）。"""
    with get_session() as session:
        return int(
            session.scalar(
                select(func.coalesce(func.sum(User.token_used), 0)).where(User.role == "member")
            )
            or 0
        )


def _assert_within_account(prospective_member_total: int, exclude_user_id: int | None = None) -> None:
    """硬约束：成员额度之和不得超过账号总额。"""
    others = allocated_sum(exclude_user_id=exclude_user_id)
    total = get_account_total()
    if others + prospective_member_total > total:
        free = max(total - others, 0)
        raise QuotaError(
            f"超出账号总额：账号共 {total:,} tokens，其他成员已占用 {others:,}，"
            f"本次最多还能分配 {free:,}。"
        )


# ---------- 额度分配 / 调整 ----------

def allocate_tokens(user_id: int, tokens: int, operator_id: int, note: str = "") -> None:
    """给成员追加额度（在原有基础上增加）。受账号总额硬约束。"""
    if tokens <= 0:
        raise QuotaError("追加额度必须大于 0")
    with get_session() as session:
        user = session.get(User, user_id)
        if not user:
            raise QuotaError("用户不存在")
        _assert_within_account(user.token_quota + tokens, exclude_user_id=user_id)
        user.token_quota += tokens
        session.add(
            QuotaTransaction(
                user_id=user_id, operator_id=operator_id,
                type="allocate", tokens=tokens, note=note,
            )
        )


def adjust_quota(user_id: int, new_total: int, operator_id: int, note: str = "") -> None:
    """把成员总额度直接重设为某个绝对值。"""
    if new_total < 0:
        raise QuotaError("额度不能为负")
    with get_session() as session:
        user = session.get(User, user_id)
        if not user:
            raise QuotaError("用户不存在")
        _assert_within_account(new_total, exclude_user_id=user_id)
        delta = new_total - user.token_quota
        user.token_quota = new_total
        session.add(
            QuotaTransaction(
                user_id=user_id, operator_id=operator_id,
                type="adjust", tokens=delta, note=note,
            )
        )


def check_can_generate(user_id: int) -> bool:
    """成员是否还有剩余额度可用于生成。"""
    with get_session() as session:
        user = session.get(User, user_id)
        return bool(user and (user.token_quota - user.token_used) > 0)


def can_afford(user_id: int, estimated_tokens: int) -> tuple[bool, int]:
    """生成前硬预检：剩余额度是否够本次预估消耗。返回 (是否可行, 剩余额度)。"""
    with get_session() as session:
        user = session.get(User, user_id)
        if not user:
            return False, 0
        remaining = max(user.token_quota - user.token_used, 0)
        return remaining >= estimated_tokens, remaining


def consume_tokens(user_id: int, tokens: int, note: str = "") -> None:
    """生成成功后扣减成员额度（在模块 1 接入生成时调用）。"""
    if tokens < 0:
        raise QuotaError("消耗额度不能为负")
    with get_session() as session:
        user = session.get(User, user_id)
        if not user:
            raise QuotaError("用户不存在")
        user.token_used += tokens
        session.add(
            QuotaTransaction(
                user_id=user_id, operator_id=None,
                type="consume", tokens=-tokens, note=note,
            )
        )


def usage_summary() -> list[dict]:
    """各用户用量汇总，供管理后台展示。"""
    with get_session() as session:
        rows = session.execute(
            select(
                User.id,
                User.username,
                User.role,
                User.token_quota,
                User.token_used,
                func.count(Generation.id).label("gen_count"),
                func.coalesce(func.sum(Generation.cost_yuan), 0.0).label("total_cost"),
            )
            .outerjoin(Generation, Generation.user_id == User.id)
            .group_by(User.id)
            .order_by(User.token_used.desc())
        ).all()
        return [dict(row._mapping) for row in rows]
