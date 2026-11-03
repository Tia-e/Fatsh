import importlib.util
import os
import sys

CORE = r"D:\Archive\.astrbot_launcher\instances\2cb7b4a6-676f-4d52-9a3b-3d679e1f24a9\core"
sys.path.insert(0, CORE)
os.chdir(CORE)
spec = importlib.util.spec_from_file_location("mem5", os.path.join(CORE, "data", "plugins", "astrbot_plugin_mem", "main.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

inst = m.MemV4.__new__(m.MemV4)
inst.store = m._Mem()
inst.config = {}
cfg = inst._cfg()
rec = inst.store.umo("person:3447899870")

v = inst._visible(rec, cfg)
print("正常注入:", {k: len(v[k]) for k in ("hour", "day", "profile")}, "| 用冻结填:", v["frozen"])

pages = rec["hour"]["pages"]
print("页格数:", len(pages), "状态:", [p.get("state") for p in pages])
if len(pages) >= 2:
    frozen_n = len(pages[-2].get("items", []))
    cur_n = len(pages[-1].get("items", []))
    print(f"当前页 {cur_n} 条，冻结页 {frozen_n} 条")
    saved = list(pages[-1]["items"])
    pages[-1]["items"] = []
    v2 = inst._visible(rec, cfg)
    print("当前页清空后:", {k: len(v2[k]) for k in ("hour", "day", "profile")}, "| 用冻结填:", v2["frozen"])
    print("注入块前 300 字:\n", inst._memory_block(rec, cfg)[:300])
    pages[-1]["items"] = saved
else:
    print("现在只有一格（没有冻结页），没法测空窗")
