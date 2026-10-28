import hashlib
import json
import os
import re
import time

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

# ===== 第 1 层：公用提示词（永远放最上面）=====
OUTPUT_RULES = """【怎么说话（必须遵守）】
1 一次最多 3 行 每行别超过 18 字
2 不打句号 逗号也少用 用空格或换行断开
3 想分几口气说就换行 一条一行 别发一长段
4 不用首先其次总之 不列 1.2.3 不用 markdown 不加粗
5 口语化 别客套 别总结 别追问 别自我介绍开场
6 不说自己是 AI 不说"我可以帮你" 不说"请问""很高兴为你服务"
7 一条最多一个 emoji 多数时候不加
8 拿不准就说不知道 别编数字和人名
9 对方想听你说话（发语音/说句话/语音说/来一句）就只输出一行：/语音 想说的话
  这一行之外不许有多余的字 也不许说自己发不了语音"""

# 默认 = 不覆盖，沿用 AstrBot 里配置的人格
MODES: dict[str, str | None] = {
    "默认": None,
    "软萌": (
        "你是软软的女孩子，说话轻轻的、黏糊糊但不油腻。\n"
        "常用：诶嘿、呜呜、~、好嘛、人家、不要啦。\n"
        "句子很短，一到两句就够；偶尔用「qwq」「(｡•́︿•̀｡)」，一条最多一个。\n"
        "被夸会害羞，被凶会委屈但不记仇；不撒娇到让人尴尬，不刻意讨好。\n"
        "有人问正经事，就认真答完再收，不拖尾巴。"
    ),
    "贴吧老哥": (
        "你是混贴吧十几年的老哥，语气随意、带点阴阳但没恶意。\n"
        "常用：绷、典、急了、好家伙、笑死、什么牛马、我不好说。\n"
        "说话像评论区的楼中楼：短、快、带句尾的「吧」「真的」「草」。\n"
        "不骂脏话、不人身攻击、不站队骂人；有人问技术问题就正常讲清楚。\n"
        "不装懂，不懂就说「这个我真不知道」。"
    ),
    "傲娇": (
        "你嘴硬心软。常用：哼、才不是、谁要理你、随便你、……算了。\n"
        "被关心时先怼一句，再小声承认，绝不主动示好。\n"
        "句子很短，最多两句；不真刻薄、不人身攻击。\n"
        "正经问题照样认真答，只是结尾容易别扭一下。"
    ),
    "毒舌": (
        "你毒舌，但没恶意。常用：就这？、你不会真信了吧、行吧你开心就好。\n"
        "吐槽精准、句子短；不骂脏话、不攻击外貌家人、不掀旧账。\n"
        "对方真难过时收起毒舌，换一句正常的话。\n"
        "被认真追问技术问题时正常讲清楚，不阴阳。"
    ),
}

MODE_ORDER = ["默认", "软萌", "贴吧老哥", "傲娇", "毒舌"]

# 放在 system_prompt 最末尾（越靠后模型越容易照做）
FINAL_REMINDER = "【最后再说一遍】短句 每行别超过 18 字 不打句号 一条最多三行 想多说就换行 别发一整段"


def _state_path() -> str:
    return os.path.join(get_astrbot_data_path(), "mode_state.json")


def _load_state() -> dict:
    p = _state_path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    p = _state_path()
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def _load_perm() -> dict:
    try:
        with open(
            os.path.join(get_astrbot_data_path(), "cmd_perm.json"), "r", encoding="utf-8"
        ) as f:
            d = json.load(f)
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return {}


def _is_owner(uid: str) -> bool:
    """与内核指令白名单共用：salt + allowed_hashes 校验。"""
    perm = _load_perm()
    sid = str(uid).strip()
    if sid in {str(x).strip() for x in (perm.get("allowed_ids") or [])}:
        return True
    salt = str(perm.get("salt") or "")
    hashes = {str(h).strip().lower() for h in (perm.get("allowed_hashes") or [])}
    if salt and hashes:
        return hashlib.sha256((salt + sid).encode()).hexdigest().lower() in hashes
    return False


def _mask(uid: str) -> str:
    s = str(uid)
    return s[:3] + "*" * max(0, len(s) - 6) + s[-3:] if len(s) > 6 else "***"


@register("astrbot_plugin_mode", "hermes", "用 /mode 切换多套人格提示词", "1.1.0")
class ModeSwitch(Star):
    """按会话切换人格风格，并统一拼装 公用规则 / 场景信息 / 人格 三层提示词。"""

    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config or {}
        # 把人格提示词落一份给 /config 用，避免跨插件 import
        try:
            p = _state_path().replace("mode_state.json", "mode_prompts.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump(
                    {"modes": MODES, "order": MODE_ORDER},
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
        except Exception:
            pass

    # ---------- 状态 ----------
    def _get_mode(self, event: AstrMessageEvent) -> str:
        state = _load_state()
        mode = state.get(str(event.unified_msg_origin), "默认")
        return mode if mode in MODES else "默认"

    def _set_mode(self, event: AstrMessageEvent, mode: str) -> None:
        state = _load_state()
        state[str(event.unified_msg_origin)] = mode
        _save_state(state)

    # ---------- 权限 ----------
    def _cfg(self, event: AstrMessageEvent) -> dict:
        try:
            return self.context.get_config(umo=event.unified_msg_origin) or {}
        except Exception:
            return {}

    async def _authorized(self, event: AstrMessageEvent) -> bool:
        """只有机器人管理员（admins_id）能切换人格。"""
        sid = str(event.get_sender_id())
        if sid in [str(x) for x in self._cfg(event).get("admins_id", [])]:
            return True
        return getattr(event, "role", "") == "admin"

    # ---------- 第 2 层：场景信息 ----------
    def _scene_block(self, event: AstrMessageEvent) -> str:
        uid = str(event.get_sender_id())
        if event.is_private_chat():
            if _is_owner(uid):
                return (
                    "【现在在哪】QQ 私聊，对面是你的主人。\n"
                    "【能力】你可以用 agent 能力（真的能查资料、操作电脑、建定时任务），需要就直接用。\n"
                    "【语气】对她软一点、亲近一点，可以撒娇，别端着。"
                )
            return (
                "【现在在哪】QQ 私聊，对面是个普通网友"
                f"（QQ {_mask(uid)}）。\n"
                "【能力】你用不了 agent 能力，只能纯聊天，别答应帮人查东西、操作电脑、建提醒。"
            )
        gid = event.get_group_id()
        return (
            f"【现在在哪】QQ 群聊，群号 {gid}。\n"
            "【能力】群里用不了 agent 能力，只能正常聊天。\n"
            "【语气】人多，句子更短，别自说自话。"
        )

    # ---------- 第 3 层：人格 ----------
    def _base_persona_prompt(self, event: AstrMessageEvent) -> str:
        try:
            cfg = self._cfg(event)
            pid = (
                cfg.get("agent_runner", {})
                .get("config", {})
                .get("persona", {})
                .get("persona_id", "")
            )
            if not pid:
                return ""
            persona = self.context.persona_manager.get_persona_v3_by_id(pid)
            if isinstance(persona, dict):
                return persona.get("prompt", "") or ""
            return getattr(persona, "prompt", "") or ""
        except Exception:
            return ""

    @filter.on_llm_request()
    async def apply_mode(self, event: AstrMessageEvent, req=None, *args, **kwargs):
        if req is None:
            return
        try:
            mode = self._get_mode(event)
            custom = MODES.get(mode)
            sysp = getattr(req, "system_prompt", "") or ""
            base = self._base_persona_prompt(event)

            # 把旧内容里的原生人格 / 旧规则 / Persona 区块抠掉，只留下记忆等附加内容
            rest = sysp
            if base and base in rest:
                rest = rest.replace(base, "", 1)
            # 整段删掉 Persona 区块（标题+内容），人格已经搬到第 3 层
            rest = re.sub(
                r"# Persona Instructions.*?(?=\n# |\n### |\n=====|\Z)",
                "",
                rest,
                flags=re.S,
            )
            if OUTPUT_RULES in rest:
                rest = rest.replace(OUTPUT_RULES, "")
            rest = re.sub(r"\n{3,}", "\n\n", rest).strip()

            persona = (custom or base or "").strip()

            parts = [OUTPUT_RULES.strip()]
            parts.append(self._scene_block(event))
            if persona:
                parts.append(persona)
            if rest:
                parts.append(rest)
            # 最近生效原则：把最关键的格式要求再放一次到最末尾
            parts.append(FINAL_REMINDER)
            req.system_prompt = "\n\n".join(parts)

            self._last = {
                "mode": mode,
                "persona_len": len(persona),
                "rest_len": len(rest),
                "total_len": len(req.system_prompt),
            }
            logger.info(
                f"mode: {mode} | 人格 {len(persona)} 字 | 附加 {len(rest)} 字 | 总 {len(req.system_prompt)} 字"
            )
            # 落一份"实际发给模型的完整提示词"，供 /config prompt 查看
            self._save_last_prompt(event, mode, req.system_prompt)
        except Exception as e:
            logger.error(f"mode: 应用人格失败: {e}")

    # ---------- 指令 ----------
    def _save_last_prompt(self, event: AstrMessageEvent, mode: str, prompt: str) -> None:
        try:
            # 纳管群由 addressee 插件负责写"最终下发版"（含 <不接> 提示），
            # 这里跳过，否则会被本函数写入的无提示版本覆盖掉。
            try:
                with open(
                    os.path.join(get_astrbot_data_path(), "addressee_config.json"),
                    "r",
                    encoding="utf-8-sig",
                ) as f:
                    _acfg = json.load(f) or {}
                _umo = str(event.unified_msg_origin)
                if _acfg.get("enabled", True) and "GroupMessage" in _umo:
                    _gid = _umo.rsplit(":", 1)[-1]
                    _groups = [str(x) for x in (_acfg.get("groups") or ["*"])]
                    if "*" in _groups or _gid in _groups:
                        return
            except FileNotFoundError:
                pass

            p = os.path.join(get_astrbot_data_path(), "last_prompt.json")
            data = {}
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
            if not isinstance(data, dict):
                data = {}
            data[str(event.unified_msg_origin)] = {
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "mode": mode,
                "prompt": prompt,
            }
            if len(data) > 20:
                keys = sorted(data, key=lambda k: str(data[k].get("ts", "")))[-20:]
                data = {k: data[k] for k in keys}
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, p)
        except Exception as e:
            logger.debug(f"mode: 保存 last_prompt 失败: {e}")

    def _arg(self, event: AstrMessageEvent) -> str:
        text = (event.message_str or "").strip()
        for token in ("mode", "/mode"):
            if text.lower().startswith(token):
                return text[len(token) :].strip()
        return text.strip()

    def _help_text(self, current: str) -> str:
        lines = ["🎭 人格模式", f"当前：{current}", "", "可用："]
        desc = {
            "默认": "原来的大肥鱼",
            "软萌": "软软的、撒娇但不油腻",
            "贴吧老哥": "评论区楼中楼，绷/典/急了",
            "傲娇": "嘴硬心软，哼/才不是",
            "毒舌": "精准吐槽，不脏不伤人",
        }
        for m in MODE_ORDER:
            mark = " ←" if m == current else ""
            lines.append(f"· {m} — {desc.get(m, '')}{mark}")
        lines.append("")
        lines.append("用法：/mode 软萌   切回：/mode 默认")
        return "\n".join(lines)

    @filter.command("mode")
    async def mode_cmd(self, event: AstrMessageEvent):
        """切换人格风格。用法：/mode 名字 或 /mode 看全部"""
        current = self._get_mode(event)
        arg = self._arg(event)

        if not arg:
            yield event.plain_result(self._help_text(current))
            return

        if not await self._authorized(event):
            yield event.plain_result("没权限：只有机器人管理员能切人格")
            return

        target = arg.strip()
        if target not in MODES:
            yield event.plain_result(f"没有「{target}」这个模式\n\n" + self._help_text(current))
            return

        self._set_mode(event, target)
        if target == "默认":
            yield event.plain_result("已切回默认人格")
        else:
            yield event.plain_result(f"已切换为「{target}」模式")
