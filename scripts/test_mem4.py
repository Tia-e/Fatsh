import asyncio
import importlib.util
import json
import os
import shutil
import sys
import tempfile

ROOT = r"D:\Archive\.astrbot_launcher\instances\2cb7b4a6-676f-4d52-9a3b-3d679e1f24a9"
CORE = os.path.join(ROOT, "core")
PLUG = r"D:\Work\Code\Hermes\Fatsh\plugins\astrbot_plugin_mem\main.py"
sys.path.insert(0, CORE)
os.chdir(CORE)

spec = importlib.util.spec_from_file_location("mem4", PLUG)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

TMP = os.path.join(tempfile.gettempdir(), "mem4_test")
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP, exist_ok=True)
m.get_astrbot_data_path = lambda: TMP

with open(os.path.join(TMP, "memory_config.json"), "w", encoding="utf-8") as f:
    json.dump(
        {
            "page_turns": 5,
            "book_pages": 2,
            "profile_books": 2,
            "archive_keep": 3,
            "idle_seconds": 1800,
            "idle_min_turns": 2,
            "max_items": {"hour": 20, "day": 12, "profile": 8},
        },
        f,
    )

inst = m.MemV4.__new__(m.MemV4)
inst.config = {}
inst.store = m._Mem()
inst._memo = m.MEMO_SYSTEM
inst._busy = set()
inst._locks = {}
inst._idle_task = None

calls = {"page": 0, "book": 0, "profile": 0}


async def fake_llm(umo, tier, prompt, date_label=""):
    calls[tier] = calls.get(tier, 0) + 1
    n = calls[tier]
    # 模拟模型：整页重写，条目数随次数增长，带 id
    items = [
        {
            "id": f"{tier}-{n}-{i}",
            "content": f"[{tier}#{n}] 事实{i}（素材长度 {len(prompt)}）",
            "type": "who",
            "importance": 3,
        }
        for i in range(min(3, n))
    ]
    return json.dumps({"items": items}, ensure_ascii=False)


inst._llm = fake_llm

UMO = "test:umo"


async def main():
    # 前 4 轮：不满一页，不该整理
    for i in range(4):
        await inst._append_turn(UMO, f"用户第{i}句", f"回复第{i}句")
        await inst._after_turn(UMO)
    rec = inst.store.umo(UMO)
    print(f"[4 轮后] 分片={m._seg_list(UMO)} 本页轮数={rec['hour']['turns_in_file']} 页条目={len((m._current_page(rec,'hour') or {}).get('items', []))} 页整理次数={calls['page']}")

    # 第 5 轮：该触发页整理 + 开新分片
    await inst._append_turn(UMO, "用户第4句", "回复第4句")
    await inst._after_turn(UMO)
    seqs = m._seg_list(UMO)
    rec = inst.store.umo(UMO)
    print(f"[5 轮后] 分片={seqs} 指针={rec['hour']['file']} 本页轮数={rec['hour']['turns_in_file']} 页条目={len((m._current_page(rec,'hour') or {}).get('items', []))} 页整理={calls['page']}")
    seg1 = m._seg_read(UMO, 1)
    print(f"         seg-0001 turns={len(seg1['turns'])} closed_at={'有' if seg1.get('closed_at') else '无'} seg-0002 turns={len(m._seg_read(UMO,2)['turns'])}")

    # 再跑到 15 轮：5 轮/页 → 3 次页整理 → 触发 1 次本整理（book_pages=2）
    for i in range(5, 15):
        await inst._append_turn(UMO, f"用户第{i}句", f"回复第{i}句")
        await inst._after_turn(UMO)
    rec = inst.store.umo(UMO)
    print(f"[15 轮后] 分片={m._seg_list(UMO)} 页={len((m._current_page(rec,'hour') or {}).get('items', []))} 条"
          f" 本={len((m._current_page(rec,'day') or {}).get('items', []))} 条"
          f" pages_done={rec['hour']['pages_done']} books_done={rec['day']['books_done']}"
          f" 调用 page={calls['page']} book={calls['book']} profile={calls['profile']}")
    print(f"         分片文件数={len(m._seg_list(UMO))}（archive_keep=3）")

    # gen：拿最近 2 个分片整理
    seqs = m._seg_list(UMO)
    take = seqs[-2:]
    done = 0
    for s in take:
        if await inst._consolidate_page(UMO, s):
            done += 1
    print(f"[gen 2] 处理 {done}/{len(take)} 个分片，页现在 {len((m._current_page(inst.store.umo(UMO),'hour') or {}).get('items', []))} 条")

    # gen 超量：要 10 个但只有 3 个 → 不报错
    seqs = m._seg_list(UMO)
    take = seqs[-10:]
    done = sum([1 for s in take if await inst._consolidate_page(UMO, s)])
    print(f"[gen 10→实际] {done}/{len(take)}，不报错 ✓")

    print("\n记忆块预览：")
    print(inst._memory_block(inst.store.umo(UMO), inst._cfg())[:400])


asyncio.run(main())
