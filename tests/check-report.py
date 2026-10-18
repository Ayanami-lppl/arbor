# -*- coding: utf-8 -*-
"""tests/check-report.py -- 全盘扫描后的综合核对（只读）
用法: python tests/check-report.py <数据库>
打印：总体、各盘一级目录大小、错误分布、前 15 个不可访问路径。
"""
import os
import sys
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analyze import dir_totals


def human(n):
    n = int(n)
    if n < 1024:
        return "%d B" % n
    x = float(n)
    for unit in ("KB", "MB", "GB", "TB"):
        x /= 1024
        if x < 1024 or unit == "TB":
            return "%.1f %s" % (x, unit)


def main():
    db = sys.argv[1]
    con = sqlite3.connect(db)
    meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
    print("== meta ==")
    for k in ("start", "elapsed_sec", "n_files", "n_dirs", "n_bytes",
              "n_hidden", "hidden_bytes", "n_system", "n_errors", "n_links"):
        print("  %s = %s" % (k, meta.get(k, "?")))
    totals = dir_totals(db)
    roots = sorted(d for d in totals
                   if os.path.dirname(d) in (d, "")
                   or os.path.dirname(d) not in totals)
    print("== 各盘一级目录（按大小）==")
    for r in roots:
        kids = sorted((d for d in totals if os.path.dirname(d) == r),
                      key=lambda d: -totals[d])
        for d in kids[:15]:
            print("  %-55s %12s" % (d, human(totals[d])))
    print("== errors 分布 ==")
    for reason, n in con.execute(
            "SELECT reason, COUNT(*) AS n FROM errors "
            "GROUP BY reason ORDER BY n DESC LIMIT 6"):
        print("  %5d  %s" % (n, reason))
    print("== 前 15 个不可访问路径 ==")
    for p, r in con.execute("SELECT path, reason FROM errors LIMIT 15"):
        print("  %s  <-  %s" % (p, r))


if __name__ == "__main__":
    main()
