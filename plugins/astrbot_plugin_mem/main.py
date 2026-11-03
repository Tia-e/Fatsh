"""三层记忆 v4 —— 轮驱动的 页 / 本 / 棚

存储（自建，不再依赖 sqlite 的上下文窗口）：
- data/mem_archive/<umo 哈希>/seg-0001.json ... 每个分片装 N 轮对话（N = page_turns）
- 分片满了 → 立刻新开下一个分片，并把刚满的这个分片喂给「页」整理
- 分片目录最多留 archive_keep 个（默认 10），超了删最早的

三层语义：
- 页：当前一页（可增删改）→ 每 book_pages 次页整理，本吸一次，页冻结翻新
- 本：当前一本（可改）→ 每 profile_books 次本整理，棚吸一次，本冻结换新
- 棚：稳定画像
"""

import asyncio
import copy
import json
import os
import re
import time

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

try:
    from astrbot.api import logger
except Exception:  # pragma: no cover
    import logging

    logger = logging.getLogger("astrbot_plugin_mem")

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
except Exception:  # pragma: no cover
    get_astrbot_data_path = None

# ---------------- 配置 ----------------
DEFAULT_CFG = {
    "enabled": True,
    # 每页多少轮（1 轮 = 1 问 1 答）；0 = 关掉自动分页，只在 /mem gen 时整理
    "page_turns": 20,
    "book_pages": 5,          # 几次页整理 → 一次本整理
    "profile_books": 5,       # 几次本整理 → 一次棚整理
    "archive_keep": 10,       # 已废弃（现在每人只有一个 <QQ>.json）
    "idle_seconds": 1800,     # 空闲兜底：这么久没新消息
    "idle_min_turns": 10,     # 且攒了这么多轮 → 提前整理
    "keep_pages": 2,          # 页/本各留 2 格（当前 + 冻结）
    "profile_cap": 40,
    # 记忆只在私聊：群里不记录、不注入（行为写死，无需配置）
    "max_items": {"hour": 25, "day": 15, "profile": 10},
    # importance 名额：None=不限，数字=该档最多几条；没列出的档位只当补位
    "importance_quota": {
        "hour": {"5": None, "4": 12, "3": 8},
        "day": {"5": None, "4": 8, "3": 6},
        "profile": {"5": None, "4": 8},
    },
    # 本/棚 只收 importance ≥ 这个值的条目
    "min_importance": {"day": 3, "profile": 3},
    "inject": {
        "hour_items": 25, "day_items": 15, "profile_items": 10,
        "hour_frozen_items": 10, "day_frozen_items": 10,
    },
}

TIER_CN = {"hour": "页（最近一段记忆）", "day": "本（这几天）", "profile": "棚（长期画像）"}
TIER_DUTY = {
    "hour": (
        "这一页只记能复用的事实：TA 是谁、喜好雷区、说定的事、关系温度。"
        "原文里的技术讨论、玩笑、临时情绪不算事实，别当条目抄进来；不要写成聊天流水账。"
    ),
    "day": (
        "这一本只写反复出现的模式（作息、情绪周期）、承诺的进展、关系的整体走向。"
        "页里的单条细事不要原样搬上来，看不到模式就少写。"
    ),
    "profile": (
        "棚是稳定画像：TA 是谁、雷打不动的偏好和雷区、我和 TA 的关系定位。"
        "去掉一次性细节，只留半年后还成立的东西。"
    ),
}

MEMO_SYSTEM = """你是「大肥鱼」。现在你要整理一下你和 TA 这段时间聊过的事，写成你自己的记忆。

用第一人称写：提到 TA 时写「TA」，说自己时写「我」。不要写「对方」「用户」这类第三人称。

【本层】<<TIER>>
【这一层的职责】<<DUTY>>
【日期】<<DATE>>
【上限】<<MAX_ITEMS>> 条（上限，不是目标；够用就少写）

【写什么】
1 TA 是谁：称呼、身份、和我的关系
2 TA 的喜好 / 厌恶 / 固定习惯 / 雷区
3 我们说好的事：计划、时间、待办、承诺
4 我和 TA 的关系：熟络度、称呼、最近的气氛

【怎么写】
- 每条一句话，≤40 字，像我自己记的小本子
- 直接写事实，不加"我觉得""可能"这类犹豫词；口吻随意，不用敬语

【importance 怎么打分】
5 = 身份 / 关系定位 / 核心承诺（TA 是谁、我们之间的定位、说死的约定）
4 = 明确偏好、雷区、待办
3 = 稳定事实（能力、职业、作息）
2 = 偏好细节、口味
1 = 可丢的琐事

【怎么改这一页（大胆点）】
- 这页是我的本子，随便增、删、改、合并、重写，不是只许追加
- 每次重写都应该比上一版更短更准：同类合并成一条，废话直接删，模糊的改具体
- **旧条目不是必须保留**：过时的、没被再提到的、价值低的，删掉就是
- 前后说法冲突时以最新的为准，旧的那条删掉，不要两条都留
- 不要为了显得记性好而堆条数；真没东西可写就输出空列表
- 下面【素材】只是参考材料，不是要你复述
- 只输出 content / type / importance 三个字段，**不要写 id、编号、序号**
- 按 importance 5→1 排好序；超过 <<MAX_ITEMS>> 条就丢 importance 最低的

【判断真假】
- 玩笑、夸张、梗、反问都不算事实，也不能拿来推断 TA 的偏好、人格或立场；
  最多记一条"TA 爱拿这类梗说笑"这种风格判断，别往具体内容上写
- 一次性的说法不成模式；只记 TA 明确表达过、或反复出现过的
- 分清"TA 说的"和"我猜的"：拿不准就不记，不要脑补

【不记】
1 寒暄、语气词、表情、玩笑本身的内容
2 一次性的情绪——只有变成稳定模式才记
3 技术细节、代码、模型名（除非 TA 本身做这行，那属于 TA 是谁）
4 我自己说过的话、我的语气、我的口头禅

【输出】只输出 JSON，不要任何解释文字：
{"items":[{"content":"第一人称一句话，≤40字","type":"who|like|deal|rel","importance":1-5}]}"""


def _pad(s: str, width: int) -> str:
    w = sum(2 if ord(c) > 127 else 1 for c in s)
    return s + " " * max(1, width - w)


def _day_label(ts: float | None = None) -> str:
    try:
        d = time.localtime(ts if ts else time.time())
        return f"{time.strftime('%Y-%m-%d', d)} 周{'一二三四五六日'[d.tm_wday]}"
    except Exception:
        return time.strftime("%Y-%m-%d", time.localtime())


def _data_dir() -> str:
    return get_astrbot_data_path() if get_astrbot_data_path else "."


def _qq_of_key(key: str) -> str | None:
    if key.startswith("person:"):
        qq = key.split(":", 1)[1].strip()
        return qq if qq.isdigit() else None
    return None


def _person_dir(key: str) -> str:
    """data/mem_archive/<QQ>/ —— 一个人的所有东西都在这"""
    qq = _qq_of_key(key) or "unknown"
    return os.path.join(_data_dir(), "mem_archive", qq)


def _mem_file(key: str) -> str:
    return os.path.join(_person_dir(key), "memory.json")


def _clean_items(items: list) -> list:
    """旧数据清洗：丢掉 id，按 importance 从高到低排。"""
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        c = str(it.get("content", "")).strip()
        if not c:
            continue
        out.append(
            {
                "content": c[:60],
                "type": str(it.get("type") or "talk"),
                "importance": max(1, min(5, int(it.get("importance") or 3))),
                "updated_at": it.get("updated_at") or time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    out.sort(key=lambda x: -int(x["importance"]))
    return out


def _umo_person(umo: str) -> str | None:
    """从 umo 里取出对方 QQ：qq-snowluma:FriendMessage:3447899870 → 3447899870。群聊返回 None。"""
    if "FriendMessage" in umo or "Friend" in umo:
        parts = umo.split(":")
        if parts:
            tail = parts[-1].strip()
            if tail.isdigit():
                return tail
    return None


def _person_key(qq: str) -> str:
    return f"person:{qq}"


# ---------------- 分片文件（每人一个目录） ----------------
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
        m = re.match(r"seg-(\d{4})\.json$", name)
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
    node = _seg_read(key, seq) or {}
    turns = node.get("turns")
    if not isinstance(turns, list):
        return []
    # 旧分片没有来源字段 → 默认按私聊
    changed = False
    for t in turns:
        if isinstance(t, dict) and "src" not in t:
            t["src"] = "private"
            changed = True
    if changed:
        node["turns"] = turns
        _seg_write(key, seq, node)
    return turns


def _turns_to_text(turns: list[dict]) -> str:
    lines = []
    for t in turns:
        when = time.strftime("%m-%d %H:%M", time.localtime(t.get("ts", 0)))
        src = "群" if str(t.get("src") or "").lower().startswith("group") else "私聊"
        if t.get("user"):
            lines.append(f"[{when}|{src}] 用户：{t['user']}")
        if t.get("reply"):
            lines.append(f"[{when}|{src}] 你：{t['reply']}")
    return "\n".join(lines)


def _merge_old_segments(key: str) -> None:
    """旧分片里没整理过的轮次并入 <QQ>.json，然后删掉全部 seg-*.json。"""
    seqs = _seg_list(key)
    if not seqs:
        return
    pending: list[dict] = []
    for sq in seqs:
        node = _seg_read(key, sq) or {}
        if node.get("closed_at") is None:
            pending.extend(node.get("turns") or [])
    if pending:
        node = _turns_read(key) or _turns_blank(key)
        exist = node.get("turns") or []
        seen = {(t.get("ts"), t.get("user", ""), t.get("reply", "")) for t in exist}
        for t in pending:
            k2 = (t.get("ts"), t.get("user", ""), t.get("reply", ""))
            if k2 in seen:
                continue
            seen.add(k2)
            exist.append(t)
        node["turns"] = exist
        _turns_write(key, node)
    for sq in seqs:
        try:
            os.remove(_seg_path(key, sq))
        except Exception:
            pass
    logger.info(f"mem4: 旧分片 {len(seqs)} 个已并入 {_qq_of_key(key)}.json（带过来待整理 {len(pending)} 轮）")


# ---------------- 待整理对话：每人一个 <QQ>.json ----------------
def _turns_path(key: str) -> str:
    qq = _qq_of_key(key) or "unknown"
    return os.path.join(_person_dir(key), f"{qq}.json")


def _turns_blank(key: str) -> dict:
    return {"key": key, "qq": _qq_of_key(key), "opened_at": time.time(), "turns": []}


def _turns_read(key: str) -> dict | None:
    try:
        with open(_turns_path(key), "r", encoding="utf-8-sig") as f:
            d = json.load(f)
        if isinstance(d, dict):
            d.setdefault("turns", [])
            return d
    except Exception:
        return None
    return None


def _turns_write(key: str, node: dict) -> None:
    d = _person_dir(key)
    os.makedirs(d, exist_ok=True)
    p = _turns_path(key)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(node, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def _turns_clear(key: str) -> None:
    node = _turns_read(key) or _turns_blank(key)
    node["turns"] = []
    node["opened_at"] = time.time()
    node["last_consolidated"] = time.time()
    _turns_write(key, node)


def _turns_list(key: str) -> list[dict]:
    node = _turns_read(key) or {}
    turns = node.get("turns")
    if not isinstance(turns, list):
        return []
    changed = False
    for t in turns:
        if isinstance(t, dict) and "src" not in t:
            t["src"] = "private"
            changed = True
    if changed:
        node["turns"] = turns
        _turns_write(key, node)
    return turns


def _turns_to_text(turns: list[dict]) -> str:
    lines = []
    for t in turns:
        when = time.strftime("%m-%d %H:%M", time.localtime(t.get("ts", 0)))
        src = "群" if str(t.get("src") or "").lower().startswith("group") else "私聊"
        if t.get("user"):
            lines.append(f"[{when}|{src}] 用户：{t['user']}")
        if t.get("reply"):
            lines.append(f"[{when}|{src}] 你：{t['reply']}")
    return "\n".join(lines)


def _render_items(items: list) -> str:
    if not items:
        return "[]"
    return json.dumps(
        [{"content": i.get("content"), "type": i.get("type"), "importance": i.get("importance")}
         for i in items],
        ensure_ascii=False,
    )


class _Mem:
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
        for k in list(self._recs.keys()):
            _merge_old_segments(k)   # 旧分片 → <QQ>.json（没有分片时直接返回）
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


def _current_page(rec: dict, tier: str) -> dict | None:
    pages = rec[tier]["pages"]
    return pages[-1] if pages else None


def _frozen_page(rec: dict, tier: str) -> dict | None:
    pages = rec[tier]["pages"]
    return pages[-2] if len(pages) >= 2 else None


def _ensure_page(rec: dict, tier: str) -> dict:
    pages = rec[tier]["pages"]
    if pages and pages[-1].get("state") == "mutable":
        return pages[-1]
    page = {"items": [], "opened_at": time.time(), "frozen_at": None, "state": "mutable"}
    pages.append(page)
    return page


def _freeze_page(rec: dict, tier: str) -> None:
    pages = rec[tier]["pages"]
    if pages and pages[-1].get("state") == "mutable":
        pages[-1]["state"] = "frozen"
        pages[-1]["frozen_at"] = time.time()


def _prune_pages(rec: dict, tier: str, keep: int) -> None:
    pages = rec[tier]["pages"]
    if len(pages) > max(1, keep):
        del pages[: len(pages) - max(1, keep)]


@register(
    "astrbot_plugin_mem",
    "hermes",
    "三层记忆 v4：轮驱动的 页/本/棚，自建 json 分片存储，不依赖 sqlite 上下文窗口",
    "4.0.0",
)
class MemV4(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config or {}
        self.store = _Mem()
        self._memo = self._load_memo()
        self._busy: set[str] = set()
        self._locks: dict[str, asyncio.Lock] = {}
        self._idle_task = None
        try:
            self._idle_task = asyncio.create_task(self._idle_loop())
            asyncio.create_task(self._startup_catchup())
        except Exception:
            self._idle_task = None

    async def _startup_catchup(self) -> None:
        """启动自检：如果某个分片已经超过 page_turns（比如上次进程被杀），补一次整理。"""
        await asyncio.sleep(20)
        try:
            if not self._enabled():
                return
            for umo in self.store.all_umos():
                await self._after_turn(umo)
        except Exception as e:
            logger.error(f"mem4: 启动补整理失败 {e}")

    # ---------------- 配置 ----------------
    def _load_memo(self) -> str:
        try:
            p = os.path.join(_data_dir(), "prompt", "memo_rules.md")
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    t = f.read().strip()
                if t:
                    return t
        except Exception:
            pass
        return MEMO_SYSTEM

    def _cfg(self) -> dict:
        cfg = copy.deepcopy(DEFAULT_CFG)
        try:
            p = os.path.join(_data_dir(), "memory_config.json")
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8-sig") as f:
                    d = json.load(f)
                if isinstance(d, dict):
                    for k, v in d.items():
                        if k in ("max_items", "inject") and isinstance(v, dict):
                            cfg[k] = {**cfg[k], **v}
                        elif k in cfg:
                            cfg[k] = v
        except Exception as e:
            logger.error(f"mem4: 读配置失败 {e}")
        try:
            cfg["page_turns"] = max(0, min(100, int(cfg.get("page_turns", 40))))
        except Exception:
            cfg["page_turns"] = 40
        return cfg

    def _save_cfg_value(self, key: str, value) -> None:
        p = os.path.join(_data_dir(), "memory_config.json")
        try:
            d = {}
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8-sig") as f:
                    d = json.load(f)
            if not isinstance(d, dict):
                d = {}
            d[key] = value
            with open(p, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"mem4: 写配置失败 {e}")

    def _enabled(self) -> bool:
        """用了这个插件就是常开，没有开关。"""
        return True

    def _lock(self, umo: str) -> asyncio.Lock:
        lk = self._locks.get(umo)
        if lk is None:
            lk = asyncio.Lock()
            self._locks[umo] = lk
        return lk

    # ---------------- 模型 ----------------
    async def _llm(self, umo: str, tier: str, user_prompt: str, date_label: str = "") -> str:
        cfg = self._cfg()
        try:
            provider = await self.context.get_using_provider_async(umo)
        except Exception as e:
            logger.error(f"mem4: 取模型失败 {e}")
            provider = None
        if provider is None:
            return ""
        pc = getattr(provider, "provider_config", None)
        saved = object()
        try:
            if isinstance(pc, dict):
                ce = pc.get("custom_extra_body")
                saved = copy.deepcopy(ce) if ce is not None else None
                pc["custom_extra_body"] = {
                    k: v for k, v in (ce or {}).items() if k not in ("reasoning_effort", "thinking")
                }
            tpl = self._memo or MEMO_SYSTEM
            sys_prompt = (
                tpl.replace("<<TIER>>", TIER_CN[tier])
                .replace("<<DUTY>>", TIER_DUTY[tier])
                .replace("<<MAX_ITEMS>>", str(cfg["max_items"].get(tier, 10)))
                .replace("<<DATE>>", date_label or _day_label())
            )
            resp = await provider.text_chat(prompt=user_prompt, contexts=[], system_prompt=sys_prompt)
            return (getattr(resp, "completion_text", "") or "").strip()
        except Exception as e:
            logger.error(f"mem4: 整理调用失败 {e}")
            return ""
        finally:
            if isinstance(pc, dict):
                if saved is None:
                    pc.pop("custom_extra_body", None)
                elif isinstance(saved, dict):
                    pc["custom_extra_body"] = saved

    @staticmethod
    def _parse_items(text: str) -> list | None:
        if not text:
            return None
        s, e = text.find("{"), text.rfind("}")
        if s != -1 and e != -1:
            try:
                obj = json.loads(text[s : e + 1])
                if isinstance(obj, dict) and isinstance(obj.get("items"), list):
                    return obj["items"]
            except Exception:
                pass
        s, e = text.find("["), text.rfind("]")
        if s != -1 and e != -1:
            try:
                arr = json.loads(text[s : e + 1])
                if isinstance(arr, list):
                    return arr
            except Exception:
                pass
        return None

    @staticmethod
    def _norm(items: list, cap: int, quota: dict | None = None, min_imp: int = 1) -> list:
        """去重 → importance 降序 → 按档位名额录取 → 不足则用低档补位。"""
        cleaned, seen = [], set()
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        for it in items:
            if not isinstance(it, dict):
                continue
            c = str(it.get("content", "")).strip()
            if not c:
                continue
            key = re.sub(r"[\s，。,.！!？?]+", "", c)[:20]
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(
                {
                    "content": c[:60],
                    "type": str(it.get("type") or "talk"),
                    "importance": max(1, min(5, int(it.get("importance") or 3))),
                    "updated_at": now,
                }
            )
        cleaned.sort(key=lambda x: -int(x.get("importance", 3)))

        if not quota:
            return [i for i in cleaned if i["importance"] >= min_imp][:cap]

        kept, used = [], {}
        for idx, it in enumerate(cleaned):
            imp = int(it["importance"])
            if imp < min_imp:
                continue
            lim = quota.get(str(imp), quota.get(imp, 0))
            if lim is None or used.get(imp, 0) < int(lim):
                kept.append(idx)
                used[imp] = used.get(imp, 0) + 1
                if len(kept) >= cap:
                    break
        # 名额没用满 → 用**没设名额的档位**（1-2 分这类）补位；设了名额的档位不再超额
        if len(kept) < cap:
            quoted = {int(k) for k in quota.keys()}
            for idx, it in enumerate(cleaned):
                if idx in kept:
                    continue
                imp = int(it["importance"])
                if imp < min_imp or imp in quoted:
                    continue
                kept.append(idx)
                if len(kept) >= cap:
                    break
        return [cleaned[i] for i in sorted(kept, key=lambda i: -cleaned[i]["importance"])]

    # ---------------- 记忆键：只服务私聊 ----------------
    def _key(self, event: AstrMessageEvent, cfg: dict | None = None) -> str | None:
        cfg = cfg or self._cfg()
        umo = str(event.unified_msg_origin)
        if "Group" in umo:
            return None                      # 群聊完全不参与记忆
        try:
            sid = str(event.get_sender_id() or "")
        except Exception:
            sid = ""
        qq = sid or _umo_person(umo)
        return _person_key(qq) if qq else None

    # ---------------- 收集一轮 ----------------
    @filter.on_llm_response()
    async def collect(self, event: AstrMessageEvent, resp=None, *args, **kwargs):
        if not self._enabled():
            return
        try:
            key = self._key(event)
            if not key:
                return
            user_text = (event.message_str or "").strip()
            reply = (getattr(resp, "completion_text", "") or "").strip()
            if not user_text or not reply:
                return
            umo = str(event.unified_msg_origin)
            src = "group" if ("GroupMessage" in umo or "Group" in umo) else "private"
            await self._append_turn(key, user_text[:300], reply[:300], src)
            asyncio.create_task(self._after_turn(key))
        except Exception as e:
            logger.error(f"mem4: 收集失败 {e}")

    async def _append_turn(self, key: str, user: str, reply: str, src: str = "private") -> None:
        async with self._lock(key):
            node = _turns_read(key) or _turns_blank(key)
            node.setdefault("turns", [])
            node["turns"].append({"ts": time.time(), "user": user, "reply": reply, "src": src})
            node["updated_at"] = time.time()
            _turns_write(key, node)
            rec = self.store.umo(key)
            rec["hour"]["turns_in_file"] = len(node["turns"])
            rec["hour"]["last_turn_at"] = time.time()
            await self.store.save(key)

    async def _after_turn(self, key: str) -> None:
        """每轮结束看一下：待整理的轮数够不够。"""
        try:
            cfg = self._cfg()
            n = int(cfg["page_turns"])
            if n <= 0 or key in self._busy:
                return
            async with self._lock(key):
                rec = self.store.umo(key)
                turns = int(rec["hour"].get("turns_in_file") or 0)
            if turns < n:
                return
            self._busy.add(key)
            try:
                await self._consolidate_page(key)
            finally:
                self._busy.discard(key)
        except Exception as e:
            logger.error(f"mem4: 后台整理失败 {e}")
            self._busy.discard(key)

    def _quota(self, cfg: dict, tier: str) -> dict:
        q = (cfg.get("importance_quota") or {}).get(tier)
        if isinstance(q, dict):
            return {int(k): (None if v is None else int(v)) for k, v in q.items()}
        return {}

    def _min_imp(self, cfg: dict, tier: str) -> int:
        return int((cfg.get("min_importance") or {}).get(tier, 1))

    # ---------------- 页整理 ----------------
    async def _consolidate_page(self, key: str) -> bool:
        cfg = self._cfg()
        turns = _turns_list(key)
        if not turns:
            return False
        raw = _turns_to_text(turns)
        rec = self.store.umo(key)
        page = _ensure_page(rec, "hour")
        prev = _frozen_page(rec, "hour")
        prev_txt = _render_items(prev.get("items", [])) if prev else ""
        prompt = (
            f"【这一页现在的内容】\n{_render_items(page.get('items', []))}\n\n"
            + (f"【上一页（已冻结，只读参考，不要改动它）】\n{prev_txt}\n\n" if prev_txt else "")
            + f"【素材：这 {len(turns)} 轮原话】\n{raw}"
        )
        text = await self._llm(key, "hour", prompt, _day_label(turns[-1].get("ts")))
        items = self._parse_items(text)
        if items is None:
            logger.error("mem4: 页整理返回无法解析，待整理对话先留着")
            return False
        page["items"] = self._norm(
            items, int(cfg["max_items"].get("hour", 25)), self._quota(cfg, "hour")
        )
        page["state"] = "mutable"
        page["sources"] = sorted({str(t.get("src") or "private") for t in turns})
        rec["hour"]["last_at"] = time.time()
        rec["hour"]["pages_done"] = int(rec["hour"].get("pages_done") or 0) + 1
        rec["hour"]["turns_in_file"] = 0
        _turns_clear(key)          # 触发整理 → 待整理对话清空
        await self.store.save(key)
        logger.info(f"mem4: 页整理 {len(turns)} 轮 → {len(page['items'])} 条；待整理已清空")
        await self._maybe_book(key)
        return True

    # ---------------- 本 / 棚 ----------------
    async def _maybe_book(self, umo: str) -> None:
        cfg = self._cfg()
        rec = self.store.umo(umo)
        done = int(rec["hour"].get("pages_done") or 0)
        if done < int(cfg.get("book_pages", 5)):
            return
        if await self._consolidate_book(umo):
            rec = self.store.umo(umo)
            rec["hour"]["pages_done"] = 0
            rec["day"]["books_done"] = int(rec["day"].get("books_done") or 0) + 1
            await self.store.save()
            await self._maybe_profile(umo)

    async def _consolidate_book(self, umo: str) -> bool:
        cfg = self._cfg()
        rec = self.store.umo(umo)
        page = _current_page(rec, "hour")
        src = list((page or {}).get("items", []))
        if not src:
            logger.info("mem4: 页里还没内容，本先不动")
            return False
        book = _ensure_page(rec, "day")
        prompt = (
            f"【这一本现在的内容】\n{_render_items(book.get('items', []))}\n\n"
            f"【刚整理完的这几页（只读参考）】\n{_render_items(src)}"
        )
        text = await self._llm(umo, "day", prompt, _day_label())
        items = self._parse_items(text)
        if items is None:
            logger.error("mem4: 本整理返回无法解析")
            return False
        book["items"] = self._norm(
            items,
            int(cfg["max_items"].get("day", 15)),
            self._quota(cfg, "day"),
            self._min_imp(cfg, "day"),
        )
        book["state"] = "mutable"
        _freeze_page(rec, "hour")
        _ensure_page(rec, "hour")
        _prune_pages(rec, "hour", int(cfg.get("keep_pages", 2)))
        rec["day"]["last_at"] = time.time()
        await self.store.save()
        logger.info(f"mem4: 本整理 吸 {len(src)} 条 → 本 {len(book['items'])} 条；页冻结翻新")
        return True

    async def _maybe_profile(self, umo: str) -> None:
        cfg = self._cfg()
        rec = self.store.umo(umo)
        done = int(rec["day"].get("books_done") or 0)
        if done < int(cfg.get("profile_books", 5)):
            return
        if await self._consolidate_profile(umo):
            rec = self.store.umo(umo)
            rec["day"]["books_done"] = 0
            await self.store.save()

    async def _consolidate_profile(self, umo: str) -> bool:
        cfg = self._cfg()
        rec = self.store.umo(umo)
        book = _current_page(rec, "day")
        src = list((book or {}).get("items", []))
        if not src:
            logger.info("mem4: 本里还没内容，棚先不动")
            return False
        prompt = (
            f"【棚现在的内容】\n{_render_items(rec['profile']['items'])}\n\n"
            f"【刚整理完的这一本（只读参考）】\n{_render_items(src)}"
        )
        text = await self._llm(umo, "profile", prompt, _day_label())
        items = self._parse_items(text)
        if items is None:
            logger.error("mem4: 棚整理返回无法解析")
            return False
        cap = min(int(cfg["max_items"].get("profile", 10)), int(cfg.get("profile_cap", 40)))
        rec["profile"]["items"] = self._norm(
            items, cap, self._quota(cfg, "profile"), self._min_imp(cfg, "profile")
        )
        rec["profile"]["last_at"] = time.time()
        _freeze_page(rec, "day")
        _ensure_page(rec, "day")
        _prune_pages(rec, "day", int(cfg.get("keep_pages", 2)))
        await self.store.save()
        logger.info(f"mem4: 棚整理 吸 {len(src)} 条 → 棚 {len(rec['profile']['items'])} 条；本冻结换新")
        return True

    # ---------------- 空闲兜底 ----------------
    async def _idle_consolidate(self, umo: str) -> None:
        """空闲兜底：攒了 ≥idle_min_turns 轮且已经静默够久，就提前整理。"""
        try:
            if umo in self._busy:
                return
            cfg = self._cfg()
            async with self._lock(umo):
                rec = self.store.umo(umo)
                turns = int(rec["hour"].get("turns_in_file") or 0)
                last = float(rec["hour"].get("last_turn_at") or 0)
            if turns < int(cfg.get("idle_min_turns", 10)):
                return
            if time.time() - last < int(cfg.get("idle_seconds", 1800)):
                return
            self._busy.add(umo)
            try:
                logger.info(f"mem4: 空闲兜底触发（{turns} 轮没到 {cfg['page_turns']} 轮）")
                await self._consolidate_page(umo)
            finally:
                self._busy.discard(umo)
        except Exception as e:
            logger.error(f"mem4: 空闲兜底失败 {e}")
            self._busy.discard(umo)

    async def _idle_loop(self) -> None:
        await asyncio.sleep(30)
        while True:
            try:
                cfg = self._cfg()
                if self._enabled() and int(cfg.get("page_turns", 40)) > 0:
                    for umo in self.store.all_umos():
                        await self._idle_consolidate(umo)
            except Exception as e:
                logger.error(f"mem4: 空闲检查失败 {e}")
            await asyncio.sleep(60)

    # ---------------- 注入 ----------------
    def _top(self, items: list, n: int) -> list:
        """按 importance 从高到低取前 n 条。"""
        ordered = sorted(items, key=lambda i: -int(i.get("importance") or 3))
        return ordered[: max(0, n)]

    def _visible(self, rec: dict, cfg: dict) -> dict:
        hp = _current_page(rec, "hour")
        hf = _frozen_page(rec, "hour")
        dp = _current_page(rec, "day")
        df = _frozen_page(rec, "day")
        inj = cfg["inject"]
        return {
            "hour": self._top((hp or {}).get("items", []), int(inj.get("hour_items", 25))),
            "hour_frozen": self._top((hf or {}).get("items", []), int(inj.get("hour_frozen_items", 10))),
            "day": self._top((dp or {}).get("items", []), int(inj.get("day_items", 15))),
            "day_frozen": self._top((df or {}).get("items", []), int(inj.get("day_frozen_items", 10))),
            "profile": self._top(rec["profile"]["items"], int(inj.get("profile_items", 10))),
        }

    def _memory_block(self, rec: dict, cfg: dict, only_profile: bool = False) -> str:
        v = self._visible(rec, cfg)
        parts = []
        keys = (("profile", "长期"),) if only_profile else (
            ("hour", "最近一段"),
            ("hour_frozen", "上一段（已冻结）"),
            ("day", "这几天"),
            ("day_frozen", "上几本（已冻结）"),
            ("profile", "长期"),
        )
        for key, label in keys:
            items = v.get(key) or []
            if not items:
                continue
            parts.append(
                f"<{key}：{label}>\n"
                + "\n".join(f"- {i.get('content','')}" for i in items)
                + f"\n</{key}>"
            )
        if not parts:
            return ""
        return (
            "\n\n====================\n### [MEMORY] ###\n"
            "（这是你记得的事，只在与当前话题相关时才用；不要主动复述）\n"
            + "\n".join(parts)
            + "\n====================\n"
        )

    @filter.on_llm_request()
    async def inject_memory(self, event: AstrMessageEvent, req=None, *args, **kwargs):
        if req is None or not self._enabled():
            return
        try:
            cfg = self._cfg()
            key = self._key(event, cfg)
            if not key:
                return
            block = self._memory_block(self.store.umo(key), cfg)
            if block:
                req.system_prompt = (getattr(req, "system_prompt", "") or "") + block
        except Exception as e:
            logger.error(f"mem4: 注入失败 {e}")

    # ---------------- 指令 ----------------
    # 权限统一由 access 插件在处理指令前拦截，这里不再判断

    @staticmethod
    def _arg(event: AstrMessageEvent) -> str:
        text = (event.message_str or "").strip()
        for t in ("/mem", "mem"):
            if text.lower().startswith(t):
                return text[len(t):].strip()
        return text.strip()

    def _status(self, event: AstrMessageEvent, with_help: bool = True) -> str:
        cfg = self._cfg()
        umo = self._key(event, cfg) or str(event.unified_msg_origin)
        rec = self.store.umo(umo)
        hp = _current_page(rec, "hour") or {}
        dp = _current_page(rec, "day") or {}
        cap_h = int(cfg["max_items"].get("hour", 25))
        cap_d = int(cfg["max_items"].get("day", 15))
        cap_p = int(cfg["max_items"].get("profile", 10))
        n = int(cfg["page_turns"])
        turns = int(rec["hour"].get("turns_in_file") or 0)
        done_h = int(rec["hour"].get("pages_done") or 0)
        done_d = int(rec["day"].get("books_done") or 0)
        cur = _turns_list(umo)
        src_priv = sum(1 for t in cur if str(t.get("src") or "private") == "private")
        src_grp = len(cur) - src_priv
        lines = [
            "🧠 记忆（常开）",
            "────────────────────────",
            f"{_pad('页', 4)}{len(hp.get('items', []))} / {cap_h} 条"
            f"   待整理 {turns} / {n if n else '∞'} 轮",
            f"{_pad('本', 4)}{len(dp.get('items', []))} / {cap_d} 条"
            f"   {done_h} / {int(cfg.get('book_pages', 5))} 页 → 换本",
            f"{_pad('棚', 4)}{len(rec['profile']['items'])} / {cap_p} 条"
            f"   {done_d} / {int(cfg.get('profile_books', 5))} 本 → 整理",
            "────────────────────────",
            f"冻结 页 {len((_frozen_page(rec,'hour') or {}).get('items', []))} 条"
            f" / 本 {len((_frozen_page(rec,'day') or {}).get('items', []))} 条（也注入）",
            f"存 {str(umo).replace('person:', '')}.json"
            f"｜来源 私聊 {src_priv} 轮 / 群 {src_grp} 轮",
        ]
        if n == 0:
            lines.append("（page 0 = 不自动分页，只在 /mem gen 时整理）")
        if dp.get("items") is None:
            lines.append("")
        if with_help:
            lines += [
                "────────────────────────",
                "/mem show          看现在会带进去的记忆",
                "/mem clear         清掉这个人的页/本/棚（分片不动）",
                "/mem page 0-100    每页多少轮（默认 40）",
                "/mem gen           立刻用当前分片整理一次",
            ]
        return "\n".join(lines)

    @filter.command("mem")
    async def mem_cmd(self, event: AstrMessageEvent):
        """记忆：查看 / 清空 / 分页 / 手动整理（权限由 access 插件统一拦截）"""
        cfg = self._cfg()
        arg = self._arg(event).lower()
        umo = self._key(event, cfg) or str(event.unified_msg_origin)

        if not arg:
            yield event.plain_result(self._status(event))
            return

        if arg in ("on", "off", "enable", "disable", "开", "关"):
            yield event.plain_result("记忆插件是常开的，没有开关。要清空就 /mem clear。")
            return

        if arg.startswith("page"):
            part = arg[4:].strip()
            if not part:
                yield event.plain_result(
                    f"每页 {cfg['page_turns']} 轮（1 轮 = 1 问 1 答）\n用法：/mem page 0-100（0=不自动分页）"
                )
                return
            try:
                n = int(part)
            except ValueError:
                yield event.plain_result("用法：/mem page 0-100")
                return
            if n < 0 or n > 100:
                yield event.plain_result("只能填 0-100")
                return
            self._save_cfg_value("page_turns", n)
            extra = "（关闭自动分页）" if n == 0 else ""
            yield event.plain_result(f"✅ 每页已设为 {n} 轮{extra}\n下个分片开始生效，当前分片不受影响")
            return

        if arg.startswith("gen"):
            turns = _turns_list(umo)
            if not turns:
                yield event.plain_result("还没有待整理的对话（先聊几句）")
                return
            ok = await self._consolidate_page(umo)
            if not ok:
                yield event.plain_result("这次整理没成功（模型没按 JSON 格式返回），对话还留着，可以再试一次")
                return
            rec = self.store.umo(umo)
            hp = _current_page(rec, "hour") or {}
            dp = _current_page(rec, "day") or {}
            yield event.plain_result(
                f"✅ 已用 {len(turns)} 轮对话整理一次（已清空）\n"
                f"页 {len(hp.get('items', []))} 条 · 本 {len(dp.get('items', []))} 条"
            )
            return

        if arg == "show":
            v = self._visible(self.store.umo(umo), cfg)
            lines = ["🧠 现在会带进聊天的记忆", "────────────────────────"]
            for k, label in (("hour", "页"), ("hour_frozen", "页·已冻结"),
                             ("day", "本"), ("day_frozen", "本·已冻结"),
                             ("profile", "棚")):
                lines.append(f"【{label}】{len(v.get(k, []))} 条")
                lines += [f"  · {i.get('content')}" for i in v.get(k, [])] or ["  （空）"]
                lines.append("")
            total = sum(len(v.get(k, [])) for k in v)
            lines.append(f"合计 {total} 条（都会注入）")
            yield event.plain_result("\n".join(lines))
            return

        if arg == "clear":
            data = self.store._load()
            data["umos"].pop(umo, None)
            await self.store.save()
            yield event.plain_result("✅ 已清空本会话的页/本/棚（归档分片保留，可用 /mem gen 重新吸）")
            return

        if arg in ("clearall", "clear_all"):
            yield event.plain_result("清空全部记忆的命令已移除（太危险）。要清就对本会话用 /mem clear。")
            return

        yield event.plain_result(
            "没有这个子命令\n可用：/mem、on、off、show、clear、page 0-100、gen 0-10"
        )
