# -*- coding: utf-8 -*-
"""tests/test_misc.py -- 收尾功能回归：排序、徽标、文件名搜索、ADS、子树收集
用法: python tests/test_misc.py
"""
import ctypes
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import gui  # noqa: E402
from scan import Scanner  # noqa: E402

FIX = os.path.join(HERE, "_misc_fixture")
DB = os.path.join(HERE, "_misc_fixture.sqlite")
ok = True


def check(name, cond, detail=""):
    global ok
    print(("[OK]   " if cond else "[FAIL] ") + name + ("" if cond else "  <- %s" % (detail,)))
    ok = ok and bool(cond)


def write(path, size):
    with open(path, "wb") as f:
        f.write(b"x" * size)


def make_fixture():
    shutil.rmtree(FIX, ignore_errors=True)
    os.makedirs(os.path.join(FIX, "hiddendir"))
    os.makedirs(os.path.join(FIX, "emptydir"))
    os.makedirs(os.path.join(FIX, "sub", "deep"))
    write(os.path.join(FIX, "aaa.bin"), 10)
    write(os.path.join(FIX, "zzz.bin"), 30)
    write(os.path.join(FIX, "sys.bin"), 5)
    write(os.path.join(FIX, "hiddendir", "inside.txt"), 7)
    write(os.path.join(FIX, "sub", "deep", "ads.txt"), 3)
    k32 = ctypes.windll.kernel32
    k32.SetFileAttributesW(os.path.join(FIX, "hiddendir"), 0x2)   # hidden
    k32.SetFileAttributesW(os.path.join(FIX, "sys.bin"), 0x4)     # system
    with open(os.path.join(FIX, "sub", "deep", "ads.txt") + ":junk",
              "w", encoding="utf-8") as f:
        f.write("payload-123")


def main():
    make_fixture()
    if os.path.exists(DB):
        os.remove(DB)
    sc = Scanner(DB)
    sc.scan([FIX], [])
    app = gui.App(db_path=DB, selftest=True)
    root = app.roots[0]
    app._fill_files("files::" + root, root)

    # 1) 隐藏目录徽标
    hid = os.path.join(root, "hiddendir")
    check("隐藏目录状态含「隐藏」",
          "隐藏" in app.tv.item(hid, "values")[4],
          app.tv.item(hid, "values"))

    # 2) 系统文件徽标
    sysfile = os.path.join(root, "sys.bin")
    check("系统文件状态含「系统」",
          "系统" in app.tv.item(sysfile, "values")[4],
          app.tv.item(sysfile, "values"))

    # 3) 排序：名称升序
    app._sort_by("name")
    names = [app.tv.item(i, "text").lower()
             for i in app.tv.get_children(root)
             if not i.startswith(("files::", "ph"))]
    expect = sorted(names)
    check("名称排序升序", names == expect, (names, expect))

    # 4) 排序：大小降序（再点一次名称变降序，再点大小）
    app._sort_by("size")
    sizes = [app.file_sizes.get(i, app.totals.get(i, 0))
             for i in app.tv.get_children(root)
             if not i.startswith(("files::", "ph"))]
    check("大小排序降序", sizes == sorted(sizes, reverse=True), sizes)

    # 5) 文件名搜索
    hits = app._search_hits("ads.txt")
    file_hits = [h for h in hits if h[2] == "文件"]
    check("搜索命中文件 ads.txt", len(file_hits) == 1, hits)
    hits2 = app._search_hits("deep")
    check("搜索同时命中目录", any(h[2] == "目录" for h in hits2), hits2)

    # 6) ADS 检测（夹具里造了 :junk 流）
    ads_file = os.path.join(root, "sub", "deep", "ads.txt")
    rows = app._scan_ads_rows([ads_file])
    check("ADS 检测到 1 个流", len(rows) == 1 and ":junk" in rows[0][1], rows)

    # 7) 子树文件收集（限 2000）
    files, truncated = app._collect_files_under(root)
    check("子树收集到全部文件", len(files) == 5 and not truncated,
          (len(files), truncated))

    # 8) 误删防护分级
    lvl_win, why_win = app._move_risk(r"C:\Windows\System32")
    check("系统目录被拦截", lvl_win == "block", (lvl_win, why_win))
    home = os.path.expanduser("~")
    lvl_app, why_app = app._move_risk(
        os.path.join(home, "AppData", "Local", "SomeUnknownApp"))
    check("AppData 未知目录需二次确认", lvl_app == "warn", (lvl_app, why_app))
    lvl_ok, why_ok = app._move_risk(r"D:\SomeTest\x.bin")
    check("普通位置可直接隔离", lvl_ok == "ok", (lvl_ok, why_ok))

    # 9) 帮助窗口能打开
    app._show_help()
    app.update()
    wins = [w for w in app.winfo_children() if isinstance(w, gui.tk.Toplevel)]
    check("帮助窗口已打开", bool(wins), wins)
    for w in wins:
        w.destroy()

    app.destroy()
    shutil.rmtree(FIX, ignore_errors=True)
    try:
        os.remove(DB)
    except OSError:
        pass
    print("RESULT:", "PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
