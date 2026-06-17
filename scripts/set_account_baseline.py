"""Set the local account quota baseline from the Volcengine package values."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import init_db
from quota import (
    QuotaError,
    account_managed_total,
    account_remaining,
    allocated_sum,
    get_account_external_used,
    get_account_total,
    set_account_quota_baseline,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--total", type=int, required=True, help="资源包 token 总额")
    parser.add_argument(
        "--external-used",
        type=int,
        default=0,
        help="本系统接管前或外部已消耗 tokens",
    )
    args = parser.parse_args()

    init_db()
    try:
        set_account_quota_baseline(args.total, args.external_used)
    except QuotaError as exc:
        raise SystemExit(str(exc)) from None

    print("Account total:", f"{get_account_total():,}")
    print("External/pre-app used:", f"{get_account_external_used():,}")
    print("Managed total:", f"{account_managed_total():,}")
    print("Allocated to members:", f"{allocated_sum():,}")
    print("Account available:", f"{account_remaining():,}")


if __name__ == "__main__":
    main()
