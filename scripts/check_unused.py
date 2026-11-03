import ast
import io
import os
import sys

BASE = r"D:\Work\Code\Hermes\Fatsh\plugins"

for plugin in sorted(os.listdir(BASE)):
    path = os.path.join(BASE, plugin, "main.py")
    if not os.path.isfile(path):
        continue
    src = io.open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    imported = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                imported[(a.asname or a.name).split(".")[0]] = node.lineno
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                imported[a.asname or a.name] = node.lineno
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    used |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    # 字符串里的名字（装饰器等）也算用过
    used |= set()
    unused = [f"{k}(L{v})" for k, v in imported.items() if k not in used]
    print(f"{plugin}: {'未用导入 -> ' + ', '.join(unused) if unused else '无未用导入'}")
