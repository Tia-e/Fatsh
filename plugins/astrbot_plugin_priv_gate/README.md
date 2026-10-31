# priv_gate · 权限闸门（agent 能力）

非白名单用户只能纯聊天，不给任何 agent 工具（搜索、沙箱、定时任务、文件操作等）。

## 机制

`on_llm_request` 里把 `req.func_tool` 换成一个空的 `ToolSet`（比置 None 安全，下游会判真值）。
同时裁掉部分版本自动塞进 system_prompt 的 skills 说明段。

## 白名单

读 `data/cmd_perm.json`：

```json
{ "enabled": true, "salt": "随机串", "allowed_hashes": ["sha256(salt+QQ)"], "allowed_ids": [] }
```

用仓库里的 `scripts/make_perm.py` 生成，不存明文 QQ。

## 注意

- 文件缺失或 `enabled: false` → 插件**直接放行所有人**（不报错）。部署时务必确认这份文件存在
- 与 `cmd_help` 的指令闸门共用同一份白名单：一处改，两处同时生效
- 与内核无关，升级不失效
