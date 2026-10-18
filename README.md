<div align="center">

# Arbor

**只读的 Windows 磁盘空间分析工具：看清每一块空间从哪来**

![Arbor](icons/arbor-512.png)

![Platform](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-0078D6?style=flat-square)
![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?style=flat-square)
![License](https://img.shields.io/badge/license-MIT-3DA639?style=flat-square)
![Dependencies](https://img.shields.io/badge/dependency-无第三方包-4B8BBE?style=flat-square)
![Read-only](https://img.shields.io/badge/scan-只读-287D78?style=flat-square)

</div>

---

## 这是什么

Arbor 把一块硬盘的空间摊开给你看：目录树、占比长条图、隐藏文件、疑似残留，一屏看完。

它只读——扫描和查看全程不修改、不移动、不删除任何文件。要清理，只能由你把条目「移入隔离区」，而隔离区是可随时还原的移动，不是删除。

## 能做什么

| 能力 | 说明 |
|---|---|
| 目录树 | 真实父子结构，同级按大小降序；展开才加载；大小 / 占父级 / 占扫描根 / 项目数 / 状态 |
| 长条占比图 | 条长按当前范围最大项归一，颜色越深越大；点条下钻，右键返回 |
| 隐藏文件 | 单独一页：隐藏 / 系统属性、隐藏目录、NTFS 备用数据流（ADS）按需检测 |
| 疑似残留 | 比对注册表卸载列表，列出对不上安装记录的目录，带置信度，只标「疑似」 |
| 大文件 / 很久没动 | 按大小、按修改时间筛选 |
| 隔离区 | 移动而非删除；清单先记 pending 再移动；界面可一键还原 |
| 防误删 | 系统与重要目录禁止移入；可疑目录二次确认；普通位置确认 |
| 帮助 | 「数据怎么读」一页讲清每个字段和百分比的分母 |

## 安全边界

- 扫描与视图全程只读，绝不自动删除
- 疑似残留＝对不上安装记录，不等于垃圾；便携 / 绿色软件会被误标
- 移入隔离区＝可回滚的移动，同盘瞬间完成
- 禁止移入：盘符根、用户主目录、桌面 / 文档 / 下载 / 图片 / 音乐 / 视频、Windows、Program Files、微软组件、系统还原点、回收站、程序自身目录
- 无需管理员权限；`Windows.old`、系统还原点这类目录要提权扫描才看得到

## 快速开始

**方式 A：下载现成程序**（不需要 Python）

到 [Releases](https://github.com/Ayanami-lppl/arbor/releases) 下载 `Arbor-windows-x64.zip`，解压后双击 `Arbor.exe`。

**方式 B：从源码运行**

```bash
git clone https://github.com/Ayanami-lppl/arbor.git
cd arbor
python scan.py C:\ D:\ --exclude data   # 第一次：全盘扫描
python gui.py                          # 打开界面（自动读取最新扫描库）
```

Python 3.10+，Tkinter 随 Python 自带，无第三方依赖。

## 扫描

```bash
python scan.py C:\ D:\ --exclude data        # 全盘扫描 → data/scan-*.sqlite
python scan.py --db data/custom.sqlite D:\   # 只扫某个盘 / 指定库
```

也可以双击 `扫描C和D盘.bat`（普通）或 `扫描C和D盘(提权).bat`（提权，能看到 `Windows.old` 等目录）。

本机实测：约 42 万个文件 / 265 GB / 45 秒；随机抽样复核 15/15 一致。

## 命令行视图（可选）

```bash
python views.py tree   data/scan-xxx.sqlite   # 占比树
python views.py hidden data/scan-xxx.sqlite   # 隐藏文件专项
python views.py ads    C:\Users               # ADS 检测
python residual.py     data/scan-xxx.sqlite   # 疑似残留
```

## 打包 exe

```bash
node tools/fetch-wheels.mjs     # 用 DSH 自带的 node 从 PyPI 拉 PyInstaller wheel
python tools/unpack-wheels.py   # 解包到 pylibs/
PYTHONPATH=pylibs python -m PyInstaller --onedir --windowed \
  --icon icons/arbor.ico --name Arbor gui.py
```

不要用 `--onefile`：单文件版启动时要解压到系统临时目录，临时目录受限的机器上会直接失败。

## 文件

| 文件 | 作用 |
|---|---|
| `scan.py` | 扫描器 → SQLite + 中文汇总报告（junction / 符号链接不跟随） |
| `analyze.py` | 目录聚合（谁包含谁、总大小、直属文件索引） |
| `views.py` | 命令行视图：tree / hidden / ads |
| `residual.py` | 疑似残留识别（注册表匹配 + 按软件去重 + 置信度） |
| `gui.py` | Tkinter 界面：目录树、长条图、列表、隔离区、帮助 |
| `quarantine.py` | 隔离区（可回滚移动 + 清单） |
| `verify-scan.py` / `verify-sample.py` | 扫描结果复核（全量 / 抽样） |
| `tests/` | 四套回归测试 |
| `icons/` | 图标（多尺寸 `.ico` + 生成脚本） |

## 测试

```bash
python tests/test_tree.py        # 目录树 15 项
python tests/test_misc.py        # 排序 / 徽标 / 搜索 / ADS / 防误删
python tests/test_phase4.py      # 启动页、坏库、加载中关闭
python tests/test_quarantine.py  # 隔离区移动与还原
python gui.py --selftest         # 全视图自检
```

## 已知限制

- 大小按逻辑大小统计；实际磁盘占用受簇大小、无权访问目录影响
- `Windows.old`、系统还原点等目录普通权限读不到，需要提权扫描
- 疑似残留会误标便携软件；中文名软件（如微信）对不上英文目录名
- 只在 Windows 10 / 11 实测，其他平台未测

## 版本

| 版本 | 内容 |
|---|---|
| v1.2 | 帮助窗口、防误删三级防护、窗口居中 + DPI 缩放 |
| v1.0 | 启动动效「空间长出枝脉」、打包回归 |
| v0.9 | 左侧导航、浅色主题、联动长条图 |
| v0.8 | 真实目录树 + 详情区 |
| v0.7 | 启动重构（后台加载、只读库、单实例） |
| v0.6 | junction 修复、隔离区加固、残留去重 |
| v0.1–v0.5 | 扫描器 → 视图 → 残留识别 → 界面 → exe |

## License

MIT
