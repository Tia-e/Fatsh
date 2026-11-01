import asyncio
import importlib.util
import os
import sys

CORE = r"D:\Archive\.astrbot_launcher\instances\2cb7b4a6-676f-4d52-9a3b-3d679e1f24a9\core"
sys.path.insert(0, CORE)
os.chdir(CORE)

spec = importlib.util.spec_from_file_location("mem4c", os.path.join(CORE, "data", "plugins", "astrbot_plugin_mem", "main.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


async def main():
    st = m._Mem()
    data = st._load()          # 触发 _migrate → 清洗 id、按 importance 排序
    await st.save()            # 落盘
    rec = list(data["umos"].values())[0]
    items = rec["hour"]["pages"][-1]["items"]
    print(f"清洗后 {len(items)} 条：")
    for it in items:
        print(f"  [{it['importance']}] {it['type']:5s} {it['content'][:34]}   keys={list(it.keys())}")
    print("\n还有没有 id:", any("id" in i for i in items))
    print("是否按 importance 降序:", [i["importance"] for i in items])


asyncio.run(main())
