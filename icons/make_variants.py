# -*- coding: utf-8 -*-
"""icons/make_variants.py -- 在枝条版（候选 1）基础上加细节的变体
A. 淡年轮底纹 + 枝条
B. 枝端改成「目录方块」（圆角小方片，深浅递增）
C. 三级枝（主枝再分叉）+ 根部高亮弧
D. 细外环徽章 + 主枝分段渐变
"""
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "variants")
os.makedirs(OUT, exist_ok=True)

DARK = (30, 78, 90)
DARK2 = (36, 88, 100)
TEAL = (40, 125, 120)
TEAL_L = (175, 212, 216)
AMBER = (196, 132, 40)
WHITE = (255, 255, 255)
S = 1024


def lerp(c1, c2, t):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def base(draw, fill=DARK):
    draw.rounded_rectangle([64, 64, S - 64, S - 64], radius=150, fill=fill)


def branch_white(d, cx, y0, y1, width=26):
    d.line([(cx, y0), (cx, y1)], fill=WHITE, width=width)
    for dx, dy in ((-200, -120), (0, -230), (200, -120)):
        d.line([(cx, y1), (cx + dx, y1 + dy)], fill=WHITE, width=24)
        ex, ey = cx + dx, y1 + dy
        d.ellipse([ex - 34, ey - 34, ex + 34, ey + 34], fill=TEAL_L)
    d.ellipse([cx - 40, y0 - 40, cx + 40, y0 + 40], fill=WHITE)


def save(img, name):
    img = img.resize((512, 512), Image.LANCZOS)
    p = os.path.join(OUT, name + ".png")
    img.save(p)
    return p


def v_rings():
    img = Image.new("RGB", (S, S), DARK)
    d = ImageDraw.Draw(img)
    base(d)
    cx, cy = S // 2, 560
    # 淡年轮底纹
    for r in (300, 380, 460):
        d.ellipse([cx - r, cy - r, cx + r, cy + r],
                  outline=DARK2, width=10)
    branch_white(d, cx, 830, cy)
    return save(img, "A-rings")


def v_blocks():
    img = Image.new("RGB", (S, S), DARK)
    d = ImageDraw.Draw(img)
    base(d)
    cx, cy = S // 2, 560
    d.line([(cx, 830), (cx, cy)], fill=WHITE, width=26)
    d.ellipse([cx - 40, 790, cx + 40, 870], fill=WHITE)
    nodes = [(-200, -120, 110), (0, -230, 130), (200, -120, 110)]
    for i, (dx, dy, sz) in enumerate(nodes):
        d.line([(cx, cy), (cx + dx, cy + dy)], fill=WHITE, width=24)
        ex, ey = cx + dx, cy + dy
        col = lerp(TEAL_L, TEAL, .3 + i * .25)
        d.rounded_rectangle([ex - sz // 2, ey - sz // 2,
                             ex + sz // 2, ey + sz // 2],
                            radius=22, fill=col)
    return save(img, "B-blocks")


def v_twigs():
    img = Image.new("RGB", (S, S), DARK)
    d = ImageDraw.Draw(img)
    base(d)
    cx, cy = S // 2, 560
    d.line([(cx, 830), (cx, cy)], fill=WHITE, width=26)
    d.ellipse([cx - 40, 790, cx + 40, 870], fill=WHITE)
    # 只有顶枝分两小杈，左右枝保持单点
    for dx, dy in ((-200, -120), (0, -230), (200, -120)):
        ex, ey = cx + dx, cy + dy
        d.line([(cx, cy), (ex, ey)], fill=WHITE, width=24)
        d.ellipse([ex - 32, ey - 32, ex + 32, ey + 32], fill=TEAL_L)
    topx, topy = cx, cy - 230
    for nx, ny in ((topx - 70, topy - 80), (topx + 70, topy - 80)):
        d.line([(topx, topy), (nx, ny)], fill=WHITE, width=18)
        d.ellipse([nx - 20, ny - 20, nx + 20, ny + 20], fill=TEAL_L)
    return save(img, "C-twigs")


def v_badge():
    img = Image.new("RGB", (S, S), DARK)
    d = ImageDraw.Draw(img)
    base(d)
    # 细外环
    d.ellipse([132, 132, S - 132, S - 132], outline=DARK2, width=12)
    cx, cy = S // 2, 560
    # 主枝分段渐变
    steps = 22
    y0, y1 = 830, cy
    for i in range(steps):
        t0 = i / steps
        y_a = y0 + (y1 - y0) * t0
        y_b = y0 + (y1 - y0) * (i + 1) / steps
        col = lerp(WHITE, TEAL_L, t0 * .8)
        d.line([(cx, y_a), (cx, y_b)], fill=col, width=26)
    for dx, dy in ((-200, -120), (0, -230), (200, -120)):
        d.line([(cx, cy), (cx + dx, cy + dy)], fill=WHITE, width=24)
        ex, ey = cx + dx, cy + dy
        d.ellipse([ex - 34, ey - 34, ex + 34, ey + 34], fill=TEAL_L)
    d.ellipse([cx - 40, y0 - 40, cx + 40, y0 + 40], fill=WHITE)
    return save(img, "D-badge")


def v_combo():
    """年轮底纹 + 方块节点（A 的细节 + B 的节点）。"""
    img = Image.new("RGB", (S, S), DARK)
    d = ImageDraw.Draw(img)
    base(d)
    cx, cy = S // 2, 560
    for r in (300, 380, 460):
        d.ellipse([cx - r, cy - r, cx + r, cy + r],
                  outline=DARK2, width=10)
    d.line([(cx, 830), (cx, cy)], fill=WHITE, width=26)
    d.ellipse([cx - 40, 790, cx + 40, 870], fill=WHITE)
    nodes = [(-200, -120, 110), (0, -230, 130), (200, -120, 110)]
    for i, (dx, dy, sz) in enumerate(nodes):
        d.line([(cx, cy), (cx + dx, cy + dy)], fill=WHITE, width=24)
        ex, ey = cx + dx, cy + dy
        col = lerp(TEAL_L, TEAL, .3 + i * .25)
        d.rounded_rectangle([ex - sz // 2, ey - sz // 2,
                             ex + sz // 2, ey + sz // 2],
                            radius=22, fill=col)
    return save(img, "E-combo")


def sheet(paths):
    imgs = [Image.open(p) for p in paths]
    cols = 2
    rows = (len(imgs) + cols - 1) // cols
    W = 512 * cols + 20 * (cols + 1)
    H = 512 * rows + 20 * (rows + 1)
    sh = Image.new("RGB", (W, H), (255, 255, 255))
    for i, im in enumerate(imgs):
        sh.paste(im, (20 + (i % cols) * 532, 20 + (i // cols) * 532))
    p = os.path.join(OUT, "all-variants.png")
    sh.save(p)
    return p


if __name__ == "__main__":
    paths = [v_rings(), v_blocks(), v_twigs(), v_badge(), v_combo()]
    print("\n".join(paths))
    print(sheet(paths))
