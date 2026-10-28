import json
import os

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

HELP_LINES = [
    "/config             本说明",
    "/config delay <0-10>  修改发送延迟：0=立刻发，5=0-5秒正态+每字0.05秒（默认），10=0-10秒+每字0.1秒",
    "/config prompt      输出这条会话实际发给模型的完整提示词（公用规则+场景+人格+记忆）",
    "/config provider    查看当前使用的模型与候选模型",
]

DELAY_CFG = "config/astrbot_plugin_human_delay_config.json"
MODE_STATE = "mode_state.json"


def _data_path(*parts: str) -> str:
    return os.path.join(get_astrbot_data_path(), *parts)


def _read_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path: str, data) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    os.replace(tmp, path)


@register("astrbot_plugin_config", "hermes", "/config 统一配置入口：延迟、提示词、模型", "1.0.0")
class ConfigCmd(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config or {}

    # ---------- 权限 ----------
    async def _authorized(self, event: AstrMessageEvent) -> bool:
        try:
            cfg = self.context.get_config(umo=event.unified_msg_origin) or {}
        except Exception:
            cfg = {}
        sid = str(event.get_sender_id())
        if sid in [str(x) for x in cfg.get("admins_id", [])]:
            return True
        return getattr(event, "role", "") == "admin"

    def _arg(self, event: AstrMessageEvent) -> str:
        text = (event.message_str or "").strip()
        for token in ("/config", "config"):
            if text.lower().startswith(token):
                return text[len(token):].strip()
        return text.strip()

    # ---------- 延迟 ----------
    def _delay_level(self) -> int:
        cfg = _read_json(_data_path(DELAY_CFG), {})
        if not cfg.get("enabled", True):
            return 0
        try:
            return int(round(float(cfg.get("max_delay", 5))))
        except Exception:
            return 5

    def _apply_delay(self, level: int) -> dict:
        level = max(0, min(10, level))
        if level == 0:
            new = {
                "enabled": False,
                "distribution": "normal",
                "mean": 0.0,
                "sigma": 0.0,
                "min_delay": 0.0,
                "max_delay": 0.0,
                "per_char_seconds": 0.0,
                "max_total": 15.0,
            }
        else:
            new = {
                "enabled": True,
                "distribution": "normal",
                "mean": round(level * 0.5, 2),
                "sigma": round(level * 0.26, 2),
                "min_delay": 0.0,
                "max_delay": float(level),
                "per_char_seconds": round(level * 0.01, 3),
                # 分段之间的最低间隔：档位 5 → 1.2 秒，档位 10 → 2.4 秒
                "segment_min_interval": round(level * 0.24, 2),
                "max_total": 15.0,
            }
        _write_json(_data_path(DELAY_CFG), new)
        return new

    # ---------- 提示词 ----------
    def _base_persona(self, event: AstrMessageEvent) -> str:
        try:
            cfg = self.context.get_config(umo=event.unified_msg_origin) or {}
            pid = cfg.get("agent_runner", {}).get("config", {}).get("persona", {}).get("persona_id", "")
            if not pid:
                return ""
            p = self.context.persona_manager.get_persona_v3_by_id(pid)
            if isinstance(p, dict):
                return p.get("prompt", "") or ""
            return getattr(p, "prompt", "") or ""
        except Exception:
            return ""

    def _mode_prompt(self, event: AstrMessageEvent) -> tuple[str, str | None]:
        state = _read_json(_data_path(MODE_STATE), {})
        mode = state.get(str(event.unified_msg_origin), "默认") if isinstance(state, dict) else "默认"
        return mode, None

    def _mode_prompt_text(self, mode: str) -> str | None:
        """从 mode 插件导出的文件里取提示词（避免跨插件 import）"""
        data = _read_json(_data_path("mode_prompts.json"), {})
        modes = data.get("modes") if isinstance(data, dict) else None
        if isinstance(modes, dict):
            return modes.get(mode)
        return None

    # ---------- 指令 ----------
    @filter.command("config")
    async def config_cmd(self, event: AstrMessageEvent):
        """配置：延迟、提示词、模型。用法：/config delay 5"""
        raw = self._arg(event)
        if not raw:
            yield event.plain_result(
                "⚙️ 当前延迟档位：" + str(self._delay_level()) + "（0=秒发，5=默认，10=最慢）\n\n"
                + "\n".join(HELP_LINES)
            )
            return

        if not await self._authorized(event):
            yield event.plain_result("没权限：只有机器人管理员能用 /config")
            return

        parts = raw.split(maxsplit=1)
        sub = parts[0].lower()
        rest = parts[1].strip() if len(parts) > 1 else ""

        if sub in ("help", "-h", "?"):
            yield event.plain_result("\n".join(HELP_LINES))
            return

        if sub == "delay":
            if not rest:
                yield event.plain_result(f"当前延迟档位：{self._delay_level()}。用法：/config delay <0-10>")
                return
            try:
                level = int(rest)
            except ValueError:
                yield event.plain_result("参数必须是 0-10 的整数")
                return
            if level < 0 or level > 10:
                yield event.plain_result("参数必须在 0-10 之间")
                return
            cfg = self._apply_delay(level)
            if level == 0:
                self._maybe_zero_chars()
                yield event.plain_result("✅ 发送延迟已设为 0：立刻发送")
            else:
                yield event.plain_result(
                    f"✅ 发送延迟已设为 {level}：基础 0-{level} 秒（正态，均值 {level/2:.1f}）"
                    f" + 每字 {level*0.01:.2f} 秒"
                )
            logger.info(f"config: delay -> {cfg}")
            return

        if sub == "prompt":
            rec = _read_json(_data_path("last_prompt.json"), {})
            item = rec.get(str(event.unified_msg_origin)) if isinstance(rec, dict) else None
            head = "🧩 这条会话实际发给模型的完整提示词"
            if not isinstance(item, dict) or not item.get("prompt"):
                yield event.plain_result(
                    head
                    + "\n\n这条会话还没有请求过模型（或记录被清了）。\n"
                    "先随便聊一句让她回一次，再发 /config prompt。"
                )
                return
            prompt = str(item.get("prompt"))
            yield event.plain_result(
                f"{head}\n"
                f"模式：{item.get('mode', '?')}｜生成于 {item.get('ts', '?')}｜共 {len(prompt)} 字\n"
                "────────────\n"
                + prompt
            )
            return

        if sub in ("provider", "model"):
            try:
                provider = await self.context.get_using_provider_async(event.unified_msg_origin)
            except Exception:
                provider = None
            if not provider:
                yield event.plain_result("当前没有可用的模型 provider")
                return
            cfg = getattr(provider, "provider_config", {}) or {}
            lines = [
                f"🧠 当前模型：{cfg.get('model', '?')}",
                f"provider id：{cfg.get('id', '?')}",
                f"类型：{cfg.get('type', '?')}",
                f"api_base：{cfg.get('api_base', '(默认)')}",
            ]
            try:
                ps = self.context.provider_manager.providers_config
                ids = [p.get("id") for p in ps if p.get("id")]
                if ids:
                    lines += ["", "已配置模型：" + "、".join(str(x) for x in ids)]
            except Exception:
                pass
            yield event.plain_result("\n".join(lines))
            return

        yield event.plain_result(f"没有「{sub}」这个子命令。可用：delay / prompt / provider")

    def _maybe_zero_chars(self) -> None:
        pass
