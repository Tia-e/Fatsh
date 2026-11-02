import importlib.util
import os
import sys

CORE = r"D:\Archive\.astrbot_launcher\instances\2cb7b4a6-676f-4d52-9a3b-3d679e1f24a9\core"
PLUG = r"D:\Work\Code\Hermes\Fatsh\plugins\astrbot_plugin_human_delay\main.py"
sys.path.insert(0, CORE)
os.chdir(CORE)
spec = importlib.util.spec_from_file_location("hd", PLUG)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

inst = m.HumanDelay.__new__(m.HumanDelay)
inst.config = {}

cases = {
    "短句口吻": "啊 我长这样 蓝毛女仆 行吧 我认",
    "两句闲聊": "今天真累\n不想动",
    "中篇（70 字）": "刚把记忆那块改完了，现在三个人格各记各的。你要是想知道我这套是怎么分的，我可以慢慢讲，不过先问一句，你是想听短的说还是想听全的。",
    "长文一段（约 200 字）": (
        "记忆这块现在分三层，页是本子的一页，本是一摞页，棚是长期画像。"
        "每四十轮聊完就把待整理的对话喂给页，页重写完就把对话清空，"
        "页被整理五次之后才轮到本去吸，本被整理五次之后才轮到棚。"
        "这样做的好处是短期细节不会挤掉长期画像，坏处是细节丢得快，"
        "你要是想留细节就得把页的上限调大或者把轮数调小。"
    ),
    "长文多段": (
        "先说结论：分段这块要分两种模式。\n\n"
        "平时闲聊还是短句，一条一两句，最多四条，一行说完就换行。这样读起来像在群里说话，不会一大坨压过来。\n\n"
        "讲正经事的时候可以写长，解释、复盘、方案、步骤这些，允许一百到两百字，分两到四段，每段一到三句，该用句号就用句号。\n\n"
        "判断标准就一条：对方是想聊还是要东西。拿不准先短，他说详细点再展开。"
    ),
    "带列表": "- 第一点\n- 第二点\n- 第三点",
    "代码块": "给你这段：\n```python\nprint('hi')\n```\n就这样",
}

for name, text in cases.items():
    segs = inst.split_segments(text)
    no_ws = len(text.replace(" ", "").replace("\n", ""))
    print(f"\n【{name}】{no_ws} 字 → {len(segs)} 段")
    for i, s in enumerate(segs, 1):
        print(f"   {i}. ({len(s)} 字) {s[:70]}{'…' if len(s) > 70 else ''}")
