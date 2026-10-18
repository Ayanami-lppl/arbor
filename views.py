# -*- coding: utf-8 -*-
"""views.py -- v0.2 视图工具，全部只读
  python views.py tree   <数据库> [路径] [--depth N]   目录占比树（# 比例条）
  python views.py hidden <数据库> [--out 文件] [--top N]  隐藏文件专项报告
  python views.py ads    <根目录> [--out 文件]          NTFS 备用数据流(ADS)检测
"""
import os
import sys
import ctypes
import sqlite3
import argparse
from collections import defaultdict

from analyze import dir_totals, area_of

VERSION = "v0.2"


def human(n):
    n = int(n)
    if n < 1024:
        return "%d B" % n
    x = float(n)
    for unit in ("KB", "MB", "GB", "TB"):
        x /= 1024
        if x < 1024 or unit == "TB":
            return "%.1f %s" % (x, unit)


def bar(size, total):
    if total <= 0:
        return ""
    n = int(round(size * 40.0 / total))
    return "#" * n


# ---------------- tree ----------------

def cmd_tree(db_path, focus, depth):
    totals = dir_totals(db_path)
    roots = [d for d in totals
             if os.path.dirname(d) in (d, "") or os.path.dirname(d) not in totals]
    if focus:
        f = os.path.normpath(focus)
        kids = sorted((d for d in totals
                       if os.path.dirname(d) == f and d != f),
                      key=lambda d: -totals[d])
        total_all = sum(totals[d] for d in kids)
        print("目录 %s 的直接子目录（合计 %s）" % (f, human(total_all)))
        for d in kids[:60]:
            s = totals.get(d, 0)
            pct = (s * 100.0 / total_all) if total_all else 0.0
            print("%-70s %12s %6.1f%% %s" % (d, human(s), pct, bar(s, total_all)))
        return
    for r in roots:
        kids = sorted((d for d in totals
                       if os.path.dirname(d) == r and d != r),
                      key=lambda d: -totals[d])
        total_r = totals.get(r, 0)
        print("== %s 一级目录（本盘合计 %s）==" % (r, human(total_r)))
        for d in kids[:25]:
            s = totals.get(d, 0)
            pct = (s * 100.0 / total_r) if total_r else 0.0
            print("%-70s %12s %6.1f%% %s" % (d, human(s), pct, bar(s, total_r)))


# ---------------- hidden ----------------

def cmd_hidden(db_path, out_path, top_n):
    con = sqlite3.connect(db_path)
    try:
        totals = dir_totals(db_path)
        h_files = list(con.execute(
            "SELECT path, size, ext FROM files WHERE hidden=1"))
        h_dirs = [d for (d,) in con.execute(
            "SELECT path FROM dirs WHERE hidden=1")
            if not (len(d) == 3 and d[1] == ":")]
        h_dirs_sized = [(d, totals.get(d, 0)) for d in h_dirs]
        h_dirs_sized.sort(key=lambda kv: -kv[1])

        ext_stats = defaultdict(lambda: [0, 0])
        for _, size, ext in h_files:
            ext_stats[ext or "(无扩展名)"][0] += 1
            ext_stats[ext or "(无扩展名)"][1] += size

        area_stats = defaultdict(lambda: [0, 0])
        for path, size, _ in h_files:
            a = area_of(path)
            area_stats[a][0] += 1
            area_stats[a][1] += size

        top_files = sorted(h_files, key=lambda r: -r[1])[:top_n]

        lines = []
        lines.append("隐藏文件专项报告 %s" % VERSION)
        lines.append("数据文件: %s" % os.path.abspath(db_path))
        lines.append("")
        lines.append("一、总体")
        lines.append("隐藏文件总数: %s 个" % format(len(h_files), ","))
        lines.append("隐藏文件总大小: %s" % human(sum(r[1] for r in h_files)))
        lines.append("隐藏目录总数: %s 个" % format(len(h_dirs), ","))
        lines.append("")
        lines.append("二、最大的 %d 个隐藏目录" % min(top_n, len(h_dirs_sized)))
        for d, s in h_dirs_sized[:top_n]:
            lines.append("%-80s %12s" % (d, human(s)))
        lines.append("")
        lines.append("三、最大的 %d 个隐藏文件" % min(top_n, len(top_files)))
        for p, s, _ in top_files:
            lines.append("%-80s %12s" % (p, human(s)))
        lines.append("")
        lines.append("四、隐藏文件按扩展名")
        for e, (cnt, s) in sorted(ext_stats.items(), key=lambda kv: -kv[1][1]):
            lines.append("%-16s %10s 个 %12s" % (e, format(cnt, ","), human(s)))
        lines.append("")
        lines.append("五、隐藏文件按区域")
        for a, (cnt, s) in sorted(area_stats.items(), key=lambda kv: -kv[1][1]):
            lines.append("%-30s %10s 个 %12s" % (a, format(cnt, ","), human(s)))
        lines.append("")
        lines.append("说明: 只读统计；隐藏=资源管理器勾选「隐藏的项目」才可见。")
        text = "\n".join(lines) + "\n"
        if out_path:
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(text)
            print("hidden-report written: %s" % out_path)
        else:
            print(text)
    finally:
        con.close()


# ---------------- ads ----------------

class WIN32_FIND_STREAM_DATA(ctypes.Structure):
    _fields_ = [
        ("StreamSize", ctypes.c_int64),
        ("cStreamName", ctypes.c_wchar * 296),
    ]


_k32 = ctypes.windll.kernel32
_k32.FindFirstStreamW.argtypes = [
    ctypes.c_wchar_p, ctypes.c_int,
    ctypes.POINTER(WIN32_FIND_STREAM_DATA), ctypes.c_uint]
_k32.FindFirstStreamW.restype = ctypes.c_void_p
_k32.FindNextStreamW.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(WIN32_FIND_STREAM_DATA)]
_k32.FindNextStreamW.restype = ctypes.c_int
_k32.FindClose.argtypes = [ctypes.c_void_p]
INVALID_HV = ctypes.c_void_p(-1).value


def file_streams(path):
    """返回 [(流名, 大小)]，不含默认流 '::$DATA'。"""
    data = WIN32_FIND_STREAM_DATA()
    h = _k32.FindFirstStreamW(path, 0, ctypes.byref(data), 0)
    if h == INVALID_HV:
        return None
    out = []
    try:
        while True:
            name = data.cStreamName
            if name != "::$DATA":
                out.append((name, data.StreamSize))
            if not _k32.FindNextStreamW(h, ctypes.byref(data)):
                break
    finally:
        _k32.FindClose(h)
    return out


def cmd_ads(root, out_path):
    lines = []
    lines.append("NTFS 备用数据流(ADS)检测报告 %s" % VERSION)
    lines.append("扫描范围: %s" % os.path.abspath(root))
    lines.append("")
    n_files = 0
    n_with_ads = 0
    n_streams = 0
    total_bytes = 0
    root_ext = "\\\\?\\" + os.path.abspath(root)
    stack = [root_ext]
    records = []
    while stack:
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for e in it:
                try:
                    if e.is_symlink():
                        continue
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
                        continue
                except OSError:
                    continue
                n_files += 1
                streams = file_streams(e.path)
                if streams is None:
                    continue
                if streams:
                    n_with_ads += 1
                    n_streams += len(streams)
                    for name, size in streams:
                        total_bytes += size
                        disp = e.path[4:] if e.path.startswith("\\\\?\\") else e.path
                        records.append((disp, name, size))
        if n_files % 2000 == 0:
            print("ads files=%d with-ads=%d streams=%d size=%s" % (
                n_files, n_with_ads, n_streams, human(total_bytes)), flush=True)
    records.sort(key=lambda r: -r[2])
    lines.append("扫描文件数: %s 个" % format(n_files, ","))
    lines.append("带 ADS 的文件: %s 个" % format(n_with_ads, ","))
    lines.append("ADS 流总数: %s 个" % format(n_streams, ","))
    lines.append("ADS 总大小: %s" % human(total_bytes))
    lines.append("")
    lines.append("明细（按流大小排序，前 200 条）:")
    for path, name, size in records[:200]:
        lines.append("%s  %s  %s" % (path, name, human(size)))
    text = "\n".join(lines) + "\n"
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
        print("ads-report written: %s" % out_path)
    else:
        print(text)


# ---------------- main ----------------

def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="disk-inspector v0.2 视图工具（只读）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_tree = sub.add_parser("tree", help="目录占比树")
    p_tree.add_argument("db")
    p_tree.add_argument("focus", nargs="?", help="只看这个目录的直接子目录")
    p_tree.add_argument("--depth", type=int, default=1)

    p_hidden = sub.add_parser("hidden", help="隐藏文件专项报告")
    p_hidden.add_argument("db")
    p_hidden.add_argument("--out")
    p_hidden.add_argument("--top", type=int, default=50)

    p_ads = sub.add_parser("ads", help="ADS 备用数据流检测")
    p_ads.add_argument("root")
    p_ads.add_argument("--out")

    args = ap.parse_args()
    if args.cmd == "tree":
        cmd_tree(args.db, args.focus, args.depth)
    elif args.cmd == "hidden":
        cmd_hidden(args.db, args.out, args.top)
    elif args.cmd == "ads":
        cmd_ads(args.root, args.out)


if __name__ == "__main__":
    main()
