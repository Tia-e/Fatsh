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
    r"<\|+\s*/?\s*DSML",          # DeepSeek DSML
    r"<｜",                        # 全角版本
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


@register(
    "astrbot_plugin_tidy",
    "hermes",
    "把 LLM 回复改成真人打字风格：去掉句号、把逗号换成换行",
    "1.0.0",
)
class TidyReply(Star):
    """提示词管不住的，用脚本兜底。"""

    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config or {}

    def _tidy(self, text: str) -> str:
        if not text:
            return text
        t = text
        # 句号一律去掉（真人一般不打句号）
        t = t.replace("。", "\n")
        # 逗号/分号改换行，形成自然断句
        t = t.replace("，", "\n").replace("；", "\n").replace(";", "\n")
        # 英文括号里的逗号不动（避免破坏代码/数字）
        t = t.replace(",\n", "，\n")
        # 清理空白行
        lines = [ln.strip() for ln in t.split("\n")]
        lines = [ln for ln in lines if ln]
        out = "\n".join(lines)
        # 连续空行压缩（这里已经过滤空行）
        out = re.sub(r"[ \t]{2,}", " ", out)
        return out.strip()

    def _leak_action(self) -> str:
        """leak_action: deflect（装傻，默认）| silent（完全静默）"""
        try:
            if not get_astrbot_data_path:
                return "deflect"
            p = os.path.join(get_astrbot_data_path(), "tidy_config.json")
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8-sig") as f:
                    d = json.load(f)
                a = str((d or {}).get("leak_action", "deflect")).lower()
                if a in ("deflect", "silent"):
                    return a
        except Exception:
            pass
        return "deflect"

    def _is_tool_leak(self, text: str) -> bool:
        return any(re.search(p, text, re.I) for p in TOOL_LEAK_PATTERNS)

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
                logger.warning(
                    f"tidy: 检测到工具调用残留（{action}）: {raw[:100]!r}"
                )
                if Plain is not None:
                    if action == "silent":
                        result.chain = []
                    else:
                        result.chain = [Plain(random.choice(DEFLECT_LINES))]
                return

            # 语音标记不要动
            if raw.lstrip().startswith(("/语音", "／语音")):
                return
            if Plain is None:
                return
            new_chain = []
            changed = False
            for comp in result.chain:
                if isinstance(comp, Plain) and comp.text:
                    fixed = self._tidy(comp.text)
                    if fixed != comp.text:
                        changed = True
                        if fixed:
                            new_chain.append(Plain(fixed))
                        continue
                new_chain.append(comp)
            if changed and new_chain:
                result.chain = new_chain
                logger.info(
                    f"tidy: 已整理（{len(raw)} 字 → {len(result.get_plain_text() or '')} 字）"
                )
        except Exception as e:
            logger.error(f"tidy: 整理失败: {e}")
