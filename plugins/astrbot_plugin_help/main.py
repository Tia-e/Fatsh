"""help —— 只发一个 "/" 时把所有插件的命令列出来

各插件启动时把自己的命令写到 data/cmd_list/<name>.txt（一行一条），
本插件只做：扫描目录、拼成一张表、回复。

触发：主人单独发 "/"（或 "／"）。非主人的 "/" 已经被 access 拦掉，跑不到这里。
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

LIST_DIR = "cmd_list"


def _data(*parts: str) -> str:
    base = get_astrbot_data_path() if get_astrbot_data_path else os.getcwd()
    return os.path.join(base, *parts)


def _owners() -> set[str]:
    try:
        with open(_data("access_config.json"), "r", encoding="utf-8-sig") as f:
            d = json.load(f) or {}
        return {str(x).strip() for x in (d.get("owner_ids") or []) if str(x).strip()}
    except Exception:
        return set()


def _raw_text(event: AstrMessageEvent) -> str:
    try:
        comps = list(event.message_obj.message)
        txt = "".join(getattr(c, "text", "") or "" for c in comps if isinstance(c, Plain))
        if txt.strip():
            return txt.strip()
    except Exception:
        pass
    return (event.message_str or "").strip()


def collect_commands() -> list[tuple[str, list[str]]]:
    """data/cmd_list/*.txt → [(插件名, [命令行...])]，按文件名排序。"""
    d = _data(LIST_DIR)
    if not os.path.isdir(d):
        return []
    out = []
    for name in sorted(os.listdir(d)):
        if not name.endswith(".txt"):
            continue
        path = os.path.join(d, name)
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                lines = [ln.rstrip() for ln in f if ln.strip()]
        except Exception as e:
            logger.error(f"help: 读 {path} 失败 {e}")
            continue
        if lines:
            out.append((name[:-4], lines))
    return out


@register(
    "astrbot_plugin_help",
    "hermes",
    "只发一个 / 就把所有插件的命令列出来（各插件写 data/cmd_list/<name>.txt）",
    "1.0.0",
)
class HelpIndex(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    @filter.event_message_type(EventMessageType.ALL, priority=9998)
    async def bare_slash(self, event: AstrMessageEvent):
        text = _raw_text(event)
        if text not in ("/", "／", "/ ", "／ "):
            return
        sid = str(event.get_sender_id())
        if sid not in _owners():
            return

        cmds = collect_commands()
        if not cmds:
            body = "还没有任何插件写命令清单（data/cmd_list/ 是空的）"
        else:
            blocks = []
            for plugin, lines in cmds:
                blocks.append(f"【{plugin}】\n" + "\n".join(lines))
            body = "\n".join(blocks)
        try:
            event.stop_event()
        except Exception:
            pass
        yield event.plain_result(f"📋 可用指令\n────────────────────────\n{body}")
