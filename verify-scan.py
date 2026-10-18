# -*- coding: utf-8 -*-
"""verify-scan.py -- 用独立遍历复核数据库里的文件数与总大小
用法: python verify-scan.py <数据库路径> <根目录>
对照 os.walk 的独立结果，报告差异（前 20 条）。
"""
import os
import sys
import sqlite3


def walk_counts(root):
    n = 0
    b = 0
    paths = set()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for f in filenames:
            p = os.path.join(dirpath, f)
            if os.path.islink(p):
                continue
            try:
                st = os.stat(p, follow_symlinks=False)
            except OSError:
                continue
            n += 1
            b += st.st_size
            paths.add(os.path.abspath(os.path.normpath(p)))
    return n, b, paths


def main():
    db_path = sys.argv[1]
    root = sys.argv[2]
    con = sqlite3.connect(db_path)
    dbn, dbb = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(size),0) FROM files").fetchone()
    db_paths = set(os.path.abspath(r[0]) for r in con.execute("SELECT path FROM files"))
    n, b, w_paths = walk_counts(root)
    print("db   files=%d size=%d" % (dbn, dbb))
    print("walk files=%d size=%d" % (n, b))
    print("count-match=%s size-match=%s" % (dbn == n, dbb == b))
    only_db = sorted(db_paths - w_paths)[:20]
    only_walk = sorted(w_paths - db_paths)[:20]
    if only_db:
        print("only-in-db:")
        for p in only_db:
            print("  ", p)
    if only_walk:
        print("only-in-walk:")
        for p in only_walk:
            print("  ", p)


if __name__ == "__main__":
    main()
