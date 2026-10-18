# -*- coding: utf-8 -*-
"""tools/fix-readme-name.py -- 把 README 里的旧名字改成 Arbor（一次性）"""
import os

P = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "README.md")

REPL = [
    ("# disk-inspector（磁盘分析工具）", "# Arbor（磁盘分析工具）"),
    ("桌面 `磁盘分析\\磁盘分析.exe`（文件夹版，无需安装、双击即用）",
     "桌面 `Arbor\\Arbor.exe`（文件夹版，无需安装、双击即用；图标 icons/arbor.ico）"),
    ("--windowed --name 磁盘分析 gui.py",
     "--windowed --icon icons/arbor.ico --name Arbor gui.py"),
    ("桌面成品：`C:\\Users\\ksl\\Desktop\\盘枝\\盘枝.exe`（onedir 文件夹版；旧 `磁盘分析` 保留不动）",
     "桌面成品：`C:\\Users\\ksl\\Desktop\\Arbor\\Arbor.exe`（onedir 文件夹版，含 arbor.ico 窗口图标；旧 `磁盘分析` 保留不动）"),
]


def main():
    with open(P, encoding="utf-8") as f:
        s = f.read()
    for old, new in REPL:
        if old in s:
            s = s.replace(old, new)
            print("replaced:", old[:30])
        else:
            print("not found:", old[:30])
    with open(P, "w", encoding="utf-8") as f:
        f.write(s)
    print("done")


if __name__ == "__main__":
    main()
