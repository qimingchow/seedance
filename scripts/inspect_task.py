"""查询一个 Ark Seedance 任务并打印解析结果。

用法：
    python scripts/inspect_task.py <task_id>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import seedance


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_id")
    parser.add_argument("--raw", action="store_true", help="同时打印原始 JSON")
    args = parser.parse_args()

    try:
        data = seedance.get_task(args.task_id)
    except seedance.SeedanceError as exc:
        raise SystemExit(f"Seedance error: {exc}") from None
    print(json.dumps(seedance.parse_task_result(data), ensure_ascii=False, indent=2))
    if args.raw:
        print("\nRAW:")
        print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
