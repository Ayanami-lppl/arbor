# -*- coding: utf-8 -*-
"""quarantine.py -- 隔离区：可回滚的移动，替代直接删除
同盘移动=瞬间改名；跨盘会退化为复制+删除（较慢）。
清单先记 pending 再移动、移动完再标 ok：进程中途被杀也不会丢记录。
C 盘条目放在 %LOCALAPPDATA%\\disk-inspector\\quarantine\\c（不依赖工程目录），
D 盘条目放在 D:\\磁盘隔离区。
"""
import os
import json
import shutil
import time

D_BASE = r"D:\磁盘隔离区"

PROTECTED = {
    "c:\\windows",
    "c:\\program files",
    "c:\\program files (x86)",
    "c:\\programdata",
    "c:\\users",
}


def _default_c_base():
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(base, "disk-inspector", "quarantine", "c")


class Quarantine:
    def __init__(self, c_base=None, d_base=None):
        self.c_base = c_base or _default_c_base()
        self.d_base = d_base or D_BASE

    def _base_for(self, path):
        vol = os.path.splitdrive(path)[0].lstrip(":").lower() or "c"
        if vol == "d" and os.path.isdir("D:\\"):
            return self.d_base
        return self.c_base

    def _is_protected(self, path):
        p = os.path.normcase(os.path.abspath(path).rstrip("\\/"))
        if not os.path.splitdrive(p)[1]:
            return True                     # 盘符根
        for base in (self.c_base, self.d_base):
            b = os.path.normcase(os.path.abspath(base).rstrip("\\/"))
            if p == b or b.startswith(p + "\\"):
                return True                 # 不能把隔离区自己（或它的上级）移走
        return p in PROTECTED

    def _manifest_path(self, base):
        return os.path.join(base, "manifest.json")

    @staticmethod
    def _reconcile(entries):
        """崩溃恢复：pending 条目按磁盘现状判定，移动没发生的条目直接丢弃。"""
        out = []
        for e in entries:
            if e.get("status", "ok") == "pending":
                orig = e.get("original", "")
                q = e.get("quarantined", "")
                if q and os.path.exists(q) and not os.path.exists(orig):
                    e["status"] = "ok"
                elif orig and os.path.exists(orig) and not (q and os.path.exists(q)):
                    continue
                else:
                    e["status"] = "unknown"
            out.append(e)
        return out

    def _load(self, base):
        p = self._manifest_path(base)
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    data = json.load(f)
                data["entries"] = self._reconcile(data.get("entries", []))
                return data
            except Exception:
                pass
        return {"entries": []}

    def _save(self, base, data):
        os.makedirs(base, exist_ok=True)
        p = self._manifest_path(base)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)

    def _unique_dest(self, base, name):
        dest = os.path.join(base, name)
        if not os.path.exists(dest):
            return dest
        stamp = time.strftime("%Y%m%d-%H%M%S")
        i = 1
        while True:
            dest = os.path.join(base, "%s-%s-%d" % (name, stamp, i))
            if not os.path.exists(dest):
                return dest
            i += 1

    def move(self, paths):
        """返回 [(原路径, 结果字符串)]。"""
        results = []
        by_base = {}
        for raw in paths:
            p = os.path.abspath(raw)
            if self._is_protected(p):
                results.append((p, "拒绝: 受保护路径"))
                continue
            by_base.setdefault(self._base_for(p), []).append(p)

        for base, plist in by_base.items():
            data = self._load(base)
            os.makedirs(base, exist_ok=True)
            for p in plist:
                p = p.rstrip("\\/")
                if not os.path.exists(p):
                    results.append((p, "skip: 不存在"))
                    continue
                dest = self._unique_dest(base, os.path.basename(p))
                entry = {
                    "original": p,
                    "quarantined": dest,
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "status": "pending",
                }
                data["entries"].append(entry)
                self._save(base, data)      # 先记账，再动文件
                try:
                    shutil.move(p, dest)
                except OSError as ex:
                    data["entries"].remove(entry)
                    self._save(base, data)
                    results.append((p, "error: %s" % ex))
                    continue
                entry["status"] = "ok"
                self._save(base, data)
                results.append((p, "ok -> %s" % dest))
        return results

    def list_all(self):
        out = []
        for base in (self.c_base, self.d_base):
            for e in self._load(base).get("entries", []):
                out.append((base, e))
        return out

    def restore(self, base, index):
        data = self._load(base)
        entries = data.get("entries", [])
        if not 0 <= index < len(entries):
            raise IndexError("隔离区条目不存在")
        e = entries[index]
        orig = e["original"]
        q = e["quarantined"]
        if not os.path.exists(q):
            raise FileNotFoundError("隔离区里的文件已不存在: %s" % q)
        if os.path.exists(orig):
            raise FileExistsError("原位置已有同名文件，未还原: %s" % orig)
        parent = os.path.dirname(orig)
        if parent:
            os.makedirs(parent, exist_ok=True)
        shutil.move(q, orig)
        entries.pop(index)
        self._save(base, data)
        return orig
