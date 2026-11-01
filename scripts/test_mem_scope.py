import asyncio
import hashlib
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
spec = importlib.util.spec_from_file_location("mem4e", PLUG)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

TMP = os.path.join(tempfile.gettempdir(), "mem4_scope")
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP, exist_ok=True)
m.get_astrbot_data_path = lambda: TMP

with open(os.path.join(TMP, "memory_config.json"), "w", encoding="utf-8") as f:
    json.dump({"unify_private_and_group": True, "group_memory_ids": ["3447899870"],
               "page_turns": 40, "max_items": {"hour": 25, "day": 15, "profile": 10}}, f)


class Ev:
    def __init__(self, umo, sid, text=""):
        self.unified_msg_origin = umo
        self._sid = str(sid)
        self.message_str = text

    def get_sender_id(self):
        return self._sid


inst = m.MemV4.__new__(m.MemV4)
inst.config = {}
inst.store = m._Mem()
inst._memo = m.MEMO_SYSTEM
inst._busy = set()
inst._locks = {}
inst._idle_task = None

print("=== 记忆键（按人） ===")
cases = [
    ("私聊·主人", Ev("qq-snowluma:FriendMessage:3447899870", "3447899870")),
    ("私聊·呼噜", Ev("qq-snowluma:FriendMessage:2993991798", "2993991798")),
    ("群·主人说话", Ev("qq-snowluma:GroupMessage:1108936584", "3447899870")),
    ("群·呼噜说话", Ev("qq-snowluma:GroupMessage:1108936584", "2993991798")),
]
for label, ev in cases:
    k = inst._key(ev)
    print(f"  {label:12s} → {k}")

print("\n=== 私聊与群 会写进同一个记忆键 ===")
k1 = inst._key(cases[0][1])
k2 = inst._key(cases[2][1])
print(f"  {k1} == {k2} :", k1 == k2)

print("\n=== 旧数据迁移（会话键 → 人键，归档目录一起改名） ===")
old_umo = "qq-snowluma:FriendMessage:3447899870"
old_hash = hashlib.sha1(old_umo.encode()).hexdigest()[:12]
old_dir = os.path.join(TMP, "mem_archive", old_hash)
os.makedirs(old_dir, exist_ok=True)
with open(os.path.join(old_dir, "seg-0001.json"), "w", encoding="utf-8") as f:
    json.dump({"seq": 1, "umo": old_umo, "turns": [{"ts": 1, "user": "u", "reply": "r"}]}, f)

rec = m._Mem._blank_rec()
rec["hour"]["pages"] = [{"items": [{"content": "TA是我的主人", "type": "who", "importance": 5}],
                          "opened_at": 1, "frozen_at": None, "state": "mutable"}]
with open(os.path.join(TMP, "memory_store_v4.json"), "w", encoding="utf-8") as f:
    json.dump({"umos": {old_umo: rec}}, f, ensure_ascii=False)

st = m._Mem()
d = st._load()
print("  store 键:", list(d["umos"].keys()))
new_hash = hashlib.sha1(m._person_key("3447899870").encode()).hexdigest()[:12]
print("  旧目录还在:", os.path.isdir(old_dir))
print("  新目录存在:", os.path.isdir(os.path.join(TMP, "mem_archive", new_hash)),
      "→ 里面文件:", os.listdir(os.path.join(TMP, "mem_archive", new_hash)) if os.path.isdir(os.path.join(TMP, "mem_archive", new_hash)) else None)
print("  页条目带过来了:", len(list(d["umos"].values())[0]["hour"]["pages"][0]["items"]), "条")
