# mode · 人格 + 提示词分层

## 5 套人格

`默认`（大肥鱼原味）· `软萌` · `贴吧老哥` · `傲娇` · `毒舌`

指令：

```
/mode            列表（标出当前）
/mode 软萌       切换（仅白名单可用）
```

## 提示词拼装顺序（on_llm_request）

```
1. 公用规则      data/prompt/chat_rules.md（改完重启生效）
2. 场景块        _scene_block()：私聊+主人 / 私聊+网友(打码) / 群聊(带群号)
3. 人格          5 套之一
4. 其余          原 system_prompt 剩下的内容
```

场景块里的"主人"判断用的是 `data/cmd_perm.json` 的 **salt + sha256**，不存明文 QQ。

> 注意：`mem` 的记忆块、`priv_gate` 的裁剪、`addressee` 的 `<不接>` 信号都在这之后追加。本插件是**整段重建** system_prompt，所以它的执行顺序必须排在它们之前（当前都未设 priority，靠注册顺序）。

## 相关文件

- `data/prompt/chat_rules.md` — 10 条公共聊天规则
- `data/mode_state.json` — 每个会话当前人格
- `data/mode_prompts.json` — 导出给 `/config prompt` 用
- `data/last_prompt.json` — 最近一次实际发送的完整提示词
