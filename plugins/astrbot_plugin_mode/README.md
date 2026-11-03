# mode · 人格 + 提示词总装 + 设置

一个插件里两件事：**拼装完整 system_prompt** 和 **`/config` 设置**。

## 提示词拼装顺序（on_llm_request）

```
1 公用规则    data/prompt/chat_rules.md（改完重启生效）
2 场景       access 插件给的 <scene>/<who>（拿不到就自己算）
3 人格       5 套里的当前那套
4 其余附加    记忆块等（mem 注入的内容保留在尾部）
```

## 5 套人格

`默认`（大肥鱼原味）· `软萌` · `贴吧老哥` · `傲娇` · `毒舌`

## 指令

```
/config                 设置总览（人格/思考/上下文/节奏）
/config mode            人格列表（标出当前）
/config mode 软萌       切换
/config ctx 2-32        上下文预算（k tokens）
/config think on|off    思考开关
/config prompt          看这条会话实际发给模型的完整提示词
```

## 配置 / 状态文件

| 文件 | 内容 |
|---|---|
| `mode_state.json` | 每个会话用哪套人格 |
| `last_prompt.json` | 最近一次实际提示词（`/config prompt` 读它） |

## 注意

- 不改内核：人格是通过覆盖/重建 `system_prompt` 实现的
- 只有这一个插件写 `system_prompt`；别的插件要加内容请用 `event.set_extra()`
- 切换人格只影响当前会话（umo）
