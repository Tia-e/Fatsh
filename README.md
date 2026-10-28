# 大肥鱼 QQ 机器人部署包

AstrBot + SnowLuma 的「拟人水群」改造包：8 个插件 + 4 处内核补丁 + 配置模板。
基于 **AstrBot 4.29.1**（Windows）实测运行。

---

## 包里有什么

```
plugins/                     8 个插件
  astrbot_plugin_mode         /mode 5 套人格 + 三层提示词拼装（公用规则→场景→人格→记忆）
  astrbot_plugin_addressee    群聊三态接话（闲置/接话/不接），模型可输出 <不接> 沉默
  astrbot_plugin_config       /config delay|prompt|provider
  astrbot_plugin_mem          /mem 记忆开关与每 10 轮整理
  astrbot_plugin_human_delay  首条正态延迟+打字，分段按字数
  astrbot_plugin_tidy         去句号、逗号断句、拦截工具调用残留
  astrbot_plugin_priv_gate    非白名单清空 agent 工具集
  astrbot_plugin_cmd_help     /cmd 指令表

patched/                     4 个已改好的内核文件（与 plugins 配套，缺一不可）
  astrbot/core/pipeline/waking_check/stage.py      指令白名单（静默丢弃）
  astrbot/core/pipeline/result_decorate/stage.py   /语音 标记 → TTS 只发语音
  astrbot/core/pipeline/respond/stage.py           分段间隔按字数
  astrbot/core/provider/sources/edge_tts_source.py ffmpeg 路径解析

config_templates/            配置模板（安装器只在目标不存在时写入）
install.py                   安装器（幂等，自动备份）
```

---

## 安装

双击 **`安装.bat`**，会打开一个小窗口：

1. **AstrBot 的 core 目录** —— 点「浏览…」选到 `...\instances\<实例ID>\core`
   （判断标准：目录里有 `astrbot` 文件夹和 `data` 文件夹）
2. **管理员 QQ** —— 填允许使用 `/` 指令的那个 QQ
3. 点「开始安装 / 修复」

界面下方是实时日志，会逐条告诉你：装了哪些插件、写了哪些配置、哪几个内核文件被替换/跳过/拒绝。

装完重启 AstrBot 即可。

### 管理员只有一个（唯一）

**整个部署只有一个管理员**：`data/cmd_perm.json` 里永远只有**一个**哈希。

- 换人 = 覆盖，旧管理员立刻失效
- 配置文件里**不存明文 QQ**，只存 `salt` + `sha256(salt+QQ)`
- 别人发任何 `/` 指令 → 事件被直接丢弃，**一个字都不回**

换管理员有两种方式：

| 方式 | 操作 |
|---|---|
| 界面 | 双击 **`修改管理员.bat`**，填新 QQ，点「修改管理员」 |
| 命令行 | `python install.py --set-admin --root "<core 路径>" --admin-qq <新QQ> --silent` |

改完**不用重启**，下一条指令就按新管理员判定。

### 命令行安装（可选）

```bat
python install.py --root "D:\...\instance\core" --admin-qq 123456789 --silent
```

两个 `.bat` 会自动找 Python（PATH 里的 `python` / `py -3` / `%LOCALAPPDATA%\Programs\Python\Python31x\python.exe`），找不到会提示先装 Python 3.10+。

### 依赖

```bat
pip install edge-tts pyffmpeg
```
TTS 用 edge-tts（免费），转码需要 ffmpeg：装 `pyffmpeg` 后二进制在
`C:\Users\<你>\.pyffmpeg\bin\ffmpeg.exe`，补丁会自动找；也可以自己装 ffmpeg 到 PATH。

QQ 侧还需要 **SnowLuma**（或其它 OneBot v11 实现），AstrBot 侧配 aiocqhttp 反连。

---

## 安装后要改的地方

| 文件 | 改什么 |
|---|---|
| `data/addressee_config.json` | `groups` 填要纳管的群号；`structured_names` 填机器人昵称 |
| `data/cmd_perm.json` | 换管理员：双击 `修改管理员.bat`（唯一，覆盖旧的），不要手改哈希 |
| `data/config/astrbot_plugin_human_delay_config.json` | 延迟强度（`per_char_seconds`、`segment_min_interval`） |
| `data/mode_prompts.json` / 插件源码里的 `MODES` | 人格文案 |
| `data/tidy_config.json` | `leak_action`: `deflect`（装傻）或 `silent`（全静默） |

---

## 行为说明（几处容易被误解的）

**指令白名单**：只认 `cmd_perm.json` 里哈希匹配的 QQ。其他人发任何 `/` 指令 → 事件被 `stop_event` 直接丢弃，**一个字都不回**。改配置即时生效，不用重启。

**群聊三态**：

```
@机器人 / 引用机器人 / 喊名字  → 必回，进入接话 60s（同人 60s 最多 2 次）
接话中   → 每条交给模型判断，可输出 <不接>；接话刷新窗口，最多接力 3 次
不接中   → 60s 内完全不调模型（结构化信号除外）
闲置     → 每条消息 10% 概率掷中才让模型判断；夜间 23:00-08:00 概率为 0
```

**分段延迟**：第一条 = 正态基础延迟(均值2.5s) + 0.05×首段字数；后续每条 = max(1.2s, 0.05×字数)。AstrBot 的拆分条件是 `len ≤ 60`，超过 60 字整条发。

**语音**：模型输出 `/语音 想说的话` → 转成语音，**不附带文字**；TTS 失败降级为纯文本（去掉标记）。

**提示词层次**：公用规则在最上，其次场景（群/私聊/能否用 agent），再人格，最后记忆块；末尾还有一句重申（模型对最后出现的要求最敏感）。`/config prompt` 输出的是**实际下发**的完整提示词。

---

## 升级 AstrBot 之后

内核补丁会被官方文件覆盖，症状：

- 指令权限失效（谁都能 `/config`）
- `/语音` 变普通文字
- 分段延迟回到固定 0.5–1.4 秒

处理：**重跑一遍 `install.py`**（它会检测已打过就跳过，没打过就补上）。

## 回滚

每次替换内核文件都会留下 `xxx.py.bak-<时间戳>`，直接改回来即可；插件目录删掉对应文件夹即可卸载。

---

## 不含什么

包里**没有** API Key、没有账号密码、没有明文 QQ（管理员以哈希形式写入）。模型、平台、账号配置请在自己机器的 AstrBot WebUI 里填。
