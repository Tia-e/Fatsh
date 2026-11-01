import importlib.util
import os
import sys

CORE = r"D:\Archive\.astrbot_launcher\instances\2cb7b4a6-676f-4d52-9a3b-3d679e1f24a9\core"
sys.path.insert(0, CORE)
os.chdir(CORE)
spec = importlib.util.spec_from_file_location("mem4j", os.path.join(CORE, "data", "plugins", "astrbot_plugin_mem", "main.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

inst = m.MemV4.__new__(m.MemV4)
inst.store = m._Mem()
inst.config = {}
cfg = inst._cfg()
KEY = "person:3447899870"
rec = inst.store.umo(KEY)

vis = inst._visible(rec, cfg)
print("注入条数分布:", {k: len(v) for k, v in vis.items()})
block = inst._memory_block(rec, cfg)
print("记忆块字数:", len(block), "≈ tokens:", int(len(block) * 1.31))
print()
print(block[:1500])
print()
pages = rec["hour"]["pages"]
print("页格数:", len(pages), "状态:", [p.get("state") for p in pages])
print("本格数:", len(rec["day"]["pages"]), "状态:", [p.get("state") for p in rec["day"]["pages"]])
