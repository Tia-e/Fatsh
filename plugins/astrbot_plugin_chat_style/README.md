# chat_style · 清洗 → 分段 → 延迟

一条发送链，**只挂一个 `on_decorating_result`**（原来的 tidy + human_delay 合并）。

## 流程

```
1 清洗   句号→换行；逗号/顿号→空格；丢独白行；条内去重；跨轮去重；≤3 字碎片并入上一行
2 拦截   模型把工具调用当文本吐出来 → 换一句"我弄不了"（或静默）
3 分段   见下表
4 延迟   首条 正态(mean,sigma) + 每字 0.05；分段地板 1.5 秒；单条上限 15 秒
```

## 分段三档

| 模式 | 判定 | 切法 |
|---|---|---|
| 整条 | 去空白 ≤10 字 | 不切 |
| 聊天 | ≤120 字且平均行长 ≤30 | 按换行/句读，再按空格 12 字软切，最多 6 段 |
| 长文 | >120 字或平均行长 >30 | 按段落/句号切，单段 ≤150，<40 并段，最多 4 段，**不按空格切** |
| 结构化 | 代码块 / `-` `1.` 列表 / `>` 引用 / 表格 | 整条发 |

## 指令

```
/style                 状态
/style delay 0-10      0=秒发，5=默认（1 秒+每字 0.05），10=2 秒+每字 0.1
/style split auto|chat|long   分段模式（auto = 按长度自动判）
/style clean on|off    清洗开关
```

## 配置 `data/chat_style_config.json`

| 字段 | 默认 | 说明 |
|---|---|---|
| `enabled` | true | 延迟总开关 |
| `mean` / `sigma` | 2.5 / 1.3 | 首条正态参数 |
| `max_delay` | 5 | 首条截断上限（也是档位值） |
| `base_delay` | 1.0 | 分段后的基础延迟 |
| `per_char_seconds` | 0.05 | 每字 |
| `segment_min_interval` | 1.5 | 分段地板 |
| `max_total` | 15 | 单条上限 |
| `split` | auto | auto / chat / long |
| `clean` | true | 文本清洗 |
| `leak_action` | deflect | 工具残留：deflect 装傻 / silent 静默 |

旧 `config/astrbot_plugin_human_delay_config.json` 会在首次启动时自动迁移过来。
