#!/usr/bin/env python3
"""
生成程序图标（多分辨率 ICO）
============================
从一个 256x256 母版缩放出 16/24/32/48/64/128/256 七档，
保证任务栏（16/24/32）、资源管理器（48）、桌面（48/64）都是清晰版本。

用法: python 创建图标.py
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw

# 程序主题色（与 kline_extreme_analyzer.py 的 THEME 保持一致）
BG = (16, 24, 38, 255)
BORDER = (45, 212, 191, 255)   # accent  #2dd4bf
UP = (34, 197, 94, 255)        # up      #22c55e
DOWN = (239, 68, 68, 255)      # down    #ef4444
WHITE = (230, 237, 243, 255)   # fg      #e6edf3

ICON_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
MASTER = 256


def _draw_master() -> Image.Image:
    """绘制 256x256 母版图标。"""
    s = MASTER
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 圆角底板
    m = int(s * 0.06)
    d.rounded_rectangle(
        [m, m, s - m, s - m],
        radius=int(s * 0.18),
        fill=BG,
        outline=BORDER,
        width=max(2, int(s * 0.022)),
    )

    # ── 左侧阳线（涨）──
    cx1 = int(s * 0.36)
    body_w = int(s * 0.105)
    d.line([cx1, int(s * 0.22), cx1, int(s * 0.78)], fill=WHITE, width=max(2, int(s * 0.018)))
    d.rounded_rectangle(
        [cx1 - body_w // 2, int(s * 0.38), cx1 + body_w // 2, int(s * 0.62)],
        radius=int(s * 0.012), fill=UP,
    )

    # ── 右侧阴线（跌）──
    cx2 = int(s * 0.64)
    d.line([cx2, int(s * 0.22), cx2, int(s * 0.78)], fill=WHITE, width=max(2, int(s * 0.018)))
    d.rounded_rectangle(
        [cx2 - body_w // 2, int(s * 0.34), cx2 + body_w // 2, int(s * 0.60)],
        radius=int(s * 0.012), fill=DOWN,
    )

    # ── 极值标记：最高点上的小三角 ──
    mx = int(s * 0.50)
    my = int(s * 0.20)
    tw = int(s * 0.045)
    th = int(s * 0.05)
    d.polygon([(mx, my - th), (mx - tw, my), (mx + tw, my)], fill=BORDER)

    # 极值水平虚线
    dash_w = max(2, int(s * 0.016))
    for x in range(int(s * 0.30), int(s * 0.70), dash_w * 3):
        d.rectangle([x, my, x + dash_w * 2, my + dash_w], fill=BORDER)

    return img


def create_icon() -> str:
    """生成并保存 kline_analyzer.ico。"""
    master = _draw_master()
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kline_analyzer.ico")
    master.save(icon_path, format="ICO", sizes=ICON_SIZES)
    size_kb = os.path.getsize(icon_path) / 1024
    print(f"[OK] Icon created: {icon_path} ({size_kb:.1f} KB, {len(ICON_SIZES)} sizes)")
    return icon_path


if __name__ == "__main__":
    create_icon()
