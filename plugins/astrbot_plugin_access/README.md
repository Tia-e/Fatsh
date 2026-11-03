# access · 指令过滤 / 工具闸门 / 会话身份

**没有命令，纯拦截**。所有权限判断只在这一处，其他插件不许再写。

## 配置 `data/access_config.json`

```json
{"owner_ids": ["3447899870"], "filter_commands": true, "scene_prompt": true}
```

明文 QQ 号，不哈希。

## 三件事

1. **指令过滤**（`priority=9999`，最先跑）：`/` 或 `／` 开头的消息
   - 发送者在 `owner_ids` → 放行，各插件 handler 正常处理
   - 不在名单 → `stop_event()`：不进上下文、不落库、不回复，AI 完全看不见
2. **agent 闸门**：非主人 `req.func_tool` 换成空 ToolSet，并附「你现在没有任何工具可用」，防止模型硬编工具调用格式
3. **会话身份**：`on_llm_request` 追加 `<scene>`（私聊 / 群聊 群号）与 `<who>`（主人 / 陌生人），并写进 `event.set_extra("scene_block")` 供 mode 拼装

## 为什么这么设计

- 鉴权只有一份实现，mem / mode 的命令不再各自判断
- 纯拦截，不需要注册命令，也就不会和别家命令交叉
