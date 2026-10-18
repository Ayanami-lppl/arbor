# -*- coding: utf-8 -*-
"""tests/test_switch.py -- 切库任务隔离测试：
第一次加载未完成时立刻发起第二次加载，最终应用的是第二次的结果，
第一次任务的结果必须被丢弃（不覆盖新页面）。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import gui


def main():
    db = sys.argv[1] if len(sys.argv) > 1 else gui._latest_db()
    if not db:
        raise SystemExit("no db")
    app = gui.App(db_path=None, selftest=False, smoke=False)
    app.withdraw()
    app.start_loading(db)
    first_tid = app.task_id
    app.start_loading(db)          # 立刻切一次库
    second_tid = app.task_id
    deadline = __import__("time").time() + 180
    while app.busy and not app._last_error and __import__("time").time() < deadline:
        app.update()
        __import__("time").sleep(0.02)
    ok = (app.ready and second_tid > first_tid
          and app.task_id == second_tid and app.load_ms > 0)
    print("first_tid=%d second_tid=%d ready=%s load_ms=%d ok=%s" % (
        first_tid, second_tid, app.ready, int(app.load_ms), ok))
    app.destroy()
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
