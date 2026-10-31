# config · 统一配置入口

```
/config                  当前状态 + 用法
/config delay 0-10       发送延迟档位（0=秒发，5=默认，10=最慢）
/config think on|off     模型思考开关（默认关；记忆整理始终开）
/config ctx 2-32         上下文预算（k tokens，默认 16）
/config prompt           看本会话实际发给模型的完整提示词
/config provider         看当前模型与候选模型
```

## 说明

- `delay` 写 `data/config/astrbot_plugin_human_delay_config.json`，立刻生效（插件每次读文件）
- `think` 改 provider 的 `custom_extra_body.reasoning_effort`，并同步已加载的 provider 实例，**免重启**
- `ctx` 写 `agent_runner.config.compression.fallback_max_tokens` 与 `overflow_strategy=truncate_by_turns`
  → **需要重启 AstrBot 才生效**（内核只在启动时读取该配置）
- `prompt` 读 `data/last_prompt.json`，需要先让机器人回过一次

所有子命令仅白名单可用（`/config` 裸输入任何人可看）。
