# human_delay · 拟人延迟 + 分段发送（v2）

**纯插件实现**，不依赖任何内核补丁。

## 两条延迟

| 环节 | 计算 |
|---|---|
| 首条 | `normal(mean=2.5, sigma=1.3)` clamp 到 0–5 秒 + `0.05×字数`，总上限 15 秒 |
| 后续每条分段 | `max(1.5, 0.05×字数)` ← **地板 1.5 秒** |

## 分段规则

1. 按换行切
2. 每行再按 `，。！？；~…` 切
3. **带空格的句子按 12 字软上限切**（tidy 会把逗号变空格，所以「啊 我长这样 蓝毛女仆 行吧 我认」会拆成两条）
4. 单段没空格又超 40 字 → 硬切
5. ≤2 字的碎片并入上一段（且上一段不超过 8 字）
6. 最多 6 段

示例：

```
啊 我长这样 蓝毛女仆 行吧 我认
→ 1. 啊 我长这样 蓝毛女仆   (11 字)
   2. 行吧 我认             (5 字)
```

发送方式：首条交给 respond 正常发送；其余由插件自己 `sleep` 后 `event.send()`。

## 依赖

**必须**把 AstrBot 内置分段关掉，否则会二次切分：

```json
"platform_settings": { "segmented_reply": { "enable": false } }
```

## 配置 `data/config/astrbot_plugin_human_delay_config.json`

| 字段 | 默认 | 说明 |
|---|---|---|
| `enabled` | true | 开关 |
| `distribution` | normal | `normal` / `uniform` |
| `mean` / `sigma` | 2.5 / 1.3 | 基础延迟分布 |
| `min_delay` / `max_delay` | 0 / 5 | 截断范围 |
| `per_char_seconds` | 0.05 | 每字打字时间 |
| `segment_min_interval` | **1.5** | 分段地板 |
| `max_total` | 15 | 单条上限 |

## 指令

`/config delay 0-10` 由 config 插件改这个文件，**立即生效**（插件每次读盘）。
