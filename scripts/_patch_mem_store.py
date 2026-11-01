import io
import re

path = r"D:\Work\Code\Hermes\Fatsh\plugins\astrbot_plugin_mem\main.py"
s = io.open(path, encoding="utf-8").read()

start = s.index("class _Mem:")
end = s.index("def _current_page(")

new_class = '''class _Mem:
    """一个人的记忆一个文件：data/mem_archive/<QQ>/memory.json"""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._recs: dict | None = None

    @staticmethod
    def _blank_rec() -> dict:
        return {
            "hour": {"pages": [], "file": None, "seq": 0, "turns_in_file": 0,
                     "last_turn_at": 0, "pages_done": 0, "last_at": 0},
            "day": {"pages": [], "books_done": 0, "last_at": 0},
            "profile": {"items": [], "last_at": 0},
        }

    @staticmethod
    def _migrate(rec: dict) -> dict:
        blank = _Mem._blank_rec()
        for k, v in blank.items():
            if not isinstance(rec.get(k), dict):
                rec[k] = v
            else:
                for kk, vv in v.items():
                    rec[k].setdefault(kk, vv)
        for tier in ("hour", "day"):
            if not isinstance(rec[tier].get("pages"), list):
                rec[tier]["pages"] = []
            for page in rec[tier]["pages"]:
                if isinstance(page, dict) and isinstance(page.get("items"), list):
                    page["items"] = _clean_items(page["items"])
        if not isinstance(rec["profile"].get("items"), list):
            rec["profile"]["items"] = []
        rec["profile"]["items"] = _clean_items(rec["profile"]["items"])
        return rec

    # ---------- 读写 ----------
    def _save_rec(self, key: str) -> None:
        rec = (self._recs or {}).get(key)
        if rec is None:
            return
        d = _person_dir(key)
        os.makedirs(d, exist_ok=True)
        p = _mem_file(key)
        tmp = p + ".tmp"
        payload = {
            "key": key,
            "qq": _qq_of_key(key),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "rec": rec,
        }
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=1)
            os.replace(tmp, p)
        except Exception as e:
            logger.error(f"mem4: 写 {p} 失败 {e}")

    async def save(self, key: str | None = None) -> None:
        async with self._lock:
            if key:
                self._save_rec(key)
            else:
                for k in list((self._recs or {}).keys()):
                    self._save_rec(k)

    def _load(self) -> dict:
        if self._recs is not None:
            return self._recs
        self._recs = {}
        base = os.path.join(_data_dir(), "mem_archive")
        if os.path.isdir(base):
            for name in os.listdir(base):
                if not name.isdigit():
                    continue
                p = os.path.join(base, name, "memory.json")
                try:
                    if os.path.isfile(p):
                        with open(p, "r", encoding="utf-8-sig") as f:
                            d = json.load(f)
                        rec = d.get("rec") if isinstance(d, dict) and isinstance(d.get("rec"), dict) else d
                    else:
                        rec = self._blank_rec()
                except Exception as e:
                    logger.error(f"mem4: 读 {name}/memory.json 失败 {e}")
                    rec = self._blank_rec()
                self._recs[f"person:{name}"] = self._migrate(rec)
        self._migrate_legacy()
        return self._recs

    def _migrate_legacy(self) -> None:
        """旧布局（单文件 store + 哈希分片目录）→ mem_archive/<QQ>/。"""
        changed = False
        # 1) 旧 store：键可能是会话 umo 或 person:QQ
        for fname in ("memory_store_v4.json", "memory_store_v3.json"):
            p = os.path.join(_data_dir(), fname)
            if not os.path.isfile(p):
                continue
            try:
                with open(p, "r", encoding="utf-8-sig") as f:
                    d = json.load(f)
            except Exception as e:
                logger.error(f"mem4: 迁移 {fname} 失败 {e}")
                continue
            for key, rec in (d.get("umos") or {}).items():
                qq = _qq_of_key(key) or _umo_person(key)
                if not qq or not isinstance(rec, dict):
                    continue
                new = _person_key(qq)
                if new in self._recs:
                    continue
                self._recs[new] = self._migrate(rec)
                changed = True
        # 2) 哈希名分片目录 → <QQ>/
        base = os.path.join(_data_dir(), "mem_archive")
        if os.path.isdir(base):
            for name in os.listdir(base):
                p = os.path.join(base, name)
                if not os.path.isdir(p) or name.isdigit():
                    continue
                qq = None
                for f in sorted(os.listdir(p)):
                    if f.startswith("seg-") and f.endswith(".json"):
                        try:
                            with open(os.path.join(p, f), "r", encoding="utf-8-sig") as fh:
                                meta = json.load(fh)
                        except Exception:
                            continue
                        src = str(meta.get("key") or meta.get("umo") or "")
                        qq = _qq_of_key(src) or _umo_person(src)
                        if qq:
                            break
                if not qq:
                    continue
                target = os.path.join(base, qq)
                os.makedirs(target, exist_ok=True)
                for f in os.listdir(p):
                    src_p = os.path.join(p, f)
                    dst_p = os.path.join(target, f)
                    if os.path.isfile(src_p) and not os.path.exists(dst_p):
                        try:
                            os.replace(src_p, dst_p)
                            changed = True
                        except Exception:
                            pass
                try:
                    if not os.listdir(p):
                        os.rmdir(p)
                except Exception:
                    pass
        if changed:
            for k in list(self._recs.keys()):
                self._save_rec(k)
            logger.info(f"mem4: 迁移完成，共 {len(self._recs)} 份记忆 → mem_archive/<QQ>/memory.json")

    def umo(self, key: str) -> dict:
        recs = self._load()
        rec = recs.get(key)
        if not isinstance(rec, dict):
            rec = self._blank_rec()
            recs[key] = rec
        return self._migrate(rec)

    def all_umos(self) -> list[str]:
        return list(self._load().keys())


'''

s = s[:start] + new_class + s[end:]
io.open(path, "w", encoding="utf-8").write(s)
print("replaced _Mem class, new length:", len(s))
