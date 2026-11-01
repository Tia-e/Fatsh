import asyncio
import importlib.util
import json
import os
import shutil
import sys
import tempfile

CORE = r"D:\Archive\.astrbot_launcher\instances\2cb7b4a6-676f-4d52-9a3b-3d679e1f24a9\core"
PLUG = r"D:\Work\Code\Hermes\Fatsh\plugins\astrbot_plugin_mem\main.py"
sys.path.insert(0, CORE)
os.chdir(CORE)
spec = importlib.util.spec_from_file_location("mem4i", PLUG)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

TMP = os.path.join(tempfile.gettempdir(), "mem4_single")
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP, exist_ok=True)
m.get_astrbot_data_path = lambda: TMP

with open(os.path.join(TMP, "memory_config.json"), "w", encoding="utf-8") as f:
    json.dump({"page_turns": 5, "book_pages": 99, "profile_books": 99,
               "unify_private_and_group": True, "group_memory_ids": ["3447899870"],
               "max_items": {"hour": 25, "day": 15, "profile": 10}}, f)

inst = m.MemV4.__new__(m.MemV4)
inst.config = {}
inst.store = m._Mem()
inst._memo = m.MEMO_SYSTEM
inst._busy = set()
inst._locks = {}
inst._idle_task = None

KEY = "person:3447899870"


async def fake_llm(key, tier, prompt, date_label=""):
    return json.dumps({"items": [{"content": f"{tier}事实{i}", "type": "who", "importance": 4}
                                 for i in range(3)]}, ensure_ascii=False)


inst._llm = fake_llm


async def main():
    print("=== 1) 每轮写进 <QQ>.json ===")
    for i in range(4):
        await inst._append_turn(KEY, f"私{i}", f"回{i}", "private")
    await inst._append_turn(KEY, "群问", "群回", "group")
    node = m._turns_read(KEY)
    print(" 文件:", m._turns_path(KEY).replace(TMP, "…"))
    print(" 轮数:", len(node["turns"]), "| src:", [t["src"] for t in node["turns"]])
    print(" 目录:", sorted(os.listdir(m._person_dir(KEY))))
    rec = inst.store.umo(KEY)
    print(" turns_in_file:", rec["hour"]["turns_in_file"])

    print("\n=== 2) 到 5 轮触发整理 → 待整理清空 ===")
    await inst._after_turn(KEY)
    node = m._turns_read(KEY)
    rec = inst.store.umo(KEY)
    page = m._current_page(rec, "hour") or {}
    print(" 整理后 turns:", len(node["turns"]), "| last_consolidated:", bool(node.get("last_consolidated")))
    print(" 页:", len(page.get("items", [])), "条 | sources:", page.get("sources"))
    print(" turns_in_file:", rec["hour"]["turns_in_file"], "| pages_done:", rec["hour"]["pages_done"])

    print("\n=== 3) gen 行为 ===")
    print(" 空的时候:")
    print("  ", await inst._consolidate_page(KEY), "(False = 没有待整理)")
    await inst._append_turn(KEY, "再问", "再回", "private")
    ok = await inst._consolidate_page(KEY)
    print(" 有 1 轮时:", ok, "| 整理后 turns:", len(m._turns_read(KEY)["turns"]))
    print(" 目录最终:", sorted(os.listdir(m._person_dir(KEY))))


asyncio.run(main())
