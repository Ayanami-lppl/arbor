# -*- coding: utf-8 -*-
"""gui.py -- Arbor · 磁盘空间分析（v1.2）
用法: python gui.py [--db data/scan-xxx.sqlite]
自检: python gui.py --db ... --selftest   同步管线，无窗口
冒烟: python gui.py --db ... --smoke      真实异步管线（线程+队列），无窗口

第一阶段改动（相对 v0.6）：
- 启动页先绘制、进入事件循环后再后台加载（Tk 控件只在主线程更新）
- 开机打点：启动页首帧 / 打开数据库 / 聚合 / 归属分析 / 首页就绪
- 旧数据库只读打开（不再 CREATE INDEX、不修改输入库）
- 目录聚合只做一次，残留分析复用同一份 totals/dirs
- 非首页标签页首次打开时才填充
- 单实例：再次双击只唤醒已有窗口
"""
import os
import sys
import time
import glob
import queue
import ctypes
import sqlite3
import argparse
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from analyze import load_snapshot, area_of
from residual import analyze as residual_analyze
from quarantine import Quarantine

APP_TITLE = "Arbor · 磁盘空间分析"
HELP_TEXT = """怎么读这张表

大小（含子项）：这个文件夹连同里面所有内容的总大小。
直属大小：只算直接放在这个文件夹里的文件，不含子文件夹。
占父级：占上一级文件夹的比例，分母是上一级总大小。
占扫描根：占整个盘（扫描范围）的比例，分母是该盘总大小。
项目数：直接子文件夹数 + 直接文件数，不含更深层。
状态：隐藏＝被设为隐藏；系统＝系统重要文件，别动；读不全＝有子目录没权限，大小被低估。
软件归属：疑似残留＝对不上安装记录，可能是卸载残留；未在名单＝更可能是正常内容。两者都只是线索，不能当删除依据。

长条图：越长越大，越深越大；右侧是「大小 · 占本范围百分比」。左键下钻，右键返回，悬停看完整路径。

隔离区：只移动不删除，随时可还原。
禁止移入：盘符根、用户主目录、桌面/文档/下载/图片/音乐/视频、Windows、Program Files、微软组件、系统还原点、回收站、Arbor 自身目录。
需二次确认：AppData、ProgramData、Program Files 中不在疑似残留名单的目录。

拿不准时：先右键看所在位置，再核对大小、时间、归属；仍不确定就移入隔离区观察，别直接删。pagefile.sys、hiberfil.sys 这类系统文件不要碰。
"""
MUTEX_NAME = "Local\\arbor_disk_analyzer_v1"
HERE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(HERE, "data", "logs")
LOG_PATH = os.path.join(LOG_DIR, "gui.log")

NORMAL_SHADES = ["#4a86c8", "#5b9bd5", "#6fa8dc", "#7fb4e4"]
COLOR_HIDDEN = "#d64541"
COLOR_RESIDUAL = "#e67e22"
COLOR_REST = "#b0b0b0"

# 阶段三视觉：浅色工作台
BG = "#F5F6F8"
SURFACE = "#FFFFFF"
TEXT = "#20252B"
MUTED = "#69727D"
BORDER = "#E4E8EC"
ACCENT = "#287D78"
WARN = "#A96813"

_MUTEX = None  # 单实例互斥体句柄（进程内保持），不能提前关闭


def human(n):
    n = int(n)
    if n < 1024:
        return "%d B" % n
    x = float(n)
    for unit in ("KiB", "MiB", "GiB", "TiB"):
        x /= 1024
        if x < 1024 or unit == "TiB":
            return "%.1f %s" % (x, unit)


def _log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    if sys.stdout is not None:
        try:
            print(line, flush=True)
        except Exception:
            pass


def _ro_uri(path):
    p = os.path.abspath(path).replace("\\", "/")
    return "file:" + p + "?mode=ro"


def _load_data(db_path, progress=None):
    """同步/线程共用的加载管线：只读打开 → 聚合快照 → 残留分析。"""
    def p(msg):
        _log("stage: " + msg)
        if progress:
            progress(msg)
    t0 = time.perf_counter()
    p("正在打开扫描快照…")
    con = sqlite3.connect(_ro_uri(db_path), uri=True)
    try:
        meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
    finally:
        con.close()
    t1 = time.perf_counter()
    _log("mark db_open=%.0fms" % ((t1 - t0) * 1000))
    p("正在整理目录层级…")
    con = sqlite3.connect(_ro_uri(db_path), uri=True)
    try:
        snap = load_snapshot(con, with_files=True)
    finally:
        con.close()
    t2 = time.perf_counter()
    _log("mark aggregate=%.0fms dirs=%d" % ((t2 - t1) * 1000, len(snap["totals"])))
    p("正在分析软件归属…")
    res = residual_analyze(db_path, totals=snap["totals"], dirs=snap["dirs"])
    t3 = time.perf_counter()
    _log("mark residual=%.0fms" % ((t3 - t2) * 1000))
    _log("mark load_total=%.0fms" % ((t3 - t0) * 1000))
    return {"db": db_path, "meta": meta, "snap": snap, "res": res,
            "load_ms": (t3 - t0) * 1000}


def _worker(db_path, tid, q):
    try:
        data = _load_data(
            db_path, progress=lambda m: q.put(("stage", tid, m)))
        q.put(("ready", tid, data))
    except Exception as ex:  # noqa: BLE001 - 任何加载失败都要回到界面
        _log("load error: %r" % (ex,))
        q.put(("error", tid, "%s: %s" % (type(ex).__name__, ex)))


def acquire_single_instance():
    """命名互斥体；已有实例时尝试唤醒它的窗口并返回 False。"""
    global _MUTEX
    k32 = ctypes.windll.kernel32
    k32.CreateMutexW.restype = ctypes.c_void_p
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                 ctypes.c_wchar_p]
    _MUTEX = k32.CreateMutexW(None, True, MUTEX_NAME)
    if not _MUTEX:
        return True
    if k32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        u32 = ctypes.windll.user32
        u32.FindWindowW.restype = ctypes.c_void_p
        u32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        hwnd = u32.FindWindowW(None, APP_TITLE)
        if hwnd:
            u32.ShowWindow(hwnd, 9)   # SW_RESTORE
            u32.SetForegroundWindow(hwnd)
        return False
    return True


def _lerp_color(c1, c2, t):
    t = max(0.0, min(1.0, t))
    a = tuple(int(c1[i:i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(c2[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(
        int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _system_dpi():
    """取系统 DPI（Win10 1607+ 用 GetDpiForSystem，失败退回 GDI）。"""
    try:
        return int(ctypes.windll.user32.GetDpiForSystem())
    except Exception:  # noqa: BLE001
        pass
    try:
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, hdc)
        return int(dpi) or 96
    except Exception:  # noqa: BLE001
        return 96


def _animations_enabled():
    """读 Windows「在 Windows 中显示动画」设置；读不到就默认开。"""
    try:
        val = ctypes.c_int(1)
        # SPI_GETCLIENTAREAANIMATION = 0x1042
        ctypes.windll.user32.SystemParametersInfoW(0x1042, 0,
                                                   ctypes.byref(val), 0)
        return bool(val.value)
    except Exception:  # noqa: BLE001
        return True


def _binary_layout(items, x, y, w, h):
    """二分矩形树图：每次按面积中位数把矩形切成两块，面积严格对应大小。"""
    if not items:
        return []
    if len(items) == 1:
        return [(items[0][0], x, y, w, h)]
    total = sum(v for _, v in items)
    if total <= 0:
        # 全零大小：等分，避免递归不收敛
        out = []
        n = len(items)
        if w >= h:
            for i, (k, _v) in enumerate(items):
                out.append((k, x + w * i / n, y, w / n, h))
        else:
            for i, (k, _v) in enumerate(items):
                out.append((k, x, y + h * i / n, w, h / n))
        return out
    acc = 0.0
    split = None
    for i, (_k, v) in enumerate(items):
        acc += v
        if acc >= total / 2:
            split = i + 1
            break
    if split is None or split >= len(items):
        split = len(items) - 1
    if split < 1:
        split = 1
    first, rest = items[:split], items[split:]
    a1 = sum(v for _, v in first)
    f = (a1 / total) if total else 0.5
    f = min(max(f, 0.001), 0.999)
    if w >= h:
        out = _binary_layout(first, x, y, w * f, h)
        out += _binary_layout(rest, x + w * f, y, w * (1 - f), h)
    else:
        out = _binary_layout(first, x, y, w, h * f)
        out += _binary_layout(rest, x, y + h * f, w, h * (1 - f))
    return out


class App(tk.Tk):
    def __init__(self, db_path=None, selftest=False, smoke=False,
                 no_anim=False):
        super().__init__()
        self._t_start = time.perf_counter()
        # 按系统 DPI 等比缩放：字体缩放交给 Tk，控件像素尺寸按 ui_scale 放大
        self._ui_scale = max(1.0, min(_system_dpi() / 96.0, 2.5))
        try:
            self.tk.call("tk", "scaling", self._ui_scale * 96.0 / 72.0)
        except tk.TclError:
            pass
        self._anim_enabled = (not no_anim) and _animations_enabled()
        self._splash_anim_after = None
        self._splash_canvas = None
        self._set_window_icon()
        self.title(APP_TITLE)
        self._center_window(self, 1200, 760)
        self.withdraw()
        self.selftest = selftest
        self.smoke = smoke
        self.db_path = None
        self._db_conn = None
        self.meta = {}
        self.snap = {}
        self.res = {}
        self.totals = {}
        self.direct = {}
        self.child_map = {}
        self.direct_counts = {}
        self.error_dirs = set()
        self._tree_loaded = set()
        self._tree_search_win = None
        self._sort_col = "size"
        self._sort_desc = True
        self._hover_row = None
        self.file_sizes = {}
        self.system_dirs = set()
        self._chart_default_text = ""
        self.roots = []
        self.hidden_dir_set = set()
        self.residual_paths = set()
        self.residual_paths_lower = set()
        self.residual_rows = []
        self.current_node = None
        self.node_stack = []
        self.rect_map = {}
        self.tm_node = None
        self.chart_map = {}
        self.load_ms = 0.0
        self.quarantine = Quarantine()
        self.q = queue.Queue()
        self.task_id = 0
        self.busy = False
        self.ready = False
        self.closing = False
        self._splash = None
        self._splash_stage = None
        self._last_error = ""
        self._loaded_tabs = set()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if self.selftest:
            if db_path:
                self._load_sync(db_path)
        elif self.smoke:
            self._pending_db = db_path
        else:
            if db_path:
                self._splash = self._make_splash()
                self.after(30, self.start_loading, db_path)
            else:
                self._show_empty()
                self.deiconify()

    # ---------- UI ----------

    def _build_ui(self):
        menubar = tk.Menu(self)
        m_file = tk.Menu(menubar, tearoff=0)
        m_file.add_command(label="打开扫描数据库...", command=self._pick_db)
        m_file.add_separator()
        m_file.add_command(label="退出", command=self._on_close)
        menubar.add_cascade(label="文件", menu=m_file)
        m_help = tk.Menu(menubar, tearoff=0)
        m_help.add_command(label="数据怎么读（新手向）", command=self._show_help)
        m_help.add_command(label="口径", command=self._show_notes)
        menubar.add_cascade(label="帮助", menu=m_help)
        self.config(menu=menubar)

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=BG, foreground=TEXT,
                        font=("Microsoft YaHei UI", 10))
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED)
        style.configure("Treeview", background=SURFACE,
                        fieldbackground=SURFACE, foreground=TEXT,
                        rowheight=int(30 * self._ui_scale),
                        bordercolor=BORDER)
        style.configure("Treeview.Heading", background="#F0F2F4",
                        foreground=MUTED)
        style.configure("Nav.TButton", anchor="w", padding=(12, 8),
                        background=BG, relief="flat")
        style.map("Nav.TButton", background=[("active", "#E9ECEF")])
        style.configure("NavSel.TButton", anchor="w", padding=(12, 8),
                        background=ACCENT, foreground="#FFFFFF",
                        relief="flat")
        style.map("NavSel.TButton", background=[("active", ACCENT)],
                  foreground=[("active", "#FFFFFF")])
        # 隐藏 Notebook 自带页签栏，用左侧导航代替
        style.layout("TNotebook", [("TNotebook.client",
                                   {"sticky": "nswe"})])

        self.status = tk.Label(self, text="正在启动…", anchor="w",
                               bg=SURFACE, fg=MUTED)
        self.status.pack(fill="x", side="bottom")

        left = tk.Frame(self, bg=BG, width=int(156 * self._ui_scale))
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        tk.Label(left, text="Arbor", font=("Microsoft YaHei UI", 16, "bold"),
                 bg=BG, fg=TEXT, anchor="w").pack(fill="x", padx=14,
                                                  pady=(16, 0))
        tk.Label(left, text="磁盘空间分析", font=("Microsoft YaHei UI", 9),
                 bg=BG, fg=MUTED, anchor="w").pack(fill="x", padx=14,
                                                   pady=(0, 10))
        self.nav_buttons = {}
        for name in ("目录树", "占比", "隐藏文件", "疑似残留", "大文件",
                     "很久没动", "隔离区"):
            btn = ttk.Button(left, text=name, style="Nav.TButton",
                             command=lambda n=name: self._nav_select(n))
            btn.pack(fill="x", padx=8, pady=1)
            self.nav_buttons[name] = btn

        self.nb = ttk.Notebook(self)
        self.nb.pack(side="left", fill="both", expand=True)
        self.nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        self._build_tree_tab()
        self._build_treemap_tab()
        self._build_hidden_tab()
        self._build_residual_tab()
        self._build_big_tab()
        self._build_old_tab()
        self._build_quarantine_tab()
        self._sync_nav("目录树")

    def _nav_select(self, name):
        for idx in range(self.nb.index("end")):
            if self.nb.tab(idx, "text") == name:
                self.nb.select(idx)
                return

    def _sync_nav(self, name):
        for n, btn in self.nav_buttons.items():
            btn.configure(style="NavSel.TButton" if n == name
                          else "Nav.TButton")

    def _build_tree_tab(self):
        f = ttk.Frame(self.nb)
        top = ttk.Frame(f)
        top.pack(fill="x", padx=4, pady=2)
        ttk.Label(top, text="搜索目录:").pack(side="left")
        self.search_var = tk.StringVar()
        ent = ttk.Entry(top, textvariable=self.search_var, width=36)
        ent.pack(side="left", padx=4)
        ent.bind("<Return>", lambda e: self._do_search())
        ttk.Button(top, text="搜索", command=self._do_search).pack(side="left")
        self.tree_hint = tk.Label(
            top, text="名称列是真实目录树；双击目录进入它的占比图", fg=MUTED,
            bg=BG)
        self.tree_hint.pack(side="left", padx=8)
        ttk.Button(top, text="空间图 ▾", command=self._toggle_chart).pack(
            side="right", padx=2)
        ttk.Button(top, text="详情 ▸", command=self._toggle_details).pack(
            side="right", padx=2)
        ttk.Button(top, text="帮助", command=self._show_help).pack(
            side="right", padx=2)

        self.chart_holder = tk.Frame(f, bg=BG)
        self.chart_holder.pack(side="bottom", fill="x")
        self.chart_label = tk.Label(self.chart_holder, text="", anchor="w",
                                    bg=BG, fg=MUTED)
        self.chart_label.pack(fill="x", padx=6)
        self.chart_canvas = tk.Canvas(
            self.chart_holder, background=SURFACE,
            height=int(230 * self._ui_scale),
            highlightthickness=1, highlightbackground=BORDER)
        self.chart_canvas.pack(fill="x", padx=6, pady=(0, 4))
        self.chart_canvas.bind("<Button-1>", self._chart_click)
        self.chart_canvas.bind("<Button-3>", lambda e: self._chart_up())
        self.chart_canvas.bind("<Motion>", self._chart_motion)
        self.chart_canvas.bind("<Leave>", self._chart_leave)
        self.chart_canvas.bind("<Configure>", lambda e: self._draw_chart())

        self.pw = tk.PanedWindow(f, orient="horizontal", sashwidth=6,
                                 bg=BG)
        self.pw.pack(fill="both", expand=True)
        pw = self.pw
        self._details_visible = True
        left = ttk.Frame(pw)
        self.tv = ttk.Treeview(
            left, columns=("size", "pct_parent", "pct_root", "items", "status"),
            show="tree headings", selectmode="browse")
        self.tv.heading("#0", text="名称",
                        command=lambda: self._sort_by("name"))
        for col, text, w, anchor in (
                ("size", "大小", 110, "e"),
                ("pct_parent", "占父级", 80, "e"),
                ("pct_root", "占扫描根", 90, "e"),
                ("items", "项目数", 80, "e"),
                ("status", "状态", 90, "w")):
            w = int(w * self._ui_scale)
            cmd = None
            if col in ("size", "pct_parent", "pct_root", "items"):
                cmd = (lambda c=col: self._sort_by(
                    "size" if c in ("pct_parent", "pct_root") else c))
            if cmd is not None:
                self.tv.heading(col, text=text, command=cmd)
            else:
                self.tv.heading(col, text=text)
            self.tv.column(col, width=w, anchor=anchor)
        self.tv.column("#0", width=int(420 * self._ui_scale),
                       minwidth=int(160 * self._ui_scale))
        self.tv.tag_configure("hover", background="#EEF3F5")
        vsb = ttk.Scrollbar(left, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=vsb.set)
        self.tv.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tv.bind("<<TreeviewOpen>>", self._on_tree_open)
        self.tv.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tv.bind("<Double-1>", self._on_tree_double)
        self.tv.bind("<Motion>", self._on_tree_motion)
        self.tv.bind("<Leave>", lambda e: self._clear_hover())
        self._update_sort_indicators()
        pw.add(left, stretch="always")

        self.details = ttk.Frame(pw)
        self.det_vars = {}
        row = 0
        for key in ("类型", "完整路径", "直接父目录", "大小（含子项）",
                    "直属大小", "占父级", "占扫描根", "项目数", "属性",
                    "读取状态", "软件归属"):
            ttk.Label(self.details, text=key + ":").grid(
                row=row, column=0, sticky="nw", padx=(6, 2), pady=1)
            var = tk.StringVar(value="")
            tk.Label(self.details, textvariable=var, anchor="w",
                     justify="left", wraplength=int(320 * self._ui_scale), fg="#20252B").grid(
                row=row, column=1, sticky="w", padx=2, pady=1)
            self.det_vars[key] = var
            row += 1
        btns = ttk.Frame(self.details)
        btns.grid(row=row, column=0, columnspan=2, sticky="w", pady=6)
        ttk.Button(btns, text="打开所在位置",
                   command=self._det_open).pack(side="left", padx=2)
        ttk.Button(btns, text="复制路径",
                   command=self._det_copy).pack(side="left", padx=2)
        ttk.Button(btns, text="移入隔离区",
                   command=self._det_quarantine).pack(side="left", padx=2)
        ttk.Button(btns, text="ADS 检测",
                   command=self._det_ads).pack(side="left", padx=2)
        self._det_path = None
        pw.add(self.details, stretch="never")
        self.nb.add(f, text="目录树")

    # ---------- 目录树模型 ----------

    def _root_of(self, d):
        p = d
        for _ in range(64):
            nxt = os.path.dirname(p)
            if not nxt or nxt == p:
                return p
            p = nxt
        return p

    def _pct(self, size, base):
        if not base or base <= 0:
            return "—"
        return "%.1f%%" % (size * 100.0 / base)

    def _dir_row_values(self, d):
        t = self.totals.get(d, 0)
        parent = os.path.dirname(d)
        if parent and parent != d and parent in self.totals:
            base = self.totals[parent]
        else:
            base = t
        root = self._root_of(d)
        items = len(self.child_map.get(d, [])) + self.direct_counts.get(d, 0)
        status = []
        if d in self.hidden_dir_set:
            status.append("隐藏")
        if d in self.system_dirs:
            status.append("系统")
        if d in self.error_dirs:
            status.append("读不全")
        return (human(t), self._pct(t, base),
                self._pct(t, self.totals.get(root, 0)),
                format(items, ","), " ".join(status))

    def _insert_dir_node(self, parent_iid, d):
        vals = self._dir_row_values(d)
        name = os.path.basename(d.rstrip("\\/")) or d
        iid = self.tv.insert(parent_iid, "end", iid=d, text=name, values=vals)
        if self.child_map.get(d) or self.direct_counts.get(d, 0) > 0:
            self.tv.insert(iid, "end", iid="ph::" + d, text="…")
        return iid

    def _populate_tree_roots(self):
        self.tv.delete(*self.tv.get_children(""))
        self._tree_loaded = set()
        for r in self.roots:
            self._insert_dir_node("", r)
            self.tv.item(r, open=True)
            self._fill_dir(r, r)
        if self.roots:
            self.tm_node = self.roots[0]
        self._show_details(None)
        self._draw_chart()

    def _fill_dir(self, parent_iid, d, sync=False):
        ph = "ph::" + d
        if self.tv.exists(ph):
            self.tv.delete(ph)
        kids = self._sorted_dirs(d)
        step = len(kids) if sync else 200

        def insert_batch(start):
            end = min(start + step, len(kids))
            for i in range(start, end):
                if not self.tv.exists(kids[i]):
                    self._insert_dir_node(parent_iid, kids[i])
            if end < len(kids):
                self.after(1, insert_batch, end)
        insert_batch(0)
        nfiles = self.direct_counts.get(d, 0)
        fid = "files::" + d
        if nfiles and not self.tv.exists(fid):
            self.tv.insert(parent_iid, "end", iid=fid,
                           text="[此目录下的文件] %s 个" % format(nfiles, ","),
                           values=("", "", "", format(nfiles, ","), ""))
            self.tv.insert(fid, "end", iid="phf::" + d, text="…")
        self._tree_loaded.add(parent_iid)

    def _fill_files(self, parent_iid, d):
        t0 = time.perf_counter()
        phf = "phf::" + d
        if self.tv.exists(phf):
            self.tv.delete(phf)
        items = self._sorted_files(d)
        base = self.totals.get(d, 0)
        root_total = self.totals.get(self._root_of(d), 0)

        def insert_batch(start):
            end = min(start + 300, len(items))
            for i in range(start, end):
                p, s, _m, h, sysf = items[i]
                self.file_sizes[p] = s
                badges = []
                if h:
                    badges.append("隐藏")
                if sysf:
                    badges.append("系统")
                self.tv.insert(parent_iid, "end", iid=p,
                               text=os.path.basename(p),
                               values=(human(s), self._pct(s, base),
                                       self._pct(s, root_total), "",
                                       " ".join(badges) or "文件"))
            if end < len(items):
                self.after(1, insert_batch, end)
        insert_batch(0)
        self._tree_loaded.add(parent_iid)
        _log("mark fill_files dir=%s rows=%d %.0fms" % (
            d, len(items), (time.perf_counter() - t0) * 1000))

    # ---------- 排序 / 悬停 ----------

    def _sorted_dirs(self, d):
        kids = list(self.child_map.get(d, []))
        if self._sort_col == "name":
            return sorted(
                kids,
                key=lambda x: (os.path.basename(x.rstrip("\\/")) or x).lower(),
                reverse=self._sort_desc)
        if self._sort_col == "items":
            return sorted(
                kids,
                key=lambda x: len(self.child_map.get(x, []))
                + self.direct_counts.get(x, 0),
                reverse=self._sort_desc)
        return sorted(kids, key=lambda x: self.totals.get(x, 0),
                      reverse=self._sort_desc)

    def _sorted_files(self, d):
        items = list(self.files_by_dir.get(d, []))
        if self._sort_col == "name":
            return sorted(items, key=lambda r: os.path.basename(r[0]).lower(),
                          reverse=self._sort_desc)
        return sorted(items, key=lambda r: r[1], reverse=self._sort_desc)

    def _sort_by(self, col):
        if col == self._sort_col:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_col = col
            self._sort_desc = (col != "name")
        self._update_sort_indicators()
        self._resort_loaded()

    def _update_sort_indicators(self):
        heads = {"name": "名称", "size": "大小", "pct_parent": "占父级",
                 "pct_root": "占扫描根", "items": "项目数"}
        arrow = " ↓" if self._sort_desc else " ↑"
        for col, base in heads.items():
            widget = "#0" if col == "name" else col
            self.tv.heading(
                widget, text=base + (arrow if col == self._sort_col else ""))

    def _resort_loaded(self):
        for parent in list(self._tree_loaded):
            if not self.tv.exists(parent):
                continue
            if parent.startswith("files::"):
                kids = list(self.tv.get_children(parent))
                if not kids:
                    continue
                if self._sort_col == "name":
                    kids.sort(key=lambda p: os.path.basename(p).lower(),
                              reverse=self._sort_desc)
                else:
                    kids.sort(key=lambda p: self.file_sizes.get(p, 0),
                              reverse=self._sort_desc)
                for idx, p in enumerate(kids):
                    self.tv.move(p, parent, idx)
                continue
            known = [k for k in self.tv.get_children(parent)
                     if not k.startswith(("ph", "files::"))]
            if not known:
                continue
            ordered = [k for k in self._sorted_dirs(parent) if k in known]
            for idx, k in enumerate(ordered):
                self.tv.move(k, parent, idx)

    def _on_tree_motion(self, event):
        row = self.tv.identify_row(event.y)
        if row == self._hover_row:
            return
        self._clear_hover()
        if row:
            self.tv.item(row, tags=("hover",))
            self._hover_row = row

    def _clear_hover(self):
        row = self._hover_row
        self._hover_row = None
        if row and self.tv.exists(row):
            self.tv.item(row, tags=())

    def _on_tree_open(self, event=None):
        iid = self.tv.focus()
        if not iid or iid in self._tree_loaded or iid.startswith("ph"):
            return
        for c in self.tv.get_children(iid):
            self.tv.delete(c)
        self._tree_loaded.discard(iid)
        if iid.startswith("files::"):
            self._fill_files(iid, iid[len("files::"):])
        else:
            self._fill_dir(iid, iid)

    def _on_tree_select(self, event=None):
        sel = self.tv.focus()
        self._show_details(sel)
        if not self.ready or not sel:
            return
        if sel.startswith("ph"):
            return
        if sel.startswith("files::"):
            self.tm_node = sel[len("files::"):]
        elif sel in self.totals:
            self.tm_node = sel
        else:
            parent = os.path.dirname(sel)
            if parent in self.totals:
                self.tm_node = parent
        self._draw_chart()

    def _on_tree_double(self, event=None):
        iid = self.tv.focus()
        if not iid or iid.startswith("ph"):
            return
        if iid.startswith("files::"):
            if not self._tree_loaded:
                pass
            self._fill_files(iid, iid[len("files::"):])
            self.tv.item(iid, open=not self.tv.item(iid, "open"))
            return
        if iid not in self.totals:
            return
        self._ensure_dir_loaded(iid)
        self.tv.item(iid, open=not self.tv.item(iid, "open"))
        self.tm_node = iid
        self._draw_chart()

    # ---------- 联动空间图（二分矩形树图） ----------

    def _toggle_chart(self):
        if self.chart_holder.winfo_ismapped():
            self.chart_holder.pack_forget()
        else:
            self.chart_holder.pack(side="bottom", fill="x")
            self._draw_chart()

    def _toggle_details(self):
        if self._details_visible:
            self.pw.forget(self.details)
            self._details_visible = False
        else:
            self.pw.add(self.details, stretch="never")
            self._details_visible = True

    def _chart_entries(self, node):
        ents = [(d, self.totals.get(d, 0), "dir")
                for d in self.child_map.get(node, [])]
        ents += [(p, s, "file")
                 for (p, s, _m, _h, _sy) in self.files_by_dir.get(node, [])]
        ents.sort(key=lambda t: -t[1])
        if len(ents) > 40:
            rest = sum(s for _, s, _ in ents[40:])
            ents = ents[:40] + [("__rest__", rest, "rest")]
        return ents

    def _draw_bars(self, canvas, entries, cmap, sel=None, pct_base=None):
        """横向长条图：条长按最大项归一，颜色从深到浅随排名递减。"""
        canvas.delete("all")
        cmap.clear()
        w = canvas.winfo_width()
        h = canvas.winfo_height()
        if w <= 1:
            w = canvas.winfo_reqwidth()
        if h <= 1:
            h = canvas.winfo_reqheight()
        w = max(w - 8, 120)
        h = max(h - 8, 30)
        if not entries:
            canvas.create_text(8, 8, anchor="nw", text="该目录没有子项",
                               fill=MUTED)
            return
        total = pct_base if pct_base else sum(s for _, s, _ in entries)
        row_h = int(20 * self._ui_scale)
        n = min(len(entries), max(1, int(h // row_h)))
        shown = entries[:n]
        max_size = max(1, max(s for _, s, _ in shown))
        name_w = min(int(w * 0.40), 280)
        bar_x = 8 + name_w + 8
        val_w = int(118 * self._ui_scale)
        bar_max = max(w - bar_x - val_w - 8, 40)
        for i, (key, size, kind) in enumerate(shown):
            y = 4 + i * row_h
            t = i / max(n - 1, 1)
            if key.startswith("("):
                label = key
                color = COLOR_REST
            else:
                label = os.path.basename(key.rstrip("\\/")) or key
            if kind == "rest":
                color = COLOR_REST
                label = "其余 %d 项" % (len(entries) - n)
            elif key in self.hidden_dir_set:
                color = COLOR_HIDDEN
            elif key in self.residual_paths:
                color = COLOR_RESIDUAL
            elif key.startswith("("):
                pass
            elif kind == "file":
                color = _lerp_color("#4E6E78", "#C9D6DA", t)
            else:
                color = _lerp_color("#17505C", "#AFD4D8", t)
            canvas.create_text(6, y + row_h / 2, anchor="w",
                               text=label[:42], fill=TEXT,
                               font=("Microsoft YaHei UI", 9))
            bw = max(1, int(bar_max * size / max_size))
            item = canvas.create_rectangle(bar_x, y + 4, bar_x + bw,
                                           y + row_h - 4, fill=color,
                                           outline="", tags=("bar",))
            cmap[item] = key
            if sel and key == sel:
                canvas.create_rectangle(bar_x - 1, y + 3, bar_x + bw + 1,
                                        y + row_h - 3, outline=TEXT, width=1)
            pct = ("%.1f%%" % (size * 100.0 / total)) if total else "—"
            canvas.create_text(bar_x + bar_max + 6, y + row_h / 2,
                               anchor="w",
                               text="%s · %s" % (human(size), pct),
                               fill=MUTED, font=("Microsoft YaHei UI", 9))

    def _draw_chart(self):
        c = self.chart_canvas
        self.chart_map = {}
        if not self.ready or not self.tm_node:
            c.delete("all")
            return
        node = self.tm_node
        ents = self._chart_entries(node)
        if not ents:
            c.delete("all")
            c.create_text(8, 8, anchor="nw", text="该目录没有子项", fill=MUTED)
            self.chart_label.config(text="范围：%s（空）" % node)
            return
        total = sum(s for _, s, _ in ents)
        sel = self.tv.focus() or ""
        self._draw_bars(c, ents, self.chart_map, sel=sel, pct_base=total)
        self._chart_default_text = (
            "范围：%s（%d 项，合计 %s）——颜色越深越大；左键下钻，右键返回上一级"
            % (node, len(ents), human(total)))
        self.chart_label.config(text=self._chart_default_text)

    def _chart_click(self, event):
        items = self.chart_canvas.find_overlapping(
            event.x - 1, event.y - 1, event.x + 1, event.y + 1)
        for item in reversed(items):
            k = self.chart_map.get(item)
            if not k:
                continue
            if k == "__rest__":
                self._show_children_list(self.tm_node)
                return
            self._locate(k)
            return

    def _chart_motion(self, event=None):
        if event is None:
            return
        items = self.chart_canvas.find_overlapping(
            event.x - 1, event.y - 1, event.x + 1, event.y + 1)
        for item in reversed(items):
            k = self.chart_map.get(item)
            if not k:
                continue
            if k == "__rest__":
                txt = "其余项（点击查看完整列表）"
            elif k in self.totals:
                txt = "%s · %s" % (k, human(self.totals[k]))
            else:
                txt = "%s · %s" % (k, human(self.file_sizes.get(k, 0)))
            self.chart_label.config(text=txt)
            return

    def _chart_leave(self, event=None):
        self.chart_label.config(text=self._chart_default_text)

    def _chart_up(self):
        if not self.tm_node:
            return
        up = os.path.dirname(self.tm_node)
        if up and up in self.totals:
            self.tm_node = up
            self._draw_chart()

    def _show_children_list(self, node):
        ents = self._chart_entries(node)
        win = tk.Toplevel(self)
        win.title("包含项：%s" % node)
        win.geometry("760x460")
        tv = ttk.Treeview(win, columns=("path", "size"), show="headings")
        tv.heading("path", text="子项")
        tv.heading("size", text="大小")
        tv.column("path", width=600)
        tv.column("size", width=110, anchor="e")
        sb = ttk.Scrollbar(win, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for k, s, t in ents:
            if k == "__rest__":
                continue
            tv.insert("", "end", iid=k, values=(k, human(s)))
        tv.bind("<Double-1>", lambda e: self._locate(tv.focus()))

    # ---------- 详情区 ----------

    def _attribution(self, path):
        d = path
        for _ in range(64):
            if d in self.residual_paths:
                return "疑似残留（不与任何卸载记录匹配；详见「疑似残留」页）"
            nxt = os.path.dirname(d)
            if not nxt or nxt == d:
                break
            d = nxt
        return "未在疑似残留名单"

    def _show_details(self, iid):
        if not iid or iid.startswith("ph"):
            self._det_path = None
            for v in self.det_vars.values():
                v.set("")
            return
        if iid.startswith("files::"):
            self._det_path = None
            d = iid[len("files::"):]
            self.det_vars["类型"].set("文件容器")
            self.det_vars["完整路径"].set(d)
            self.det_vars["直接父目录"].set(os.path.dirname(d))
            self.det_vars["大小（含子项）"].set(human(self.totals.get(d, 0)))
            self.det_vars["直属大小"].set(human(self.direct.get(d, 0)))
            self.det_vars["占父级"].set("—")
            self.det_vars["占扫描根"].set("—")
            self.det_vars["项目数"].set(format(self.direct_counts.get(d, 0), ","))
            self.det_vars["属性"].set("")
            self.det_vars["读取状态"].set("")
            self.det_vars["软件归属"].set("")
            return
        path = iid
        self._det_path = path
        if path in self.totals:
            t = self.totals.get(path, 0)
            parent = os.path.dirname(path)
            base = self.totals.get(parent, t)
            root = self._root_of(path)
            n_kids = len(self.child_map.get(path, []))
            n_files = self.direct_counts.get(path, 0)
            self.det_vars["类型"].set(
                "扫描根" if path in self.roots else "目录")
            self.det_vars["完整路径"].set(path)
            self.det_vars["直接父目录"].set(
                parent if parent != path else "（无）")
            self.det_vars["大小（含子项）"].set(human(t))
            self.det_vars["直属大小"].set(human(self.direct.get(path, 0)))
            self.det_vars["占父级"].set("%s（分母 %s）" % (
                self._pct(t, base), human(base)))
            self.det_vars["占扫描根"].set("%s（分母 %s）" % (
                self._pct(t, self.totals.get(root, 0)),
                human(self.totals.get(root, 0))))
            self.det_vars["项目数"].set("%s（子目录 %s + 直属文件 %s）" % (
                format(n_kids + n_files, ","), format(n_kids, ","),
                format(n_files, ",")))
            attrs = []
            if path in self.hidden_dir_set:
                attrs.append("隐藏")
            if path in self.system_dirs:
                attrs.append("系统")
            self.det_vars["属性"].set(" ".join(attrs) or "普通")
            self.det_vars["读取状态"].set(
                "读取不完整（有子目录无权限）" if path in self.error_dirs
                else "完整")
            self.det_vars["软件归属"].set(self._attribution(path))
            return
        row = self._conn().execute(
            "SELECT size, mtime, hidden, system FROM files WHERE path=?",
            (path,)).fetchone()
        if not row:
            return
        size, mtime, hidden, sysf = row
        parent = os.path.dirname(path)
        base = self.totals.get(parent, 0)
        root = self._root_of(path)
        self.det_vars["类型"].set("文件")
        self.det_vars["完整路径"].set(path)
        self.det_vars["直接父目录"].set(parent)
        self.det_vars["大小（含子项）"].set(human(size))
        self.det_vars["直属大小"].set(human(size))
        self.det_vars["占父级"].set("%s（分母 %s）" % (
            self._pct(size, base), human(base)))
        self.det_vars["占扫描根"].set("%s（分母 %s）" % (
            self._pct(size, self.totals.get(root, 0)),
            human(self.totals.get(root, 0))))
        self.det_vars["项目数"].set("—")
        self.det_vars["属性"].set(" ".join(
            [x for x, on in (("隐藏", hidden), ("系统", sysf)) if on]) or "普通")
        self.det_vars["读取状态"].set("—")
        self.det_vars["软件归属"].set(self._attribution(path))

    def _det_open(self):
        if self._det_path:
            self._open_explorer(self._det_path)

    def _det_copy(self):
        if self._det_path:
            self._copy_path(self._det_path)

    def _det_quarantine(self):
        if self._det_path:
            self._quarantine([self._det_path])

    # ---------- ADS 检测（按需，不随扫描全盘跑） ----------

    def _collect_files_under(self, d, limit=2000):
        out = []
        stack = [d]
        while stack and len(out) < limit:
            cur = stack.pop()
            out.extend(r[0] for r in self.files_by_dir.get(cur, []))
            stack.extend(self.child_map.get(cur, []))
        return out[:limit], len(out) > limit

    def _scan_ads_rows(self, paths):
        from views import file_streams
        rows = []
        for p in paths:
            sp = p if p.startswith("\\\\?\\") else (
                "\\\\?\\" + os.path.abspath(p))
            streams = file_streams(sp)
            if streams:
                for name, size in streams:
                    rows.append((p, name, size))
        return rows

    def _det_ads(self):
        path = self._det_path
        if not path:
            messagebox.showinfo("ADS 检测", "先在树里选中一个目录或文件")
            return
        truncated = False
        if path in self.totals:
            targets, truncated = self._collect_files_under(path)
            scope = "目录 %s（限 2000 个文件）" % path
        else:
            targets = [path]
            scope = "文件 %s" % path
        self.config(cursor="watch")
        self.update_idletasks()
        t0 = time.perf_counter()
        try:
            rows = self._scan_ads_rows(targets)
        finally:
            self.config(cursor="")
        ms = (time.perf_counter() - t0) * 1000
        _log("mark ads scan files=%d hits=%d %.0fms" % (
            len(targets), len(rows), ms))
        win = tk.Toplevel(self)
        win.title("ADS 检测结果")
        win.geometry("840x440")
        head = "范围：%s\n扫描 %d 个文件，发现 %d 个备用数据流%s（耗时 %.0f ms）" % (
            scope, len(targets), len(rows),
            "；已达上限、未扫完" if truncated else "", ms)
        tk.Label(win, text=head, anchor="w", justify="left",
                 bg=SURFACE, fg=TEXT).pack(fill="x", padx=8, pady=4)
        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True)
        tv = ttk.Treeview(frame, columns=("file", "stream", "size"),
                          show="headings")
        tv.heading("file", text="文件")
        tv.heading("stream", text="数据流")
        tv.heading("size", text="大小")
        tv.column("file", width=540)
        tv.column("stream", width=170)
        tv.column("size", width=90, anchor="e")
        sb = ttk.Scrollbar(frame, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for p, name, size in rows:
            tv.insert("", "end", values=(p, name, human(size)))
        if not rows:
            tk.Label(win, text="没有发现备用数据流", bg=SURFACE,
                     fg=MUTED).pack(pady=6)

    # ---------- 搜索 ----------

    def _search_hits(self, q):
        hits = []
        for d in self.totals:
            if q in d.lower():
                hits.append((d, self.totals[d], "目录"))
        for items in self.files_by_dir.values():
            for r in items:
                if q in os.path.basename(r[0]).lower():
                    hits.append((r[0], r[1], "文件"))
        hits.sort(key=lambda x: -x[1])
        return hits

    def _do_search(self):
        if not self.ready:
            return
        q = self.search_var.get().strip().lower()
        if not q:
            return
        t0 = time.perf_counter()
        hits = self._search_hits(q)
        total = len(hits)
        hits = hits[:1000]
        _log("mark search q=%r hits=%d shown=%d %.0fms" % (
            q, total, len(hits), (time.perf_counter() - t0) * 1000))
        if self._tree_search_win is not None:
            try:
                self._tree_search_win.destroy()
            except tk.TclError:
                pass
        win = tk.Toplevel(self)
        win.title("搜索结果：%s — 目录 %s 个 + 文件 %s 个，命中 %d 个" % (
            q, format(len(self.totals), ","),
            format(sum(len(v) for v in self.files_by_dir.values()), ","),
            total))
        win.geometry("980x480")
        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True)
        tv = ttk.Treeview(frame, columns=("path", "size", "kind"),
                          show="headings")
        tv.heading("path", text="路径（双击定位到目录树）")
        tv.heading("size", text="大小")
        tv.heading("kind", text="类型")
        tv.column("path", width=700)
        tv.column("size", width=110, anchor="e")
        tv.column("kind", width=70, anchor="center")
        sb = ttk.Scrollbar(frame, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for d, s, kind in hits:
            tv.insert("", "end", iid=d, values=(d, human(s), kind))
        tv.bind("<Double-1>", lambda e: self._locate(tv.focus()))
        if total > len(hits):
            tk.Label(win, text="只显示前 %d 个（按大小排序），继续缩小关键词可看更多"
                     % len(hits), fg=WARN, bg=SURFACE).pack(fill="x")
        self._tree_search_win = win

    def _ensure_dir_loaded(self, d):
        """从上往下逐级展开到目录 d（只在树里真实存在的节点上操作）。"""
        top = None
        p = d
        while True:
            if self.tv.exists(p):
                top = p
                break
            nxt = os.path.dirname(p)
            if nxt == p or not nxt:
                break
            p = nxt
        if top is None:
            return
        chain = []
        q = d
        while True:
            chain.append(q)
            if q == top:
                break
            q = os.path.dirname(q)
        for q in reversed(chain):
            if q != top:
                parent = os.path.dirname(q)
                if parent not in self._tree_loaded:
                    ph = "ph::" + parent
                    if self.tv.exists(ph):
                        self.tv.delete(ph)
                    self._fill_dir(parent, parent, sync=True)
                self.tv.item(parent, open=True)
        if d not in self._tree_loaded:
            ph = "ph::" + d
            if self.tv.exists(ph):
                self.tv.delete(ph)
            self._fill_dir(d, d, sync=True)
        self.tv.item(d, open=True)

    def _select_in_tree(self, path):
        if not self.tv.exists(path):
            return
        self.tv.see(path)
        self.tv.selection_set(path)
        self.tv.focus(path)
        self._on_tree_select()

    def _locate(self, path):
        if not path:
            return
        if path in self.totals:
            self._ensure_dir_loaded(path)
            self._select_in_tree(path)
            return
        parent = os.path.dirname(path)
        if parent not in self.totals:
            return
        self._ensure_dir_loaded(parent)
        fid = "files::" + parent
        if self.tv.exists(fid) and not self.tv.exists(path):
            self._fill_files(fid, parent)
        self._select_in_tree(path)


    def _build_treemap_tab(self):
        f = ttk.Frame(self.nb)
        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Label(top, text="磁盘:").pack(side="left")
        self.drive_var = tk.StringVar()
        self.drive_box = ttk.Combobox(top, textvariable=self.drive_var,
                                      width=6, state="readonly")
        self.drive_box.pack(side="left", padx=4)
        self.drive_box.bind("<<ComboboxSelected>>", self._on_drive)
        ttk.Button(top, text="返回上一级", command=self._treemap_up).pack(
            side="left", padx=4)
        self.tree_path_label = tk.Label(top, text="", anchor="w", fg="#555")
        self.tree_path_label.pack(side="left", padx=6, fill="x", expand=True)
        ttk.Label(top, text="点击方块下钻，右键方块上一级",
                  foreground="#555").pack(side="right", padx=6)

        self.tc = tk.Canvas(f, background="#ffffff")
        self.tc.pack(fill="both", expand=True)
        self.tc.bind("<Button-1>", self._treemap_click)
        self.tc.bind("<Button-3>", lambda e: self._treemap_up())
        self.nb.add(f, text="占比")

    def _build_hidden_tab(self):
        f = ttk.Frame(self.nb)
        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Label(top, text="区域筛选:").pack(side="left", padx=4)
        self.hidden_area_var = tk.StringVar(value="全部")
        self.hidden_area_box = ttk.Combobox(
            top, textvariable=self.hidden_area_var, width=24, state="readonly")
        self.hidden_area_box.pack(side="left")
        self.hidden_area_box.bind("<<ComboboxSelected>>",
                                  lambda e: self._populate_hidden())
        ttk.Label(top, text="（右键行可打开位置/复制路径/移入隔离区）",
                  foreground="#555").pack(side="left", padx=8)
        self.hidden_tree = self._make_tree(f, ["类型", "路径", "大小", "修改时间"])
        self.nb.add(f, text="隐藏文件")

    def _build_residual_tab(self):
        f = ttk.Frame(self.nb)
        self.residual_info = tk.Label(f, text="未打开数据库", anchor="w",
                                      justify="left", wraplength=1160)
        self.residual_info.pack(fill="x", padx=4, pady=2)
        self.residual_tree = self._make_tree(
            f, ["路径", "大小", "区域", "厂商", "应用", "置信度"])
        self.nb.add(f, text="疑似残留")

    def _build_big_tab(self):
        f = ttk.Frame(self.nb)
        self.big_tree = self._make_tree(f, ["路径", "大小", "修改时间"])
        self.nb.add(f, text="大文件")

    def _build_old_tab(self):
        f = ttk.Frame(self.nb)
        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Label(top, text="多久没动过:").pack(side="left", padx=4)
        self.old_years_var = tk.StringVar(value="1 年")
        self.old_box = ttk.Combobox(top, textvariable=self.old_years_var,
                                    width=6, state="readonly",
                                    values=["1 年", "2 年", "3 年"])
        self.old_box.pack(side="left")
        self.old_box.bind("<<ComboboxSelected>>",
                          lambda e: self._populate_old())
        self.old_tree = self._make_tree(f, ["路径", "大小", "修改时间"])
        self.nb.add(f, text="很久没动")

    def _build_quarantine_tab(self):
        f = ttk.Frame(self.nb)
        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Button(top, text="还原选中",
                   command=self._restore_selected).pack(side="left", padx=4)
        ttk.Button(top, text="打开隔离区文件夹",
                   command=self._open_quarantine_folder).pack(side="left", padx=4)
        self.quarantine_info = tk.Label(top, text="", anchor="w", fg="#555")
        self.quarantine_info.pack(side="left", padx=8)
        self.quarantine_tree = self._make_tree(
            f, ["原路径", "隔离区路径", "时间", "状态"], popup=False)
        self.quarantine_rows = {}
        self.nb.add(f, text="隔离区")

    def _make_tree(self, parent, cols, popup=True):
        frame = ttk.Frame(parent)
        tree = ttk.Treeview(frame, columns=cols, show="headings")
        for c in cols:
            tree.heading(c, text=c)
        widths = [80, 620, 110, 150, 90, 90]
        for i, c in enumerate(cols):
            tree.column(c, width=widths[i] if i < len(widths) else 90,
                        anchor="w")
        sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        tree.path_col = cols.index("路径") if "路径" in cols else 0
        if popup:
            tree.bind("<Button-3>", self._popup)
        frame.pack(fill="both", expand=True)
        return tree

    def _center_window(self, win, w, h):
        """把窗口放到屏幕居中，并按屏幕尺寸收窄，避免超出屏幕。"""
        try:
            sw = win.winfo_screenwidth()
            sh = win.winfo_screenheight()
        except tk.TclError:
            return
        w = int(w * self._ui_scale)
        h = int(h * self._ui_scale)
        w = min(w, max(sw - 40, 200))
        h = min(h, max(sh - 80, 150))
        x = max((sw - w) // 2, 0)
        y = max((sh - h) // 2, 0)
        win.geometry("%dx%d+%d+%d" % (w, h, x, y))

    def _icon_file(self):
        bases = []
        if getattr(sys, "frozen", False):
            bases.append(os.path.dirname(os.path.abspath(sys.executable)))
        bases.append(HERE)
        bases.append(os.path.join(HERE, "icons"))
        for b in bases:
            p = os.path.join(b, "arbor.ico")
            if os.path.exists(p):
                return p
        return None

    def _set_window_icon(self):
        p = self._icon_file()
        if not p:
            return
        try:
            self.iconbitmap(p)
        except tk.TclError:
            pass

    def _make_splash(self):
        sp = tk.Toplevel(self)
        sp.title(APP_TITLE)
        self._center_window(sp, 520, 330)
        icon = self._icon_file()
        if icon:
            try:
                sp.iconbitmap(icon)
            except tk.TclError:
                pass
        sp.resizable(False, False)
        sp.configure(bg=BG)
        tk.Label(sp, text="Arbor", font=("Microsoft YaHei UI", 22, "bold"),
                 bg=BG, fg=TEXT).pack(pady=(26, 0))
        tk.Label(sp, text="磁盘空间分析 · 看清空间，也看清来处",
                 font=("Microsoft YaHei UI", 9), bg=BG,
                 fg=MUTED).pack()
        self._splash_canvas = tk.Canvas(sp, width=int(520 * self._ui_scale),
                                        height=int(170 * self._ui_scale), bg=BG,
                                        highlightthickness=0)
        self._splash_canvas.pack()
        self._splash_nodes = [
            (30, 40, 90, 34), (160, 26, 100, 34), (300, 40, 120, 34),
            (55, 100, 110, 34), (205, 108, 120, 34), (365, 96, 110, 34),
        ]
        self._splash_stage = tk.Label(sp, text="正在启动…",
                                      font=("Microsoft YaHei UI", 9),
                                      bg=BG, fg=ACCENT)
        self._splash_stage.pack()
        tk.Label(sp, text="关闭本窗口可取消启动（不影响已保存的扫描数据）",
                 font=("Microsoft YaHei UI", 8), bg=BG, fg=MUTED).pack(
            pady=(2, 8))
        sp.update_idletasks()
        _log("mark splash_first_frame=%.0fms" % (
            (time.perf_counter() - self._t_start) * 1000))
        if self._anim_enabled:
            self._splash_anim_after = sp.after(30, self._splash_frame, 0)
        else:
            self._splash_frame(99)
        return sp

    def _splash_frame(self, i=0):
        """「空间长出枝脉」：细线从中心分叉，端点长出空间块。"""
        if not (self._splash is not None
                and self._splash.winfo_exists()
                and self._splash_canvas is not None):
            return
        c = self._splash_canvas
        c.delete("anim")
        S = self._ui_scale
        cx0, cy0 = 260 * S, 85 * S
        try:
            step = 12 if i >= 99 else i
            c.create_oval(cx0 - 10 * S, cy0 - 10 * S, cx0 + 10 * S,
                          cy0 + 10 * S, fill=ACCENT, outline="",
                          tags="anim")
            for idx, (x, y, w, h) in enumerate(self._splash_nodes):
                x, y, w, h = x * S, y * S, w * S, h * S
                cx, cy = x + w / 2, y + h / 2
                p = max(0.0, min(1.0, (step - idx * 0.6) / 4.0))
                if p > 0:
                    ex = cx + (cx0 - cx) * (1 - p)
                    ey = cy + (cy0 - cy) * (1 - p)
                    c.create_line(cx0, cy0, ex, ey, fill=ACCENT,
                                  width=max(1, int(S)), tags="anim")
                q = max(0.0, min(1.0, (step - 5 - idx * 0.6) / 4.0))
                if q > 0:
                    nw, nh = max(w * q, 2), max(h * q, 2)
                    xx, yy = cx - nw / 2, cy - nh / 2
                    c.create_rectangle(xx, yy, xx + nw, yy + nh,
                                       fill=NORMAL_SHADES[idx % 4],
                                       outline=SURFACE, tags="anim")
        except tk.TclError:
            return
        if i < 99 and self._anim_enabled:
            try:
                self._splash_anim_after = self._splash.after(
                    60, self._splash_frame, i + 1)
            except tk.TclError:
                pass

    def _stop_splash(self):
        if self._splash_anim_after is not None and self._splash is not None:
            try:
                self._splash.after_cancel(self._splash_anim_after)
            except tk.TclError:
                pass
            self._splash_anim_after = None
        if self._splash is not None:
            try:
                self._splash.destroy()
            except tk.TclError:
                pass
            self._splash = None
        self._splash_stage = None
        self._splash_canvas = None

    # ---------- 加载 ----------

    def _show_empty(self):
        self.status.config(
            text="未找到扫描数据库；用「文件 → 打开扫描数据库…」选择 .sqlite")

    def _set_stage(self, text):
        if self._splash_stage is not None:
            try:
                self._splash_stage.config(text=text)
            except tk.TclError:
                pass

    def start_loading(self, db_path):
        self.db_path = os.path.abspath(db_path)
        self.task_id += 1
        tid = self.task_id
        self.busy = True
        self.ready = False
        self._last_error = ""
        self._close_db_conn()
        self._reset_view()
        self._set_stage("正在打开扫描快照…")
        _log("mark worker_start=%.0fms" % (
            (time.perf_counter() - self._t_start) * 1000))
        threading.Thread(
            target=_worker, args=(self.db_path, tid, self.q),
            daemon=True).start()
        self.after(50, self._poll)

    def _poll(self):
        if self.closing:
            return
        while True:
            try:
                msg = self.q.get_nowait()
            except queue.Empty:
                break
            if msg[1] != self.task_id:
                continue  # 旧任务的结果直接丢弃
            if msg[0] == "stage":
                self._set_stage(msg[2])
            elif msg[0] == "ready":
                self._apply_data(msg[2])
                self._finish_loading()
                return
            elif msg[0] == "error":
                self._finish_error(msg[2])
                return
        if self.busy:
            self.after(50, self._poll)

    def _apply_data(self, data):
        self.meta = data["meta"]
        self.snap = data["snap"]
        self.res = data["res"]
        self.load_ms = data.get("load_ms", 0.0)
        self.totals = self.snap["totals"]
        self.direct = self.snap["direct"]
        self.child_map = self.snap["children"]
        self.direct_counts = self.snap.get("direct_counts", {})
        self.files_by_dir = self.snap.get("files_by_dir", {})
        self.error_dirs = self.snap.get("error_dirs", set())
        self.roots = self.snap["roots"]
        self.hidden_dir_set = self.snap["hidden_dir_set"]
        self.system_dirs = self.snap.get("system_dir_set", set())
        self.residual_paths = self.res["residual_paths"]
        self.residual_paths_lower = {
            x.lower().rstrip("\\/") for x in self.residual_paths}
        self.residual_rows = self.res["residual"]
        self.drive_box["values"] = self.roots
        if self.roots:
            self.current_node = self.roots[0]
            self.drive_var.set(self.roots[0])
            self.tm_node = self.roots[0]
        self.residual_info.config(text=(
            "卸载列表 %d 条；候选目录 %d 个；匹配 %d 个（宽松 %d 个）；"
            "同厂商已安装忽略 %d 个；疑似残留 %d 个（去重前 %d 个目录）。"
            "口径：只读；「疑似」不等于垃圾；便携软件会被误标；"
            "置信度「中」= 同厂商有安装记录，可能只是名字对不上；删除永远手动。" % (
                self.res["n_entries"], self.res["n_candidates"],
                self.res["n_matched"], self.res["n_matched_loose"],
                self.res["n_vendor_suppressed"],
                len(self.res["residual"]), self.res["n_residual_dirs"])))
        self._update_status()

    def _finish_loading(self):
        self.ready = True
        self.busy = False
        self._stop_splash()
        self.deiconify()
        try:
            cur = self.nb.tab(self.nb.index("current"), "text")
        except tk.TclError:
            cur = "目录树"
        self._load_tab(cur)
        _log("mark ready=%.0fms" % (
            (time.perf_counter() - self._t_start) * 1000))

    def _finish_error(self, text):
        self.busy = False
        self.ready = False
        self._last_error = text
        self._stop_splash()
        self.deiconify()
        self.status.config(text="加载失败：%s（可用「文件 → 打开扫描数据库…」换一个）"
                               % text)
        if not (self.selftest or self.smoke):
            messagebox.showerror("加载失败", text)

    def _update_status(self, extra=""):
        if not self.snap:
            return
        self.status.config(text=(
            "%s   文件 %s 个 / %s    隐藏文件 %s 个 / %s    加载 %d ms%s" % (
                self.db_path,
                format(int(self.snap["n_files"]), ","),
                human(self.snap["n_bytes"]),
                format(int(self.meta.get("n_hidden", 0)), ","),
                human(int(self.meta.get("hidden_bytes", 0))),
                int(self.load_ms), extra)))

    def _load_sync(self, db_path):
        """自检用：同一条管线同步跑完，随后填充全部页签。"""
        self.db_path = os.path.abspath(db_path)
        try:
            data = _load_data(self.db_path)
        except Exception as ex:  # noqa: BLE001
            self._finish_error("%s: %s" % (type(ex).__name__, ex))
            return
        self._apply_data(data)
        self.ready = True
        self._load_all_tabs_for_test()

    def smoke_run(self):
        """冒烟：真实异步管线，无可见窗口。"""
        self._splash = self._make_splash()
        self._splash.withdraw()
        self.start_loading(self._pending_db)
        deadline = time.time() + 180
        while (not self.ready and not self._last_error
               and time.time() < deadline):
            self.update()
            time.sleep(0.02)
        if not self.ready:
            raise SystemExit("smoke: 加载未完成（%s）" % (self._last_error or "超时"))
        self._load_all_tabs_for_test()
        report = self.selftest_report()
        line = repr(report)
        if sys.stdout is not None:
            print(line)
        else:
            with open(os.path.join(HERE, "selftest.txt"), "w",
                      encoding="utf-8") as f:
                f.write(line + "\n")

    # ---------- 页签懒加载 ----------

    def _on_tab_changed(self, event=None):
        try:
            name = self.nb.tab(self.nb.index("current"), "text")
        except tk.TclError:
            return
        self._sync_nav(name)
        self._load_tab(name)

    def _load_tab(self, name):
        if name == "隔离区":
            if "隔离区" not in self._loaded_tabs:
                self._loaded_tabs.add("隔离区")
                self._refresh_quarantine()
            return
        if not self.ready or name in self._loaded_tabs:
            return
        self._loaded_tabs.add(name)
        if name == "目录树":
            self._populate_tree_roots()
        elif name == "占比":
            self._draw_treemap()
        elif name == "隐藏文件":
            self._populate_hidden_areas()
            self._populate_hidden()
        elif name == "疑似残留":
            self._populate_residual()
        elif name == "大文件":
            self._populate_big()
        elif name == "很久没动":
            self._populate_old()

    def _load_all_tabs_for_test(self):
        for name in ("目录树", "占比", "隐藏文件", "疑似残留", "大文件",
                     "很久没动", "隔离区"):
            self._load_tab(name)

    def _reset_view(self):
        self._loaded_tabs = set()
        for tree in (self.hidden_tree, self.residual_tree, self.big_tree,
                     self.old_tree):
            tree.delete(*tree.get_children())
        try:
            self.tv.delete(*self.tv.get_children(""))
        except tk.TclError:
            pass
        self._tree_loaded = set()
        self.tc.delete("all")
        self.chart_canvas.delete("all")
        self.chart_map = {}
        self.tm_node = None
        self.rect_map = {}
        self.totals = {}
        self.direct = {}
        self.snap = {}
        self.roots = []
        self.current_node = None
        self.node_stack = []
        self.tree_path_label.config(text="")

    # ---------- 数据访问 ----------

    def _conn(self):
        if self._db_conn is None:
            self._db_conn = sqlite3.connect(_ro_uri(self.db_path), uri=True)
        return self._db_conn

    def _close_db_conn(self):
        if self._db_conn is not None:
            try:
                self._db_conn.close()
            except sqlite3.Error:
                pass
            self._db_conn = None

    def _on_close(self):
        self.closing = True
        self._close_db_conn()
        self.destroy()

    def _pick_db(self):
        p = filedialog.askopenfilename(
            filetypes=[("SQLite 数据库", "*.sqlite"), ("所有文件", "*.*")])
        if p:
            self.start_loading(p)

    def _on_drive(self, event=None):
        self.node_stack = []
        self.current_node = self.drive_var.get()
        self._draw_treemap()

    # ---------- 占比 treemap ----------

    def _treemap_entries(self, node):
        kids = sorted((d for d in self.totals
                       if os.path.dirname(d) == node),
                      key=lambda d: -self.totals[d])
        entries = [(d, self.totals[d]) for d in kids]
        direct = self.direct.get(node, 0)
        if direct > 0:
            entries.append(("(此目录下的文件)", direct))
        entries.sort(key=lambda kv: -kv[1])
        if len(entries) > 40:
            head = entries[:39]
            rest = sum(s for _, s in entries[39:])
            head.append(("(其余合计)", rest))
            entries = head
        return entries

    def _draw_treemap(self):
        self.tc.delete("all")
        self.rect_map = {}
        if not self.current_node:
            return
        self.tree_path_label.config(text=self.current_node)
        raw = self._treemap_entries(self.current_node)
        ents = []
        for name, size in raw:
            if name == "(此目录下的文件)":
                ents.append((name, size, "file"))
            elif name == "(其余合计)":
                ents.append((name, size, "rest"))
            else:
                ents.append((name, size, "dir"))
        total = sum(s for _, s, _ in ents)
        self._draw_bars(self.tc, ents, self.rect_map, pct_base=total)

    def _layout(self, entries, x0, y0, w, h, depth):
        total = sum(s for _, s in entries)
        if total <= 0:
            return []
        rects = []
        acc = 0.0
        vertical = (depth % 2 == 0)
        for name, s in entries:
            f = s / total
            if vertical:
                rects.append((name, s, x0 + acc, y0, w * f, h, depth))
                acc += w * f
            else:
                rects.append((name, s, x0, y0 + acc, w, h * f, depth))
                acc += h * f
        return rects

    def _treemap_click(self, event):
        item = self.tc.find_closest(event.x, event.y)
        if not item:
            return
        name = self.rect_map.get(item[0])
        if not name or name.startswith("("):
            return
        if name not in self.totals:
            return
        self.node_stack.append(self.current_node)
        self.current_node = name
        self._draw_treemap()

    def _treemap_up(self):
        if self.node_stack:
            self.current_node = self.node_stack.pop()
            self._draw_treemap()

    # ---------- 各列表 ----------

    def _fmt_time(self, ts):
        try:
            return time.strftime("%Y-%m-%d", time.localtime(ts))
        except Exception:
            return ""

    def _populate_hidden_areas(self):
        areas = ["全部"] + sorted({area_of(r[0]) for r in self._conn().execute(
            "SELECT path FROM files WHERE hidden=1 LIMIT 5000")})
        self.hidden_area_box["values"] = areas

    def _populate_hidden(self):
        self.hidden_tree.delete(*self.hidden_tree.get_children())
        if not self.ready:
            return
        area = self.hidden_area_var.get()
        rows = []
        for path, size, mtime in self._conn().execute(
                "SELECT path, size, mtime FROM files "
                "WHERE hidden=1 ORDER BY size DESC LIMIT 2000"):
            if area != "全部" and area_of(path) != area:
                continue
            rows.append((size, "文件", path, human(size), self._fmt_time(mtime)))
        for d in self.hidden_dir_set:
            s = self.totals.get(d, 0)
            if area != "全部" and area_of(d) != area:
                continue
            rows.append((s, "目录", d, human(s), ""))
        rows.sort(key=lambda r: -r[0])
        for _size, *vals in rows[:2000]:
            self.hidden_tree.insert("", "end", values=vals)

    def _populate_residual(self):
        self.residual_tree.delete(*self.residual_tree.get_children())
        if not self.ready:
            return
        for d, size, area, vendor, app, conf in self.residual_rows[:1000]:
            self.residual_tree.insert("", "end", values=(
                d, human(size), area, vendor, app or "", conf))

    def _populate_big(self):
        self.big_tree.delete(*self.big_tree.get_children())
        if not self.ready:
            return
        for path, size, mtime in self._conn().execute(
                "SELECT path, size, mtime FROM files "
                "ORDER BY size DESC LIMIT 500"):
            self.big_tree.insert("", "end", values=(
                path, human(size), self._fmt_time(mtime)))

    def _populate_old(self):
        self.old_tree.delete(*self.old_tree.get_children())
        if not self.ready:
            return
        years = int(self.old_years_var.get().split(" ")[0])
        cutoff = time.time() - years * 365.25 * 86400
        for path, size, mtime in self._conn().execute(
                "SELECT path, size, mtime FROM files WHERE mtime < ? "
                "ORDER BY size DESC LIMIT 500", (cutoff,)):
            self.old_tree.insert("", "end", values=(
                path, human(size), self._fmt_time(mtime)))

    # ---------- 右键菜单 ----------

    def _popup(self, event):
        tree = event.widget
        row = tree.identify_row(event.y)
        if not row:
            return
        values = tree.item(row)["values"]
        path = values[tree.path_col]
        if path.startswith("("):
            return
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="打开所在文件夹",
                         command=lambda: self._open_explorer(path))
        menu.add_command(label="复制路径",
                         command=lambda: self._copy_path(path))
        menu.add_separator()
        menu.add_command(label="移入隔离区（可还原）",
                         command=lambda: self._quarantine([path]))
        menu.tk_popup(event.x_root, event.y_root)

    def _open_explorer(self, path):
        try:
            if os.path.isdir(path):
                os.startfile(path)
            else:
                subprocess.Popen(["explorer", "/select,", path])
        except OSError as ex:
            messagebox.showerror("打开失败", str(ex))

    def _copy_path(self, path):
        self.clipboard_clear()
        self.clipboard_append(path)

    def _move_risk(self, path):
        """移入隔离区的风险分级：block（禁止）/ warn（需二次确认）/ ok。"""
        p = os.path.abspath(path).lower().rstrip("\\/")
        home = os.path.expanduser("~").lower().rstrip("\\/")
        checks = [
            (p in ("c:", "c:\\", "d:", "d:\\"), "整个盘符根目录"),
            (p == home, "你的用户主目录"),
            (p == home + "\\desktop", "桌面"),
            (p == home + "\\documents", "文档"),
            (p == home + "\\downloads", "下载"),
            (p == home + "\\pictures", "图片"),
            (p == home + "\\music", "音乐"),
            (p == home + "\\videos", "视频"),
            (p.startswith("c:\\windows"), "Windows 系统目录"),
            (p.startswith("c:\\program files"), "程序安装目录"),
            (p.startswith("c:\\programdata\\microsoft"), "微软系统组件目录"),
            ("system volume information" in p, "系统还原点目录"),
            ("$recycle.bin" in p, "回收站"),
            (p.startswith(home + "\\appdata\\local\\microsoft\\windows"),
             "Windows 用户配置"),
        ]
        for flag, why in checks:
            if flag:
                return "block", why
        base = (os.path.dirname(os.path.abspath(sys.executable))
                if getattr(sys, "frozen", False) else HERE)
        base = base.lower().rstrip("\\/")
        if p == base or p.startswith(base + "\\"):
            return "block", "Arbor 程序自己的目录"
        d = p
        for _ in range(64):
            if d in self.residual_paths_lower:
                return "ok", ""
            nxt = os.path.dirname(d)
            if not nxt or nxt == d:
                break
            d = nxt
        if (p.startswith(home + "\\appdata") or p.startswith("c:\\programdata")
                or p.startswith("c:\\program files")):
            return "warn", "不在疑似残留名单，可能是正在使用的软件数据"
        return "ok", ""

    def _quarantine(self, paths):
        if self.selftest or self.smoke:
            return
        blocked, warned, oks = [], [], []
        for p in paths:
            lvl, why = self._move_risk(p)
            if lvl == "block":
                blocked.append((p, why))
            elif lvl == "warn":
                warned.append((p, why))
            else:
                oks.append(p)
        if blocked:
            messagebox.showwarning(
                "已阻止，未移入隔离区",
                "下面这些位置不能移入隔离区：\n\n"
                + "\n".join("%s（%s）" % (p, why) for p, why in blocked[:10])
                + ("\n... 等 %d 项" % len(blocked) if len(blocked) > 10 else "")
                + "\n\n这些是系统或你的重要目录，动错会出问题。"
                  "如果确实要处理，请先在文件资源管理器里手动确认。")
        targets = oks + [p for p, _ in warned]
        if not targets:
            return

        def fmt(items):
            return "\n".join(items[:8]) + (
                "\n... 等 %d 项" % len(items) if len(items) > 8 else "")

        if warned:
            wpaths = [p for p, _ in warned]
            text = ("以下 %d 项不在「疑似残留」名单里，可能属于正在使用的软件：\n\n%s\n\n"
                    "移入隔离区后原位置就没有了（可以随时还原）。确定继续？"
                    % (len(warned), fmt(wpaths)))
            if not messagebox.askyesno("需要确认", text):
                return
            if not messagebox.askyesno(
                    "再确认一次",
                    "真的要移入吗？\n\n%s\n\n如果它是正在用的软件，"
                    "可能会打不开或丢配置。" % fmt(wpaths)):
                return
        else:
            if not messagebox.askyesno(
                    "移入隔离区",
                    "把以下 %d 项移入隔离区？（不删除，可随时还原）\n\n%s"
                    % (len(targets), fmt(targets))):
                return
        results = self.quarantine.move(targets)
        ok = sum(1 for _, r in results if r.startswith("ok"))
        messagebox.showinfo(
            "完成",
            "%d 项已移入隔离区，%d 项未处理。\n\n可以在「隔离区」页随时还原。"
            % (ok, len(results) - ok))
        self._refresh_quarantine()

    # ---------- 隔离区 ----------

    def _refresh_quarantine(self):
        self.quarantine_tree.delete(*self.quarantine_tree.get_children())
        self.quarantine_rows = {}
        n = 0
        for base in (self.quarantine.c_base, self.quarantine.d_base):
            for i, e in enumerate(
                    self.quarantine._load(base).get("entries", [])):
                iid = self.quarantine_tree.insert("", "end", values=(
                    e.get("original", ""), e.get("quarantined", ""),
                    e.get("time", ""), e.get("status", "ok")))
                self.quarantine_rows[iid] = (base, i)
                n += 1
        if n:
            self.quarantine_info.config(
                text="共 %d 项（还原 = 移回原路径，不删除）" % n)
        else:
            self.quarantine_info.config(text="隔离区为空")

    def _restore_selected(self):
        if self.selftest or self.smoke:
            return
        sel = self.quarantine_tree.selection()
        if not sel:
            messagebox.showinfo("还原", "先在列表里选中一行")
            return
        base, idx = self.quarantine_rows[sel[0]]
        orig = self.quarantine_tree.item(sel[0])["values"][0]
        if not messagebox.askyesno("还原", "把这一项移回原路径？\n\n%s" % orig):
            return
        try:
            path = self.quarantine.restore(base, idx)
        except Exception as ex:  # noqa: BLE001
            messagebox.showerror("还原失败", str(ex))
            return
        self._refresh_quarantine()
        messagebox.showinfo("已还原", path)

    def _open_quarantine_folder(self):
        base = self.quarantine.c_base
        sel = self.quarantine_tree.selection()
        if sel and sel[0] in self.quarantine_rows:
            base = self.quarantine_rows[sel[0]][0]
        try:
            os.makedirs(base, exist_ok=True)
            os.startfile(base)
        except OSError as ex:
            messagebox.showerror("打开失败", str(ex))

    # ---------- 其他 ----------

    def _show_help(self):
        win = tk.Toplevel(self)
        win.title("数据怎么读")
        self._center_window(win, 720, 560)
        win.configure(bg=SURFACE)
        btns = ttk.Frame(win)
        btns.pack(side="bottom", fill="x", pady=6)
        ttk.Button(btns, text="知道了", command=win.destroy).pack()
        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True)
        txt = tk.Text(frame, wrap="word", padx=14, pady=10,
                      font=("Microsoft YaHei UI", 10), bg=SURFACE, fg=TEXT,
                      relief="flat", borderwidth=0)
        sb = ttk.Scrollbar(frame, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        txt.insert("1.0", HELP_TEXT)
        txt.configure(state="disabled")
        win.transient(self)

    def _show_notes(self):
        messagebox.showinfo("口径", "\n".join([
            "扫描与视图全程只读，绝不自动删除。",
            "疑似残留 = 对应不上卸载列表，不等于垃圾；",
            "便携软件、绿色软件会被误标，请人工确认。",
            "移入隔离区 = 可回滚的移动（同盘瞬间完成）。",
            "大小按逻辑大小统计，实际磁盘占用可能更高（簇大小 + 无权访问目录）。",
            "junction / 符号链接不跟随，不会重复计数。",
            "删不删永远由你手动决定，建议先隔离观察 30 天。",
        ]))

    def selftest_report(self):
        return {
            "db": self.db_path,
            "totals_dirs": len(self.totals),
            "tree_roots": len(self.tv.get_children("")),
            "tree_rows": self._count_tree_rows(),
            "rects": len(self.rect_map),
            "chart_rects": len(self.chart_map),
            "hidden_rows": len(self.hidden_tree.get_children()),
            "residual_rows": len(self.residual_tree.get_children()),
            "big_rows": len(self.big_tree.get_children()),
            "old_rows": len(self.old_tree.get_children()),
            "quarantine_rows": len(self.quarantine_tree.get_children()),
            "load_ms": int(self.load_ms),
        }

    def _count_tree_rows(self):
        n = 0
        stack = list(self.tv.get_children(""))
        while stack:
            iid = stack.pop()
            n += 1
            stack.extend(self.tv.get_children(iid))
        return n


def _latest_db():
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(os.path.join(
            os.path.dirname(os.path.abspath(sys.executable)), "data"))
    else:
        candidates.append(os.path.join(HERE, "data"))
    candidates.append(
        r"C:\Users\ksl\Documents\deepseek-harness\default-workspace"
        r"\disk-inspector\data")
    dbs = []
    for d in candidates:
        dbs += glob.glob(os.path.join(d, "scan-*.sqlite"))
    dbs = sorted(set(dbs), key=os.path.getmtime, reverse=True)
    return dbs[0] if dbs else None


def main():
    # 窗口版 exe（--windowed）没有控制台，sys.stdout 为 None，不能直接 reconfigure
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    # DPI 感知：让系统缩放时字体和控件按比例放大，而不是整窗位图拉伸
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--db")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--no-anim", action="store_true",
                    help="关闭启动动效（也受 Windows「显示动画」开关控制）")
    args = ap.parse_args()
    db = args.db or _latest_db()
    if not (args.selftest or args.smoke):
        if not acquire_single_instance():
            _log("another instance is running; wake it and exit")
            return
    app = App(db_path=db, selftest=args.selftest, smoke=args.smoke,
              no_anim=args.no_anim)
    if args.smoke:
        app.smoke_run()
        return
    if args.selftest:
        try:
            line = repr(app.selftest_report())
        except Exception:  # noqa: BLE001
            line = "{}"
        try:
            if sys.stdout is not None:
                print(line)
            else:
                with open(os.path.join(HERE, "selftest.txt"), "w",
                          encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception:  # noqa: BLE001
            pass
        app.destroy()
        return
    app.mainloop()


if __name__ == "__main__":
    main()
