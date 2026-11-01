import asyncio
import importlib.util
import json
import os
import shutil
import sys
import tempfile

CORE = r"D:\Archive\.astrbot_launcher\instances\2cb7b4a6-676f-4d52-9a3b-3d679e1f24a9\core"
LIVE_DATA = os.path.join(CORE, "data")
PLUG = r"D:\Work\Code\Hermes\Fatsh\plugins\astrbot_plugin_mem\main.py"
sys.path.insert(0, CORE)
os.chdir(CORE)
spec = importlib.util.spec_from_file_location("mem4m", PLUG)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

TMP = os.path.join(tempfile.gettempdir(), "mem4_migrate")
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP, exist_ok=True)

# 拷贝现网旧的布局
for f in ("memory_store_v4.json", "memory_config.json"):
    src = os.path.join(LIVE_DATA, f)
    if os.path.isfile(src):
        shutil.copy(src, os.path.join(TMP, f))
shutil.copytree(os.path.join(LIVE_DATA, "mem_archive"), os.path.join(TMP, "mem_archive"))

print("迁移前:")
print("  目录:", sorted(os.listdir(os.path.join(TMP, "mem_archive"))))
store = json.load(open(os.path.join(TMP, "memory_store_v4.json"), encoding="utf-8-sig"))
print("  store 键:", list(store["umos"].keys()))

m.get_astrbot_data_path = lambda: TMP


async def main():
    st = m._Mem()
    recs = st._load()
    print("\n迁移后:")
    print("  目录:", sorted(os.listdir(os.path.join(TMP, "mem_archive"))))
    print("  键:", list(recs.keys()))
    for k, rec in recs.items():
        print("   ", k, "页条目:", len(((m._current_page(rec, "hour") or {}).get("items")) or []),
              "本页轮数:", rec["hour"]["turns_in_file"])
    d = os.path.join(TMP, "mem_archive", "3447899870")
    print("  <QQ> 目录内容:", sorted(os.listdir(d)))
    mem = json.load(open(os.path.join(d, "memory.json"), encoding="utf-8-sig"))
    print("  memory.json: key=", mem.get("key"), "qq=", mem.get("qq"))
    seg = json.load(open(os.path.join(d, "seg-0003.json"), encoding="utf-8-sig"))
    print("  seg-0003 turns:", len(seg.get("turns", [])), "首轮 src:", seg["turns"][0].get("src") if seg.get("turns") else None)


asyncio.run(main())
