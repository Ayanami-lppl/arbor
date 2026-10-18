# -*- coding: utf-8 -*-
"""icons/make_icon.py -- 生成正式图标 arbor.ico（E 版：年轮底纹 + 方块节点）
每个尺寸单独按比例渲染色条宽，保证 16px 也清楚；小尺寸去掉年轮避免糊。
"""
import os

from PIL import Image, ImageChops, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_ICO = os.path.join(HERE, "arbor.ico")
OUT_PNG = os.path.join(HERE, "arbor-512.png")

DARK = (30, 78, 90)
DARK2 = (40, 96, 108)
TEAL = (40, 125, 120)
TEAL_L = (175, 212, 216)
WHITE = (255, 255, 255)
SIZES = [16, 24, 32, 48, 64, 128, 256]


def lerp(c1, c2, t):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def render(size, ss=4):
    """按最终像素尺寸画（内部 4 倍超采样）。"""
    s = size * ss
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = 0.0625 * s
    d.rounded_rectangle([pad, pad, s - pad, s - pad],
                        radius=0.146 * s, fill=DARK)
    cx, cy = 0.5 * s, 0.547 * s
    # 年轮底纹（小尺寸省略），裁剪在圆角方块内
    if size > 20:
        tile = Image.new("L", (s, s), 0)
        ImageDraw.Draw(tile).rounded_rectangle(
            [pad, pad, s - pad, s - pad], radius=0.146 * s, fill=255)
        layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        for r in (0.293, 0.371, 0.449):
            rr = r * s
            ld.ellipse([cx - rr, cy - rr, cx + rr, cy + rr],
                       outline=DARK2, width=max(int(0.010 * s), 6))
        mask = ImageChops.multiply(layer.getchannel("A"), tile)
        img.paste(layer, (0, 0), mask)
    # 主干
    y0, y1 = 0.81 * s, cy
    d.line([(cx, y0), (cx, y1)], fill=WHITE,
           width=max(int(0.025 * s), int(0.10 * size)))
    d.ellipse([cx - 0.039 * s, y0 - 0.039 * s,
               cx + 0.039 * s, y0 + 0.039 * s], fill=WHITE)
    # 三条枝 + 方块节点
    nodes = ((-0.195, -0.117, 0.055), (0, -0.225, 0.066),
             (0.195, -0.117, 0.055))
    for i, (dx, dy, half) in enumerate(nodes):
        ex, ey = cx + dx * s, cy + dy * s
        d.line([(cx, cy), (ex, ey)], fill=WHITE,
               width=max(int(0.023 * s), int(0.09 * size)))
        col = lerp(TEAL_L, TEAL, 0.25 + i * 0.25)
        h = half * s
        d.rounded_rectangle([ex - h, ey - h, ex + h, ey + h],
                            radius=max(int(0.021 * s), 2), fill=col)
    return img.resize((size, size), Image.LANCZOS)


def main():
    frames = {s: render(s) for s in SIZES}
    big = frames[256]
    try:
        big.save(OUT_ICO, format="ICO",
                 append_images=[frames[s] for s in SIZES if s != 256],
                 sizes=[(s, s) for s in SIZES])
    except (TypeError, ValueError):
        big.save(OUT_ICO, format="ICO", sizes=[(s, s) for s in SIZES])
    im = Image.open(OUT_ICO)
    print("ico:", OUT_ICO, "entries:", sorted(im.info.get("sizes", [])))
    render(512).save(OUT_PNG)
    print("png:", OUT_PNG)


if __name__ == "__main__":
    main()
