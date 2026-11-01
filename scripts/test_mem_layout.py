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
spec = importlib.util.spec_from_file_location("mem4g", PLUG)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

TMP = os.path.join(tempfile.gettempdir(), "mem4_layout")
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP, exist_ok=True)
m.get_astrbot_data_path = lambda: TMP

with open(os.path.join(TMP, "memory_config.json"), "w", encoding="utf-8") as f:
    json.dump({"page_turns": 10, "book_pages": 3, "archive_keep": 4,
               "unify_private_and_group": True, "group_memory_ids": ["3447899870"],
               "max_items": {"hour": 25, "day": 15, "profile": 10}}, f)

inst = m.MemV4.__new__(m.MemV4)
inst.config = {}
inst.store = m._Mem()
inst._memo = m.MEMO_SYSTEM
inst._busy = set()
inst._locks = {}
inst._idle_task = None


class Ev:
    def __init__(self, umo, sid):
        self.unified_msg_origin = umo
        self._sid = str(sid)

    def get_sender_id(self):
        return self._sid


PRIV = Ev("qq-snowluma:FriendMessage:3447899870", "3447899870")
GROUP = Ev("qq-snowluma:GroupMessage:1108936584", "3447899870")
GROUP_OTHER = Ev("qq-snowluma:GroupMessage:1108936584", "2993991798")

KEY = "person:3447899870"
print("键:", inst._key(PRIV), "|", inst._key(GROUP), "|", inst._key(GROUP_OTHER))


async def fake_llm(key, tier, prompt, date_label=""):
    items = [{"content": f"{tier}-事实{i}", "type": "who", "importance": 5 - (i % 3)}
             for i in range(3)]
    return json.dumps({"items": items}, ensure_ascii=False)


inst._llm = fake_llm


async def main():
    print("\n=== 1) 私聊 6 轮 + 群 4 轮 → 同一个人的目录 ===")
    for i in range(6):
        await inst._append_turn(KEY, f"私{i}", f"私回{i}", "private")
    for i in range(4):
        await inst._append_turn(KEY, f"群{i}", f"群回{i}", "group")
    seg = m._seg_read(KEY, 1)
    print(" 目录:", m._person_dir(KEY).replace(TMP, "…"))
    print(" 分片 turns:", [(t["user"], t["src"]) for t in seg["turns"]])
    print(" memory.json 存在:", os.path.isfile(m._mem_file(KEY)))

    print("\n=== 2) 满 10 轮触发页整理，页要标来源 ===")
    await inst._after_turn(KEY)
    rec = inst.store.umo(KEY)
    page = m._current_page(rec, "hour") or {}
    print(" 页条目:", len(page.get("items", [])), "| 来源:", page.get("sources"))
    print(" 指针:", rec["hour"]["file"], "| 本页轮数:", rec["hour"]["turns_in_file"])
    print(" 分片文件:", sorted(os.listdir(m._person_dir(KEY))))

    print("\n=== 3) /mem gen（无参数）：用当前分片立刻整理 ===")
    for i in range(3):
        await inst._append_turn(KEY, f"再{i}", f"再回{i}", "private")
    seq = int(inst.store.umo(KEY)["hour"]["seq"])
    turns = m._seg_turns(KEY, seq)
    ok = await inst._close_and_consolidate(KEY, seq)
    print(f" 当前分片 seg-{seq:04d} 有 {len(turns)} 轮 → 整理成功={ok}")
    print(" 现在指针:", inst.store.umo(KEY)["hour"]["file"], "本页轮数:", inst.store.umo(KEY)["hour"]["turns_in_file"])

    print("\n=== 4) 当前分片为空时，gen 用最近一个有内容的分片 ===")
    filled = [s for s in m._seg_list(KEY) if len(m._seg_turns(KEY, s)) > 0]
    print(" 有内容的分片:", filled, "→ 取", filled[-1])
    ok2 = await inst._consolidate_page(KEY, filled[-1])
    print(" 整理成功:", ok2)


asyncio.run(main())
