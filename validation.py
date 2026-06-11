"""人脸素材格式校验（对齐 Seedance 入库规则）。

图片 (≤30MB)：jpg/png/webp/bmp/tiff/gif/heic/heif；300~6000px；宽高比 0.4~2.5
视频 (≤50MB)：mp4/mov；2~15s；24~60fps；480p~1080p

图片用 Pillow 校验尺寸/宽高比；视频深度校验（时长/帧率/分辨率）用 opencv，
缺失 opencv 时自动降级为「仅校验格式与大小」并提示。
"""
from __future__ import annotations

import io
import os
import tempfile

IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "bmp", "tif", "tiff", "gif", "heic", "heif"}
VIDEO_EXTS = {"mp4", "mov"}

IMG_MAX_MB, VID_MAX_MB = 30, 50
IMG_MIN_PX, IMG_MAX_PX = 300, 6000
IMG_ASPECT_MIN, IMG_ASPECT_MAX = 0.4, 2.5
VID_MIN_S, VID_MAX_S = 2, 15
VID_FPS_MIN, VID_FPS_MAX = 24, 60
VID_RES_MIN, VID_RES_MAX = 480, 1080


def ext_of(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def classify(filename: str) -> str | None:
    e = ext_of(filename)
    if e in IMAGE_EXTS:
        return "image"
    if e in VIDEO_EXTS:
        return "video"
    return None


def validate_image(data: bytes, filename: str) -> tuple[bool, str]:
    e = ext_of(filename)
    if e not in IMAGE_EXTS:
        return False, f"不支持的图片格式 .{e}"
    mb = len(data) / 1024 / 1024
    if mb > IMG_MAX_MB:
        return False, f"图片超过 {IMG_MAX_MB}MB（当前 {mb:.1f}MB）"
    try:
        from PIL import Image

        try:  # HEIC/HEIF 需 pillow-heif，缺失则忽略
            import pillow_heif

            pillow_heif.register_heif_opener()
        except Exception:
            pass
        with Image.open(io.BytesIO(data)) as img:
            w, h = img.size
    except Exception as ex:
        return False, f"无法解析图片：{ex}"

    if not (IMG_MIN_PX <= w <= IMG_MAX_PX and IMG_MIN_PX <= h <= IMG_MAX_PX):
        return False, f"尺寸需 {IMG_MIN_PX}~{IMG_MAX_PX}px（当前 {w}x{h}）"
    aspect = w / h if h else 0
    if not (IMG_ASPECT_MIN <= aspect <= IMG_ASPECT_MAX):
        return False, f"宽高比需 {IMG_ASPECT_MIN}~{IMG_ASPECT_MAX}（当前 {aspect:.2f}）"
    return True, f"OK {w}x{h}"


def validate_video(data: bytes, filename: str) -> tuple[bool, str]:
    e = ext_of(filename)
    if e not in VIDEO_EXTS:
        return False, f"不支持的视频格式 .{e}"
    mb = len(data) / 1024 / 1024
    if mb > VID_MAX_MB:
        return False, f"视频超过 {VID_MAX_MB}MB（当前 {mb:.1f}MB）"

    try:
        import cv2
    except Exception:
        return True, f"OK（{mb:.1f}MB；未装 opencv，未深检时长/帧率/分辨率）"

    tmp = tempfile.NamedTemporaryFile(suffix="." + e, delete=False)
    try:
        tmp.write(data)
        tmp.flush()
        tmp.close()
        cap = cv2.VideoCapture(tmp.name)
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        cap.release()
    except Exception as ex:
        return False, f"无法解析视频：{ex}"
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass

    dur = (frames / fps) if fps else 0
    short = min(w, h) if (w and h) else 0
    if not (VID_MIN_S <= dur <= VID_MAX_S):
        return False, f"时长需 {VID_MIN_S}~{VID_MAX_S}s（当前 {dur:.1f}s）"
    if not (VID_FPS_MIN <= fps <= VID_FPS_MAX):
        return False, f"帧率需 {VID_FPS_MIN}~{VID_FPS_MAX}fps（当前 {fps:.0f}）"
    if not (VID_RES_MIN <= short <= VID_RES_MAX):
        return False, f"分辨率需 {VID_RES_MIN}p~{VID_RES_MAX}p（当前短边 {short}px）"
    return True, f"OK {w}x{h} {dur:.1f}s {fps:.0f}fps"


def validate(data: bytes, filename: str) -> tuple[str | None, bool, str]:
    """返回 (类型, 是否通过, 说明)。"""
    kind = classify(filename)
    if kind == "image":
        ok, msg = validate_image(data, filename)
        return kind, ok, msg
    if kind == "video":
        ok, msg = validate_video(data, filename)
        return kind, ok, msg
    return None, False, f"不支持的文件类型：{filename}"
