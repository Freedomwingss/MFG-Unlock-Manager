#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成程序图标：深色圆角底 + 三个递增的播放三角（多帧倍增）"""
import os
from PIL import Image, ImageDraw

S = 1024  # 先大图，最后缩小


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def rounded_bg(size):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 对角渐变底
    top = (46, 66, 92)
    bottom = (16, 24, 34)
    grad = Image.new("RGB", (1, size))
    gd = ImageDraw.Draw(grad)
    for y in range(size):
        gd.point((0, y), lerp(top, bottom, y / max(1, size - 1)))
    grad = grad.resize((size, size))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=int(size * 0.22), fill=255)
    img.paste(grad, (0, 0), mask)
    return img, d


def draw_triangle(d, cx, cy, h, color):
    """右向三角，h 为高度"""
    w = h * 0.86
    pts = [(cx - w / 2, cy - h / 2), (cx + w / 2, cy), (cx - w / 2, cy + h / 2)]
    d.polygon(pts, fill=color)


def build(size=1024):
    img, d = rounded_bg(size)
    u = size / 1024.0

    # 三个三角：从左到右递增、渐亮，垂直居中
    specs = [
        (250, 512, 300, (48, 104, 143, 240)),
        (505, 512, 400, (72, 163, 214, 248)),
        (800, 512, 520, (99, 212, 255, 255)),
    ]
    for cx, cy, h, col in specs:
        draw_triangle(d, cx * u, cy * u, h * u, col)
    return img


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(root, "assets")
    os.makedirs(out_dir, exist_ok=True)

    base = build(1024)
    png = os.path.join(out_dir, "icon.png")
    base.resize((512, 512), Image.LANCZOS).save(png)

    sizes = [16, 24, 32, 48, 64, 128, 256]
    imgs = [base.resize((s, s), Image.LANCZOS) for s in sizes]
    ico = os.path.join(out_dir, "icon.ico")
    imgs[-1].save(ico, format="ICO", sizes=[(s, s) for s in sizes])
    print("wrote", png, ico)


if __name__ == "__main__":
    main()
