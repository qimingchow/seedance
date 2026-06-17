"""Seedance 最小化小样测试。

默认 dry-run，只打印请求 payload、预估 token 和预估费用；必须显式传 `--run`
才会真正提交到火山方舟并消耗额度。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pricing
import seedance


def _split(raw: str) -> list[str]:
    if not raw:
        return []
    return [x.strip() for x in raw.replace(",", "\n").splitlines() if x.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true", help="真实提交任务，会消耗额度")
    parser.add_argument("--poll", action="store_true", help="提交后轮询到终态")
    parser.add_argument(
        "--model",
        choices=["pro", "fast"],
        default=seedance.default_model_key(),
    )
    parser.add_argument("--prompt", default="一只白色陶瓷杯放在木桌上，晨光照入，镜头缓慢推进。")
    parser.add_argument("--ratio", default="9:16")
    parser.add_argument("--resolution", default="480p")
    parser.add_argument("--duration", type=int, default=4)
    parser.add_argument("--generate-audio", action="store_true")
    parser.add_argument("--watermark", action="store_true")
    parser.add_argument("--image-url", action="append", default=[])
    parser.add_argument("--video-url", action="append", default=[])
    parser.add_argument("--audio-url", action="append", default=[])
    args = parser.parse_args()

    image_urls = [u for item in args.image_url for u in _split(item)]
    video_urls = [u for item in args.video_url for u in _split(item)]
    audio_urls = [u for item in args.audio_url for u in _split(item)]
    try:
        payload = seedance.build_payload(
            args.model,
            args.prompt,
            ratio=args.ratio,
            resolution=args.resolution,
            duration=args.duration,
            image_urls=image_urls,
            video_urls=video_urls,
            audio_urls=audio_urls,
            generate_audio=args.generate_audio,
            watermark=args.watermark,
        )
    except seedance.SeedanceError as exc:
        raise SystemExit(f"Seedance config error: {exc}") from None

    print("Model key:", args.model)
    est_input_duration = pricing.estimate_reference_video_seconds(
        args.duration,
        len(video_urls),
    )
    est_tokens = pricing.estimate_tokens(
        args.resolution,
        args.ratio,
        args.duration,
        24,
        input_duration=est_input_duration,
    )
    est_cost = pricing.tokens_to_yuan(est_tokens, has_video_input=bool(video_urls))
    print("Estimated tokens:", f"{est_tokens:,}")
    print("Estimated cost:", f"¥{est_cost:.4f}")
    print("Payload:")
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    if not args.run:
        print("\nDry-run only. Add --run to submit this task to Ark.")
        return

    try:
        create_response = seedance.submit_task(payload)
    except seedance.SeedanceError as exc:
        raise SystemExit(f"\nSeedance error: {exc}") from None
    print("\nCreate response:")
    print(json.dumps(create_response, ensure_ascii=False, indent=2))
    task_id = create_response.get("id")
    if not args.poll or not task_id:
        return

    print("\nPolling:", task_id)
    latest = seedance.get_task(task_id)
    for _ in range(120):
        parsed = seedance.parse_task_result(latest)
        print(parsed)
        if parsed["status"] in seedance.TERMINAL:
            break
        time.sleep(5)
        try:
            latest = seedance.get_task(task_id)
        except seedance.SeedanceError as exc:
            raise SystemExit(f"\nSeedance error: {exc}") from None

    print("\nFinal raw response:")
    print(json.dumps(latest, ensure_ascii=False, indent=2))
    parsed = seedance.parse_task_result(latest)
    actual = parsed["total_tokens"]
    if actual:
        delta = actual - est_tokens
        pct = (delta / est_tokens * 100) if est_tokens else 0
        print(f"\nEstimate delta: {delta:+,} tokens ({pct:+.2f}%)")


if __name__ == "__main__":
    main()
