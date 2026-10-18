# -*- coding: utf-8 -*-
"""tests/test_phase4.py -- 阶段四回归：启动页/动效、坏库、加载中关闭
用法: python tests/test_phase4.py
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import gui  # noqa: E402

PY = sys.executable
GUI = os.path.join(ROOT, "gui.py")
ok = True


def check(name, cond, detail=""):
    global ok
    print(("[OK]   " if cond else "[FAIL] ") + name + ("" if cond else "  <- %s" % (detail,)))
    ok = ok and bool(cond)


def run_smoke(db):
    # 沙箱里带管道的 subprocess 会被拒（CreatePipe 权限），用 DEVNULL 只看退出码
    p = subprocess.run([PY, GUI, "--smoke", "--db", db],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=180)
    return p


def main():
    latest = gui._latest_db()

    # 1) 缺失数据库
    p = run_smoke(os.path.join(HERE, "_no_such.sqlite"))
    check("缺失数据库：冒烟失败退出", p.returncode != 0, p.returncode)

    # 2) 损坏数据库
    bad = os.path.join(HERE, "_corrupt.sqlite")
    with open(bad, "w", encoding="utf-8") as f:
        f.write("this is not a sqlite database")
    p = run_smoke(bad)
    check("损坏数据库：冒烟失败退出", p.returncode != 0, p.returncode)

    # 3) 加载中关闭不崩
    app = gui.App(db_path=latest, smoke=True)
    app._splash = app._make_splash()
    app._splash.withdraw()
    app.start_loading(latest)
    app._on_close()
    crashed = False
    try:
        app.update()
    except Exception:  # noqa: BLE001
        crashed = True
    check("加载中关闭窗口：无异常", not crashed)

    # 4) 启动动效帧
    app = gui.App(db_path=latest, smoke=True)
    app._splash = app._make_splash()
    app._splash.withdraw()
    app.start_loading(latest)
    t = time.time() + 0.6
    while time.time() < t:
        app.update()
        time.sleep(0.02)
    alive = app._splash is not None and app._splash.winfo_exists()
    items = len(app._splash_canvas.find_all()) if app._splash_canvas else 0
    check("启动页动效有绘制内容", alive and items > 0, items)
    deadline = time.time() + 60
    while not app.ready and not app._last_error and time.time() < deadline:
        app.update()
        time.sleep(0.02)
    check("动效期间数据加载完成", app.ready, app._last_error)
    app.destroy()

    # 5) 关闭动效（静态帧）
    app = gui.App(db_path=latest, smoke=True, no_anim=True)
    app._splash = app._make_splash()
    app._splash.withdraw()
    app.start_loading(latest)
    deadline = time.time() + 60
    while not app.ready and not app._last_error and time.time() < deadline:
        app.update()
        time.sleep(0.02)
    check("--no-anim 仍能正常加载", app.ready, app._last_error)
    app.destroy()

    try:
        os.remove(bad)
    except OSError:
        pass
    print("RESULT:", "PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
