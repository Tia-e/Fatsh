# cmd_help · 指令表 + 指令静默闸门（v2）

一个插件干两件事：给白名单看指令表，给所有人挡指令。

## 指令表

```
/cmd（别名 /指令、/指令表）  → 中文指令表
```

## 指令闸门（纯插件，替代内核 waking_check 补丁）

- 钩子：`@filter.event_message_type(ALL, priority=9999)`
- 依据：**原始消息链**里的 Plain 文本（唤醒阶段会把 `/` 剥掉，`message_str` 不可靠）
- 判定：以 `/` 开头 且 发送者不在 `data/cmd_perm.json` 白名单 → `event.stop_event()`
- 效果：命令 handler 一个都不会执行，**对方收不到任何回复**（连"没权限"都没有）

优先级依据：`star_handler` 按 `-priority` 排序（数字大先跑），`star_request` 每执行一个 handler 前检查 `is_stopped()`。

## 注意

- 白名单与 `priv_gate` 共用 `data/cmd_perm.json`
- 非白名单在群里发 `/xxx` 也会被静默丢弃
- 如果白名单文件缺失 → 所有人都会被拦（包括主人），部署时先确认文件在
