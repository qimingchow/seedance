"""Print a local quota ledger summary without exposing secrets."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select

from database import init_db, get_session
from models import Generation, QuotaTransaction, User
from quota import (
    account_managed_total,
    account_overrun,
    account_remaining,
    account_reserved_sum,
    account_used_sum,
    allocated_sum,
    get_account_external_used,
    get_account_total,
)


def main() -> None:
    init_db()
    total = get_account_total()
    external_used = get_account_external_used()
    managed_total = account_managed_total()
    allocated = allocated_sum()
    used = account_used_sum()
    reserved = account_reserved_sum()
    remaining = account_remaining()
    overrun = account_overrun()
    print("Account total:", f"{total:,}")
    print("External/pre-app used:", f"{external_used:,}")
    print("Managed total:", f"{managed_total:,}")
    print("Allocated to members:", f"{allocated:,}")
    print("Account used:", f"{used:,}")
    print("Account reserved:", f"{reserved:,}")
    print("Account available:", f"{remaining:,}")
    print("Account overrun:", f"{overrun:,}")

    with get_session() as session:
        users = session.query(User).order_by(User.role, User.username).all()
        issues: list[str] = []
        print("\nUsers:")
        for user in users:
            user_overrun = (
                max(user.token_used + user.token_reserved - user.token_quota, 0)
                if user.role != "admin"
                else 0
            )
            print(
                f"- #{user.id} {user.username} ({user.role}/{user.status}) "
                f"quota={user.token_quota:,} used={user.token_used:,} "
                f"reserved={user.token_reserved:,} available={user.token_remaining:,} "
                f"overrun={user_overrun:,}"
            )
            if user_overrun:
                issues.append(
                    f"user #{user.id} {user.username} is over quota by {user_overrun:,} tokens"
                )

        tx_count = session.query(QuotaTransaction).count()
        print("\nQuota transactions:", f"{tx_count:,}")

        succeeded_usage = dict(
            session.execute(
                select(
                    Generation.user_id,
                    func.coalesce(func.sum(Generation.tokens_used), 0),
                )
                .where(Generation.status == "succeeded")
                .group_by(Generation.user_id)
            ).all()
        )
        active_reserved = dict(
            session.execute(
                select(
                    Generation.user_id,
                    func.coalesce(func.sum(Generation.reserved_tokens), 0),
                )
                .where(Generation.status.in_(["queued", "pending", "running", "needs_settlement"]))
                .group_by(Generation.user_id)
            ).all()
        )
        consumed_by_tx = dict(
            session.execute(
                select(
                    QuotaTransaction.user_id,
                    func.coalesce(func.sum(-QuotaTransaction.tokens), 0),
                )
                .where(QuotaTransaction.type == "consume")
                .group_by(QuotaTransaction.user_id)
            ).all()
        )

        for user in users:
            actual_success_tokens = int(succeeded_usage.get(user.id, 0) or 0)
            reserved_tokens = int(active_reserved.get(user.id, 0) or 0)
            tx_consumed = int(consumed_by_tx.get(user.id, 0) or 0)
            if user.token_used != actual_success_tokens:
                issues.append(
                    f"user #{user.id} {user.username} used={user.token_used:,} "
                    f"but succeeded generations sum={actual_success_tokens:,}"
                )
            if user.token_reserved != reserved_tokens:
                issues.append(
                    f"user #{user.id} {user.username} reserved={user.token_reserved:,} "
                    f"but active generations reserve sum={reserved_tokens:,}"
                )
            if user.token_used != tx_consumed:
                issues.append(
                    f"user #{user.id} {user.username} used={user.token_used:,} "
                    f"but consume transactions sum={tx_consumed:,}"
                )

        unsettled = session.scalars(
            select(Generation).where(Generation.status == "needs_settlement")
        ).all()
        if unsettled:
            print("\nNeeds manual settlement:")
            for gen in unsettled:
                print(
                    f"- generation #{gen.id} task={gen.task_id or '-'} "
                    f"user_id={gen.user_id} reserved={gen.reserved_tokens:,}"
                )
            issues.append(f"{len(unsettled)} generation(s) need manual settlement")

        print("\nLedger audit:", "OK" if not issues else "ISSUES FOUND")
        for issue in issues:
            print("-", issue)


if __name__ == "__main__":
    main()
