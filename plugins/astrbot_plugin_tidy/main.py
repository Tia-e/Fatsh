import difflib
import json
import os
import random
import re

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

try:
    from astrbot.core.message.components import Plain
except Exception:  # pragma: no cover
    Plain = None

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
except Exception:  # pragma: no cover
    get_astrbot_data_path = None

# 模型把"工具调用"当文本吐出来的各种痕迹
TOOL_LEAK_PATTERNS = [
    r"<\|+\s*/?\s*DSML",
    r"<｜",
    r"invoke\s+name\s*=",
    r"tool[▁_ ]?calls",
    r"<tool_call",
    r"</?function_call",
    r'"name"\s*:\s*"[a-z0-9_]*(execute|shell|read|write|grep|search|tool)',
    r'"arguments"\s*:',
    r"astrbot_[a-z_]+",
    r'"command"\s*:\s*"',
]

DEFLECT_LINES = [
    "啊这 我弄不了这个",
    "这个我真做不到",
    "想啥呢 我哪有这本事",
    "emm 这个我帮不上",
    "我只会聊天 干不了这个",
]

# 独白/草稿行特征
MONOLOGUE_HEAD = ("（", "(", "[", "【")
MONOLOGUE_WORDS = ("草稿", "我心里", "我想想", "我心里想", "其实我", "内心", "旁白", "OS：", "os：")

# 上一轮机器人发言，用于跨轮去重：umo -> list[str]
_LAST_SENT: dict[str, list[str]] = {}


def _data(*parts: str) -> str:
    base = get_astrbot_data_path() if get_astrbot_data_path else os.getcwd()
    return os.path.join(base, *parts)


@register(
    "astrbot_plugin_tidy",
    "hermes",
    "改成人话：去句号、逗号变空格、丢草稿/独白、跨轮去重",
    "2.0.0",
)
class TidyReply(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config or {}

    # ---------- 配置 ----------
    def _leak_action(self) -> str:
        try:
            p = _data("tidy_config.json")
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8-sig") as f:
                    d = json.load(f) or {}
                a = str(d.get("leak_action", "deflect")).lower()
                if a in ("deflect", "silent"):
                    return a
        except Exception:
            pass
        return "deflect"

    # ---------- 判定 ----------
    def _is_tool_leak(self, text: str) -> bool:
        return any(re.search(pat, text, re.I) for pat in TOOL_LEAK_PATTERNS)

    def _is_monologue(self, line: str) -> bool:
        s = line.strip()
        if not s:
            return False
        if s.startswith(MONOLOGUE_HEAD):
            return True
        return any(w in s for w in MONOLOGUE_WORDS)

    def _similar(self, a: str, b: str, ratio: float = 0.8) -> bool:
        a, b = a.strip(), b.strip()
        if not a or not b:
            return False
        if a == b:
            return True
        return difflib.SequenceMatcher(None, a, b).ratio() >= ratio

    # ---------- 主处理 ----------
    def _tidy(self, text: str, umo: str) -> str:
        # 1) 句号换行，逗号/分号/顿号变空格（不再把逗号也换成换行，避免碎片化）
        t = text.replace("。", "\n")
        for ch in ("，", ",", "；", ";", "、"):
            t = t.replace(ch, " ")

        # 2) 拆行 + 丢弃独白行
        lines = [ln.strip() for ln in t.split("\n")]
        lines = [ln for ln in lines if ln]
        kept = []
        for ln in lines:
            if self._is_monologue(ln):
                logger.info(f"tidy: 丢弃独白行 {ln[:24]!r}")
                continue
            kept.append(ln)

        # 3) 条内两版去重：相似度高的只留后面那版
        dedup = []
        for ln in kept:
            if dedup and self._similar(dedup[-1], ln, 0.7):
                logger.info(f"tidy: 条内重复（保留后版）{ln[:20]!r}")
                dedup[-1] = ln
                continue
            dedup.append(ln)

        # 4) 跨轮去重：和上一轮机器人发言比
        prev = _LAST_SENT.get(umo, [])
        final = []
        for ln in dedup:
            if any(self._similar(p, ln, 0.8) for p in prev):
                logger.info(f"tidy: 跨轮重复，丢弃 {ln[:20]!r}")
                continue
            final.append(ln)

        # 5) 合并过短的行（<8 字）到相邻行，减少碎片
        merged = []
        for ln in final:
            if merged and len(ln) < 8:
                merged[-1] = merged[-1] + " " + ln
            else:
                merged.append(ln)
        if len(merged) >= 2 and len(merged[0]) < 8:
            merged[1] = merged[0] + " " + merged[1]
            merged.pop(0)

        out = "\n".join(merged)
        out = re.sub(r"[ \t]{2,}", " ", out).strip()
        return out

    @filter.on_decorating_result()
    async def tidy_result(self, event: AstrMessageEvent):
        try:
            result = event.get_result()
            if result is None or not result.is_llm_result():
                return
            raw = result.get_plain_text() or ""
            if not raw.strip():
                return

            # 工具调用残留：静默或装傻
            if self._is_tool_leak(raw):
                action = self._leak_action()
                logger.warning(f"tidy: 检测到工具调用残留（{action}）: {raw[:100]!r}")
                if Plain is not None:
                    result.chain = [] if action == "silent" else [
                        Plain(random.choice(DEFLECT_LINES))
                    ]
                return

            # 语音标记不要动
            if raw.lstrip().startswith(("/语音", "／语音")):
                return

            if Plain is None:
                return
            umo = str(event.unified_msg_origin)
            new_chain = []
            changed = False
            final_text_parts = []
            for comp in result.chain:
                if isinstance(comp, Plain) and comp.text:
                    fixed = self._tidy(comp.text, umo)
                    if fixed != comp.text:
                        changed = True
                    if fixed:
                        new_chain.append(Plain(fixed))
                        final_text_parts.append(fixed)
                    continue
                new_chain.append(comp)
            if changed:
                result.chain = new_chain
                logger.info(f"tidy: 整理 {len(raw)} 字 → {len(result.get_plain_text() or '')} 字")
            if final_text_parts:
                # 记录本轮最终发出的话，供下一轮跨轮去重
                sents = []
                for part in final_text_parts:
                    sents.extend([s.strip() for s in part.split("\n") if s.strip()])
                _LAST_SENT[umo] = sents[-6:]
        except Exception as e:
            logger.error(f"tidy: 整理失败: {e}")
