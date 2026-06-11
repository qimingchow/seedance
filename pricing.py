"""费用估算。

Seedance（火山）按 token 计费，文档给出的计费公式约为：
    token ≈ (输入视频时长 + 输出视频时长) × 输出宽 × 输出高 × 帧率 / 1024

本模块用于「生成前」估算，作为额度硬闸门的预检依据。
真正扣减以接口返回的 usage.total_tokens（实际计费）为准 —— 见 seedance.parse_result。

注意：下面的分辨率→像素映射是估算用的近似值，请按你账号实际计费校准。
"""
from config import settings

# (resolution, ratio) -> (width, height) 近似映射
_RES_SHORT = {"480p": 480, "720p": 720, "1080p": 1080}

_RATIOS = {
    "16:9": (16, 9),
    "9:16": (9, 16),
    "1:1": (1, 1),
    "4:3": (4, 3),
    "3:4": (3, 4),
    "21:9": (21, 9),
}


def resolution_to_wh(resolution: str, ratio: str) -> tuple[int, int]:
    """把 (分辨率, 宽高比) 估算成像素宽高。short = 短边像素。"""
    short = _RES_SHORT.get(resolution, 720)
    rw, rh = _RATIOS.get(ratio, (16, 9))
    if rw >= rh:  # 横屏或方形，短边是高
        height = short
        width = round(short * rw / rh)
    else:         # 竖屏，短边是宽
        width = short
        height = round(short * rh / rw)
    return width, height


def estimate_tokens(
    resolution: str, ratio: str, duration: int, fps: int = 24, input_duration: int = 0
) -> int:
    """生成前的 token 估算（用于额度预检）。"""
    width, height = resolution_to_wh(resolution, ratio)
    tokens = (input_duration + duration) * width * height * fps / 1024
    return int(tokens)


def tokens_to_yuan(tokens: int) -> float:
    """按配置的单价把 token 折算成人民币（用于展示费用）。"""
    return round(tokens * settings.TOKEN_UNIT_PRICE_YUAN, 4)
