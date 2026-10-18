# -*- coding: utf-8 -*-
"""tests/make-fixture.py -- 生成隐藏文件/隐藏目录/ADS 测试夹具（只写 _fixture 目录）"""
import ctypes
import os
import shutil

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_fixture")
ROOT = os.path.normpath(ROOT)

k32 = ctypes.windll.kernel32
FA_HIDDEN = 0x2


def main():
    shutil.rmtree(ROOT, ignore_errors=True)
    os.makedirs(os.path.join(ROOT, "a"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "b"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "c", "secret"), exist_ok=True)
    with open(os.path.join(ROOT, "a", "1.txt"), "w", encoding="utf-8") as f:
        f.write("x" * 1000)
    with open(os.path.join(ROOT, "a", "2.txt"), "w", encoding="utf-8") as f:
        f.write("y" * 500)
    with open(os.path.join(ROOT, "b", "hidden1.bin"), "wb") as f:
        f.write(b"z" * 2000)
    with open(os.path.join(ROOT, "b", "normal.bin"), "wb") as f:
        f.write(b"w" * 300)
    with open(os.path.join(ROOT, "c", "secret", "inside.txt"), "w", encoding="utf-8") as f:
        f.write("i" * 400)
    with open(os.path.join(ROOT, "d-ads.txt"), "w", encoding="utf-8") as f:
        f.write("base data")
    with open(os.path.join(ROOT, "d-ads.txt") + ":junk", "w", encoding="utf-8") as f:
        f.write("secret payload stream")
    k32.SetFileAttributesW(os.path.join(ROOT, "b", "hidden1.bin"), FA_HIDDEN)
    k32.SetFileAttributesW(os.path.join(ROOT, "c", "secret"), FA_HIDDEN)
    print("fixture ready at %s" % ROOT)


if __name__ == "__main__":
    main()
