"""拟人延迟 + 分段发送（纯插件，不依赖任何内核补丁）

- 第一条：正态/均匀基础延迟 + 每字打字时间
- 后续分段：由本插件自己切分、自己 sleep、自己发送
- 分段地板 1.5 秒（不再是内核里的 1.2）
- 需要把 AstrBot 内置的 segmented_reply 关掉（enable=false），否则会重复切分
"""

import asyncio
import json
import os
import random
import re

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star, register
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

DELAY_CFG = os.path.join("config", "astrbot_plugin_human_delay_config.json")

MAX_SEG_CHARS = 40      # 单段超过这个长度再按句读二次切
SOFT_MAX_CHARS = 12     # 有空格的句子按这个长度软切（"啊 我长这样 蓝毛女仆" → 断成两三条）
MAX_SEGMENTS = 6        # 一条回复最多拆几段
MIN_SEG_CHARS = 3       # 太短的段并到上一段


@register(
    "astrbot_plugin_human_delay",
    "hermes",
    "拟人延迟 + 分段发送：首条随机延迟+打字时间，后续分段自己切、自己发（地板 1.5s）",
    "2.0.0",
)
class HumanDelay(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config or {}
        self._cfg_cache = None
        try:
            logger.info(f"human_delay 生效配置: {dict(self.config)}")
        except Exception:
            pass

    # ---------- 配置 ----------
    def _cfg(self) -> dict:
        try:
            p = os.path.join(get_astrbot_data_path(), DELAY_CFG)
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8-sig") as f:
                    d = json.load(f)
                if isinstance(d, dict):
                    self._cfg_cache = d
                    return d
        except Exception:
            pass
        return self._cfg_cache or (self.config if isinstance(self.config, dict) else {})

    def _per_char(self) -> float:
        return float(self._cfg().get("per_char_seconds", 0.05))

    def _floor(self) -> float:
        return float(self._cfg().get("segment_min_interval", 1.5))

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
        typing = max(0, text_len) * self._per_char()
        cap = float(cfg.get("max_total", 15))
        return max(0.0, min(base + typing, cap))

    def segment_delay(self, seg_len: int) -> float:
        """分段之间的等待：取 地板 与 打字时间 的较大者。"""
        return max(self._floor(), seg_len * self._per_char())

    # ---------- 切分 ----------
    def split_segments(self, text: str) -> list[str]:
        lines = [s.strip() for s in re.split(r"[\r\n]+", text) if s.strip()]
        parts: list[str] = []
        for line in lines:
            # 1) 有句读的先按句读切
            pieces = [p.strip() for p in re.split(r"(?<=[，。！？；~…])", line) if p.strip()]
            for piece in pieces or [line]:
                # 2) 有空格的长句：按空格做"软切"，每段不超过 SOFT_MAX_CHARS
                words = piece.split()
                if len(words) > 1:
                    buf = ""
                    for word in words:
                        cand = f"{buf} {word}".strip()
                        if buf and len(cand) > SOFT_MAX_CHARS:
                            parts.append(buf)
                            buf = word
                        else:
                            buf = cand
                    if buf:
                        parts.append(buf)
                elif len(piece) > MAX_SEG_CHARS:
                    # 3) 没空格又超长：硬切
                    parts.extend(
                        piece[i : i + MAX_SEG_CHARS] for i in range(0, len(piece), MAX_SEG_CHARS)
                    )
                else:
                    parts.append(piece)
        merged: list[str] = []
        for seg in parts:
            if merged and len(seg) <= 2 and len(merged[-1]) <= 8:
                merged[-1] = merged[-1] + seg
            else:
                merged.append(seg)
        return merged[:MAX_SEGMENTS]

    # ---------- 钩子 ----------
    @filter.on_decorating_result(priority=-100)
    async def delay_before_send(self, event: AstrMessageEvent):
        cfg = self._cfg()
        if not cfg.get("enabled", True):
            return

        # 指令类回复不延迟
        if (event.message_str or "").strip().startswith("/"):
            return
        if event.get_extra("handlers_parsed_params", {}) or event.get_extra("parsed_params", None):
            return

        res = event.get_result()
        if res is None:
            return
        try:
            chain = list(getattr(res, "chain", []) or [])
        except Exception:
            return
        if not chain or not all(isinstance(c, Plain) for c in chain):
            return  # 含图片/语音/At 的回复不接管
        text = "".join(c.text or "" for c in chain).strip()
        if not text:
            return

        segs = self.split_segments(text)
        if len(segs) <= 1:
            delay = self.compute_delay(len(text))
            if delay > 0.05:
                logger.info(f"human_delay: 整条等待 {delay:.2f}s（{len(text)} 字）")
                await asyncio.sleep(delay)
            return

        # 只留第一段交给 respond 发送，其余由本插件排期发送
        first = segs[0]
        res.chain = [Plain(text=first)]
        delay = self.compute_delay(len(first))
        logger.info(
            f"human_delay: 拆成 {len(segs)} 段，首条 {len(first)} 字等 {delay:.2f}s，"
            f"后续间隔 ≥{self._floor():.1f}s"
        )
        if delay > 0.05:
            await asyncio.sleep(delay)
        asyncio.create_task(self._send_rest(event, segs[1:]))

    async def _send_rest(self, event: AstrMessageEvent, segs: list[str]) -> None:
        try:
            for seg in segs:
                await asyncio.sleep(self.segment_delay(len(seg)))
                if not seg.strip():
                    continue
                await event.send(MessageChain([Plain(text=seg)]))
                logger.info(f"human_delay: 已发送分段 {len(seg)} 字")
        except Exception as e:
            logger.error(f"human_delay: 分段发送失败 {e}")
