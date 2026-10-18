# -*- coding: utf-8 -*-
"""icons/make_candidates.py -- 生成 Arbor 图标候选（512px PNG + 对比图）
候选：
  1. branch  枝条分叉（深青底 + 白线）
  2. rings   年轮环 + 高亮扇区（浅底 + 青环）
  3. bars    三条横向长条、颜色深浅递增（浅底 + 青条）
  4. letter  字母 A 做成枝条（深青底 + 白字）
"""
import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "candidates")
os.makedirs(OUT, exist_ok=True)

DARK = (30, 78, 90)
TEAL = (40, 125, 120)
TEAL_L = (175, 212, 216)
BG = (245, 246, 248)
INK = (32, 37, 43)

S = 1024  # 先画 4 倍再缩小，抗锯齿


def lerp(c1, c2, t):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def rounded_bg(draw, fill):
    draw.rounded_rectangle([64, 64, S - 64, S - 64], radius=150, fill=fill)


def save(img, name):
    img = img.resize((512, 512), Image.LANCZOS)
    path = os.path.join(OUT, name + ".png")
    img.save(path)
    return path


def icon_branch():
    img = Image.new("RGB", (S, S), BG)
    d = ImageDraw.Draw(img)
    rounded_bg(d, DARK)
    cx, cy = S // 2, 560
    # 主干
    d.line([(cx, 830), (cx, 560)], fill=(255, 255, 255), width=26)
    # 三条分叉（左右对称 + 顶端）
    for (dx, dy) in ((-200, -120), (0, -230), (200, -120)):
        d.line([(cx, 560), (cx + dx, 560 + dy)], fill=(255, 255, 255),
               width=24)
        ex, ey = cx + dx, 560 + dy
        d.ellipse([ex - 34, ey - 34, ex + 34, ey + 34], fill=TEAL_L)
    # 根部圆点
    d.ellipse([cx - 40, 806, cx + 40, 886], fill=(255, 255, 255))
    return save(img, "1-branch")


def icon_rings():
    img = Image.new("RGB", (S, S), BG)
    d = ImageDraw.Draw(img)
    rounded_bg(d, BG)
    # 外圈描边增加对比
    d.rounded_rectangle([64, 64, S - 64, S - 64], radius=150,
                        outline=(210, 216, 222), width=8)
    c = S // 2
    for r, w, col in ((330, 40, TEAL), (240, 36, lerp(TEAL, TEAL_L, .6)),
                      (150, 30, TEAL_L)):
        d.ellipse([c - r, c - r, c + r, c + r], outline=col, width=w)
    # 高亮扇区（占比感，用琥珀色和青色区分）
    d.arc([c - 330, c - 330, c + 330, c + 330], start=-90, end=-15,
          fill=(169, 104, 19), width=44)
    return save(img, "2-rings")


def icon_bars():
    img = Image.new("RGB", (S, S), BG)
    d = ImageDraw.Draw(img)
    rounded_bg(d, BG)
    d.rounded_rectangle([64, 64, S - 64, S - 64], radius=150,
                        outline=(210, 216, 222), width=8)
    widths = (620, 420, 260)
    cols = (DARK, lerp(TEAL, TEAL_L, .35), TEAL_L)
    for i, (w, col) in enumerate(zip(widths, cols)):
        y = 300 + i * 130
        d.rounded_rectangle([170, y, 170 + w, y + 90], radius=45, fill=col)
    return save(img, "3-bars")


def icon_disk():
    """圆盘 + 从中长出的枝干。"""
    img = Image.new("RGB", (S, S), BG)
    d = ImageDraw.Draw(img)
    rounded_bg(d, DARK)
    cx = S // 2
    # 盘：下半圆
    d.arc([cx - 330, 620 - 330, cx + 330, 620 + 330], start=0, end=180,
          fill=(255, 255, 255), width=34)
    # 枝干从盘内长出来
    d.line([(cx, 800), (cx, 420)], fill=(255, 255, 255), width=26)
    for dx, dy in ((-170, -150), (170, -150)):
        d.line([(cx, 480), (cx + dx, 480 + dy)], fill=(255, 255, 255),
               width=22)
        ex, ey = cx + dx, 480 + dy
        d.ellipse([ex - 30, ey - 30, ex + 30, ey + 30], fill=TEAL_L)
    d.ellipse([cx - 26, 394, cx + 26, 446], fill=TEAL_L)
    return save(img, "4-disk")


def sheet(paths):
    imgs = [Image.open(p) for p in paths]
    W = 512 * 2 + 40
    H = 512 * 2 + 40
    sheet_img = Image.new("RGB", (W, H), (255, 255, 255))
    for i, im in enumerate(imgs):
        x = 10 + (i % 2) * 522
        y = 10 + (i // 2) * 522
        sheet_img.paste(im, (x, y))
    p = os.path.join(OUT, "all-four.png")
    sheet_img.save(p)
    return p


if __name__ == "__main__":
    paths = [icon_branch(), icon_rings(), icon_bars(), icon_disk()]
    print("\n".join(paths))
    print(sheet(paths))
