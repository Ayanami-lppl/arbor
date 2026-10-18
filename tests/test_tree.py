# -*- coding: utf-8 -*-
"""tests/test_tree.py -- 阶段二：真实目录树 + 详情区测试
用法: python tests/test_tree.py
自动生成夹具目录 → 扫描成库 → 用 App(selftest) 构建 → 断言树模型。
"""
import os
import sys
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import gui  # noqa: E402
from scan import Scanner  # noqa: E402

FIXTURE = os.path.join(HERE, "_tree_fixture")
DB = os.path.join(HERE, "_tree_fixture.sqlite")


def write(path, size):
    with open(path, "wb") as f:
        f.write(b"x" * size)


def make_fixture():
    shutil.rmtree(FIXTURE, ignore_errors=True)
    for sub in ("a/empty", "a/same", "b/same", "c/深/层/目录", "d/中文 名称"):
        os.makedirs(os.path.join(FIXTURE, sub), exist_ok=True)
    write(os.path.join(FIXTURE, "a", "same", "x.txt"), 10)
    write(os.path.join(FIXTURE, "b", "same", "x.txt"), 20)
    write(os.path.join(FIXTURE, "top1.bin"), 100)
    write(os.path.join(FIXTURE, "a", "top1.bin"), 50)
    write(os.path.join(FIXTURE, "c", "深", "层", "目录", "deep.txt"), 30)
    write(os.path.join(FIXTURE, "d", "中文 名称",
                       "长文件名" + "长" * 40 + ".txt"), 5)
    deep = os.path.join(FIXTURE, "long")
    p = deep
    for i in range(8):
        p = os.path.join(p, "lvl%02d" % i)
    os.makedirs(p, exist_ok=True)
    write(os.path.join(p, "end.txt"), 7)


def main():
    make_fixture()
    if os.path.exists(DB):
        os.remove(DB)
    sc = Scanner(DB)
    sc.scan([FIXTURE], [])
    app = gui.App(db_path=DB, selftest=True)
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print(("[OK]   " if cond else "[FAIL] ") + name + (
            "" if cond else "  <- " + str(detail)))
        ok = ok and bool(cond)

    roots = app.roots
    check("扫描根 = 1 个", len(roots) == 1, roots)
    root = roots[0]
    kids = app.tv.get_children(root)
    names = [app.tv.item(i, "text") for i in kids]
    # 期望顺序：按大小降序的目录，最后是文件容器
    dirs = sorted(app.child_map.get(root, []),
                  key=lambda d: -app.totals[d])
    expect = [os.path.basename(d.rstrip("\\/")) for d in dirs]
    got = [n for n in names if not n.startswith("[")]
    check("一级目录按大小降序", got == expect, (got, expect))
    check("一级直属文件容器存在",
          ("files::" + root) in kids, names)

    files_iid = "files::" + root
    app._fill_files(files_iid, root)
    file_rows = app.tv.get_children(files_iid)
    check("文件容器列出的直属文件数 = 1", len(file_rows) == 1, file_rows)
    check("文件行文本 = top1.bin",
          file_rows and app.tv.item(file_rows[0], "text") == "top1.bin",
          file_rows)

    a = os.path.join(root, "a")
    same_a = os.path.join(a, "same")
    b = os.path.join(root, "b")
    same_b = os.path.join(b, "same")
    app._fill_dir(a, a, sync=True)
    app._fill_dir(b, b, sync=True)
    app._fill_dir(same_a, same_a, sync=True)
    app._fill_dir(same_b, same_b, sync=True)
    app._fill_files("files::" + same_a, same_a)
    app._fill_files("files::" + same_b, same_b)
    fa = app.tv.get_children("files::" + same_a)
    fb = app.tv.get_children("files::" + same_b)
    check("同名文件在 a/same 与 b/same 各自独立",
          bool(fa) and bool(fb) and fa != fb
          and len(fa) == 1 and len(fb) == 1, (fa, fb))
    check("同名文件大小不同（10 vs 20）",
          app.tv.item(fa[0], "values")[0] != app.tv.item(fb[0], "values")[0])

    empty = os.path.join(a, "empty")
    app._fill_dir(empty, empty, sync=True)
    check("空目录无子节点", not app.tv.get_children(empty),
          app.tv.get_children(empty))
    check("空目录大小 0", app.totals.get(empty, None) == 0,
          app.totals.get(empty))

    deep = os.path.join(root, "c", "深", "层", "目录", "deep.txt")
    check("深层中文路径在库中", app._conn().execute(
        "SELECT COUNT(*) FROM files WHERE path=?", (deep,)).fetchone()[0] == 1)

    # 父子求和一致性
    bad = []
    for d, t in app.totals.items():
        s = app.direct.get(d, 0) + sum(app.totals.get(c, 0)
                                       for c in app.child_map.get(d, []))
        if s != t:
            bad.append((d, t, s))
    check("所有目录：总大小 = 直属 + 子目录之和", not bad, bad[:3])

    # 重复展开不重复插入
    n1 = len(app.tv.get_children(a))
    app._fill_dir(a, a, sync=True)
    n2 = len(app.tv.get_children(a))
    check("重复展开不重复插入", n1 == n2, (n1, n2))

    app._show_details(a)
    check("详情区显示路径", app.det_vars["完整路径"].get() == a,
          app.det_vars["完整路径"].get())
    check("详情区占父级含分母", "分母" in app.det_vars["占父级"].get(),
          app.det_vars["占父级"].get())

    longdir = os.path.join(root, "long")
    app._locate(deep)
    check("定位到深层文件后可见", app.tv.exists(deep))

    app.destroy()
    shutil.rmtree(FIXTURE, ignore_errors=True)
    try:
        os.remove(DB)
    except OSError:
        pass
    print("RESULT:", "PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
