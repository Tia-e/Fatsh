import hashlib
import json
import os

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, register

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
except Exception:  # 兼容旧版本
    get_astrbot_data_path = None


def _data_path() -> str:
    if get_astrbot_data_path:
        return get_astrbot_data_path()
    return os.path.join(os.getcwd(), "data")


def _load_perm() -> dict:
    try:
        with open(
            os.path.join(_data_path(), "cmd_perm.json"), "r", encoding="utf-8"
        ) as f:
            d = json.load(f)
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return {"enabled": False, "allowed_ids": []}


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


@register(
    "astrbot_plugin_priv_gate",
    "hermes",
    "非白名单用户禁用 Agent 工具（只保留纯聊天）",
    "1.0.0",
)
class PrivGate(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    @filter.on_llm_request()
    async def gate_tools(self, event: AstrMessageEvent, req: ProviderRequest):
        """非白名单用户：清空工具集，禁止调用 agent 能力。"""
        perm = _load_perm()
        if not perm.get("enabled", False):
            return
        if _allowed(event.get_sender_id()):
            return

        had_tools = bool(getattr(req, "func_tool", None))
        try:
            from astrbot.core.agent.tool import ToolSet

            req.func_tool = ToolSet()  # 空工具集，比 None 更安全（下游会检查真值）
        except Exception:
            req.func_tool = None

        # 一些版本会把 skills 描述塞进 system_prompt，一并裁掉
        try:
            sp = getattr(req, "system_prompt", "") or ""
            if "## Skills" in sp or "<skills>" in sp:
                idx = sp.find("## Skills")
                if idx < 0:
                    idx = sp.find("<skills>")
                req.system_prompt = sp[:idx].rstrip()
        except Exception:
            pass

        if had_tools:
            logger.info(
                f"priv_gate: 已剥离 {event.get_sender_id()} 的 agent 工具集（非白名单）"
            )

        # 光剥工具不够：模型有时会硬编出工具调用格式当文本发出去
        try:
            cur = getattr(req, "system_prompt", "") or ""
            if "【没有工具】" not in cur:
                req.system_prompt = (
                    cur
                    + "\n\n【没有工具】你现在没有任何工具可用：不能读文件、不能执行命令、不能上网、不能建提醒。"
                    " 有人让你做这类事，就直接说做不到（一句大白话就行），"
                    "绝对不要输出任何工具调用格式（比如 <|...|>、invoke、JSON 里的 name/arguments），"
                    "也不要假装你正在查。"
                ).strip()
        except Exception:
            pass
