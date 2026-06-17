"""TOS connectivity smoke test.

Default mode only checks local configuration. Use --run to upload and delete a
small temporary object in the configured bucket.
"""
from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings
import tos_client


def _mask(value: str) -> str:
    if not value:
        return "未配置"
    if len(value) <= 8:
        return "已配置"
    return f"{value[:4]}...{value[-4:]}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true", help="真实上传并删除一个小对象")
    args = parser.parse_args()

    print("TOS_ACCESS_KEY:", _mask(settings.TOS_ACCESS_KEY))
    print("TOS_SECRET_KEY:", _mask(settings.TOS_SECRET_KEY))
    print("TOS_BUCKET:", settings.TOS_BUCKET or "未配置")
    print("TOS_ENDPOINT:", settings.TOS_ENDPOINT)
    print("TOS_REGION:", settings.TOS_REGION)
    print("TOS_CONFIGURED:", tos_client.is_configured())

    if not args.run:
        print("\nDry-run only. Add --run to upload and delete a smoke object.")
        return
    if not tos_client.is_configured():
        raise SystemExit("TOS 未配置完整，无法运行真实小样。")

    key = f"smoke/{uuid.uuid4().hex}.txt"
    url = tos_client.upload_bytes(
        key,
        b"seedance tos smoke\n",
        content_type="text/plain; charset=utf-8",
    )
    deleted = tos_client.delete_object(key)
    print("Uploaded key:", key)
    print("URL:", url)
    print("Deleted:", deleted)


if __name__ == "__main__":
    main()
