# -*- coding: utf-8 -*-
"""tests/hold_mutex.py -- 单实例互斥体测试：第一个进程持有 4 秒，第二个进程应失败"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gui

print("hold:", gui.acquire_single_instance(), flush=True)
time.sleep(4)
print("hold-exit", flush=True)
