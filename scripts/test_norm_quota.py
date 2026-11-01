import importlib.util
import os
import sys
from collections import Counter

CORE = r"D:\Archive\.astrbot_launcher\instances\2cb7b4a6-676f-4d52-9a3b-3d679e1f24a9\core"
PLUG = r"D:\Work\Code\Hermes\Fatsh\plugins\astrbot_plugin_mem\main.py"
sys.path.insert(0, CORE)
os.chdir(CORE)
spec = importlib.util.spec_from_file_location("mem4d", PLUG)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

NORM = m.MemV4._norm
QH = {5: None, 4: 12, 3: 8}
QD = {5: None, 4: 8, 3: 6}


def items(tag, n, imp):
    return [{"content": f"{tag}-{i}", "type": "who", "importance": imp} for i in range(n)]


def show(title, src, cap, quota, min_imp=1):
    out = NORM(src, cap, quota, min_imp)
    c = Counter(i["importance"] for i in out)
    print(f"{title}\n  输入 {len(src)} 条 → 输出 {len(out)} 条 | 分布 {dict(sorted(c.items(), reverse=True))}")
    return out


print("=== 页（上限 25，5 分不限 / 4 分 12 / 3 分 8）===")
show("A: 10×5 + 20×4 + 15×3", items("a", 10, 5) + items("b", 20, 4) + items("c", 15, 3), 25, QH)
show("B: 30×5（5 分不限，但总上限 25）", items("a", 30, 5), 25, QH)
show("C: 无 5 分，5×4 + 20×3 + 10×2", items("b", 5, 4) + items("c", 20, 3) + items("d", 10, 2), 25, QH)
show("D: 只有 2 分（补位仍能进）", items("d", 30, 2), 25, QH)

print("\n=== 本（上限 15，只收 ≥3 分）===")
show("E: 20×2（全被拒）", items("d", 20, 2), 15, QD, 3)
show("F: 8×4 + 10×3 + 5×2", items("b", 8, 4) + items("c", 10, 3) + items("d", 5, 2), 15, QD, 3)

print("\n=== 真实 20 条按新规则重排（模拟）===")
real = [
    ("TA是我的「爸爸」，也是我的主人", 5),
    ("TA会中日英三门语言，日语是N1", 5),
    ("TA教我：记忆是小本本上的一页", 5),
    ("TA爱开猎奇玩笑，砍头炖身子那套", 2),
    ("TA爱较真辩理，会追着问理由", 2),
    ("TA接漫画嵌字的活儿", 3),
]
out = NORM([{"content": c, "type": "who", "importance": i} for c, i in real], 25, QH)
for it in out:
    print(f"  [{it['importance']}] {it['content']}")
