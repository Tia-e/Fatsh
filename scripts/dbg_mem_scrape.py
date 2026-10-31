import importlib.util
import json
import os
import sys
import time

ROOT = r"D:\Archive\.astrbot_launcher\instances\2cb7b4a6-676f-4d52-9a3b-3d679e1f24a9"
CORE = os.path.join(ROOT, "core")
DATA = os.path.join(CORE, "data")
sys.path.insert(0, CORE)
os.chdir(CORE)

spec = importlib.util.spec_from_file_location("mem", os.path.join(DATA, "plugins", "astrbot_plugin_mem", "main.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

inst = m.MemV3.__new__(m.MemV3)
inst.config = {}

UMO = "qq-snowluma:FriendMessage:3447899870"
now = time.time()

print("=== 抓取实测（_db_entries）===")
for label, since, limit in (
    ("最近1小时", now - 3600, 100),
    ("最近6小时", now - 6 * 3600, 250),
    ("最近24小时", now - 24 * 3600, 250),
    ("全部(since=0)", 0, 999),
):
    e = inst._db_entries(UMO, since, limit)
    if e:
        t0 = time.strftime("%m-%d %H:%M", time.localtime(e[0]["ts"]))
        t1 = time.strftime("%m-%d %H:%M", time.localtime(e[-1]["ts"]))
        uniq = len({x["ts"] for x in e})
        print(f"{label:14s} → {len(e):3d} 条 | {t0} ~ {t1} | 不同时间戳 {uniq} 个")
    else:
        print(f"{label:14s} → 0 条")

print()
print("=== 时间戳重复情况（全部历史）===")
e = inst._db_entries(UMO, 0, 9999)
from collections import Counter
c = Counter(x["ts"] for x in e)
dups = [(time.strftime('%m-%d %H:%M', time.localtime(k)), v) for k, v in c.most_common(6)]
print("最常见的几个时间点（时间, 该时间下条数）:")
for d in dups:
    print("  ", d)

print()
print("=== 当前页/本/棚 ===")
store = m._Mem()
store._load()
rec = store.umo(UMO)
hp = m._current_page(rec, "hour") or {}
print(f"页 last_at={time.strftime('%m-%d %H:%M', time.localtime(rec['hour']['last_at']))}  cursor={time.strftime('%m-%d %H:%M', time.localtime(rec['hour']['cursor']))}  items={len(hp.get('items', []))}")
for i in hp.get("items", []):
    print("   ·", i.get("content"))
dp = m._current_page(rec, "day") or {}
print(f"本 items={len(dp.get('items', []))} | 棚 items={len(rec['profile']['items'])}")
print()
print("=== _day_label 检查 ===")
print("今天:", m._day_label())
for n in range(0, 4):
    print(f" {-n} 天:", m._day_label(m._days_ago(n)))
