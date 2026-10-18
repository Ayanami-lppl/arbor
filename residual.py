# -*- coding: utf-8 -*-
"""residual.py -- v0.3 疑似残留识别（只读，只标疑似，绝不删除）
用法: python residual.py <数据库> [--out 报告.txt]
把 AppData/ProgramData/Program Files/D盘根 等目录与系统卸载列表比对，
找出"没有卸载记录却还占着地方"的目录。
GUI 与报告共用 analyze()。
"""
import os
import re
import sys
import sqlite3
import argparse
import winreg
from collections import defaultdict

from analyze import dir_totals

VERSION = "v0.6"

UNINSTALL_PATHS = [
    (winreg.HKEY_LOCAL_MACHINE,
     r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_LOCAL_MACHINE,
     r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_CURRENT_USER,
     r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
]

# 直接跳过、不参与比对的名字（系统机制、运行库、UWP、公共目录）
WHITELIST = {
    "microsoft", "windows", "windowsapps", "packages", "package cache",
    "temp", "crashdumps", "elevateddiagnostics", "deliveryoptimization",
    "pip", "cache", "npm", "npm-cache", "pnpm", "python", "pypa", "uv", "nodejs",
    "start menu", "desktop", "documents", "downloads", "music", "pictures",
    "videos", "favorites", "links", "onedrive", "recent", "saved games",
    "searches", "contacts", "3d objects", "internet explorer",
    "windowsdefenderadvancedthreatprotection", "windowsdefender",
    "windowssecurity", "windowsmail", "windowsphotos", "windowsphotoviewer",
    "common files", "microsoft shared",
}

# 中英文厂商名对应（发布商字段常是中文，目录名常是英文）
VENDOR_ALIASES = {
    "tencent": ("腾讯",),
    "netease": ("网易",),
    "alibaba": ("阿里",),
    "baidu": ("百度",),
    "xiaomi": ("小米",),
    "huawei": ("华为",),
    "sogou": ("搜狗",),
    "bytedance": ("字节", "抖音"),
    "kingsoft": ("金山",),
    "qiyi": ("爱奇艺",),
    "youku": ("优酷",),
}


def human(n):
    n = int(n)
    if n < 1024:
        return "%d B" % n
    x = float(n)
    for unit in ("KB", "MB", "GB", "TB"):
        x /= 1024
        if x < 1024 or unit == "TB":
            return "%.1f %s" % (x, unit)


_WORD_SPLIT = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")


def norm(s):
    s = s.lower()
    s = _WORD_SPLIT.sub("", s)
    s = re.sub(r"(v?\d+(\.\d+)+)$", "", s)
    return s


def tokens(s):
    return {t for t in _WORD_SPLIT.split(s.lower()) if t}


WHITELIST_NORM = {norm(x) for x in WHITELIST}


def load_uninstall():
    entries = []
    seen = set()
    for root, path in UNINSTALL_PATHS:
        try:
            key = winreg.OpenKey(root, path)
        except OSError:
            continue
        try:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(key, i)
                    i += 1
                except OSError:
                    break
                try:
                    k = winreg.OpenKey(key, sub)
                    try:
                        def get(v):
                            try:
                                val, _ = winreg.QueryValueEx(k, v)
                                return str(val).strip()
                            except OSError:
                                return ""
                        dn = get("DisplayName")
                        loc = get("InstallLocation")
                        pub = get("Publisher")
                    finally:
                        winreg.CloseKey(k)
                except OSError:
                    continue
                if dn:
                    sig = (dn.lower(), loc.lower())
                    if sig not in seen:
                        seen.add(sig)
                        entries.append((dn, loc, pub))
        finally:
            winreg.CloseKey(key)
    return entries


def load_dirs(db_path):
    con = sqlite3.connect(db_path)
    try:
        return [r[0] for r in con.execute("SELECT path FROM dirs")]
    finally:
        con.close()


def classify(path):
    """返回 (区域, 厂商, 应用名)，应用名为 None 表示这是厂商级目录。
    路径保留数据库里的原始大小写。"""
    p = path.lower()
    prof = os.path.expanduser("~").lower() + "\\"
    base_roots = [
        (prof + "appdata\\local\\", "AppData\\Local"),
        (prof + "appdata\\roaming\\", "AppData\\Roaming"),
        ("c:\\programdata\\", "ProgramData"),
        ("c:\\program files (x86)\\", "Program Files (x86)"),
        ("c:\\program files\\", "Program Files"),
    ]
    for prefix, area in base_roots:
        if not p.startswith(prefix):
            continue
        rest = path[len(prefix):]
        parts = [x for x in rest.split("\\") if x]
        if not parts:
            return None
        vendor = parts[0]
        app = parts[1] if len(parts) > 1 else None
        return area, vendor, app
    if p.startswith("d:\\"):
        rest = path[3:]
        parts = [x for x in rest.split("\\") if x]
        if len(parts) == 1:
            return "D盘根目录", parts[0], None
    return None


def prepare_entries(entries):
    """把卸载记录预处理成可快速比对的形态。"""
    prepared = []
    publisher_tokens = set()
    publisher_texts = []
    for dn, loc, pub in entries:
        prepared.append({
            "dn_n": norm(dn),
            "loc_n": norm(loc),
            "loc_segs": {norm(x) for x in re.split(r"[\\/]+", loc) if x},
            "dn_tokens": tokens(dn),
            "loc_tokens": tokens(loc),
        })
        publisher_tokens |= tokens(pub)
        publisher_texts.append(norm(pub))
    return prepared, publisher_tokens, publisher_texts


def match_score(prep, cand_n):
    """0=不沾边；1=宽松包含（旧口径）；2=词边界命中；3=路径段或整名命中。"""
    if not cand_n:
        return 0
    if cand_n == prep["dn_n"] or cand_n in prep["loc_segs"]:
        return 3
    if cand_n in prep["dn_tokens"] or cand_n in prep["loc_tokens"]:
        return 2
    # 短名字不做宽松包含，避免 "AI" 命中一切含 ai 的软件名
    if len(cand_n) < 4:
        return 0
    if prep["loc_n"] and (cand_n in prep["loc_n"] or prep["loc_n"] in cand_n):
        return 1
    if prep["dn_n"] and (cand_n in prep["dn_n"] or prep["dn_n"] in cand_n):
        return 1
    return 0


def vendor_installed(vendor_n, prepared, pub_tokens, pub_texts):
    """同一个厂商是否有安装记录（用于给残留行标置信度）。"""
    if not vendor_n:
        return False
    if vendor_n in pub_tokens:
        return True
    aliases = VENDOR_ALIASES.get(vendor_n, ())
    if aliases and any(alias in p for p in pub_texts for alias in aliases):
        return True
    if len(vendor_n) >= 2 and any(
            vendor_n in p or p in vendor_n for p in pub_texts if p):
        return True
    for p in prepared:
        if vendor_n in p["dn_n"] or vendor_n in p["loc_n"]:
            return True
    return False


def analyze(db_path, totals=None, dirs=None):
    """核心分析。返回 dict，供 GUI 与报告共用。

    totals / dirs 可由调用方传入（GUI 已聚合过一次，避免重复整库遍历）。
    """
    entries = load_uninstall()
    prepared, pub_tokens, pub_texts = prepare_entries(entries)
    if dirs is None:
        dirs = load_dirs(db_path)
    if totals is None:
        totals = dir_totals(db_path)

    candidates = []        # (db路径, 区域, 厂商, 应用名)
    vendor_dirs = {}       # (区域, 厂商) -> db路径
    for d in dirs:
        c = classify(d)
        if c is None:
            continue
        area, vendor, app = c
        if app is None:
            if vendor and norm(vendor) not in WHITELIST_NORM:
                vendor_dirs[(area, vendor)] = d
            continue
        if norm(vendor) in WHITELIST_NORM or norm(app) in WHITELIST_NORM:
            continue
        candidates.append((d, area, vendor, app))

    # 厂商目录本身能对上安装记录 → 它下面的子目录不再单独标残留
    # （如 Roaming\Cursor\CachedData 会被误当成一个独立软件）
    vendor_matched = set()
    for (area, vendor) in vendor_dirs:
        if max((match_score(p, norm(vendor)) for p in prepared), default=0):
            vendor_matched.add((area, norm(vendor)))

    residual_all = []
    n_matched = 0
    n_matched_loose = 0
    n_vendor_suppressed = 0
    for d, area, vendor, app in candidates:
        cand_n = norm(app) if app else norm(vendor)
        best = max((match_score(p, cand_n) for p in prepared), default=0)
        if best:
            n_matched += 1
            if best == 1:
                n_matched_loose += 1
            continue
        size = totals.get(d, 0)
        if size <= 0:
            continue
        if (area, norm(vendor)) in vendor_matched:
            n_vendor_suppressed += 1
            continue
        conf = "中" if vendor_installed(
            norm(vendor), prepared, pub_tokens, pub_texts) else "高"
        residual_all.append((d, size, area, vendor, app, conf))

    # 同一软件的子目录匹配结果必然相同，只保留最浅的一层，避免同一软件刷几十行
    by_app = {}
    for row in residual_all:
        key = (row[2], norm(row[3]), norm(row[4]))
        prev = by_app.get(key)
        if prev is None or len(row[0]) < len(prev[0]):
            by_app[key] = row
    residual = list(by_app.values())

    vendor_rows = []
    for (area, vendor), vd in vendor_dirs.items():
        if area == "D盘根目录":
            continue
        vs = totals.get(vd, 0)
        if vs > 0:
            vendor_rows.append((vd, vs))

    residual.sort(key=lambda r: -r[1])
    vendor_rows.sort(key=lambda r: -r[1])
    return {
        "entries": entries,
        "n_entries": len(entries),
        "n_candidates": len(candidates),
        "n_matched": n_matched,
        "n_matched_loose": n_matched_loose,
        "n_vendor_suppressed": n_vendor_suppressed,
        "n_residual_dirs": len(residual_all),
        "residual": residual,
        "vendor_rows": vendor_rows,
        "residual_paths": set(r[0] for r in residual_all),
    }


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--out")
    args = ap.parse_args()

    r = analyze(args.db)
    lines = []
    lines.append("疑似残留识别报告 %s" % VERSION)
    lines.append("数据文件: %s" % os.path.abspath(args.db))
    lines.append("卸载列表条目数: %d 条（三处注册表合计）" % r["n_entries"])
    lines.append("候选目录: %d 个；匹配到卸载记录: %d 个（其中宽松匹配 %d 个）；"
                 "同厂商已安装忽略 %d 个；疑似残留: %d 个"
                 "（按软件去重，去重前 %d 个目录）" % (
                     r["n_candidates"], r["n_matched"], r["n_matched_loose"],
                     r["n_vendor_suppressed"], len(r["residual"]),
                     r["n_residual_dirs"]))
    lines.append("")
    lines.append("一、口径（先读）")
    lines.append("- 只读分析，不修改、不删除任何文件")
    lines.append("- 「疑似残留」= 该目录对应不上任何一条卸载记录，不等于垃圾")
    lines.append("- 便携软件、绿色软件、解压即用的程序不进卸载列表，会被误标，请人工确认")
    lines.append("- 白名单已排除: %s" % "、".join(sorted(WHITELIST)))
    lines.append("")
    lines.append("二、疑似残留软件目录 Top 100（按软件去重）")
    if r["residual"]:
        for d, size, area, vendor, app, conf in r["residual"][:100]:
            lines.append("%-100s %12s  [%s] 置信度:%s" % (
                d, human(size), area, conf))
    else:
        lines.append("（无）")
    lines.append("")
    lines.append("三、厂商目录汇总（供人工核对，前 50）")
    for vd, vs in r["vendor_rows"][:50]:
        lines.append("%-80s %12s" % (vd, human(vs)))
    lines.append("")
    lines.append("四、说明")
    lines.append("- 置信度 高 = 查不到同厂商的安装记录；中 = 同厂商有安装记录，"
                 "可能只是中英文/改名对不上（如微信），需人工确认")
    lines.append("- 删不删永远由你手动决定；建议先移入隔离区观察 30 天")
    lines.append("- AppData\\Local 里以已卸载软件命名的目录是最高置信的残留")

    text = "\n".join(lines) + "\n"
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print("residual-report written: %s" % args.out)
    else:
        print(text)


if __name__ == "__main__":
    main()
