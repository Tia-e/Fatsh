import importlib.util
import os
import sys

CORE = r"D:\Archive\.astrbot_launcher\instances\2cb7b4a6-676f-4d52-9a3b-3d679e1f24a9\core"
sys.path.insert(0, CORE)
os.chdir(CORE)
spec = importlib.util.spec_from_file_location("help1", os.path.join(CORE, "data", "plugins", "astrbot_plugin_help", "main.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

cmds = m.collect_commands()
print("扫到", len(cmds), "个插件：", [c[0] for c in cmds])
print()
print("📋 可用指令")
print("────────────────────────")
for plugin, lines in cmds:
    print(f"【{plugin}】")
    for ln in lines:
        print(" ", ln)
print()
print("主人名单:", m._owners())
