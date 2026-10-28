"""群聊"要不要接话"状态机。

三种状态（按群隔离）：
  idle  闲置：每条群消息掷 idle_probability（默认 0.1）的骰子，命中才让模型判断
  talk  接话：60 秒内每条消息都交给模型判断（模型可输出 <不接>），接话刷新窗口
  mute  不接：60 秒内完全不跑模型（只记账），结束后回 idle

结构化信号（@机器人 / 引用机器人 / 叫名字）永远必回，且进入 talk 状态。
判定由模型在同一次推理里完成：不想接就输出 <不接>，插件在发送前把它抹掉。
"""

import json
import os
import random
import time

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

try:
    from astrbot.core.message.components import At, Plain, Reply
except Exception:  # pragma: no cover
    At = Reply = Plain = None

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
except Exception:  # pragma: no cover
    get_astrbot_data_path = None

DEFAULT_CFG = {
    "enabled": True,
    "idle_probability": 0.1,
    "talk_seconds": 60,
    "mute_seconds": 60,
    "max_refresh": 3,
    "cooldown_seconds": 60,
    "structured_names": ["大肥鱼", "肥鱼"],
    "structured_cooldown_seconds": 60,
    "structured_max_per_sender": 2,
    "night_silence": ["23:00", "08:00"],
    "groups": ["*"],
}

PROMPT_HINT = (
    "\n\n【群里说话前先想一句】现在你可以选择不说话。"
    "如果这句话明显不是问你、或者轮不到你插嘴，就只输出 <不接> 四个字符，别的什么都不要写。"
    "只有确实该你开口时，才正常回复。"
)


def _data(*parts: str) -> str:
    base = get_astrbot_data_path() if get_astrbot_data_path else os.getcwd()
    return os.path.join(base, *parts)


class AddresseeState(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config or {}
        self._pending: dict[str, str] = {}  # umo -> structured/talk/idle
        self._state = self._load_state()

    # ---------- 持久化 ----------
    def _cfg(self) -> dict:
        cfg = dict(DEFAULT_CFG)
        try:
            p = _data("addressee_config.json")
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8-sig") as f:
                    d = json.load(f)
                if isinstance(d, dict):
                    cfg.update({k: v for k, v in d.items() if v is not None})
        except Exception as e:
            logger.error(f"addressee: 读配置失败 {e}")
        return cfg

    def _load_state(self) -> dict:
        try:
            p = _data("addressee_state.json")
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    d = json.load(f)
                return d if isinstance(d, dict) else {}
        except Exception:
            pass
        return {}

    def _save_state(self) -> None:
        try:
            p = _data("addressee_state.json")
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._state, f, ensure_ascii=False)
            os.replace(tmp, p)
        except Exception:
            pass

    # ---------- 状态读写 ----------
    def _get(self, umo: str) -> dict:
        rec = self._state.get(umo) or {}
        return {
            "state": rec.get("state", "idle"),
            "until": float(rec.get("until", 0)),
            "refreshes": int(rec.get("refreshes", 0)),
        }

    def _set(self, umo: str, state: str, seconds: float, refreshes: int = 0) -> None:
        self._state[umo] = {
            "state": state,
            "until": time.time() + seconds,
            "refreshes": refreshes,
        }
        self._save_state()

    def _active_state(self, umo: str) -> str:
        rec = self._get(umo)
        if rec["until"] > time.time():
            return rec["state"]
        return "idle"

    # ---------- 判定辅助 ----------
    def _is_night(self, cfg: dict) -> bool:
        try:
            raw = cfg.get("night_silence") or []
            if not raw or len(raw) != 2:
                return False
            start_s, end_s = str(raw[0]), str(raw[1])
            sh, sm = [int(x) for x in start_s.split(":")]
            eh, em = [int(x) for x in end_s.split(":")]
            lt = time.localtime()
            cur = lt.tm_hour * 60 + lt.tm_min
            start, end = sh * 60 + sm, eh * 60 + em
            if start <= end:
                return start <= cur < end
            return cur >= start or cur < end  # 跨午夜
        except Exception:
            return False

    def _structured(self, event: AstrMessageEvent, cfg: dict) -> bool:
        self_id = str(event.get_self_id())
        try:
            for comp in event.get_messages():
                if At is not None and isinstance(comp, At):
                    if str(getattr(comp, "qq", "")) == self_id:
                        return True
                if Reply is not None and isinstance(comp, Reply):
                    if str(getattr(comp, "sender_id", "")) == self_id:
                        return True
        except Exception:
            pass
        text = event.message_str or ""
        for name in cfg.get("structured_names") or []:
            if name and str(name) in text:
                return True
        return False

    def _structured_allowed(self, event: AstrMessageEvent, cfg: dict) -> bool:
        """同一个人 60 秒内最多触发 N 次，防止连 @ 刷屏。"""
        try:
            key = f"sc:{event.unified_msg_origin}:{event.get_sender_id()}"
            now = time.time()
            window = float(cfg.get("structured_cooldown_seconds", 60))
            hits = [t for t in (self._state.get(key) or []) if now - float(t) < window]
            if len(hits) >= int(cfg.get("structured_max_per_sender", 2)):
                logger.info(
                    f"addressee: 结构化信号被限流（{event.get_sender_id()} {window:.0f}s 内已有 {len(hits)} 次）"
                )
                return False
            hits.append(now)
            self._state[key] = hits
            self._save_state()
            return True
        except Exception:
            return True

    def _group_ok(self, event: AstrMessageEvent, cfg: dict) -> bool:
        gid = str(event.get_group_id() or "")
        if not gid:
            return False
        groups = cfg.get("groups") or ["*"]
        return "*" in groups or gid in [str(g) for g in groups]

    # ---------- 钩子 1：决定要不要跑模型 ----------
    # priority=-100：必须排在 mode 插件"重新拼装 system_prompt"之后，
    # 否则追加的 <不接> 提示会被整段覆盖掉（实测 552 字的 prompt 里没有这句）。
    @filter.on_llm_request(priority=-100)
    async def gate(self, event: AstrMessageEvent, req=None, *args, **kwargs):
        cfg = self._cfg()
        if not cfg.get("enabled", True):
            return
        if not self._group_ok(event, cfg):
            return  # 私聊 / 未纳管群：不干预
        umo = str(event.unified_msg_origin)

        if self._structured(event, cfg):
            if not self._structured_allowed(event, cfg):
                event.stop_event()
                return
            self._pending[umo] = "structured"
            logger.info(
                f"addressee: [结构化必回] {event.get_sender_id()} → 放行（{self._active_state(umo)} 状态）"
            )
            self._set(umo, "talk", float(cfg.get("talk_seconds", 60)), 0)
            return

        state = self._active_state(umo)
        if state == "mute":
            left = max(0.0, self._get(umo)["until"] - time.time())
            logger.info(f"addressee: [不接中] 静默丢弃 {event.get_sender_id()}（剩 {left:.0f}s）")
            event.stop_event()
            return

        if state == "talk":
            self._pending[umo] = "talk"
            self._append_hint(req, event)
            logger.info(f"addressee: [接话状态] 交给模型判断 {event.get_sender_id()}")
            return

        # idle
        p = float(cfg.get("idle_probability", 0.1))
        if self._is_night(cfg):
            p = 0.0
        dice = random.random()
        if dice < p:
            self._pending[umo] = "idle"
            self._append_hint(req, event)
            logger.info(f"addressee: [闲置骰子] 命中 {dice:.3f} < {p} → 让模型判断")
        else:
            logger.info(f"addressee: [闲置骰子] 未中 {dice:.3f} ≥ {p} → 不跑模型")
            event.stop_event()

    def _append_hint(self, req, event=None) -> None:
        try:
            if req is None:
                return
            cur = getattr(req, "system_prompt", "") or ""
            if "【群里说话前先想一句】" not in cur:
                req.system_prompt = cur + PROMPT_HINT
                logger.info(
                    f"addressee: 已注入 <不接> 提示（prompt {len(cur)} → {len(req.system_prompt)} 字）"
                )
                self._sync_last_prompt(event, req.system_prompt)
        except Exception:
            pass

    def _sync_last_prompt(self, event, prompt: str) -> None:
        """/config prompt 要显示真实下发的内容，所以同步一份。"""
        if event is None:
            return
        try:
            p = _data("last_prompt.json")
            data = {}
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
            if not isinstance(data, dict):
                data = {}
            key = str(event.unified_msg_origin)
            rec = data.get(key) or {}
            rec["prompt"] = prompt
            rec["hint"] = True
            rec["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
            if not rec.get("mode"):
                try:
                    with open(
                        os.path.join(
                            get_astrbot_data_path() if get_astrbot_data_path else ".",
                            "mode_state.json",
                        ),
                        "r",
                        encoding="utf-8",
                    ) as f:
                        rec["mode"] = (json.load(f) or {}).get(key, "默认")
                except Exception:
                    rec["mode"] = "默认"
            data[key] = rec
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, p)
        except Exception:
            pass

    # ---------- 钩子 2：读模型的选择 ----------
    @filter.on_decorating_result()
    async def settle(self, event: AstrMessageEvent):
        cfg = self._cfg()
        if not cfg.get("enabled", True):
            return
        umo = str(event.unified_msg_origin)
        reason = self._pending.pop(umo, None)
        if reason is None:
            return
        try:
            result = event.get_result()
            if result is None:
                return
            text = result.get_plain_text() or ""
        except Exception:
            return
        if not text.strip():
            return

        if "<不接>" in text:
            # 模型选择沉默：整条不发
            if Plain is not None:
                result.chain = []
            self._set(umo, "mute", float(cfg.get("mute_seconds", 60)))
            logger.info(f"addressee: [判定<不接>] 进入不接状态 {cfg.get('mute_seconds')}s")
            return

        # 正常接话
        if reason == "structured":
            self._set(umo, "talk", float(cfg.get("talk_seconds", 60)), 0)
            logger.info("addressee: [结构化必回] 已回复，进入接话状态")
            return

        rec = self._get(umo)
        refreshes = rec["refreshes"] + 1
        if refreshes > int(cfg.get("max_refresh", 3)):
            self._set(umo, "mute", float(cfg.get("cooldown_seconds", 60)))
            logger.info("addressee: [接力到顶] 强制冷却")
            return
        self._set(umo, "talk", float(cfg.get("talk_seconds", 60)), refreshes)
        logger.info(
            f"addressee: [接话] 刷新窗口 {cfg.get('talk_seconds')}s（第 {refreshes} 次接力）"
        )
