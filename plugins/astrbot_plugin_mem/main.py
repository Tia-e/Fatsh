"""三层记忆 v3.2 —— 页与页、本与本 的滚动本子

设计：
- 小时层 = 当前一页（可随时增删改），不是每小时一个桶，也没有 5 条上限
- 触发天整理 → 这一页冻结（只读，作为参考）→ 翻到新的一页继续写
- 天层 = 一本本子（当前本可改）→ 触发长期整理 → 冻旧本 → 拿新本
- 长期层 = 永久文档（≤profile 上限）
- 触发一律按“上次整理时间”，全异步；整理时强制开思考
- 注入只带“当前页/当前本”，冻结的只作整理参考
"""

import asyncio
import copy
import json
import os
import re
import sqlite3
import time
from urllib.parse import quote

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
    "hour_gap_seconds": 3600,       # 1 小时
    "day_gap_seconds": 86400,       # 1 天
    "profile_gap_seconds": 259200,  # 3 天
    "keep_pages": 2,                # 每层保留几页（当前 + 上一页）
    "profile_cap": 40,
    # 每页最多几条（上限，不是目标）
    "max_items": {"hour": 20, "day": 12, "profile": 8},
    # 注入上限（一般等于 max_items）
    "inject": {"hour_items": 20, "day_items": 12, "profile_items": 8},
    "buffer_max": 200,              # 每会话原始对话缓冲
    "hour_input_max": 100,          # 单次整理最多喂多少条对话（50-250 可调）
    "min_new_entries": 6,           # 距上次整理至少新增这么多条对话才触发（门槛）
}

MEM_KEYS = ("hour", "day", "profile")
_MISSING = object()

MEMO_SYSTEM = """你是「大肥鱼」。现在你要整理一下你和 TA 这段时间聊过的事，写成你自己的记忆。

用第一人称写：提到 TA 时写「TA」，说自己时写「我」。不要写「对方」「用户」这类第三人称。

【本层】<<TIER>>
【本层说明】<<DESC>>
【这一层的职责】<<DUTY>>
【日期】<<DATE>>
【上限】<<MAX_ITEMS>> 条（这是上限，不是目标；够用就少写，别为了凑满硬加）

【写什么】
1 TA 是谁：称呼、身份、和我的关系
2 TA 的喜好 / 厌恶 / 固定习惯 / 雷区
3 我们说好的事：计划、时间、待办、承诺
4 我和 TA 的关系：熟络度、称呼、最近的气氛
5 这一段聊了什么：TA 和我聊了……，TA 喜欢的是……

【怎么写】
- 每条一句话，≤40 字，像我自己记的小本子
- 回顾类可以带日期：2026-10-31 周六 TA和我聊了记忆分层，方案由TA主导
- 示例（照这个口吻）：
  TA是我的「主人」，性格与语气由TA一手养成
  TA自己写代码，设计记忆分层和人格系统，方案由TA主导
  TA不喜欢把方案全权交给 agent，觉得套路、没人味
  TA和我聊了XX，TA喜欢的是XXX

【怎么改这一页】
- 这是我自己的一页本子，可以随时增、删、改，不是只许追加
- 没变的条目原样抄回并保留原 id；只有出现新事实、或旧事实被推翻时才动它
- 同一个事实只留一条，不要换个说法写两遍
- 如果已有条目不是「TA/我」这个写法，就按新写法重写
- 删掉的条目不要出现在输出里
- 总条数不超过 <<MAX_ITEMS>>；实在没东西可记就 {"items":[]}

【不记】
1 寒暄、语气词、表情、玩笑本身
2 一次性的情绪（"今天累死了"）——只有变成稳定模式才记（"经常凌晨才睡"）
3 我们聊过的技术细节、代码、模型名（除非 TA 本身就做这行，那属于 TA 是谁）
4 我自己说过的话、我的语气、我的口头禅

【输出】只输出 JSON，不要任何解释文字：
{"items":[{"id":"原 id 或新 id","content":"第一人称一句话，≤40字","type":"who|like|deal|rel|talk","importance":1-5}]}"""

TIER_CN = {"hour": "页（小时记忆）", "day": "本（天记忆）", "profile": "棚（长期画像）"}
TIER_INPUT = {
    "hour": "输入是最近一小时的原始对话，加上这一页和上一页（上一页只读）",
    "day": "输入是这一本里记下的内容，加上刚冻结的那一页小时记忆（只读参考）",
    "profile": "输入是当前这本天记忆，加上棚里已有的画像",
}
TIER_DUTY = {
    "hour": (
        "这一页记具体事实：TA 今天说了什么、答应了什么、现在的气氛。"
        "细一点没关系，但只留能复用的事实，不要流水账。"
    ),
    "day": (
        "这一本不重复页里的具体事实，只写这几天反复出现的模式（作息、情绪周期）、"
        "承诺的进展、关系的变化。看不到模式就少写几条。"
    ),
    "profile": (
        "棚是稳定画像：TA 是谁、雷打不动的偏好和雷区、我和 TA 的关系定位。"
        "去掉一次性的细节，只留半年后还成立的东西。"
    ),
}


def _pad(s: str, width: int) -> str:
    """按显示宽度补空格（中文算 2 格）。"""
    w = sum(2 if ord(c) > 127 else 1 for c in s)
    return s + " " * max(1, width - w)


def _day_label(day: str | None = None) -> str:
    """'2026-10-31 周六' 这种带星期的日期标签。"""
    try:
        d = time.strptime(day, "%Y-%m-%d") if day else time.localtime()
        wd = "一二三四五六日"[d.tm_wday]
        return f"{time.strftime('%Y-%m-%d', d)} 周{wd}"
    except Exception:
        return time.strftime("%Y-%m-%d", time.localtime())


def _date(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts if ts else time.time()))


def _days_ago(n: int) -> str:
    return _date(time.time() - n * 86400)


class _Mem:
    """持久化：一个文件装所有会话的所有层 + 原始对话缓冲。"""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._data: dict | None = None
        self._bufdata: dict | None = None
        base = get_astrbot_data_path() if get_astrbot_data_path else "."
        self._path = os.path.join(base, "memory_store_v3.json")
        self._buf_path = os.path.join(base, "memory_buffer.json")

    def path(self) -> str:
        return self._path

    def _blank_rec(self) -> dict:
        return {
            "hour": {"last_at": 0, "pages": []},
            "day": {"last_at": 0, "pages": []},
            "profile": {"last_at": 0, "items": []},
        }

    def _migrate(self, rec: dict) -> dict:
        """旧版（每小时一个桶）→ 新版（一页纸）。"""
        for key in ("hour", "day"):
            tier = rec.get(key)
            if not isinstance(tier, dict):
                rec[key] = {"last_at": 0, "pages": []}
                continue
            if tier.get("buckets") is not None and not tier.get("pages"):
                merged = []
                for _, b in sorted(tier.get("buckets", {}).items()):
                    merged.extend(b.get("items", []))
                # 简易去重：同样的开头 20 字只留一条
                seen, kept = set(), []
                for it in merged:
                    k = re.sub(r"\s+", "", str(it.get("content", "")))[:20]
                    if k and k in seen:
                        continue
                    seen.add(k)
                    kept.append(it)
                tier["pages"] = [
                    {
                        "items": kept,
                        "opened_at": time.time(),
                        "frozen_at": None,
                        "state": "mutable",
                    }
                ] if kept else []
                tier.pop("buckets", None)
            tier.setdefault("pages", [])
            tier.setdefault("last_at", 0)
        rec.setdefault("profile", {"last_at": 0, "items": []})
        rec["profile"].setdefault("items", [])
        rec["profile"].setdefault("last_at", 0)
        return rec

    def _load(self):
        if self._data is not None:
            return self._data
        try:
            if os.path.exists(self._path):
                with open(self._path, "r", encoding="utf-8") as f:
                    d = json.load(f)
                if isinstance(d, dict) and "umos" in d:
                    for u, rec in list(d["umos"].items()):
                        if isinstance(rec, dict):
                            d["umos"][u] = self._migrate(rec)
                    self._data = d
                    return self._data
        except Exception as e:
            logger.error(f"mem3: 读取记忆库失败 {e}")
        self._data = {"umos": {}, "updated_at": None}
        return self._data

    async def save(self):
        async with self._lock:
            data = self._load()
            data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)

    def buffers(self) -> dict:
        if self._bufdata is None:
            try:
                if os.path.exists(self._buf_path):
                    with open(self._buf_path, "r", encoding="utf-8") as f:
                        d = json.load(f)
                    self._bufdata = (
                        d if isinstance(d, dict) and isinstance(d.get("umos"), dict) else {"umos": {}}
                    )
                else:
                    self._bufdata = {"umos": {}}
            except Exception:
                self._bufdata = {"umos": {}}
        return self._bufdata["umos"]

    async def save_buffers(self):
        async with self._lock:
            umos = self.buffers()
            os.makedirs(os.path.dirname(self._buf_path), exist_ok=True)
            tmp = self._buf_path + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({"umos": umos}, f, ensure_ascii=False)
                os.replace(tmp, self._buf_path)
            except Exception as e:
                logger.error(f"mem3: 写缓冲失败 {e}")

    def umo(self, umo: str) -> dict:
        data = self._load()
        rec = data["umos"].get(umo)
        if not isinstance(rec, dict):
            rec = self._blank_rec()
            data["umos"][umo] = rec
        return self._migrate(rec)


def _current_page(rec: dict, tier: str) -> dict | None:
    pages = rec[tier]["pages"]
    return pages[-1] if pages else None


def _ensure_page(rec: dict, tier: str, cfg: dict) -> dict:
    pages = rec[tier]["pages"]
    if pages and pages[-1].get("state") == "mutable":
        return pages[-1]
    page = {
        "items": [],
        "opened_at": time.time(),
        "frozen_at": None,
        "state": "mutable",
    }
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
    "三层记忆 v3.2：小时一页纸、天一本小本子、长期画像；页/本按父层整理冻结滚动",
    "3.2.0",
)
class MemV3(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config or {}
        self.store = _Mem()
        self.buffers: dict[str, list] = self.store.buffers()
        self._extracting: set[str] = set()
        self._locks: dict[str, asyncio.Lock] = {}
        self._memo = self._load_memo()

    # ---------------- 配置 ----------------
    def _load_memo(self) -> str:
        try:
            p = os.path.join(
                get_astrbot_data_path() if get_astrbot_data_path else ".", "prompt", "memo_rules.md"
            )
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
            p = os.path.join(
                get_astrbot_data_path() if get_astrbot_data_path else ".", "memory_config.json"
            )
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8-sig") as f:
                    d = json.load(f)
                if isinstance(d, dict):
                    for k, v in d.items():
                        if k in ("max_items", "inject") and isinstance(v, dict):
                            cfg[k] = {**cfg[k], **v}
                        else:
                            cfg[k] = v
        except Exception as e:
            logger.error(f"mem3: 读配置失败 {e}")
        try:
            cfg["hour_input_max"] = max(50, min(250, int(cfg.get("hour_input_max", 100))))
        except Exception:
            cfg["hour_input_max"] = 100
        return cfg

    def _save_cfg_value(self, key: str, value) -> None:
        base = get_astrbot_data_path() if get_astrbot_data_path else "."
        p = os.path.join(base, "memory_config.json")
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
            logger.error(f"mem3: 写配置失败 {e}")

    def _state_path(self) -> str:
        return os.path.join(
            get_astrbot_data_path() if get_astrbot_data_path else ".", "mem_state.json"
        )

    def _enabled(self) -> bool:
        try:
            with open(self._state_path(), "r", encoding="utf-8") as f:
                return bool(json.load(f).get("enabled", True))
        except Exception:
            return bool(self._cfg().get("enabled", True))

    def _set_enabled(self, on: bool) -> None:
        try:
            with open(self._state_path(), "w", encoding="utf-8") as f:
                json.dump(
                    {"enabled": bool(on), "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")},
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
        except Exception as e:
            logger.error(f"mem3: 写开关失败 {e}")

    def _lock(self, umo: str) -> asyncio.Lock:
        lk = self._locks.get(umo)
        if lk is None:
            lk = asyncio.Lock()
            self._locks[umo] = lk
        return lk

    # ---------------- 模型调用（强制思考） ----------------
    async def _llm(
        self, event: AstrMessageEvent, tier: str, user_prompt: str, date_label: str = ""
    ) -> str:
        cfg = self._cfg()
        try:
            provider = await self.context.get_using_provider_async(event.unified_msg_origin)
        except Exception as e:
            logger.error(f"mem3: 取模型失败 {e}")
            provider = None
        if provider is None:
            return ""
        pc = getattr(provider, "provider_config", None)
        saved = _MISSING
        try:
            if isinstance(pc, dict):
                ce = pc.get("custom_extra_body")
                saved = copy.deepcopy(ce) if ce is not None else _MISSING
                pc["custom_extra_body"] = {
                    k: v for k, v in (ce or {}).items() if k not in ("reasoning_effort", "thinking")
                }
            tpl = self._memo or MEMO_SYSTEM
            sys_prompt = (
                tpl.replace("<<TIER>>", TIER_CN[tier])
                .replace("<<DESC>>", TIER_INPUT[tier])
                .replace("<<DUTY>>", TIER_DUTY[tier])
                .replace("<<MAX_ITEMS>>", str(cfg["max_items"].get(tier, 10)))
                .replace("<<DATE>>", date_label or _day_label())
            )
            resp = await provider.text_chat(
                prompt=user_prompt, contexts=[], system_prompt=sys_prompt
            )
            return (getattr(resp, "completion_text", "") or "").strip()
        except Exception as e:
            logger.error(f"mem3: 整理调用失败 {e}")
            return ""
        finally:
            if isinstance(pc, dict):
                if saved is _MISSING:
                    pc.pop("custom_extra_body", None)
                elif saved is not None:
                    pc["custom_extra_body"] = saved

    @staticmethod
    def _parse_items(text: str) -> list | None:
        if not text:
            return None
        s, e = text.find("{"), text.rfind("}")
        if s == -1 or e == -1:
            s, e = text.find("["), text.rfind("]")
            if s == -1 or e == -1:
                return None
            try:
                arr = json.loads(text[s : e + 1])
                return arr if isinstance(arr, list) else None
            except Exception:
                return None
        try:
            obj = json.loads(text[s : e + 1])
        except Exception:
            return None
        if isinstance(obj, dict) and isinstance(obj.get("items"), list):
            return obj["items"]
        return None

    def _norm(self, items: list, cap: int) -> list:
        out, seen = [], set()
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
            out.append(
                {
                    "id": str(it.get("id") or f"m-{int(time.time()*1000)}-{len(out)}"),
                    "content": c[:60],
                    "type": str(it.get("type") or "talk"),
                    "importance": max(1, min(5, int(it.get("importance") or 3))),
                    "updated_at": now,
                }
            )
            if len(out) >= cap:
                break
        return out

    # ---------------- 取对话 ----------------
    _REMINDER_RE = re.compile(r"<system_reminder>[\s\S]*?</system_reminder>")
    _DT_RE = re.compile(r"Current datetime:\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2})")
    _DB_NAME = "data_v4.db"

    def _db_entries(self, umo: str, since: float, limit: int) -> list:
        base = get_astrbot_data_path() if get_astrbot_data_path else "."
        db = os.path.join(base, self._DB_NAME)
        if not os.path.exists(db):
            return []
        out = []
        try:
            con = sqlite3.connect(
                "file:" + quote(db.replace("\\", "/"), safe="/:") + "?mode=ro", uri=True, timeout=3
            )
            try:
                rows = con.execute(
                    "select content from conversations where user_id=? order by updated_at desc limit 30",
                    (umo,),
                ).fetchall()
            finally:
                con.close()
        except Exception as e:
            logger.error(f"mem3: 读会话库失败 {e}")
            return []
        for (blob,) in rows:
            try:
                msgs = json.loads(blob)
            except Exception:
                continue
            if not isinstance(msgs, list):
                continue
            cur_ts = None
            for msg in msgs:
                if not isinstance(msg, dict):
                    continue
                role = msg.get("role")
                content = msg.get("content")
                if isinstance(content, str):
                    raw = content
                elif isinstance(content, list):
                    raw = "\n".join(
                        s.get("text", "")
                        for s in content
                        if isinstance(s, dict) and s.get("type") == "text"
                    )
                else:
                    raw = ""
                m = self._DT_RE.search(raw)
                if m:
                    try:
                        cur_ts = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M"))
                    except Exception:
                        pass
                clean = self._REMINDER_RE.sub("", raw).strip()
                if not clean or cur_ts is None or cur_ts < since:
                    continue
                out.append(
                    {
                        "ts": cur_ts,
                        "role": "user" if role == "user" else "assistant",
                        "text": clean[:300],
                    }
                )
        out.sort(key=lambda x: x["ts"])
        return out[-limit:]

    def _collect_entries(self, umo: str, since: float, limit: int) -> list:
        merged = self._db_entries(umo, since, limit)
        buf = [b for b in (self.buffers.get(umo) or []) if b.get("ts", 0) >= since and b.get("text")]
        seen = {(e["role"], e["text"][:60]) for e in merged}
        for b in buf:
            key = (b.get("role"), str(b.get("text", ""))[:60])
            if key in seen:
                continue
            seen.add(key)
            merged.append(b)
        merged.sort(key=lambda x: x.get("ts", 0))
        return merged[-limit:]

    def _render_raw(self, entries: list) -> str:
        lines = []
        for e in entries:
            t = time.strftime("%H:%M", time.localtime(e.get("ts", 0)))
            who = "用户" if e.get("role") == "user" else "你"
            lines.append(f"[{t}] {who}：{e.get('text','')}")
        return "\n".join(lines)

    def _render_items(self, items: list) -> str:
        if not items:
            return "[]"
        return json.dumps(
            [
                {
                    "id": i.get("id"),
                    "content": i.get("content"),
                    "type": i.get("type"),
                    "importance": i.get("importance"),
                }
                for i in items
            ],
            ensure_ascii=False,
        )

    # ---------------- 三层整理 ----------------
    async def _consolidate_hour(
        self, event, umo: str, cfg: dict, window_sec: float, ignore_last: bool = False
    ) -> int:
        rec = self.store.umo(umo)
        now = time.time()
        since = 0.0 if window_sec == 0 else now - window_sec
        if not ignore_last:
            since = max(float(rec["hour"]["last_at"] or 0), since)
        entries = self._collect_entries(umo, since, cfg["hour_input_max"])
        if not entries:
            return 0
        page = _ensure_page(rec, "hour", cfg)
        prev = ""
        pages = rec["hour"]["pages"]
        if len(pages) >= 2:
            prev = self._render_items(pages[-2].get("items", []))
        prompt = (
            f"【这一页现在的内容】\n{self._render_items(page.get('items', []))}\n\n"
            + (f"【上一页（已经冻结，只读参考，不要改动它）】\n{prev}\n\n" if prev else "")
            + f"【新增内容（最近一小时的原话）】\n{self._render_raw(entries)}"
        )
        text = await self._llm(event, "hour", prompt, _day_label())
        items = self._parse_items(text)
        if items is None:
            return 0
        page["items"] = self._norm(items, cfg["max_items"].get("hour", 20))
        page["state"] = "mutable"
        rec["hour"]["last_at"] = time.time()
        rec["hour"]["cursor"] = max([float(e.get("ts", 0)) for e in entries] + [rec["hour"]["last_at"]] )
        await self.store.save()
        logger.info(
            f"mem3: 小时页整理完成 {umo} → {len(page['items'])} 条（输入 {len(entries)} 条）"
        )
        return len(entries)

    async def _consolidate_day(self, event, umo: str, cfg: dict):
        rec = self.store.umo(umo)
        hour_page = _current_page(rec, "hour")
        src = list((hour_page or {}).get("items", []))
        if not src:
            logger.info(f"mem3: 本：页里还没内容，跳过（不冻结、不计时） {umo}")
            return
        day_page = _ensure_page(rec, "day", cfg)
        if src:
            prompt = (
                f"【这一本现在的内容】\n{self._render_items(day_page.get('items', []))}\n\n"
                f"【刚冻结的那一页小时记忆（只读参考）】\n{self._render_items(src)}"
            )
            text = await self._llm(event, "day", prompt, _day_label())
            items = self._parse_items(text)
            if items is not None:
                day_page["items"] = self._norm(items, cfg["max_items"].get("day", 10))
                day_page["state"] = "mutable"
        # 冻结小时页 → 立刻翻开新的一页
        _freeze_page(rec, "hour")
        _ensure_page(rec, "hour", cfg)
        _prune_pages(rec, "hour", int(cfg.get("keep_pages", 2)))
        rec["day"]["last_at"] = time.time()
        await self.store.save()
        logger.info(
            f"mem3: 天整理完成 {umo} 吸取 {len(src)} 条；小时页已冻结翻新，天本 {len(day_page.get('items', []))} 条"
        )

    async def _consolidate_profile(self, event, umo: str, cfg: dict):
        rec = self.store.umo(umo)
        day_page = _current_page(rec, "day")
        src = list((day_page or {}).get("items", []))
        if not src:
            logger.info(f"mem3: 棚：本里还没内容，跳过（不冻结、不计时） {umo}")
            return
        # 这本还没攒满一个天整理周期 → 不提前吸它
        opened = float(day_page.get("opened_at") or 0)
        if opened and time.time() - opened < int(cfg["day_gap_seconds"]):
            logger.info(f"mem3: 棚：这本才攒了 {(time.time()-opened)//3600:.0f} 小时，等满 1 天再吸 {umo}")
            return
        prompt = (
            f"【长期记忆现在的内容】\n{self._render_items(rec['profile']['items'])}\n\n"
            f"【刚冻结的那一本天记忆（只读参考）】\n{self._render_items(src)}"
        )
        text = await self._llm(event, "profile", prompt, _day_label())
        items = self._parse_items(text)
        if items is not None:
            cap = min(cfg["max_items"].get("profile", 8), cfg["profile_cap"])
            rec["profile"]["items"] = self._norm(items, cap)
            rec["profile"]["last_at"] = time.time()
        _freeze_page(rec, "day")
        _ensure_page(rec, "day", cfg)
        _prune_pages(rec, "day", int(cfg.get("keep_pages", 2)))
        await self.store.save()
        logger.info(
            f"mem3: 长期整理完成 {umo} 吸取 {len(src)} 条；天本已冻结换新，长期 {len(rec['profile']['items'])} 条"
        )

    def _count_new(self, umo: str, cursor: float) -> int:
        """门槛用：上次整理之后又多了多少条对话（以 cursor 为水位，不去重）。"""
        buf = len([b for b in (self.buffers.get(umo) or []) if b.get("ts", 0) > cursor])
        return buf + len(self._db_entries(umo, cursor + 1, 999))

    async def _run_due(self, event, umo: str):
        cfg = self._cfg()
        rec = self.store.umo(umo)
        now = time.time()
        async with self._lock(umo):
            h_last = float(rec["hour"]["last_at"] or 0)
            due = h_last == 0 or now - h_last >= cfg["hour_gap_seconds"]
            if due and h_last > 0:
                need = int(cfg.get("min_new_entries", 6))
                cursor = float(rec["hour"].get("cursor") or 0) or h_last
                fresh = self._count_new(umo, cursor)
                if fresh < need:
                    due = False
                    logger.debug(f"mem3: 新增 {fresh}/{need} 条，本次不整理")
            if due:
                await self._consolidate_hour(event, umo, cfg, cfg["hour_gap_seconds"])

            boot = False
            d_last = float(rec["day"]["last_at"] or 0)
            if d_last == 0:
                rec["day"]["last_at"] = now
                rec["day"].setdefault("boot_at", now)
                boot = True
                logger.info(
                    f"mem3: 首次基准 本：{_day_label(_date(now + cfg['day_gap_seconds']))} 左右开始第一次换本 {umo}"
                )
            elif now - d_last >= cfg["day_gap_seconds"]:
                await self._consolidate_day(event, umo, cfg)

            p_last = float(rec["profile"]["last_at"] or 0)
            if p_last == 0:
                rec["profile"]["last_at"] = now
                rec["profile"].setdefault("boot_at", now)
                boot = True
                logger.info(
                    f"mem3: 首次基准 棚：{cfg['profile_gap_seconds']//86400} 天后开始第一次整理 {umo}"
                )
            elif now - p_last >= cfg["profile_gap_seconds"]:
                await self._consolidate_profile(event, umo, cfg)

            if boot:
                await self.store.save()

    # ---------------- 收集与注入 ----------------
    @filter.on_llm_response()
    async def collect(self, event: AstrMessageEvent, resp=None, *args, **kwargs):
        if not self._enabled():
            return
        try:
            umo = str(event.unified_msg_origin)
            user_text = (event.message_str or "").strip()
            reply = (getattr(resp, "completion_text", "") or "").strip()
            if not user_text or not reply:
                return
            buf = self.buffers.setdefault(umo, [])
            now = time.time()
            buf.append({"ts": now, "role": "user", "text": user_text[:300]})
            buf.append({"ts": now, "role": "assistant", "text": reply[:300]})
            maxn = int(self._cfg().get("buffer_max", 200))
            if len(buf) > maxn:
                del buf[: len(buf) - maxn]
            await self.store.save_buffers()
            if umo in self._extracting:
                return
            self._extracting.add(umo)
            asyncio.create_task(self._task_run(event, umo))
        except Exception as e:
            logger.error(f"mem3: 收集失败 {e}")

    async def _task_run(self, event, umo: str):
        try:
            await self._run_due(event, umo)
        except Exception as e:
            logger.error(f"mem3: 后台整理失败 {e}")
        finally:
            self._extracting.discard(umo)

    def _visible_items(self, rec: dict, cfg: dict) -> dict:
        hour_page = _current_page(rec, "hour")
        day_page = _current_page(rec, "day")
        hour_items = list((hour_page or {}).get("items", []))[-int(cfg["inject"]["hour_items"]) :]
        day_items = list((day_page or {}).get("items", []))[-int(cfg["inject"]["day_items"]) :]
        prof = rec["profile"]["items"][-int(cfg["inject"]["profile_items"]) :]
        return {"hour": hour_items, "day": day_items, "profile": prof}

    def _memory_block(self, rec: dict, cfg: dict) -> str:
        v = self._visible_items(rec, cfg)
        parts = []
        for key, label in (("hour", "最近几小时"), ("day", "这几天"), ("profile", "长期")):
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
            block = self._memory_block(self.store.umo(str(event.unified_msg_origin)), self._cfg())
            if block:
                req.system_prompt = (getattr(req, "system_prompt", "") or "") + block
        except Exception as e:
            logger.error(f"mem3: 注入失败 {e}")

    # ---------------- 指令 ----------------
    async def _authorized(self, event: AstrMessageEvent) -> bool:
        try:
            cfg = self.context.get_config(umo=event.unified_msg_origin) or {}
        except Exception:
            cfg = {}
        sid = str(event.get_sender_id())
        return sid in [str(x) for x in cfg.get("admins_id", [])] or getattr(event, "role", "") == "admin"

    def _arg(self, event: AstrMessageEvent) -> str:
        text = (event.message_str or "").strip()
        for t in ("/mem", "mem"):
            if text.lower().startswith(t):
                return text[len(t) :].strip()
        return text.strip()

    @staticmethod
    def _due_text(last, gap, now) -> str:
        left = int(float(last or 0) + gap - now)
        if left <= 0:
            return "现在就能"
        if left < 3600:
            return f"{max(1, left // 60)} 分钟后"
        if left < 86400:
            return f"{left // 3600} 小时后"
        return f"{left // 86400} 天后"

    def _status(self, event: AstrMessageEvent, with_help: bool = True) -> str:
        cfg = self._cfg()
        umo = str(event.unified_msg_origin)
        rec = self.store.umo(umo)
        now = time.time()
        hp = _current_page(rec, "hour") or {}
        dp = _current_page(rec, "day") or {}
        cap_h = int(cfg["max_items"].get("hour", 20))
        cap_d = int(cfg["max_items"].get("day", 12))
        cap_p = int(cfg["max_items"].get("profile", 8))
        lines = [
            f"🧠 记忆 · {'已开启' if self._enabled() else '已关闭'}",
            "────────────────────────",
            f"{_pad('页', 4)}{len(hp.get('items', []))} / {cap_h} 条"
            f"   {self._due_text(rec['hour']['last_at'], cfg['hour_gap_seconds'], now)}整理",
            f"{_pad('本', 4)}{len(dp.get('items', []))} / {cap_d} 条"
            f"   {self._due_text(rec['day']['last_at'], cfg['day_gap_seconds'], now)}换本",
            f"{_pad('棚', 4)}{len(rec['profile']['items'])} / {cap_p} 条"
            f"   {self._due_text(rec['profile']['last_at'], cfg['profile_gap_seconds'], now)}整理",
            "────────────────────────",
            f"门槛 距上次整理新增 ≥{int(cfg.get('min_new_entries', 6))} 条才触发"
            f"｜现存 {len(rec['hour']['pages'])} 页 / {len(rec['day']['pages'])} 本",
        ]
        if with_help:
            lines += [
                "────────────────────────",
                "/mem on｜off   开关记忆",
                "/mem show      看现在会带进去的记忆",
                "/mem clear     清掉本会话的页/本/棚",
                "/mem gen 6     手动整理最近 6 小时",
                "/mem gen 0     手动整理全部历史",
                f"/mem limit {cfg['hour_input_max']}   单次喂料 50-250",
            ]
        # 本/棚 还没启动过：明确标出来，避免误以为“跑过但空了”
        if not rec["day"]["pages"]:
            lines.insert(4, "（本尚未启动：到点才会第一次换本）")
        if not rec["profile"]["items"]:
            lines.insert(6, "（棚尚未启动：第一次整理后才会有画像）")
        return "\n".join(lines)

    @filter.command("mem")
    async def mem_cmd(self, event: AstrMessageEvent):
        """记忆：开关 / 查看 / 清空 / 主动整理"""
        if not await self._authorized(event):
            yield event.plain_result("没权限")
            return
        arg = self._arg(event).lower()
        umo = str(event.unified_msg_origin)
        rec = self.store.umo(umo)

        if not arg:
            yield event.plain_result(self._status(event))
            return

        if arg in ("on", "off", "enable", "disable", "开", "关"):
            on = arg in ("on", "enable", "开")
            self._set_enabled(on)
            yield event.plain_result(f"🧠 记忆已{'开启' if on else '关闭'}")
            return

        if arg.startswith("gen"):
            part = arg[3:].strip()
            if not part:
                yield event.plain_result("用法：/mem gen 6（0-24，0=整理全部历史）")
                return
            try:
                hours = int(part)
            except ValueError:
                yield event.plain_result("用法：/mem gen 6（0-24，0=整理全部历史）")
                return
            if hours < 0 or hours > 24:
                yield event.plain_result("参数只能是 0-24")
                return
            cfg = self._cfg()
            window = hours * 3600 if hours else 0
            async with self._lock(umo):
                used = await self._consolidate_hour(event, umo, cfg, window, ignore_last=True)
            if not used:
                yield event.plain_result(
                    f"没有可整理的对话\n最近 {hours or '全部'} 小时里没抓到带时间戳的消息。"
                )
                return
            rec = self.store.umo(umo)
            hp = _current_page(rec, "hour") or {}
            span = f"最近 {hours} 小时" if hours else "全部历史"
            yield event.plain_result(
                f"✅ 已整理{span}\n抓到 {used} 条对话 · 这一页现在 {len(hp.get('items', []))} 条"
            )
            return

        if arg.startswith("limit"):
            cfg = self._cfg()
            part = arg[5:].strip()
            if not part:
                yield event.plain_result(
                    f"单次整理喂 {cfg['hour_input_max']} 条对话\n用法：/mem limit 50-250"
                )
                return
            try:
                n = int(part)
            except ValueError:
                yield event.plain_result("用法：/mem limit 50-250")
                return
            if n < 50 or n > 250:
                yield event.plain_result("只能填 50-250")
                return
            self._save_cfg_value("hour_input_max", n)
            yield event.plain_result(f"✅ 单次整理上限已设为 {n} 条")
            return

        if arg == "show":
            cfg = self._cfg()
            v = self._visible_items(rec, cfg)
            lines = ["🧠 现在会带进聊天的记忆", "────────────────────────"]
            for k, label in (("hour", "页"), ("day", "本"), ("profile", "棚")):
                lines.append(f"【{label}】{len(v[k])} 条")
                lines += [f"  · {i.get('content')}" for i in v[k]] or ["  （空）"]
                lines.append("")
            frozen = len(rec["hour"]["pages"]) - 1 + len(rec["day"]["pages"]) - 1
            lines.append(f"另外冻结着 {max(0, frozen)} 页/本，只作整理参考、不带进聊天")
            yield event.plain_result("\n".join(lines))
            return

        if arg == "clear":
            data = self.store._load()
            data["umos"].pop(umo, None)
            await self.store.save()
            self.buffers.pop(umo, None)
            await self.store.save_buffers()
            yield event.plain_result("✅ 已清空本会话的三层记忆")
            return

        if arg in ("clearall", "clear_all"):
            yield event.plain_result("清空全部记忆的命令已移除（太危险）。要清就对本会话用 /mem clear。")
            return

        yield event.plain_result("没有这个子命令\n可用：/mem、on、off、show、clear、gen 0-24、limit 50-250")
