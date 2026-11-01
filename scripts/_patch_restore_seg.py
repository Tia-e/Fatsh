import io

path = r"D:\Work\Code\Hermes\Fatsh\plugins\astrbot_plugin_mem\main.py"
s = io.open(path, encoding="utf-8").read()

block = '''# ---------------- 分片文件（每人一个目录） ----------------
def _seg_path(key: str, seq: int) -> str:
    return os.path.join(_person_dir(key), f"seg-{seq:04d}.json")


def _seg_write(key: str, seq: int, data: dict) -> None:
    d = _person_dir(key)
    os.makedirs(d, exist_ok=True)
    p = _seg_path(key, seq)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def _seg_read(key: str, seq: int) -> dict | None:
    try:
        with open(_seg_path(key, seq), "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return None


def _seg_list(key: str) -> list[int]:
    d = _person_dir(key)
    if not os.path.isdir(d):
        return []
    out = []
    for name in os.listdir(d):
        m = re.match(r"seg-(\\d{4})\\.json$", name)
        if m:
            out.append(int(m.group(1)))
    return sorted(out)


def _seg_prune(key: str, keep: int) -> list[int]:
    seqs = _seg_list(key)
    keep = max(2, int(keep))
    if len(seqs) <= keep:
        return []
    drop = seqs[: len(seqs) - keep]
    for s in drop:
        try:
            os.remove(_seg_path(key, s))
        except Exception:
            pass
    return drop


def _seg_turns(key: str, seq: int) -> list[dict]:
    d = _seg_read(key, seq) or {}
    turns = d.get("turns")
    return turns if isinstance(turns, list) else []


def _turns_to_text(turns: list[dict]) -> str:
    lines = []
    for t in turns:
        when = time.strftime("%m-%d %H:%M", time.localtime(t.get("ts", 0)))
        src = "群" if str(t.get("src") or "").lower().startswith("group") else "私聊"
        if t.get("user"):
            lines.append(f"[{when}|{src}] 用户：{t['user']}")
        if t.get("reply"):
            lines.append(f"[{when}|{src}] 你：{t['reply']}")
    return "\\n".join(lines)


def _render_items(items: list) -> str:
    if not items:
        return "[]"
    return json.dumps(
        [{"content": i.get("content"), "type": i.get("type"), "importance": i.get("importance")}
         for i in items],
        ensure_ascii=False,
    )


'''

idx = s.index("class _Mem:")
s = s[:idx] + block + s[idx:]
io.open(path, "w", encoding="utf-8").write(s)
print("inserted seg helpers before _Mem, length:", len(s))
