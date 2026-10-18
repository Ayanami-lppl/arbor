# -*- coding: utf-8 -*-
"""tests/test_quarantine.py -- 隔离区移动/还原回归（只用夹具目录，不碰真实数据）
用法: python tests/test_quarantine.py
"""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from quarantine import Quarantine  # noqa: E402

ok = True


def check(name, cond, detail=""):
    global ok
    print(("[OK]   " if cond else "[FAIL] ") + name + ("" if cond else "  <- %s" % (detail,)))
    ok = ok and bool(cond)


def main():
    base = os.path.join(HERE, "_q_base")
    src = os.path.join(HERE, "_q_src")
    shutil.rmtree(base, ignore_errors=True)
    shutil.rmtree(src, ignore_errors=True)
    os.makedirs(os.path.join(src, "junk"))
    with open(os.path.join(src, "junk", "a.txt"), "w", encoding="utf-8") as f:
        f.write("a" * 100)
    with open(os.path.join(src, "junk", "b.txt"), "w", encoding="utf-8") as f:
        f.write("b" * 200)

    q = Quarantine(c_base=base)
    results = q.move([os.path.join(src, "junk")])
    check("移动成功", results and results[0][1].startswith("ok"), results)
    check("原路径已不存在", not os.path.exists(os.path.join(src, "junk")))
    manifest = os.path.join(base, "manifest.json")
    check("清单文件存在", os.path.exists(manifest))
    data = json.load(open(manifest, encoding="utf-8"))
    check("清单记录 1 条", len(data.get("entries", [])) == 1, data)
    if data.get("entries"):
        e = data["entries"][0]
        check("清单记录原路径", e["original"].endswith("junk"), e)
        check("隔离区文件存在", os.path.exists(e["quarantined"]))

    # 还原（list_all 返回 [(base, 条目dict)]，restore 需要下标）
    q2 = Quarantine(c_base=base)
    entries = q2.list_all()
    base_path = entries[0][0]
    idx = 0
    restored = q2.restore(base_path, idx)
    check("还原回原路径", os.path.exists(os.path.join(src, "junk", "a.txt")), restored)
    check("还原后清单清空",
          json.load(open(manifest, encoding="utf-8")).get("entries") == [])

    shutil.rmtree(base, ignore_errors=True)
    shutil.rmtree(src, ignore_errors=True)
    print("RESULT:", "PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
