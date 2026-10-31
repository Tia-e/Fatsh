"""指令表 /cmd + 指令闸门（纯插件，替代内核 waking_check 补丁）

闸门逻辑：
- 只看以 "/" 开头的消息（唤醒前缀）
- 发送者在 data/cmd_perm.json 白名单里（salt+sha256 或 allowed_ids）→ 放行
- 否则 event.stop_event()，后面的命令 handler 一个都不会执行 → 对方看到的是"什么都没有"
"""

import hashlib
import json
import os

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star, register
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

HELP_TEXT = """📋 指令表
────────────────────────
/mem        记忆（页/本/棚）：开关/查看/整理
/config     延迟 思考 上下文 提示词
/mode       人格风格（5 套）
/reset      清空当前会话上下文
/new        另开一条新对话
/stats      本会话 token 用量
/stop       打断她正在生成的回复
/sid        查看会话 ID
/provider   查看 / 切换模型（管理员）
/name       设置会话显示名（管理员）
/cmd        这张表"""


def _data_path() -> str:
    try:
        return get_astrbot_data_path()
    except Exception:
        return os.path.join(os.getcwd(), "data")


def _load_perm() -> dict:
    try:
        with open(os.path.join(_data_path(), "cmd_perm.json"), "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return {"enabled": False, "allowed_ids": [], "allowed_hashes": [], "salt": ""}


def _allowed(sender_id) -> bool:
    perm = _load_perm()
    sid = str(sender_id).strip()
    ids = {str(x).strip() for x in (perm.get("allowed_ids") or []) if str(x).strip()}
    if sid in ids:
        return True
    salt = str(perm.get("salt") or "")
    hashes = {
        str(h).strip().lower()
        for h in (perm.get("allowed_hashes") or [])
        if str(h).strip()
    }
    if salt and hashes:
        return hashlib.sha256((salt + sid).encode()).hexdigest().lower() in hashes
    return False


def _raw_text(event: AstrMessageEvent) -> str:
    """取未被剥离唤醒前缀的原文（message_obj 里是原始消息链）。"""
    try:
        comps = list(event.message_obj.message)
        txt = "".join(
            (getattr(c, "text", "") or "") for c in comps if isinstance(c, Plain)
        )
        if txt.strip():
            return txt.strip()
    except Exception:
        pass
    return (event.message_str or "").strip()


@register("astrbot_plugin_cmd_help", "hermes", "中文指令表 /cmd + 指令静默闸门", "2.0.0")
class CmdHelp(Star):
    """指令表 + 闸门：非白名单发 "/" 指令直接静默丢弃。"""

    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.context = context

    @filter.event_message_type(filter.EventMessageType.ALL, priority=9999)
    async def guard_slash_command(self, event: AstrMessageEvent) -> None:
        """非白名单的 "/" 指令：完全静默（不回复、不执行）。"""
        try:
            raw = _raw_text(event)
            if not raw.startswith("/"):
                return
            if _allowed(event.get_sender_id()):
                return
            logger.info(
                f"cmd_gate: 静默拦截 {event.get_sender_id()} → {raw[:20]}"
            )
            event.stop_event()
        except Exception as e:
            logger.error(f"cmd_gate: 判定失败 {e}")

    @filter.command("cmd", alias={"指令", "指令表"}, priority=-100)
    async def cmd_list(self, event: AstrMessageEvent):
        """查看全部指令及作用"""
        if not _allowed(event.get_sender_id()):
            return
        yield event.plain_result(HELP_TEXT)
