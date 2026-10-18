# -*- coding: utf-8 -*-
"""tools/unpack-wheels.py -- 把 wheels/*.whl 直接解压到 pylibs/（跳过 .data），替代 pip 安装
只写工作区内的 pylibs 目录。
"""
import os
import sys
import zipfile
import shutil

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WHEELS = os.path.join(HERE, "wheels")
OUT = os.path.join(HERE, "pylibs")


def main():
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT, exist_ok=True)
    for w in sorted(os.listdir(WHEELS)):
        if not w.endswith(".whl"):
            continue
        with zipfile.ZipFile(os.path.join(WHEELS, w)) as z:
            names = [n for n in z.namelist() if ".data/" not in n]
            z.extractall(OUT, names)
        print("unpacked", w, len(names), "files")
    print("pylibs ready:", OUT)


if __name__ == "__main__":
    main()
