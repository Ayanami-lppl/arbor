# -*- coding: utf-8 -*-
"""analyze.py -- 从扫描数据库做聚合：目录总大小、归属区域分类
被 scan.py 之外的视图工具复用。
"""
import os
import sqlite3
from collections import defaultdict


def load_snapshot(con, with_files=False):
    """一次遍历构建目录聚合快照（GUI 与残留分析共享，避免重复聚合）。

    with_files=True 时额外建「目录 → 直属文件列表」索引（GUI 目录树用）。
    返回 dict：
      totals          {目录: 含子目录总大小}
      direct          {目录: 直属文件大小}
      direct_counts   {目录: 直属文件数}
      files_by_dir    {目录: [(路径, 大小, 修改时间, 隐藏)]}（仅 with_files）
      children        {目录: [直接子目录]}
      roots           [扫描根]
      dirs            [全部目录路径]（供 residual 复用）
      hidden_dir_set  {隐藏目录}
      error_dirs      {读取不完整的目录}
      n_files / n_bytes  文件数与逻辑大小
    """
    dir_info = {}
    for path, hidden, system in con.execute(
            "SELECT path, hidden, system FROM dirs"):
        dir_info[path] = (hidden, system)
    direct = defaultdict(int)
    direct_counts = defaultdict(int)
    files_by_dir = defaultdict(list)
    n_files = 0
    n_bytes = 0
    for path, size, mtime, hidden, system in con.execute(
            "SELECT path, size, mtime, hidden, system FROM files"):
        parent = os.path.dirname(path)
        direct[parent] += size
        direct_counts[parent] += 1
        if with_files:
            files_by_dir[parent].append((path, size, mtime, hidden, system))
        if parent not in dir_info:
            dir_info[parent] = (0, 0)
        n_files += 1
        n_bytes += size
    error_dirs = {r[0] for r in con.execute(
        "SELECT path FROM errors WHERE reason LIKE 'open-dir%'")}
    children = defaultdict(list)
    for d in dir_info:
        p = os.path.dirname(d)
        if p and p != d:
            children[p].append(d)
    roots = [d for d in dir_info
             if os.path.dirname(d) in (d, "") or os.path.dirname(d) not in dir_info]
    total = {}
    stack = [(r, False) for r in roots]
    visited = set()
    while stack:
        d, done = stack.pop()
        if done:
            total[d] = direct.get(d, 0) + sum(total.get(c, 0)
                                              for c in children[d])
            continue
        if d in visited:
            continue
        visited.add(d)
        stack.append((d, True))
        for c in children[d]:
            if c not in visited:
                stack.append((c, False))
    return {
        "totals": total,
        "direct": dict(direct),
        "direct_counts": dict(direct_counts),
        "files_by_dir": dict(files_by_dir),
        "children": dict(children),
        "roots": sorted(roots),
        "dirs": list(dir_info.keys()),
        "hidden_dir_set": {d for d, flags in dir_info.items() if flags[0]},
        "system_dir_set": {d for d, flags in dir_info.items() if flags[1]},
        "error_dirs": error_dirs,
        "n_files": n_files,
        "n_bytes": n_bytes,
    }


def dir_totals(db_path):
    """返回 {目录路径(规范化): 总大小}，大小含全部子目录与其中文件。"""
    con = sqlite3.connect(db_path)
    try:
        return load_snapshot(con)["totals"]
    finally:
        con.close()


def area_of(path):
    """把路径归类到人话区域，用于隐藏文件按区域统计。"""
    p = path.lower()
    if p.startswith("c:\\windows\\") or p == "c:\\windows":
        return "Windows 系统目录"
    if p.startswith("c:\\program files\\") or p.startswith("c:\\program files (x86)\\"):
        return "程序安装目录 (Program Files)"
    if p.startswith("c:\\programdata\\"):
        return "公共数据 ProgramData"
    if p.startswith("c:\\users\\"):
        return "用户目录 (C:\\Users)"
    if p.startswith("d:\\"):
        return "D 盘"
    if p.startswith("c:\\"):
        return "C 盘根与其他"
    return "其他"
