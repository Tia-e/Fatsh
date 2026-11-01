import io

path = r"D:\Work\Code\Hermes\Fatsh\plugins\astrbot_plugin_mem\main.py"
s = io.open(path, encoding="utf-8").read()

# 1) 插入 _merge_old_segments（放在 seg 辅助函数之后、_turns_to_text 之前）
anchor = "# ---------------- 待整理对话：每人一个 <QQ>.json ----------------"
merge_fn = '''def _merge_old_segments(key: str) -> None:
    """旧分片里没整理过的轮次并入 <QQ>.json，然后删掉全部 seg-*.json。"""
    seqs = _seg_list(key)
    if not seqs:
        return
    pending: list[dict] = []
    for sq in seqs:
        node = _seg_read(key, sq) or {}
        if node.get("closed_at") is None:
            pending.extend(node.get("turns") or [])
    if pending:
        node = _turns_read(key) or _turns_blank(key)
        exist = node.get("turns") or []
        seen = {(t.get("ts"), t.get("user", ""), t.get("reply", "")) for t in exist}
        for t in pending:
            k2 = (t.get("ts"), t.get("user", ""), t.get("reply", ""))
            if k2 in seen:
                continue
            seen.add(k2)
            exist.append(t)
        node["turns"] = exist
        _turns_write(key, node)
    for sq in seqs:
        try:
            os.remove(_seg_path(key, sq))
        except Exception:
            pass
    logger.info(f"mem4: 旧分片 {len(seqs)} 个已并入 {_qq_of_key(key)}.json（带过来待整理 {len(pending)} 轮）")


'''
s = s.replace(anchor, merge_fn + anchor, 1)

# 2) 替换 _append_turn / _after_turn / _close_and_consolidate / _consolidate_page
start = s.index("    async def _append_turn(")
end = s.index("    # ---------------- 本 / 棚 ----------------")

new_block = '''    async def _append_turn(self, key: str, user: str, reply: str, src: str = "private") -> None:
        async with self._lock(key):
            node = _turns_read(key) or _turns_blank(key)
            node.setdefault("turns", [])
            node["turns"].append({"ts": time.time(), "user": user, "reply": reply, "src": src})
            node["updated_at"] = time.time()
            _turns_write(key, node)
            rec = self.store.umo(key)
            rec["hour"]["turns_in_file"] = len(node["turns"])
            rec["hour"]["last_turn_at"] = time.time()
            await self.store.save(key)

    async def _after_turn(self, key: str) -> None:
        """每轮结束看一下：待整理的轮数够不够。"""
        try:
            cfg = self._cfg()
            n = int(cfg["page_turns"])
            if n <= 0 or key in self._busy:
                return
            async with self._lock(key):
                rec = self.store.umo(key)
                turns = int(rec["hour"].get("turns_in_file") or 0)
            if turns < n:
                return
            self._busy.add(key)
            try:
                await self._consolidate_page(key)
            finally:
                self._busy.discard(key)
        except Exception as e:
            logger.error(f"mem4: 后台整理失败 {e}")
            self._busy.discard(key)

    def _quota(self, cfg: dict, tier: str) -> dict:
        q = (cfg.get("importance_quota") or {}).get(tier)
        if isinstance(q, dict):
            return {int(k): (None if v is None else int(v)) for k, v in q.items()}
        return {}

    def _min_imp(self, cfg: dict, tier: str) -> int:
        return int((cfg.get("min_importance") or {}).get(tier, 1))

    # ---------------- 页整理 ----------------
    async def _consolidate_page(self, key: str) -> bool:
        cfg = self._cfg()
        turns = _turns_list(key)
        if not turns:
            return False
        raw = _turns_to_text(turns)
        rec = self.store.umo(key)
        page = _ensure_page(rec, "hour")
        prev = _frozen_page(rec, "hour")
        prev_txt = _render_items(prev.get("items", [])) if prev else ""
        prompt = (
            f"【这一页现在的内容】\\n{_render_items(page.get('items', []))}\\n\\n"
            + (f"【上一页（已冻结，只读参考，不要改动它）】\\n{prev_txt}\\n\\n" if prev_txt else "")
            + f"【素材：这 {len(turns)} 轮原话】\\n{raw}"
        )
        text = await self._llm(key, "hour", prompt, _day_label(turns[-1].get("ts")))
        items = self._parse_items(text)
        if items is None:
            logger.error("mem4: 页整理返回无法解析，待整理对话先留着")
            return False
        page["items"] = self._norm(
            items, int(cfg["max_items"].get("hour", 25)), self._quota(cfg, "hour")
        )
        page["state"] = "mutable"
        page["sources"] = sorted({str(t.get("src") or "private") for t in turns})
        rec["hour"]["last_at"] = time.time()
        rec["hour"]["pages_done"] = int(rec["hour"].get("pages_done") or 0) + 1
        rec["hour"]["turns_in_file"] = 0
        _turns_clear(key)          # 触发整理 → 待整理对话清空
        await self.store.save(key)
        logger.info(f"mem4: 页整理 {len(turns)} 轮 → {len(page['items'])} 条；待整理已清空")
        await self._maybe_book(key)
        return True

'''
s = s[:start] + new_block + s[end:]
io.open(path, "w", encoding="utf-8").write(s)
print("replaced runtime methods; len:", len(s))
