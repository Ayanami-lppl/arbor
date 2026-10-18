# -*- coding: utf-8 -*-
"""disk-inspector v0.6 -- 只读磁盘扫描器
用法: python scan.py [--db 路径] [--report 路径] [--exclude 路径]... 根目录 [根目录...]
只读取文件元数据，不做任何修改或删除。
"""
import os
import sys
import time
import sqlite3
import ctypes
import argparse
from collections import defaultdict
from datetime import datetime

VERSION = "v0.6"

FA_READONLY = 0x1
FA_HIDDEN = 0x2
FA_SYSTEM = 0x4
INVALID_ATTRS = 0xFFFFFFFF

# 重解析点标签：junction 必须跳过，否则会重复计数（能读时）或污染 errors（无权时）
IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003
IO_REPARSE_TAG_SYMLINK = 0xA000000C

_kernel32 = ctypes.windll.kernel32


def get_attrs(path):
    a = _kernel32.GetFileAttributesW(path)
    if a == INVALID_ATTRS:
        return None
    return a


def human(n):
    n = int(n)
    if n < 1024:
        return "%d B" % n
    x = float(n)
    for unit in ("KB", "MB", "GB", "TB"):
        x /= 1024
        if x < 1024 or unit == "TB":
            return "%.1f %s" % (x, unit)


def root_display(root):
    """统一所有根目录的显示形式（数据库里存这个形式）。"""
    a = os.path.abspath(os.path.normpath(root))
    if len(a) == 3 and a[1] == ":":
        return a  # C:\ 这种，保留末尾反斜杠
    if len(a) == 2 and a[1] == ":":
        return a + "\\"
    return a.rstrip("\\/")


class Scanner:
    def __init__(self, db_path):
        self.db_path = db_path
        db_dir = os.path.dirname(os.path.abspath(db_path))
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        self.con = sqlite3.connect(db_path)
        cur = self.con.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS files(
                path TEXT PRIMARY KEY, size INTEGER, mtime REAL,
                ctime REAL, atime REAL, hidden INTEGER,
                system INTEGER, readonly INTEGER, ext TEXT);
            CREATE TABLE IF NOT EXISTS dirs(
                path TEXT PRIMARY KEY, hidden INTEGER, system INTEGER);
            CREATE TABLE IF NOT EXISTS errors(path TEXT, reason TEXT);
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
            """
        )
        self.con.commit()
        self.file_rows = []
        self.dir_rows = []
        self.err_rows = []
        self.n_files = 0
        self.n_dirs = 0
        self.n_bytes = 0
        self.n_hidden = 0
        self.hidden_bytes = 0
        self.n_system = 0
        self.n_errors = 0
        self.n_links = 0
        self.t0 = time.time()

    def _flush(self):
        cur = self.con.cursor()
        cur.executemany(
            "INSERT OR IGNORE INTO files VALUES(?,?,?,?,?,?,?,?,?)",
            self.file_rows)
        cur.executemany(
            "INSERT OR IGNORE INTO dirs VALUES(?,?,?)", self.dir_rows)
        cur.executemany(
            "INSERT INTO errors VALUES(?,?)", self.err_rows)
        self.con.commit()
        self.file_rows = []
        self.dir_rows = []
        self.err_rows = []

    def scan(self, roots, excludes):
        for root in roots:
            self._scan_root(root, excludes)
        self._flush()
        return self._meta()

    def _scan_root(self, root, excludes):
        disp_base = root_display(root).rstrip("\\/")
        abs_root = os.path.abspath(root)
        if len(abs_root) == 3 and abs_root[1] == ":":
            root_ext = "\\\\?\\" + abs_root          # 盘符根保留尾反斜杠
        else:
            root_ext = "\\\\?\\" + abs_root.rstrip("\\/")
        prefix_len = len(root_ext)
        ex = [root_display(e).lower() for e in excludes]
        stack = [root_ext]
        while stack:
            d = stack.pop()
            suffix = d[prefix_len:]
            if suffix:
                disp = disp_base + (suffix if suffix[0] == "\\" else "\\" + suffix)
            elif len(disp_base) == 2 and disp_base[1] == ":":
                disp = disp_base + "\\"
            else:
                disp = disp_base
            dl = disp.rstrip("\\/").lower()
            if any(dl == e.rstrip("\\/") for e in ex):
                continue
            da = get_attrs(d)
            self.dir_rows.append((
                disp,
                int(bool(da & FA_HIDDEN)) if da is not None else 0,
                int(bool(da & FA_SYSTEM)) if da is not None else 0))
            self.n_dirs += 1
            try:
                it = os.scandir(d)
            except OSError as oserr:
                self.err_rows.append((disp, "open-dir: %s" % oserr))
                self.n_errors += 1
                continue
            with it:
                for e in it:
                    suffix_e = e.path[prefix_len:]
                    edisp = disp_base + (
                        suffix_e if suffix_e[:1] == "\\" else "\\" + suffix_e)
                    try:
                        st = e.stat(follow_symlinks=False)
                        tag = getattr(st, "st_reparse_tag", 0)
                        if e.is_symlink() or tag in (
                                IO_REPARSE_TAG_MOUNT_POINT,
                                IO_REPARSE_TAG_SYMLINK):
                            self.n_links += 1
                            continue
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                            continue
                    except OSError as oserr:
                        self.err_rows.append((edisp, "stat: %s" % oserr))
                        self.n_errors += 1
                        continue
                    a = get_attrs(e.path)
                    if a is not None:
                        hidden = int(bool(a & FA_HIDDEN))
                        system = int(bool(a & FA_SYSTEM))
                        readonly = int(bool(a & FA_READONLY))
                    else:
                        hidden = system = readonly = 0
                    ext = os.path.splitext(e.name)[1].lower()
                    self.file_rows.append((
                        edisp, st.st_size, st.st_mtime, st.st_ctime,
                        st.st_atime, hidden, system, readonly, ext))
                    self.n_files += 1
                    self.n_bytes += st.st_size
                    if hidden:
                        self.n_hidden += 1
                        self.hidden_bytes += st.st_size
                    if system:
                        self.n_system += 1
                    if len(self.file_rows) >= 5000:
                        self._flush()
                        self._progress()

    def _progress(self):
        elapsed = time.time() - self.t0
        print("files=%d dirs=%d size=%s hidden=%d denied=%d elapsed=%ds" % (
            self.n_files, self.n_dirs, human(self.n_bytes),
            self.n_hidden, self.n_errors, int(elapsed)), flush=True)

    def _meta(self):
        elapsed = time.time() - self.t0
        cur = self.con.cursor()
        cur.executemany("INSERT INTO meta VALUES(?,?)", [
            ("version", VERSION),
            ("start", datetime.now().isoformat(timespec="seconds")),
            ("elapsed_sec", "%.1f" % elapsed),
            ("n_files", str(self.n_files)),
            ("n_dirs", str(self.n_dirs)),
            ("n_bytes", str(self.n_bytes)),
            ("n_hidden", str(self.n_hidden)),
            ("hidden_bytes", str(self.hidden_bytes)),
            ("n_system", str(self.n_system)),
            ("n_errors", str(self.n_errors)),
            ("n_links", str(self.n_links)),
        ])
        self.con.commit()
        return dict(
            n_files=self.n_files, n_dirs=self.n_dirs, n_bytes=self.n_bytes,
            n_hidden=self.n_hidden, hidden_bytes=self.hidden_bytes,
            n_system=self.n_system, n_errors=self.n_errors,
            n_links=self.n_links, elapsed=elapsed)


def build_report(db_path, roots, meta, report_path):
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    dirs = set(r[0] for r in cur.execute("SELECT path FROM dirs"))
    direct = defaultdict(int)
    for path, size in cur.execute("SELECT path, size FROM files"):
        parent = os.path.dirname(path)
        direct[parent] += size
        dirs.add(parent)
    children = defaultdict(list)
    for d in dirs:
        p = os.path.dirname(d)
        if p and p != d:
            children[p].append(d)
    total = {}
    stack = [(r, False) for r in roots]
    visited = set()
    while stack:
        d, done = stack.pop()
        if done:
            total[d] = direct.get(d, 0) + sum(total.get(c, 0) for c in children[d])
            continue
        if d in visited:
            continue
        visited.add(d)
        stack.append((d, True))
        for c in children[d]:
            if c not in visited:
                stack.append((c, False))

    ext_stats = defaultdict(lambda: [0, 0])
    for ext, size in cur.execute("SELECT ext, size FROM files"):
        ext_stats[ext][0] += 1
        ext_stats[ext][1] += size

    top_dirs = sorted(
        ((d, total[d]) for d in total if d not in roots),
        key=lambda kv: -kv[1])[:20]
    top_ext = sorted(
        ((e, s[0], s[1]) for e, s in ext_stats.items()),
        key=lambda kv: -kv[2])[:15]

    err_rows = list(cur.execute("SELECT path, reason FROM errors LIMIT 50"))
    n_errors = meta["n_errors"]

    lines = []
    lines.append("磁盘扫描报告 %s" % VERSION)
    lines.append("生成时间: %s（本地时间）" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("扫描范围: %s" % " ; ".join(roots))
    lines.append("耗时: %d 秒" % int(meta["elapsed"]))
    lines.append("数据文件: %s" % os.path.abspath(db_path))
    lines.append("")
    lines.append("一、总体")
    lines.append("文件总数: %s 个" % format(meta["n_files"], ","))
    lines.append("目录总数: %s 个" % format(meta["n_dirs"], ","))
    lines.append("总大小（逻辑大小）: %s" % human(meta["n_bytes"]))
    lines.append("隐藏文件: %s 个，共 %s" % (
        format(meta["n_hidden"], ","), human(meta["hidden_bytes"])))
    lines.append("系统属性文件: %s 个" % format(meta["n_system"], ","))
    lines.append("无法访问: %s 处（前 50 处见附录）" % format(n_errors, ","))
    lines.append("未跟踪的链接/联接点: %s 个" % format(meta["n_links"], ","))
    lines.append("")
    lines.append("二、各盘一级目录占比")
    for root in roots:
        kids = sorted(children[root], key=lambda d: -total.get(d, 0))
        for d in kids[:25]:
            lines.append("%-60s %12s" % (d, human(total.get(d, 0))))
    lines.append("")
    lines.append("三、最大的 20 个文件夹")
    for d, s in top_dirs:
        lines.append("%-80s %12s" % (d, human(s)))
    lines.append("")
    lines.append("四、按文件类型（前 15 名，按总大小）")
    for e, cnt, s in top_ext:
        name = e if e else "(无扩展名)"
        lines.append("%-16s %10s 个 %12s" % (name, format(cnt, ","), human(s)))
    lines.append("")
    lines.append("五、说明")
    lines.append("- 大小按文件逻辑大小计算，不等于磁盘实际占用（受簇大小影响，小文件会偏小）")
    lines.append("- 链接/联接点未跟踪，避免重复计数（如 AppData 的别名目录）")
    lines.append("- 访问时间(atime)在 Windows 上经常不更新，仅供参考")
    lines.append("- 本扫描全程只读，没有修改、移动或删除任何文件")
    if err_rows:
        lines.append("")
        lines.append("附录：无法访问的路径（前 50 处，其余在数据库 errors 表里）")
        for p, reason in err_rows:
            lines.append("%s  <-  %s" % (p, reason))

    text = "\n".join(lines) + "\n"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(text)
    con.close()
    return report_path


def main():
    ap = argparse.ArgumentParser(description="disk-inspector v0.1 只读扫描器")
    ap.add_argument("--db", help="数据库输出路径，默认 data/scan-时间戳.sqlite")
    ap.add_argument("--report", help="报告输出路径，默认与数据库同名 -report.txt")
    ap.add_argument("--exclude", action="append", default=[],
                    help="跳过这些目录（可重复），例如扫描输出的 data 目录")
    ap.add_argument("roots", nargs="+", help="要扫描的盘符或目录，如 C:\\ D:\\")
    args = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    db_path = args.db or os.path.join("data", "scan-%s.sqlite" % stamp)
    report_path = args.report or os.path.splitext(db_path)[0] + "-report.txt"
    roots = [root_display(r) for r in args.roots]

    print("SCAN-START roots=%s db=%s" % (";".join(roots), db_path), flush=True)
    scanner = Scanner(db_path)
    meta = scanner.scan(args.roots, args.exclude)
    rp = build_report(db_path, roots, meta, report_path)
    print("SCAN-DONE files=%d dirs=%d size=%s hidden=%d denied=%d elapsed=%ds" % (
        meta["n_files"], meta["n_dirs"], human(meta["n_bytes"]),
        meta["n_hidden"], meta["n_errors"], int(meta["elapsed"])), flush=True)
    print("REPORT=%s" % rp, flush=True)


if __name__ == "__main__":
    main()
