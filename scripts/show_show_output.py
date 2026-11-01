import importlib.util
import os
import sys

CORE = r"D:\Archive\.astrbot_launcher\instances\2cb7b4a6-676f-4d52-9a3b-3d679e1f24a9\core"
sys.path.insert(0, CORE)
os.chdir(CORE)
spec = importlib.util.spec_from_file_location("mem4k", os.path.join(CORE, "data", "plugins", "astrbot_plugin_mem", "main.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

inst = m.MemV4.__new__(m.MemV4)
inst.store = m._Mem()
inst.config = {}
cfg = inst._cfg()
rec = inst.store.umo("person:3447899870")
v = inst._visible(rec, cfg)

# 复刻 /mem show 的输出
lines = ["🧠 现在会带进聊天的记忆", "────────────────────────"]
for k, label in (("hour", "页"), ("hour_frozen", "页·已冻结"),
                 ("day", "本"), ("day_frozen", "本·已冻结"),
                 ("profile", "棚")):
    items = v.get(k, [])
    lines.append(f"【{label}】{len(items)} 条")
    lines += [f"  · {i.get('content')}" for i in items] or ["  （空）"]
    lines.append("")
lines.append(f"合计 {sum(len(v.get(k, [])) for k in v)} 条（都会注入）")
print("\n".join(lines))
