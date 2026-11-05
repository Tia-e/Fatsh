# help · 只发一个 `/` 就列出所有命令

没有自己的命令。主人单独发 `/`（或 `／`）时，扫描 `data/cmd_list/*.txt` 拼成一张表回复。

## 怎么来的

各插件启动时写自己的清单（一行一条）：

```
data/cmd_list/
├── config.txt    ← mode 插件写
├── mem.txt       ← mem 插件写
└── style.txt     ← chat_style 插件写
```

新增插件要出现在 `/` 列表里，只需在自己的 `__init__` 写一个 `cmd_list/<名字>.txt`，不需要改 help 插件。

## 权限

非主人的 `/` 已经被 access 拦掉（`stop_event`），跑不到这里；这里还会再比对一次
`data/access_config.json` 的 `owner_ids` 兜底。

## 钩子

`event_message_type(EventMessageType.ALL, priority=9998)` —— 排在 access（9999）之后、命令 handler 之前。
命中后 `stop_event()`，不会漏进模型上下文。
