import asyncio
import importlib.util
import os
import sys

CORE = r"D:\Archive\.astrbot_launcher\instances\2cb7b4a6-676f-4d52-9a3b-3d679e1f24a9\core"
sys.path.insert(0, CORE)
os.chdir(CORE)
spec = importlib.util.spec_from_file_location("mem4f", os.path.join(CORE, "data", "plugins", "astrbot_plugin_mem", "main.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


async def main():
    st = m._Mem()
    d = st._load()
    await st.save()
    print("落盘后的键:", list(d["umos"].keys()))
    rec = list(d["umos"].values())[0]
    print("页条目:", len(rec["hour"]["pages"][-1]["items"]),
          "| 本页轮数:", rec["hour"]["turns_in_file"],
          "| 指针:", rec["hour"]["file"],
          "| pages_done:", rec["hour"]["pages_done"])


asyncio.run(main())
