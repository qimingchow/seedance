"""额度治理：分配 / 调整 / 校验 / 消耗 / 统计。

注意：Seedance 账号只有一个真实 token 池。这里的 token_quota 是
管理员给每个成员划出的「逻辑子预算」，用于约束和统计，并非物理隔离。
"""
from sqlalchemy import func, select

from config import settings
from database import get_session
from models import AppSetting, Generation, QuotaTransaction, User

# 账号 token 总额上限（资源包/自设上限）。可由管理员在后台修改。
DEFAULT_ACCOUNT_TOTAL = settings.ACCOUNT_TOTAL_TOKENS
DEFAULT_ACCOUNT_EXTERNAL_USED = settings.ACCOUNT_EXTERNAL_USED_TOKENS
_ACCOUNT_TOTAL_KEY = "account_total_tokens"
_ACCOUNT_EXTERNAL_USED_KEY = "account_external_used_tokens"


class QuotaError(Exception):
    pass


# ---------- 账号级配置与硬约束 ----------

def get_account_total() -> int:
    with get_session() as session:
        return _account_total_in_session(session)


def _account_total_in_session(session) -> int:
    row = session.get(AppSetting, _ACCOUNT_TOTAL_KEY)
    if row and row.value.isdigit():
        return int(row.value)
    return DEFAULT_ACCOUNT_TOTAL


def get_account_external_used() -> int:
    with get_session() as session:
        return _account_external_used_in_session(session)


def _account_external_used_in_session(session) -> int:
    row = session.get(AppSetting, _ACCOUNT_EXTERNAL_USED_KEY)
    if row and row.value.isdigit():
        return int(row.value)
    return DEFAULT_ACCOUNT_EXTERNAL_USED


def account_managed_total() -> int:
    """本应用可分配/可管控的额度 = 资源包总额 - 期初/外部已消耗。"""
    with get_session() as session:
        return _account_managed_total_in_session(session)


def _account_managed_total_in_session(session) -> int:
    total = _account_total_in_session(session)
    external_used = _account_external_used_in_session(session)
    return max(total - external_used, 0)


def set_account_total(tokens: int) -> None:
    if tokens < 0:
        raise QuotaError("账号总额不能为负")
    with get_session() as session:
        row = session.get(AppSetting, _ACCOUNT_TOTAL_KEY)
        if row:
            row.value = str(int(tokens))
        else:
            session.add(AppSetting(key=_ACCOUNT_TOTAL_KEY, value=str(int(tokens))))


def set_account_external_used(tokens: int) -> None:
    if tokens < 0:
        raise QuotaError("期初/外部已消耗不能为负")
    with get_session() as session:
        row = session.get(AppSetting, _ACCOUNT_EXTERNAL_USED_KEY)
        if row:
            row.value = str(int(tokens))
        else:
            session.add(AppSetting(key=_ACCOUNT_EXTERNAL_USED_KEY, value=str(int(tokens))))


def set_account_quota_baseline(total_tokens: int, external_used_tokens: int) -> None:
    if total_tokens < 0 or external_used_tokens < 0:
        raise QuotaError("额度不能为负")
    if external_used_tokens > total_tokens:
        raise QuotaError("期初/外部已消耗不能大于资源包总额")
    with get_session() as session:
        total_row = session.get(AppSetting, _ACCOUNT_TOTAL_KEY)
        if total_row:
            total_row.value = str(int(total_tokens))
        else:
            session.add(AppSetting(key=_ACCOUNT_TOTAL_KEY, value=str(int(total_tokens))))

        used_row = session.get(AppSetting, _ACCOUNT_EXTERNAL_USED_KEY)
        if used_row:
            used_row.value = str(int(external_used_tokens))
        else:
            session.add(
                AppSetting(
                    key=_ACCOUNT_EXTERNAL_USED_KEY,
                    value=str(int(external_used_tokens)),
                )
            )


def allocated_sum(exclude_user_id: int | None = None) -> int:
    """所有成员已分配额度之和（可排除某个成员，便于「重设」时计算）。"""
    with get_session() as session:
        stmt = select(func.coalesce(func.sum(User.token_quota), 0)).where(User.role == "member")
        if exclude_user_id is not None:
            stmt = stmt.where(User.id != exclude_user_id)
        return int(session.scalar(stmt) or 0)


def account_used_sum() -> int:
    """所有用户已消耗 token 之和（本应用作为唯一消费方，等同账号真实消耗）。"""
    with get_session() as session:
        return int(
            session.scalar(select(func.coalesce(func.sum(User.token_used), 0)))
            or 0
        )


def account_reserved_sum() -> int:
    """所有运行中任务预占 token 之和。"""
    with get_session() as session:
        return int(
            session.scalar(select(func.coalesce(func.sum(User.token_reserved), 0)))
            or 0
        )


def account_remaining() -> int:
    """账号级剩余 token：可管控总额 - 本地已消耗 - 运行中预占。"""
    with get_session() as session:
        return max(_account_available_in_session(session), 0)


def _account_available_in_session(session) -> int:
    total = _account_managed_total_in_session(session)
    used = int(session.scalar(select(func.coalesce(func.sum(User.token_used), 0))) or 0)
    reserved = int(
        session.scalar(select(func.coalesce(func.sum(User.token_reserved), 0))) or 0
    )
    return total - used - reserved


def account_overrun() -> int:
    """账号级超额：本地已消耗 + 预占超过本应用可管控总额的部分。"""
    with get_session() as session:
        total = _account_managed_total_in_session(session)
        used = int(session.scalar(select(func.coalesce(func.sum(User.token_used), 0))) or 0)
        reserved = int(
            session.scalar(select(func.coalesce(func.sum(User.token_reserved), 0))) or 0
        )
        return max(used + reserved - total, 0)


def _assert_within_account(prospective_member_total: int, exclude_user_id: int | None = None) -> None:
    """硬约束：成员额度之和不得超过本应用可管控总额。"""
    others = allocated_sum(exclude_user_id=exclude_user_id)
    total = account_managed_total()
    if others + prospective_member_total > total:
        free = max(total - others, 0)
        raise QuotaError(
            f"超出可分配总额：本应用可管控 {total:,} tokens，其他成员已占用 {others:,}，"
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
        if not user:
            return False
        if user.role == "admin":
            return _account_available_in_session(session) > 0
        return (user.token_quota - user.token_used - user.token_reserved) > 0


def can_afford(user_id: int, estimated_tokens: int) -> tuple[bool, int]:
    """生成前硬预检：成员与账号剩余额度是否够本次预估消耗。"""
    with get_session() as session:
        user = session.get(User, user_id)
        if not user:
            return False, 0
        account_free = _account_available_in_session(session)
        if user.role == "admin":
            return account_free >= estimated_tokens, max(account_free, 0)
        remaining = user.token_remaining
        return remaining >= estimated_tokens and account_free >= estimated_tokens, min(
            remaining,
            max(account_free, 0),
        )


def reserve_tokens(user_id: int, tokens: int, note: str = "") -> None:
    """提交任务时预占额度，避免并发超额。"""
    if tokens < 0:
        raise QuotaError("预占额度不能为负")
    with get_session() as session:
        user = session.get(User, user_id)
        if not user:
            raise QuotaError("用户不存在")
        if user.role != "admin":
            remaining = user.token_quota - user.token_used - user.token_reserved
            if remaining < tokens:
                raise QuotaError(f"额度不足：当前可用 {max(remaining, 0):,} tokens")
        account_free = _account_available_in_session(session)
        if account_free < tokens:
            raise QuotaError(f"账号总额度不足：当前账号可用 {max(account_free, 0):,} tokens")
        user.token_reserved += tokens
        session.add(
            QuotaTransaction(
                user_id=user_id,
                operator_id=None,
                type="reserve",
                tokens=-tokens,
                note=note,
            )
        )


def release_reserved_tokens_in_session(
    session,
    user_id: int,
    tokens: int,
    note: str = "",
    operator_id: int | None = None,
) -> None:
    """在既有事务中释放预占额度。"""
    if tokens < 0:
        raise QuotaError("释放额度不能为负")
    user = session.get(User, user_id)
    if not user:
        raise QuotaError("用户不存在")
    released = min(user.token_reserved, tokens)
    user.token_reserved -= released
    session.add(
        QuotaTransaction(
            user_id=user_id,
            operator_id=operator_id,
            type="release",
            tokens=released,
            note=note,
        )
    )


def release_reserved_tokens(user_id: int, tokens: int, note: str = "") -> None:
    """任务失败/取消时释放预占额度。"""
    with get_session() as session:
        release_reserved_tokens_in_session(session, user_id, tokens, note)


def settle_reserved_tokens_in_session(
    session,
    user_id: int,
    reserved_tokens: int,
    actual_tokens: int,
    note: str = "",
    operator_id: int | None = None,
) -> None:
    """在既有事务中把预占额度结算为实际消耗。"""
    if reserved_tokens < 0 or actual_tokens < 0:
        raise QuotaError("结算额度不能为负")
    user = session.get(User, user_id)
    if not user:
        raise QuotaError("用户不存在")
    released = min(user.token_reserved, reserved_tokens)
    over_reserved = max(actual_tokens - released, 0)
    user.token_reserved -= released
    user.token_used += actual_tokens
    over_quota = (
        max(user.token_used + user.token_reserved - user.token_quota, 0)
        if user.role != "admin"
        else 0
    )
    settle_note = (
        f"{note} · 实际 {actual_tokens:,} / 预占 {released:,}"
        if note
        else f"实际 {actual_tokens:,} / 预占 {released:,}"
    )
    if over_reserved:
        settle_note += f" · 超预占 {over_reserved:,}"
    if over_quota:
        settle_note += f" · 成员超额 {over_quota:,}"
    if released:
        session.add(
            QuotaTransaction(
                user_id=user_id,
                operator_id=operator_id,
                type="release",
                tokens=released,
                note=f"{note} · 释放预占 {released:,}" if note else f"释放预占 {released:,}",
            )
        )
    session.add(
        QuotaTransaction(
            user_id=user_id,
            operator_id=operator_id,
            type="consume",
            tokens=-actual_tokens,
            note=settle_note,
        )
    )


def settle_reserved_tokens(
    user_id: int, reserved_tokens: int, actual_tokens: int, note: str = ""
) -> None:
    """任务成功后把预占额度结算为实际消耗。"""
    with get_session() as session:
        settle_reserved_tokens_in_session(session, user_id, reserved_tokens, actual_tokens, note)


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
                User.status,
                User.token_quota,
                User.token_used,
                User.token_reserved,
                func.count(Generation.id).label("gen_count"),
                func.coalesce(func.sum(Generation.cost_yuan), 0.0).label("total_cost"),
            )
            .outerjoin(Generation, Generation.user_id == User.id)
            .group_by(User.id)
            .order_by(User.token_used.desc())
        ).all()
        return [dict(row._mapping) for row in rows]
