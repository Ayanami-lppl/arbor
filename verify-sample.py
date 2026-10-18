# -*- coding: utf-8 -*-
"""verify-sample.py -- 从数据库随机抽样 N 个目录，独立重走一遍复核
用法: python verify-sample.py <数据库路径> [抽样数，默认 30]
对每个目录比对：文件数、总大小、隐藏文件数。
"""
import os
import random
import sqlite3
import sys

from scan import get_attrs, FA_HIDDEN


def walk_dir(d):
    n = 0
    b = 0
    nh = 0
    try:
        for dirpath, dirnames, filenames in os.walk(d, followlinks=False):
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
                a = get_attrs(p)
                if a is not None and (a & FA_HIDDEN):
                    nh += 1
        return n, b, nh, None
    except OSError as ex:
        return 0, 0, 0, str(ex)


def main():
    db_path = sys.argv[1]
    n_sample = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    con = sqlite3.connect(db_path)
    dirs = [r[0] for r in con.execute("SELECT path FROM dirs")]
    if len(dirs) > n_sample:
        sample = random.sample(dirs, n_sample)
    else:
        sample = dirs
    ok = 0
    bad = 0
    denied = 0
    for d in sample:
        prefix = d.rstrip("\\/") + "\\"
        dbn, dbb, dbh = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(size),0),"
            " COALESCE(SUM(hidden),0) FROM files WHERE path LIKE ?",
            (prefix + "%",)).fetchone()
        n, b, nh, err = walk_dir(d)
        if err:
            denied += 1
            print("SKIP(denied) %s  <-  %s" % (d, err))
            continue
        if (n, b, nh) == (dbn, dbb, dbh):
            ok += 1
        else:
            bad += 1
            print("MISMATCH %s" % d)
            print("    db   files=%d size=%d hidden=%d" % (dbn, dbb, dbh))
            print("    walk files=%d size=%d hidden=%d" % (n, b, nh))
    print("sample=%d ok=%d mismatch=%d walk-denied=%d" % (
        len(sample), ok, bad, denied))


if __name__ == "__main__":
    main()
