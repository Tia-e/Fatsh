# 大肥鱼 QQ 机器人 · 纯插件包

给 AstrBot（4.29.x，已在 **4.29.3** 上验证）用的一套**纯插件**定制：拟人聊天、三层记忆、群聊接话、指令闸门。
**不需要修改 AstrBot 内核**——升级直接覆盖 `astrbot/` 也不会丢功能。

## 目录

```
plugins/                     5 个插件（每个一个目录）
  astrbot_plugin_access/     指令过滤 / 工具闸门 / 会话身份
  astrbot_plugin_addressee/  群聊接话状态机
  astrbot_plugin_chat_style/ 清洗 → 分段 → 延迟（/style）
  astrbot_plugin_mem/        页/本/棚 记忆（/mem，仅私聊）
  astrbot_plugin_mode/       人格 + 提示词总装 + 设置（/config）
data/
  access_config.json         明文白名单 {"owner_ids": ["3447899870"]}
  memory_config.json         记忆参数
  addressee_config.json      群接话参数
  prompt/chat_rules.md       公用说话规则
  prompt/memo_rules.md       记忆整理提示词
scripts/                     测试与调试脚本
AGENTS.md                    架构与开发约定（先看这个）
```

## 安装

1. 把 `plugins/*` 拷到 `AstrBot/data/plugins/`
2. 把 `data/*.json` 拷到 `AstrBot/data/`，`data/prompt/*.md` 拷到 `AstrBot/data/prompt/`
3. 改 `data/access_config.json` 里的 `owner_ids`（明文 QQ，多个用逗号分开）
4. 重启 AstrBot

## 插件

| 插件 | 作用 |
|---|---|
| `access` | `/` 指令只有主人放行，其他人完全静默；非主人没有 agent 工具；给每次请求加会话/身份提示 |
| `addressee` | 群聊三态接话（闲置掷骰、模型可输出 `<不接>`、夜间静默） |
| `chat_style` | 文本清洗 + 分段（聊天/长文/结构化三档）+ 拟人延迟 |
| `mem` | 页/本/棚 三层记忆，每 20 轮整理一次，只在私聊 |
| `mode` | 5 套人格 + 提示词分层拼装 + `/config` 设置 |
| `help` | 单独发一个 `/` 时列出所有插件的命令（各插件写 `data/cmd_list/<名字>.txt`） |

## 指令

```
/config                 设置总览
/config mode [名字]     人格列表 / 切换
/config ctx 2-32        上下文预算
/config think on|off    思考开关
/config prompt          看这条会话实际提示词

/mem                    记忆状态
/mem show | clear | gen
/mem page 0-100         多少轮触发一次整理

/style                  发送节奏
/style delay 0-10       0=秒发，5=默认
/style split auto|chat|long
/style clean on|off
```

## 说明

- 记忆只在私聊累积；群里不记录、不注入（隐私）
- 记忆存储：`data/mem_archive/<QQ>/<QQ>.json`（待整理）+ `memory.json`（页/本/棚）
- 公用说话规则改 `data/prompt/chat_rules.md`，记忆整理提示词改 `data/prompt/memo_rules.md`，改完重启生效
- 架构、耦合规则、已知坑见 [AGENTS.md](AGENTS.md)
