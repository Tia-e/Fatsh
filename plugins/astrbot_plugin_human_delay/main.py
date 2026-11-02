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

MAX_SEG_CHARS = 40      # 聊天模式：单段超过这个长度再按句读二次切
SOFT_MAX_CHARS = 12     # 聊天模式：有空格的句子按这个长度软切
MAX_SEGMENTS = 6        # 聊天模式：一条回复最多拆几段
MIN_SEG_CHARS = 3       # 太短的段并到上一段

# 长文模式（讲事情、解释、方案这类，不要按聊天节奏切碎）
LONG_CHARS = 120        # 去空白字数超过这个 → 长文
LONG_AVG_LINE = 30      # 平均每行字数超过这个 → 长文
LONG_MAX_SEG = 150      # 长文单段上限
LONG_MIN_SEG = 40       # 长文最小段，太小就并到前一段
LONG_MAX_SEGMENTS = 4   # 长文最多几段


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
        """首条：正态基础延迟（0-5 秒）+ 每字打字时间。"""
        cfg = self._cfg()
        lo = float(cfg.get("min_delay", 0))
        hi = float(cfg.get("max_delay", 5))
        if hi < lo:
            lo, hi = hi, lo
        mode = str(cfg.get("distribution", "normal")).lower()
        if mode == "uniform":
            base = random.uniform(lo, hi)
        else:
            mean = float(cfg.get("mean", 2.5))
            sigma = float(cfg.get("sigma", 1.3))
            base = random.gauss(mean, sigma)
        base = min(max(base, lo), hi)
        typing = max(0, text_len) * self._per_char()
        cap = float(cfg.get("max_total", 15))
        return max(0.0, min(base + typing, cap))

    def segment_delay(self, seg_len: int) -> float:
        """断句后的每条：1 秒 + 每字 0.05（地板 1.5）。"""
        cfg = self._cfg()
        base = float(cfg.get("base_delay", 1.0))
        floor = self._floor()
        cap = float(cfg.get("max_total", 15))
        return max(0.0, min(max(floor, base + seg_len * self._per_char()), cap))

    # ---------- 切分 ----------
    @staticmethod
    def _structured(text: str) -> bool:
        """代码块 / 列表 / 引用 / 表格 → 整条发，不切。"""
        if "```" in text:
            return True
        for line in text.splitlines():
            s = line.strip()
            if not s:
                continue
            if s.startswith(("- ", "* ", "> ", "|")) or re.match(r"^\d+[.、)]\s+", s):
                return True
        return "|" in text and text.count("|") >= 3

    def _mode_of(self, text: str, lines: list[str]) -> str:
        no_ws = re.sub(r"\s", "", text)
        if len(no_ws) <= 10:
            return "single"
        avg = len(no_ws) / max(1, len(lines))
        if len(no_ws) > LONG_CHARS or avg > LONG_AVG_LINE:
            return "long"
        return "chat"

    def _split_long(self, text: str) -> list[str]:
        """长文：按段落/句子切，单段 ≤150 字，最多 4 段，不按空格切。"""
        paras = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
        if len(paras) <= 1:
            paras = [p.strip() for p in re.split(r"[\r\n]+", text) if p.strip()]
        segs: list[str] = []
        for para in paras:
            if len(para) <= LONG_MAX_SEG:
                segs.append(para)
                continue
            pieces = [p.strip() for p in re.split(r"(?<=[。！？!?])", para) if p.strip()]
            buf = ""
            for piece in pieces:
                while len(piece) > LONG_MAX_SEG:
                    # 单句超长：优先在逗号处断开
                    cut = max(piece.rfind("，", 0, LONG_MAX_SEG), piece.rfind(",", 0, LONG_MAX_SEG))
                    cut = cut if cut > 20 else LONG_MAX_SEG
                    segs.append(piece[:cut].strip())
                    piece = piece[cut:].strip()
                cand = f"{buf}{piece}"
                if buf and len(cand) > LONG_MAX_SEG:
                    segs.append(buf)
                    buf = piece
                else:
                    buf = cand
            if buf:
                segs.append(buf)
        merged: list[str] = []
        for seg in segs:
            if merged and len(merged[-1]) < LONG_MIN_SEG and len(merged[-1]) + len(seg) <= LONG_MAX_SEG:
                merged[-1] = merged[-1] + "\n" + seg
            else:
                merged.append(seg)
        if len(merged) > LONG_MAX_SEGMENTS:
            head = merged[: LONG_MAX_SEGMENTS - 1]
            head.append("\n".join(merged[LONG_MAX_SEGMENTS - 1:]))
            return head
        return merged

    def split_segments(self, text: str) -> list[str]:
        text = (text or "").strip()
        if not text:
            return []
        if self._structured(text):
            return [text]
        lines = [s.strip() for s in re.split(r"[\r\n]+", text) if s.strip()]
        mode = self._mode_of(text, lines)
        if mode == "single":
            return [text.replace("\n", " ")]
        if mode == "long":
            return self._split_long(text)

        # 聊天模式：短句、碎、多口气
        parts: list[str] = []
        for line in lines:
            pieces = [p.strip() for p in re.split(r"(?<=[，。！？；~…])", line) if p.strip()]
            for piece in pieces or [line]:
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
        out = merged[:MAX_SEGMENTS]
        if len(merged) > MAX_SEGMENTS:
            out[-1] = " ".join(merged[MAX_SEGMENTS - 1:])
        return out

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
        no_ws = re.sub(r"\s", "", text)
        mode = "整条" if len(segs) == 1 else (
            "长文" if no_ws and len(no_ws) > LONG_CHARS else "聊天"
        )
        logger.info(
            f"human_delay: {mode}模式，{no_ws and len(no_ws) or 0} 字拆成 {len(segs)} 段，"
            f"首条 {len(first)} 字等 {delay:.2f}s，后续间隔 ≥{self._floor():.1f}s"
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
