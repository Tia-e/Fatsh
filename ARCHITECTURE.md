"""架构说明（AGENTS.md 被系统保护，写不了，放这里）"""

# 大肥鱼 QQ 机器人 · 架构

AstrBot 4.29.x + SnowLuma。只桥接小号，只回复主人私聊和指定群。全部逻辑做成插件，不改内核。

## 五个插件，职责唯一

| 插件 | 管什么 | 钩子 | 命令 | 配置 |
|---|---|---|---|---|
| `astrbot_plugin_access` | 指令过滤 + agent 闸门 + 会话身份提示 | `event_message_type(ALL, priority=9999)`、`on_llm_request` | 无 | `access_config.json` |
| `astrbot_plugin_addressee` | 群聊接不接话（三态状态机） | `on_llm_request` | 无 | `addressee_config.json` |
| `astrbot_plugin_chat_style` | 清洗 → 分段 → 延迟（一条发送链） | `on_decorating_result`（唯一） | `/style` | `chat_style_config.json` |
| `astrbot_plugin_mem` | 页/本/棚 记忆（只在私聊） | `on_llm_response`、`on_llm_request` | `/mem` | `memory_config.json` |
| `astrbot_plugin_mode` | 人格 + 提示词总装 + 设置 | `on_llm_request` | `/config` | `mode_state.json` |

## 三条硬规矩

1. **system_prompt 只有一个 owner**：`mode` 整段拼装（公用规则 → 场景 → 人格 → 附加）。其他插件通过 `event.set_extra()` 交付内容。
2. **鉴权只有一个 owner**：`access`。名单是明文 QQ（`access_config.json`）；不在名单的 `/` 指令 `stop_event()`，不进上下文、不落库、不回复。其他插件不许再判断权限。
3. **没有插件互相 import**：只通过配置文件 / `event.set_extra()` 通信。

## 命令归属

```
/config                   设置总览
/config mode [名字]       人格列表 / 切换
/config ctx 2-32          上下文预算
/config think on|off      思考开关
/config prompt            看这条会话实际提示词

/mem                      记忆状态
/mem show | clear | gen
/mem page 0-100           多少轮触发一次整理

/style                    发送节奏
/style delay 0-10         0=秒发，5=默认
/style split auto|chat|long
/style clean on|off
```

`/cmd` 已删除；`/mode` 并入 `/config mode`。

## 行为要点

- **access**：追加 `<scene>` / `<who>`；非主人清空 `func_tool` + 「没有工具」提示。
- **chat_style**：清洗（去句号/独白/重复）→ 分段（≤10 字整条；≤120 字聊天模式 12 字软切最多 6 段；>120 字长文模式按段落切单段 ≤150 最多 4 段；代码块/列表/表格整条）→ 延迟（首条正态 + 每字 0.05，分段地板 1.5 秒）。
- **mem**：只在私聊；20 轮整理页并清空待整理；页 5 次 → 本吸；本 5 次 → 棚吸。存储 `data/mem_archive/<QQ>/`。
- **mode**：5 套人格；场景优先取 access 的 extra，兜底自算。

## 数据目录（`core/data/`）

```
access_config.json / mode_state.json / chat_style_config.json / memory_config.json / last_prompt.json
mem_archive/<QQ>/{<QQ>.json 待整理, memory.json 页本棚}
prompt/chat_rules.md / prompt/memo_rules.md
_disabled_plugins/  旧插件备份
```

## 部署 / 重启

```bash
cp plugins/* core/data/plugins/
cp data/*.json core/data/
cp data/prompt/*.md core/data/prompt/
taskkill /F /T /PID <父进程>          # 父子双进程，必须杀树
powershell -File D:\Apps\QQBot\autostart.ps1
```

## 约定与坑

- 插件目录名 = 模块名，`@register` 保持一致。
- `.ps1` 必须 UTF-8 with BOM（PowerShell 5.1）。
- `MessageChain` 从 `astrbot.api.event` 导入。
- 内核 `conversations` 只是 40 轮上下文窗口，不是聊天记录库；`platform_message_history` 只有群、80 条。
- 记忆整理失败不清空待整理，可 `/mem gen` 重试。
