import io

path = r"D:\Work\Code\Hermes\Fatsh\plugins\astrbot_plugin_mode\main.py"
s = io.open(path, encoding="utf-8").read()

start = s.index("    def _arg(self, event: AstrMessageEvent) -> str:")

tail = '''    def _read_json(self, path: str, default):
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8-sig") as f:
                    return json.load(f)
        except Exception:
            pass
        return default

    def _arg(self, event: AstrMessageEvent) -> str:
        text = (event.message_str or "").strip()
        for token in ("/config", "config"):
            if text.lower().startswith(token):
                return text[len(token):].strip()
        return text.strip()

    # ---------- 设置项 ----------
    CTX_MIN, CTX_MAX, CTX_DEFAULT = 2, 32, 16

    def _cfg_obj(self, event: AstrMessageEvent):
        try:
            return self.context.get_config(umo=event.unified_msg_origin)
        except Exception:
            return None

    def _think_state(self, event: AstrMessageEvent) -> tuple[bool, list[str]]:
        cfg = self._cfg_obj(event) or {}
        on, ids = False, []
        for p in cfg.get("provider", []):
            if p.get("type") != "openai_chat_completion":
                continue
            ids.append(str(p.get("id")))
            ce = p.get("custom_extra_body") or {}
            if str(ce.get("reasoning_effort", "")).lower() not in ("none", "off", "disabled"):
                on = True
        return on, ids

    def _apply_think(self, event: AstrMessageEvent, on: bool) -> list[str]:
        cfg = self._cfg_obj(event)
        ids = []
        if cfg is None:
            return ids
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
        for inst in getattr(self.context.provider_manager, "provider_insts", []):
            pid = str((inst.provider_config or {}).get("id", ""))
            if pid in ids:
                ce = inst.provider_config.setdefault("custom_extra_body", {})
                if on:
                    ce.pop("reasoning_effort", None)
                else:
                    ce["reasoning_effort"] = "none"
        return ids

    def _ctx_level(self, event: AstrMessageEvent) -> int:
        cfg = self._cfg_obj(event) or {}
        try:
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
        cfg = self._cfg_obj(event)
        comp = cfg["agent_runner"]["config"].setdefault("compression", {})
        comp["fallback_max_tokens"] = int(level) * 1000
        comp["overflow_strategy"] = "truncate_by_turns"
        cfg.save_config()
        return comp

    def _prompt_record(self, event: AstrMessageEvent):
        rec = self._read_json(os.path.join(get_astrbot_data_path(), "last_prompt.json"), {})
        if isinstance(rec, dict):
            return rec.get(str(event.unified_msg_origin))
        return None

    def _help_text(self, current: str) -> str:
        desc = {
            "默认": "大肥鱼原味",
            "软萌": "软软的 撒娇但不油腻",
            "贴吧老哥": "绷 典 急了",
            "傲娇": "嘴硬心软 哼",
            "毒舌": "精准吐槽 不脏人",
        }
        lines = [
            "🎭 人格模式",
            "────────────────────────",
            f"当前   {current}",
            "",
        ]
        for m in MODE_ORDER:
            mark = "  ←" if m == current else ""
            lines.append(f"{_pad(m, 8)} {desc.get(m, '')}{mark}")
        lines += [
            "────────────────────────",
            "/config mode 软萌   切到这个人格",
            "/config mode        看这张表",
        ]
        return "\\n".join(lines)

    def _status_text(self, event: AstrMessageEvent) -> str:
        current = self._get_mode(event)
        on, _ = self._think_state(event)
        try:
            low = os.path.join(get_astrbot_data_path(), "config",
                               "astrbot_plugin_human_delay_config.json")
            delay = int(round(float(self._read_json(low, {}).get("max_delay", 5) or 5)))
        except Exception:
            delay = 5
        return (
            "⚙️ 设置\\n"
            "────────────────────────\\n"
            f"{_pad('人格', 6)}{current}\\n"
            f"{_pad('思考', 6)}{'开' if on else '关'}\\n"
            f"{_pad('上下文', 6)}{self._ctx_level(event)}k tokens\\n"
            f"{_pad('节奏', 6)}{delay} 档（/style delay 调）\\n"
            "────────────────────────\\n"
            "/config mode [名字]   看 / 切人格\\n"
            "/config ctx 2-32      上下文预算\\n"
            "/config think on|off  思考开关\\n"
            "/config prompt        看这条会话实际提示词"
        )

    # ---------- 指令 ----------
    @filter.command("config")
    async def config_cmd(self, event: AstrMessageEvent):
        """设置：人格 / 上下文 / 思考 / 提示词"""
        raw = self._arg(event)
        if not raw:
            yield event.plain_result(self._status_text(event))
            return

        parts = raw.split(maxsplit=1)
        sub = parts[0].lower()
        rest = parts[1].strip() if len(parts) > 1 else ""

        if sub == "mode":
            if not rest:
                yield event.plain_result(self._help_text(self._get_mode(event)))
                return
            target = rest.strip()
            if target not in MODES:
                yield event.plain_result(f"没有「{target}」这个模式\\n\\n" + self._help_text(self._get_mode(event)))
                return
            self._set_mode(event, target)
            yield event.plain_result("已切回默认人格" if target == "默认" else f"已切换为「{target}」模式")
            return

        if sub == "ctx":
            if not rest:
                yield event.plain_result(
                    f"📦 当前上下文预算：{self._ctx_level(event)}k tokens\\n用法：/config ctx <2-32>"
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
                f"✅ 上下文预算已设为 {level}k tokens（上限 {comp.get('fallback_max_tokens')}）\\n"
                "超出后丢最早轮次；重启 AstrBot 生效"
            )
            return

        if sub == "think":
            if not rest:
                on, _ = self._think_state(event)
                yield event.plain_result(f"🧠 当前思考：{'开' if on else '关'}\\n用法：/config think on|off")
                return
            val = rest.strip().lower()
            if val in ("on", "开", "1", "true", "yes"):
                ids = self._apply_think(event, True)
                yield event.plain_result(f"✅ 已开启思考（{', '.join(ids)}）\\n会更慢更贵，输出 tokens 约涨 3-4 倍")
            elif val in ("off", "关", "0", "false", "no"):
                ids = self._apply_think(event, False)
                yield event.plain_result(f"✅ 已关闭思考（{', '.join(ids)}），立即生效")
            else:
                yield event.plain_result("参数只能是 on 或 off")
            return

        if sub == "prompt":
            item = self._prompt_record(event)
            head = "🧩 这条会话实际发给模型的完整提示词"
            if not isinstance(item, dict) or not item.get("prompt"):
                yield event.plain_result(head + "\\n\\n还没有记录。先聊一句让她回一次，再发 /config prompt。")
                return
            prompt = str(item.get("prompt"))
            yield event.plain_result(
                f"{head}\\n模式：{item.get('mode', '?')}｜生成于 {item.get('ts', '?')}｜共 {len(prompt)} 字\\n"
                "────────────\\n" + prompt
            )
            return

        yield event.plain_result("可用：/config mode [名字]、ctx 2-32、think on|off、prompt")

'''

s = s[:start] + tail
io.open(path, "w", encoding="utf-8").write(s)
print("mode -> /config ok, len:", len(s))
