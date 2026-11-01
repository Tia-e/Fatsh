import asyncio
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time

ROOT = r"D:\Archive\.astrbot_launcher\instances\2cb7b4a6-676f-4d52-9a3b-3d679e1f24a9"
CORE = os.path.join(ROOT, "core")
PLUG = r"D:\Work\Code\Hermes\Fatsh\plugins\astrbot_plugin_mem\main.py"
sys.path.insert(0, CORE)
os.chdir(CORE)

spec = importlib.util.spec_from_file_location("mem4b", PLUG)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

TMP = os.path.join(tempfile.gettempdir(), "mem4_fix")
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP, exist_ok=True)
m.get_astrbot_data_path = lambda: TMP

with open(os.path.join(TMP, "memory_config.json"), "w", encoding="utf-8") as f:
    json.dump({"page_turns": 10, "book_pages": 3, "profile_books": 3, "archive_keep": 4,
               "idle_seconds": 2, "idle_min_turns": 2,
               "max_items": {"hour": 20, "day": 12, "profile": 8}}, f)

inst = m.MemV4.__new__(m.MemV4)
inst.config = {}
inst.store = m._Mem()
inst._memo = m.MEMO_SYSTEM
inst._busy = set()
inst._locks = {}
inst._idle_task = None

calls = {"page": 0}


async def fake_llm(umo, tier, prompt, date_label=""):
    calls["page"] += 1
    items = [{"id": f"h-{calls['page']}-{i}", "content": f"事实{i}（素材{len(prompt)}字）",
              "type": "who", "importance": 3} for i in range(min(3, calls["page"]))]
    return json.dumps({"items": items}, ensure_ascii=False)


inst._llm = fake_llm
UMO = "t:umo"


async def main():
    print("=== 1) 满 10 轮必须立刻换分片 ===")
    for i in range(1, 26):
        await inst._append_turn(UMO, f"u{i}", f"r{i}")
        await inst._after_turn(UMO)
        rec = inst.store.umo(UMO)
        if rec["hour"]["turns_in_file"] == 0:
            print(f"  第 {i} 轮后换片 → 现在 seg-{rec['hour']['seq']:04d}.json，pages_done={rec['hour']['pages_done']}")
    rec = inst.store.umo(UMO)
    print(f"  结束：分片={m._seg_list(UMO)} 指针={rec['hour']['file']} 本页轮数={rec['hour']['turns_in_file']}"
          f" pages_done={rec['hour']['pages_done']} 页条目={len(m._current_page(rec,'hour')['items'])}")
    assert rec["hour"]["turns_in_file"] < 10, "还有分片没换"

    print("\n=== 2) 空闲兜底 ===")
    before = m._seg_list(UMO)
    for i in range(3):
        await inst._append_turn(UMO, f"闲{i}", f"闲回{i}")
    await asyncio.sleep(2.5)
    await inst._idle_consolidate(UMO)
    rec = inst.store.umo(UMO)
    after = m._seg_list(UMO)
    print(f"  3 轮 + 静默 2.5s → 分片 {before} → {after}，本页轮数={rec['hour']['turns_in_file']}")

    print("  再调两次（应该什么都不做，不会重复换片）")
    await inst._idle_consolidate(UMO)
    await inst._idle_consolidate(UMO)
    rec2 = inst.store.umo(UMO)
    print(f"  分片={m._seg_list(UMO)} 本页轮数={rec2['hour']['turns_in_file']} 指针={rec2['hour']['file']}")
    assert m._seg_list(UMO) == after, "重复换片了"

    print(f"\n_busy 残留: {inst._busy}（应为空）")
    assert not inst._busy, "busy 没清干净"

    print("\n=== 3) 关掉后继续聊，能正常再触发 ===")
    for i in range(1, 12):
        await inst._append_turn(UMO, f"续{i}", f"续回{i}")
        await inst._after_turn(UMO)
    rec = inst.store.umo(UMO)
    print(f"  分片={m._seg_list(UMO)} 指针={rec['hour']['file']} 本页轮数={rec['hour']['turns_in_file']}"
          f" pages_done={rec['hour']['pages_done']} 本={len((m._current_page(rec,'day') or {}).get('items',[]))} 条")
    print("\n全部通过 ✓")


asyncio.run(main())
