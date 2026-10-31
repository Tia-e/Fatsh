# 大肥鱼 QQ 机器人 · 纯插件包

给 AstrBot（4.29.1）用的一套**纯插件**定制：拟人聊天、三层记忆、群聊接话、指令闸门。
**不需要修改 AstrBot 内核**——升级直接覆盖 `astrbot/` 也不会丢功能。

## 目录

```
plugins/            8 个插件，整个目录拷到 AstrBot 的 data/plugins/
data/
  prompt/           两套提示词（聊天规则 / 记忆整理规则）
  memory_config.json        记忆参数（页/本/棚 上限、门槛）
  addressee_config.json     群聊接话参数（把 groups 改成你的群号）
  config/                   拟人延迟参数（地板 1.5 秒）
  cmd_perm.example.json     白名单模板（用 scripts/make_perm.py 生成真的）
scripts/make_perm.py        生成 cmd_perm.json（salt+sha256，不存明文 QQ）
```

## 安装

1. 把 `plugins/` 下 8 个目录拷到 AstrBot 的 `data/plugins/`
2. 把 `data/prompt/` 拷到 `data/prompt/`
3. 把 `data/memory_config.json`、`data/addressee_config.json`、`data/config/astrbot_plugin_human_delay_config.json` 拷到对应位置
4. 生成白名单：
   ```
   python scripts/make_perm.py 你的QQ号 [salt]
   ```
   把生成的 `data/cmd_perm.json` 放到 AstrBot 的 `data/`
5. AstrBot 后台/配置里改这几项：
   - `admins_id` 填你的 QQ（AstrBot 自己的管理员，明文）
   - `platform_settings.segmented_reply.enable = false`（分发改由 human_delay 插件做）
   - `provider_tts_settings.enable = false`（本包不含语音）
   - `agent_runner.config.compression.fallback_max_tokens` 建议 16000
6. 重启 AstrBot

## 插件一览

| 插件 | 作用 |
|---|---|
| `mem` | 页/本/棚三层记忆：小时层是一页可改的纸，触发天整理冻结换新页；天层是一本，触发长期整理冻旧本换新本 |
| `mode` | 5 套人格 + 提示词分层：公用规则 → 场景 → 人格 → 记忆块 |
| `config` | `/config delay|think|ctx|prompt|provider` |
| `addressee` | 群聊三态接话（闲置 10% 掷骰、模型可输出 `<不接>`、夜间静默） |
| `tidy` | 断句整理：句号→换行、去草稿、拦工具残留 |
| `human_delay` | 首条随机延迟+打字时间；后续分段自己切、自己发（地板 1.5s） |
| `priv_gate` | 非白名单清空 agent 工具集 |
| `cmd_help` | `/cmd` 指令表 + **指令静默闸门**（非白名单发 `/` 指令直接丢弃） |

## 指令

```
/mem            记忆状态
/mem on|off     开关
/mem show       看会注入的内容
/mem clear      清空本会话
/mem gen 6      手动整理最近 6 小时（0=全部历史）
/mem limit 100  单次整理喂多少条（50-250）
/config ...     延迟/思考/上下文/提示词/模型
/mode           人格列表，/mode 软萌 切换
/cmd            指令表
```

## 说明

- **指令权限**：只有 `cmd_perm.json` 里的号能用 `/` 指令；其他人发 `/` 会被 `cmd_help` 的闸门直接 `stop_event()`，**一个字都不回**
- **记忆**：默认页 20 条 / 本 12 条 / 棚 8 条；距上次整理新增不足 6 条对话不触发；页/本各留 2 格
- **不需要**任何 `astrbot/` 目录下的补丁；本仓库不含任何密钥、数据库、聊天记录
