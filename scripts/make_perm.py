"""生成 data/cmd_perm.json（salt + sha256 白名单，不出现明文 QQ）

用法：
    python make_perm.py 123456789            # 随机 salt
    python make_perm.py 123456789 mysalt     # 指定 salt
    python make_perm.py 123456789 mysalt 987654321   # 多个 QQ

生成后把 cmd_perm.json 放到 AstrBot 的 data/ 目录。
"""

import hashlib
import json
import os
import secrets
import sys


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    qqs = [a for a in argv[1:] if a.isdigit()]
    salt = next((a for a in argv[1:] if not a.isdigit()), None) or secrets.token_hex(8)
    if not qqs:
        print("至少给一个 QQ 号")
        return 1

    hashes = [hashlib.sha256((salt + qq).encode()).hexdigest() for qq in qqs]
    perm = {"enabled": True, "salt": salt, "allowed_hashes": hashes, "allowed_ids": []}

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "cmd_perm.json")
    out = os.path.abspath(out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(perm, f, ensure_ascii=False, indent=2)
    print(f"已写入 {out}")
    print(f"salt = {salt}")
    print(f"白名单 {len(qqs)} 个号，哈希：")
    for qq, h in zip(qqs, hashes):
        print(f"  {qq} -> {h}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
