"""Mirror a succeeded Ark task's output media to the configured TOS bucket."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import seedance
import tos_client


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_id")
    parser.add_argument("--prefix", default="manual", help="TOS key prefix")
    args = parser.parse_args()

    if not tos_client.is_configured():
        raise SystemExit("TOS 未配置完整，无法转存。")

    try:
        data = seedance.get_task(args.task_id)
    except seedance.SeedanceError as exc:
        raise SystemExit(f"Seedance error: {exc}") from None

    parsed = seedance.parse_task_result(data)
    if parsed["status"] != seedance.SUCCEEDED:
        raise SystemExit(f"任务尚未成功，当前状态：{parsed['status'] or 'unknown'}")

    outputs = []
    if parsed["video_url"]:
        outputs.append(
            (
                "video",
                tos_client.mirror_url(
                    f"{args.prefix}/{args.task_id}/video.mp4",
                    parsed["video_url"],
                    content_type="video/mp4",
                ),
            )
        )
    if parsed["audio_url"]:
        outputs.append(
            (
                "audio",
                tos_client.mirror_url(
                    f"{args.prefix}/{args.task_id}/audio.mp3",
                    parsed["audio_url"],
                    content_type="audio/mpeg",
                ),
            )
        )
    if parsed["last_frame_url"]:
        outputs.append(
            (
                "last_frame",
                tos_client.mirror_url(
                    f"{args.prefix}/{args.task_id}/last_frame.jpg",
                    parsed["last_frame_url"],
                    content_type="image/jpeg",
                ),
            )
        )

    if not outputs:
        print("任务成功，但未找到可转存的媒体 URL。")
        return
    for kind, url in outputs:
        print(f"{kind}: {url}")


if __name__ == "__main__":
    main()
