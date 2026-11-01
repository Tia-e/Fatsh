import importlib.util
import json
import os
import sys
import time

CORE = r"D:\Archive\.astrbot_launcher\instances\2cb7b4a6-676f-4d52-9a3b-3d679e1f24a9\core"
sys.path.insert(0, CORE)
os.chdir(CORE)
spec = importlib.util.spec_from_file_location("mem4p", os.path.join(CORE, "data", "plugins", "astrbot_plugin_mem", "main.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

inst = m.MemV4.__new__(m.MemV4)
inst.config = {}
tpl = inst._load_memo()
cfg = inst._cfg()

print("模板文件:", "data/prompt/memo_rules.md" if tpl != m.MEMO_SYSTEM else "内置 MEMO_SYSTEM")
print("page_turns:", cfg["page_turns"], "| max_items:", cfg["max_items"], "| quota:", cfg["importance_quota"])
print()

for tier in ("hour", "day", "profile"):
    sys_prompt = (
        tpl.replace("<<TIER>>", m.TIER_CN[tier])
        .replace("<<DUTY>>", m.TIER_DUTY[tier])
        .replace("<<MAX_ITEMS>>", str(cfg["max_items"].get(tier, 10)))
        .replace("<<DATE>>", m._day_label())
    )
    print("=" * 70)
    print(f"### {tier.upper()} 的 system_prompt（{len(sys_prompt)} 字）")
    print("=" * 70)
    print(sys_prompt)
    print()

print("=" * 70)
print("### user_prompt 结构（三个函数里拼的）")
print("=" * 70)
print("""[页 _consolidate_page]
【这一页现在的内容】
{当前页 items 的 JSON（content/type/importance，不带 id）}

【上一页（已冻结，只读参考，不要改动它）】
{冻结页 items 的 JSON}      ← 有上一页时才出现

【素材：这 N 轮原话】
[11-01 16:40] 用户：xxx
[11-01 16:40] 你：xxx
…

[本 _consolidate_book]
【这一本现在的内容】
{当前本 items 的 JSON}

【刚整理完的这几页（只读参考）】
{当前页 items 的 JSON}

[棚 _consolidate_profile]
【棚现在的内容】
{棚 items 的 JSON}

【刚整理完的这一本（只读参考）】
{当前本 items 的 JSON}""")
