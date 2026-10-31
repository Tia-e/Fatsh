import json
import os

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

HELP_LINES = [
    "/config delay 0-10    改发送延迟（0=秒发，5=默认，10=最慢）",
    "/config think on|off  改思考开关（默认关；整理记忆时始终开）",
    "/config ctx 2-32      改上下文预算（单位 k，默认 16）",
    "/config prompt        看这条会话的完整提示词",
    "/config provider      看当前模型",
]

DELAY_CFG = "config/astrbot_plugin_human_delay_config.json"
MODE_STATE = "mode_state.json"


def _data_path(*parts: str) -> str:
    return os.path.join(get_astrbot_data_path(), *parts)


def _pad(s: str, width: int) -> str:
    """按显示宽度补空格（中文算 2 格）。"""
    w = sum(2 if ord(c) > 127 else 1 for c in s)
    return s + " " * max(1, width - w)


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

    # ---------- 思考开关 ----------
    def _think_state(self, event: AstrMessageEvent) -> tuple[bool, list[str]]:
        try:
            cfg = self.context.get_config(umo=event.unified_msg_origin) or {}
        except Exception:
            cfg = {}
        on = False
        ids = []
        for p in cfg.get("provider", []):
            if p.get("type") != "openai_chat_completion":
                continue
            ids.append(str(p.get("id")))
            ce = p.get("custom_extra_body") or {}
            if str(ce.get("reasoning_effort", "")).lower() not in (
                "none",
                "off",
                "disabled",
            ):
                on = True
        return on, ids

    def _apply_think(self, event: AstrMessageEvent, on: bool) -> list[str]:
        cfg = self.context.get_config(umo=event.unified_msg_origin)
        ids = []
        for p in cfg.get("provider", []):
            if p.get("type") != "openai_chat_completion":
                continue
            ids.append(str(p.get("id")))
            ce = p.setdefault("custom_extra_body", {})
            if on:
                ce.pop("reasoning_effort", None)
            else:
                ce["reasoning_effort"] = "none"
        cfg.save_config()
        # 运行时立即生效：改已加载的 provider 实例
        for inst in getattr(self.context.provider_manager, "provider_insts", []):
            pid = str((inst.provider_config or {}).get("id", ""))
            if pid in ids:
                ce = inst.provider_config.setdefault("custom_extra_body", {})
                if on:
                    ce.pop("reasoning_effort", None)
                else:
                    ce["reasoning_effort"] = "none"
        return ids

    # ---------- 上下文预算 ----------
    CTX_MIN, CTX_MAX, CTX_DEFAULT = 2, 32, 16

    def _ctx_level(self, event: AstrMessageEvent) -> int:
        try:
            cfg = self.context.get_config(umo=event.unified_msg_origin) or {}
            v = int(
                cfg.get("agent_runner", {})
                .get("config", {})
                .get("compression", {})
                .get("fallback_max_tokens", self.CTX_DEFAULT * 1000)
            )
            return max(self.CTX_MIN, min(self.CTX_MAX, round(v / 1000)))
        except Exception:
            return self.CTX_DEFAULT

    def _apply_ctx(self, event: AstrMessageEvent, level: int) -> dict:
        cfg = self.context.get_config(umo=event.unified_msg_origin)
        comp = cfg["agent_runner"]["config"].setdefault("compression", {})
        comp["fallback_max_tokens"] = int(level) * 1000
        # 预算很小时，用"丢最早轮次"而不是"每轮都调模型做摘要"
        comp["overflow_strategy"] = "truncate_by_turns"
        cfg.save_config()
        return comp

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
            _on, _ = self._think_state(event)
            _dly = _read_json(_data_path(DELAY_CFG), {}) or {}
            yield event.plain_result(
                "⚙️ 配置\n"
                "────────────────────────\n"
                f"{_pad('延迟', 8)}{self._delay_level()} 档 · 每字 {_dly.get('per_char_seconds', 0)} 秒"
                " · 分段最低 1.2 秒\n"
                f"{_pad('思考', 8)}{'开' if _on else '关'}\n"
                f"{_pad('上下文', 8)}{self._ctx_level(event)}k tokens\n"
                "────────────────────────\n"
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
                yield event.plain_result("✅ 发送延迟已设为 0：立刻发送（分段也不等）")
            else:
                yield event.plain_result(
                    f"✅ 发送延迟已设为 {level}：基础 0-{level} 秒（正态，均值 {level/2:.1f}）"
                    f" + 每字 {level*0.01:.2f} 秒"
                    f"\n分段之间最低间隔 {cfg.get('segment_min_interval', 0):.1f} 秒"
                    f"（长句按 每字×字数 算，取两者较大值）"
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

        if sub == "think":
            if not rest:
                on, _ = self._think_state(event)
                yield event.plain_result(
                    f"🧠 当前思考：{'开' if on else '关'}\n用法：/config think on|off（默认关）"
                )
                return
            val = rest.strip().lower()
            if val in ("on", "开", "1", "true", "yes"):
                ids = self._apply_think(event, True)
                yield event.plain_result(
                    f"✅ 已开启思考（{', '.join(ids)}）\n注意：会更慢、更贵，输出 tokens 约涨 3-4 倍"
                )
            elif val in ("off", "关", "0", "false", "no"):
                ids = self._apply_think(event, False)
                yield event.plain_result(f"✅ 已关闭思考（{', '.join(ids)}），立即生效")
            else:
                yield event.plain_result("参数只能是 on 或 off。用法：/config think off")
            return

        if sub == "ctx":
            if not rest:
                yield event.plain_result(
                    f"📦 当前上下文预算：{self._ctx_level(event)}k tokens\n"
                    "用法：/config ctx <2-32>（2=2k，32=32k，默认 16）"
                )
                return
            try:
                level = int(rest)
            except ValueError:
                yield event.plain_result("参数必须是 2-32 的整数")
                return
            if level < self.CTX_MIN or level > self.CTX_MAX:
                yield event.plain_result("参数必须在 2-32 之间")
                return
            comp = self._apply_ctx(event, level)
            yield event.plain_result(
                f"✅ 上下文预算已设为 {level}k tokens\n"
                f"当前上限 {comp.get('fallback_max_tokens')}\n"
                "（重启 AstrBot 后生效；超出后丢最早的轮次，不额外调模型）"
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
