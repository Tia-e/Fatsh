"""access —— 指令过滤 / agent 闸门 / 会话身份标记

职责（只做这三件事，不注册任何用户命令）：
1 指令过滤：/ 开头的消息，只有主人放行；其他人 event.stop_event()，AI 完全看不见
2 agent 闸门：非主人清空工具集，并明说"没有工具"
3 会话身份：给每次请求追加 <scene> / <who> 两段，告诉模型现在是私聊还是群、对方是谁

配置 data/access_config.json（明文 QQ，不哈希）：
{"owner_ids": ["3447899870"], "filter_commands": true, "scene_prompt": true}
"""

import json
import os

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.event.filter import EventMessageType
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star, register

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
except Exception:  # pragma: no cover
    get_astrbot_data_path = None

CFG_NAME = "access_config.json"
DEFAULT_CFG = {"owner_ids": [], "filter_commands": True, "scene_prompt": True}


def _data_path() -> str:
    if get_astrbot_data_path:
        try:
            return get_astrbot_data_path()
        except Exception:
            pass
    return os.path.join(os.getcwd(), "data")


def _cfg() -> dict:
    cfg = dict(DEFAULT_CFG)
    try:
        with open(os.path.join(_data_path(), CFG_NAME), "r", encoding="utf-8-sig") as f:
            d = json.load(f)
        if isinstance(d, dict):
            cfg.update({k: v for k, v in d.items() if k in DEFAULT_CFG})
    except Exception:
        pass
    cfg["owner_ids"] = [str(x).strip() for x in (cfg.get("owner_ids") or []) if str(x).strip()]
    return cfg


def _is_owner(sid, cfg: dict) -> bool:
    return str(sid).strip() in set(cfg.get("owner_ids") or [])


def _raw_text(event: AstrMessageEvent) -> str:
    """取没有被剥离唤醒前缀的原始文本。"""
    try:
        comps = list(event.message_obj.message)
        txt = "".join(getattr(c, "text", "") or "" for c in comps if isinstance(c, Plain))
        if txt.strip():
            return txt.strip()
    except Exception:
        pass
    return (event.message_str or "").strip()


def _scene(event: AstrMessageEvent, owner: bool) -> str:
    umo = str(event.unified_msg_origin)
    is_group = "Group" in umo
    tail = umo.rsplit(":", 1)[-1] if ":" in umo else ""
    if is_group:
        speakers = "群里说话的人里可能有 TA，也可能只是普通群友；拿不准就按普通群友对待，别把私聊的事说出去"
        scene = f"群聊（群号 {tail}），消息可能来自多个人"
    else:
        speakers = "TA 就是眼前这个人，可以放开聊" if owner else "对方是陌生人，只闲聊"
        scene = "私聊（一对一）"
    who = "主人（TA 养你、有全部权限）" if owner else "陌生人（没有权限，只能闲聊）"
    return (
        f"<scene>当前会话：{scene}</scene>\n"
        f"<who>对方是：{who}；{speakers}</who>"
    )


@register(
    "astrbot_plugin_access",
    "hermes",
    "访问控制：指令过滤 + agent 闸门 + 会话身份提示（明文白名单，不做哈希）",
    "1.0.0",
)
class Access(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    # ---------- 1) 指令过滤 ----------
    @filter.event_message_type(EventMessageType.ALL, priority=9999)
    async def filter_commands(self, event: AstrMessageEvent):
        cfg = _cfg()
        if not cfg.get("filter_commands", True):
            return
        text = _raw_text(event)
        if not text or not text.startswith(("/", "／")):
            return
        if _is_owner(event.get_sender_id(), cfg):
            return  # 主人的指令放行，交给各插件的 handler
        try:
            event.stop_event()
        except Exception:
            pass
        logger.debug(
            f"access: 拦截非主人指令 <{str(event.get_sender_id())}> {text[:24]}"
        )

    # ---------- 2) agent 闸门 + 3) 会话身份 ----------
    @filter.on_llm_request()
    async def gate_and_scene(self, event: AstrMessageEvent, req=None, *args, **kwargs):
        if req is None:
            return
        cfg = _cfg()
        owner = _is_owner(event.get_sender_id(), cfg)

        if not owner:
            had_tools = bool(getattr(req, "func_tool", None))
            try:
                from astrbot.core.agent.tool import ToolSet

                req.func_tool = ToolSet()
            except Exception:
                req.func_tool = None
            try:
                sp = getattr(req, "system_prompt", "") or ""
                for marker in ("## Skills", "<skills>"):
                    idx = sp.find(marker)
                    if idx >= 0:
                        req.system_prompt = sp[:idx].rstrip()
                        break
            except Exception:
                pass
            if "【没有工具】" not in (getattr(req, "system_prompt", "") or ""):
                req.system_prompt = (
                    (getattr(req, "system_prompt", "") or "")
                    + "\n\n【没有工具】你现在没有任何工具可用：不能读文件、不能执行命令、不能上网、不能建提醒。"
                    "有人让你做这类事，就直接说做不到，绝对不要输出任何工具调用格式。"
                ).strip()
            if had_tools:
                logger.info(f"access: 已剥离 {event.get_sender_id()} 的工具集（非主人）")

        if cfg.get("scene_prompt", True):
            block = _scene(event, owner)
            try:
                event.set_extra("scene_block", block)
            except Exception:
                pass
            # 先追加；如果 persona 重建了 system_prompt，它会从 extra 里再拼一次
            if block not in (getattr(req, "system_prompt", "") or ""):
                req.system_prompt = ((getattr(req, "system_prompt", "") or "") + "\n\n" + block).strip()
