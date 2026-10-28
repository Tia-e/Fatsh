import asyncio
import json
import os
import random
import re

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

DELAY_CFG = os.path.join("config", "astrbot_plugin_human_delay_config.json")


@register("astrbot_plugin_human_delay", "hermes", "发送回复前加随机延迟，像人在打字", "1.0.0")
class HumanDelay(Star):
    """在真正发出回复前等待一段随机时间，可选均匀/正态分布，并按字数追加"打字时间"。"""

    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config or {}
        try:
            logger.info(f"human_delay 生效配置: {dict(self.config)}")
        except Exception:
            pass

    # ---------- 配置 ----------
    def _cfg(self) -> dict:
        """每次读文件，/config delay 改完立刻生效，不用重启。"""
        try:
            p = os.path.join(get_astrbot_data_path(), DELAY_CFG)
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8-sig") as f:
                    d = json.load(f)
                if isinstance(d, dict):
                    return d
        except Exception:
            pass
        return self.config if isinstance(self.config, dict) else {}

    # ---------- 计算 ----------
    def compute_delay(self, text_len: int) -> float:
        cfg = self._cfg()
        lo = float(cfg.get("min_delay", 0))
        hi = float(cfg.get("max_delay", 10))
        if hi < lo:
            lo, hi = hi, lo
        mode = str(cfg.get("distribution", "normal")).lower()

        if mode == "uniform":
            base = random.uniform(lo, hi)
        else:
            mean = float(cfg.get("mean", (lo + hi) / 2))
            sigma = float(cfg.get("sigma", max((hi - lo) / 4.0, 0.5)))
            base = random.gauss(mean, sigma)
        base = min(max(base, lo), hi)

        per_char = float(cfg.get("per_char_seconds", 0.05))
        typing = max(0, text_len) * per_char

        cap = float(cfg.get("max_total", 15))
        return max(0.0, min(base + typing, cap))

    # ---------- 分段 ----------
    def _first_segment_len(self, text: str) -> int:
        """若开了 AstrBot 内置分段回复，用第一段的字数算打字时间。

        后续分段由 AstrBot 自己的 interval 控制（≈只算打字时间，不再加基础延迟）。
        """
        try:
            p = os.path.join(get_astrbot_data_path(), "cmd_config.json")
            with open(p, "r", encoding="utf-8-sig") as f:
                cfg = json.load(f)
            seg = ((cfg.get("platform_settings") or {}).get("segmented_reply")) or {}
            if not seg.get("enable"):
                return len(text)
            threshold = int(seg.get("words_count_threshold", 60) or 60)
            # AstrBot 的规则是：长度 <= 阈值 才拆分；超过阈值整条发
            if len(text) > threshold:
                return len(text)
            pattern = seg.get("regex") or r".*?[。？！~…]+|.+$"
            segs = [s.strip() for s in re.findall(pattern, text) if s and s.strip()]
            if len(segs) <= 1:
                return len(text)
            logger.info(f"human_delay: 将分成 {len(segs)} 条发送，首条 {len(segs[0])} 字")
            return len(segs[0])
        except Exception:
            return len(text)

    # ---------- 钩子 ----------
    @filter.on_decorating_result()
    async def delay_before_send(self, event: AstrMessageEvent):
        cfg = self._cfg()
        if not cfg.get("enabled", True):
            return

        # 指令类回复不延迟（/clear、/memstat 这些要秒回）
        text_msg = (event.message_str or "").strip()
        if text_msg.startswith("/"):
            return
        # 命中任何注册指令的回复也跳过（参数已被解析 = 是指令）
        if event.get_extra("handlers_parsed_params", {}) or event.get_extra("parsed_params", None):
            return

        text = ""
        try:
            res = event.get_result()
            if res is not None:
                text = res.get_plain_text() or ""
        except Exception:
            text = ""

        if not text.strip():
            return

        delay = self.compute_delay(self._first_segment_len(text))
        if delay <= 0.05:
            logger.info("human_delay: 延迟为 0，直接发送")
            return
        logger.info(f"human_delay: 等待 {delay:.2f}s 后发送（{len(text)} 字）")
        import time as _time
        _t0 = _time.time()
        await asyncio.sleep(delay)
        logger.info(f"human_delay: 已等待 {_time.time() - _t0:.2f}s，开始发送")
