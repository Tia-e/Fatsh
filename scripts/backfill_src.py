import importlib.util
import os
import sys

CORE = r"D:\Archive\.astrbot_launcher\instances\2cb7b4a6-676f-4d52-9a3b-3d679e1f24a9\core"
sys.path.insert(0, CORE)
os.chdir(CORE)
spec = importlib.util.spec_from_file_location("mem4h", os.path.join(CORE, "data", "plugins", "astrbot_plugin_mem", "main.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

KEY = "person:3447899870"
for seq in m._seg_list(KEY):
    turns = m._seg_turns(KEY, seq)   # 内部会把缺失的 src 补成 private 并写回
    print(f"seg-{seq:04d}: {len(turns)} 轮，src 全有 =",
          all("src" in t for t in turns))
print("目录:", m._person_dir(KEY).split("\\")[-1])
