import asyncio
import json
import os
import time

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

MEM_KEYS = ("core_memory", "long_term", "medium_term")
INTERVAL = 10  # 每 10 轮对话（用户+回复）整理一次记忆

HELP_LINES = [
    "/mem enable|disable   开关",
    "/mem status|show      状态 / 内容",
    "/mem clear|clearall   清本会话 / 清整库",
]

EXTRACT_PROMPT = """你在整理聊天记忆。下面是最近的一段对话。

请提取值得长期记住的信息，只输出一个 JSON 数组，每项格式：
{"content": "记忆内容（第三人称陈述，20字以内）", "importance": 1-5, "category": "profile|preference|fact|task"}

只记以后有用的：对方的身份/称呼、明确偏好、约定与计划、长期稳定的客观事实。
不要记寒暄、情绪起伏、一次性琐事、你自己的想法。最多 5 条；没有就输出 []。
不要输出任何解释文字。"""


def _data_path(name: str) -> str:
    return os.path.join(get_astrbot_data_path(), name)


def _mem_file() -> str:
    return _data_path("memory_store_global.json")


def _read_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path: str, data) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _load_mem() -> dict:
    d = _read_json(_mem_file(), {})
    if not isinstance(d, dict):
        d = {}
    for k in MEM_KEYS:
        if not isinstance(d.get(k), list):
            d[k] = []
    return d


@register("astrbot_plugin_mem", "hermes", "/mem 管理长期记忆：开关、查看、清理、批量整理", "1.0.0")
class MemPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config or {}
        self.buffers: dict[str, list] = {}
        self._extracting: set[str] = set()

    # ---------- 状态 ----------
    def _state(self) -> dict:
        st = _read_json(_data_path("mem_state.json"), {})
        if not isinstance(st, dict):
            st = {}
        st.setdefault("enabled", False)
        return st

    def _enabled(self) -> bool:
        return bool(self._state().get("enabled", False))

    def _set_enabled(self, on: bool) -> None:
        st = self._state()
        st["enabled"] = bool(on)
        st["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _write_json(_data_path("mem_state.json"), st)

    # ---------- 会话身份 ----------
    def _targets(self, event: AstrMessageEvent) -> tuple[str, list[str]]:
        parts = str(event.unified_msg_origin).split(":")
        msg_type = parts[-2] if len(parts) >= 2 else ""
        subject = parts[-1] if parts else ""
        targets = [subject]
        if msg_type == "GroupMessage":
            targets.append(str(event.get_sender_id()))
        return msg_type, targets

    # ---------- 权限 ----------
    def _cfg(self, event: AstrMessageEvent) -> dict:
        try:
            return self.context.get_config(umo=event.unified_msg_origin) or {}
        except Exception:
            return {}

    async def _is_admin(self, event: AstrMessageEvent) -> bool:
        sid = str(event.get_sender_id())
        if sid in [str(x) for x in self._cfg(event).get("admins_id", [])]:
            return True
        return getattr(event, "role", "") == "admin"

    async def _is_admin_or_group_admin(self, event: AstrMessageEvent) -> bool:
        if await self._is_admin(event):
            return True
        gid = event.get_group_id()
        bot = getattr(event, "bot", None)
        if gid and bot is not None:
            try:
                info = await bot.call_action(
                    "get_group_member_info",
                    group_id=int(gid),
                    user_id=int(event.get_sender_id()),
                    no_cache=True,
                )
                return str((info or {}).get("role", "")).lower() in ("owner", "admin")
            except Exception:
                return False
        return False

    # ---------- 注入 ----------
    def _memory_block(self, event: AstrMessageEvent) -> str:
        data = _load_mem()
        _, targets = self._targets(event)
        parts = []
        for k in MEM_KEYS:
            items = [e for e in data[k] if str(e.get("subject_id")) in targets]
            if not items:
                continue
            lines = [f"- {str(e.get('content', ''))[:80]}" for e in items[:30]]
            parts.append(f"<{k}>\n" + "\n".join(lines) + f"\n</{k}>")
        if not parts:
            return ""
        return (
            "\n\n====================\n"
            "### [RETRIEVED MEMORIES] ###\n"
            "（以下是你记住的事，只能把 subject 与当前会话匹配的内容用于当前对话）\n"
            + "\n".join(parts)
            + "\n====================\n"
        )

    @filter.on_llm_request()
    async def inject_memory(self, event: AstrMessageEvent, req=None, *args, **kwargs):
        if req is None or not self._enabled():
            return
        try:
            block = self._memory_block(event)
            if block:
                req.system_prompt = (getattr(req, "system_prompt", "") or "") + block
        except Exception as e:
            logger.error(f"mem: 注入记忆失败: {e}")

    @filter.on_llm_response()
    async def collect(self, event: AstrMessageEvent, resp=None, *args, **kwargs):
        if not self._enabled():
            return
        try:
            uid = str(event.unified_msg_origin)
            user_text = (event.message_str or "").strip()
            reply = ""
            if resp is not None:
                reply = (getattr(resp, "completion_text", "") or "").strip()
            if not user_text or not reply:
                return
            buf = self.buffers.setdefault(uid, [])
            buf.append({"user": user_text[:300], "assistant": reply[:300]})
            if len(buf) < INTERVAL or uid in self._extracting:
                return
            pairs = buf[:INTERVAL]
            self.buffers[uid] = buf[INTERVAL:]
            self._extracting.add(uid)
            asyncio.create_task(self._extract(event, uid, pairs))
        except Exception as e:
            logger.error(f"mem: 收集对话失败: {e}")

    async def _extract(self, event: AstrMessageEvent, uid: str, pairs: list) -> None:
        try:
            provider = await self.context.get_using_provider_async(uid)
            if not provider:
                return
            convo = "\n".join(f"用户：{p['user']}\n你：{p['assistant']}" for p in pairs)
            resp = await provider.text_chat(
                prompt="对话如下：\n" + convo + "\n\n请按要求输出 JSON 数组。",
                contexts=[],
                system_prompt=EXTRACT_PROMPT,
            )
            text = (resp.completion_text or "").strip()
            start, end = text.find("["), text.rfind("]")
            if start == -1 or end == -1:
                return
            items = json.loads(text[start : end + 1])
            if not isinstance(items, list):
                return

            _, targets = self._targets(event)
            subject = targets[0]
            data = _load_mem()
            existing = {str(e.get("content", "")).strip() for e in data["long_term"]}
            added = 0
            for it in items[:5]:
                if not isinstance(it, dict):
                    continue
                content = str(it.get("content", "")).strip()
                if not content or content in existing:
                    continue
                data["long_term"].append(
                    {
                        "memory_id": f"lt-{int(time.time() * 1000)}-{added}",
                        "content": content[:120],
                        "importance": int(it.get("importance", 3) or 3),
                        "category": str(it.get("category", "fact")),
                        "subject_id": subject,
                        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                )
                existing.add(content)
                added += 1
            if added:
                _write_json(_mem_file(), data)
                logger.info(f"mem: 从 {len(pairs)} 轮对话新增 {added} 条记忆（{subject}）")
        except Exception as e:
            logger.error(f"mem: 整理记忆失败: {e}")
        finally:
            self._extracting.discard(uid)

    # ---------- 指令 ----------
    def _arg(self, event: AstrMessageEvent) -> str:
        text = (event.message_str or "").strip()
        for token in ("/mem", "mem"):
            if text.lower().startswith(token):
                return text[len(token):].strip().lower()
        return text.strip().lower()

    def _status_text(self, event: AstrMessageEvent) -> str:
        state = self._state()
        on = bool(state.get("enabled"))
        data = _load_mem()
        _, targets = self._targets(event)
        counts = {k: sum(1 for e in data[k] if str(e.get("subject_id")) in targets) for k in MEM_KEYS}
        total = sum(counts.values())
        head = "🟢 长期记忆：已开启" if on else "⚪ 长期记忆：已关闭（默认）"
        detail = f"本会话：core {counts['core_memory']} / long {counts['long_term']} / medium {counts['medium_term']}，共 {total} 条"
        if on:
            detail += f"\n每 {INTERVAL} 轮对话自动整理一次；当前缓冲 {len(self.buffers.get(str(event.unified_msg_origin), []))} 轮"
        return head + "\n" + detail

    @filter.command("mem")
    async def mem_cmd(self, event: AstrMessageEvent):
        """长期记忆管理。用法：/mem 看说明与状态"""
        arg = self._arg(event)

        if not arg:
            yield event.plain_result(
                "🧠 长期记忆 — " + self._status_text(event).replace("长期记忆：", "") + "\n\n" + "\n".join(HELP_LINES)
            )
            return

        if arg in ("help", "-h", "?"):
            yield event.plain_result("🧠 长期记忆\n\n" + "\n".join(HELP_LINES))
            return

        if arg in ("status", "state"):
            yield event.plain_result(self._status_text(event))
            return

        if arg in ("show", "list", "dump"):
            if not await self._is_admin_or_group_admin(event):
                yield event.plain_result("没权限：只有管理员、群主/群管理能看记忆内容")
                return
            data = _load_mem()
            _, targets = self._targets(event)
            lines = []
            for k in MEM_KEYS:
                items = [e for e in data[k] if str(e.get("subject_id")) in targets]
                if not items:
                    continue
                lines.append(f"【{k}】{len(items)} 条")
                for e in items[:20]:
                    lines.append(f"· {str(e.get('content', ''))[:70]}")
            yield event.plain_result("\n".join(lines) if lines else "当前会话还没有任何记忆")
            return

        if arg in ("enable", "on"):
            if not await self._is_admin(event):
                yield event.plain_result("没权限：只有机器人管理员能开关长期记忆")
                return
            self._set_enabled(True)
            yield event.plain_result(f"🟢 长期记忆已开启\n每 {INTERVAL} 轮对话自动整理一次并注入上下文")
            return

        if arg in ("disable", "off"):
            if not await self._is_admin(event):
                yield event.plain_result("没权限：只有机器人管理员能开关长期记忆")
                return
            self._set_enabled(False)
            self.buffers.clear()
            yield event.plain_result("⚪ 长期记忆已关闭\n不再注入记忆，也不再自动整理")
            return

        if arg in ("clear", "clearsession"):
            if not await self._is_admin_or_group_admin(event):
                yield event.plain_result("没权限：只有管理员、群主/群管理能清记忆")
                return
            data = _load_mem()
            _, targets = self._targets(event)
            removed = 0
            for k in MEM_KEYS:
                keep = []
                for e in data[k]:
                    if str(e.get("subject_id")) in targets:
                        removed += 1
                    else:
                        keep.append(e)
                data[k] = keep
            _write_json(_mem_file(), data)
            self.buffers.pop(str(event.unified_msg_origin), None)
            yield event.plain_result(f"已清除本会话 {removed} 条记忆" if removed else "本会话本来就没有记忆")
            return

        if arg in ("clearall", "clearall!", "reset"):
            if not await self._is_admin(event):
                yield event.plain_result("没权限：只有机器人管理员能清空整个记忆库")
                return
            data = _load_mem()
            removed = sum(len(data[k]) for k in MEM_KEYS)
            _write_json(_mem_file(), {k: [] for k in MEM_KEYS})
            self.buffers.clear()
            yield event.plain_result(f"已清空整个记忆库，共 {removed} 条")
            return

        yield event.plain_result(
            f"没有「{arg}」这个子命令。可用：enable / disable / status / show / clear / clearall"
        )
