# mem · 页 / 本 / 棚 三层记忆

全部逻辑落在插件内，**不读 sqlite**——AstrBot 的 `conversations` 表只是"当前上下文窗口"（`max_turns=40`，每次写入砍最老一轮），不是聊天记录库。

## 存储布局：一个人一个目录、一个 json

```
data/mem_archive/
└── 3447899870/
    ├── 3447899870.json   ← 待整理的对话，每轮追加；触发整理后清空
    └── memory.json       ← 页/本/棚 + 计数（key/qq 也写在里面）
```

`3447899870.json`：

```json
{"key":"person:3447899870","qq":"3447899870","opened_at":…,"last_consolidated":…,
 "turns":[{"ts":…,"user":"…","reply":"…","src":"private"},
          {"ts":…,"user":"…","reply":"…","src":"group"}]}
```

- 每轮聊天追加一条，`src` 标来源：`private`（私聊）/ `group`（群聊）
- **触发整理 → 直接清空**（`turns: []`，同时写 `last_consolidated`），不再有分片文件
- 整理失败（模型没按 JSON 返回）时**不清空**，对话还留着，可以再 `/mem gen`

## 记忆按「人」存（私聊 + 群互通）

```
person:3447899870   ← 私聊和群里都由这个人的话累积到同一个目录
```

- 私聊：`qq-snowluma:FriendMessage:3447899870` → `person:3447899870`
- 群聊：**只有** `group_memory_ids` 里的人说的话进记忆；群里别人说的不记录、不注入
- 群里的注入级别 `group_inject_level`：`all`（页+本+棚）/ `profile`（只给棚）/ `off`（不注入）

## 三层怎么滚

```
待整理满 N 轮（默认 20）   → 整理「页」：重写这一页，然后清空待整理
页被整理 5 次             → 整理「本」：吸当前页 → 页冻结、开新页
本被整理 5 次             → 整理「棚」：吸当前本 → 本冻结、开新本
```

- 存储永远只有 **页 2 格 / 本 2 格 / 棚 1 份**（当前 + 冻结）
- 上限：页 25 条、本 15 条、棚 10 条（上限，不是目标）
- 名额（importance）：页 5 分不限 / 4 分 ≤12 / 3 分 ≤8，1-2 分只补位；本、棚只收 ≥3 分
- 注入带：当前页 + 冻结页（上一段）+ 当前本 + 冻结本（上几本）+ 棚
  - 条数上限：页 25 / 冻结页 10 / 本 15 / 冻结本 6 / 棚 10（`inject` 里可调）
  - 冻结内容按 importance 取前 N 条，不会全量涌入

## 条目格式

```json
{"content":"第一人称一句话，≤40字","type":"who|like|deal|rel","importance":1-5}
```

- 没有 id，靠内容前 20 字去重
- importance 决定顺序（存储、注入、`/mem show` 都按 5→1）

## 为什么 20 轮

实测 ≈167 tokens/轮。16k 的账：固定开销 ~2.6k + 记忆块 ~3.6k（页25/冻页10/本15/冻本10/棚10）
→ 20 轮历史 ≈3.3k，合计 ≈9.5k（占 16k 的 59%），留出空间给长文回复和工具说明。

全用 ≈77 轮、66% ≈45 轮，20 轮比 66% 更保守，是因为分段器现在允许长文（单条 200 字 ≈260 tokens）。
换 ctx 或想更密就改 `/mem page`。

## 指令

```
/mem                 状态（页/本/棚、待整理轮数、来源统计）
/mem show            看现在会注入的记忆
/mem clear           清掉这个人的页/本/棚（3447899870.json 不动）
/mem page 0-100      每多少轮整理一次（默认 20；0 = 不自动整理）
/mem gen             立刻把待整理的对话整理一次（整理完就清空）
```

没有 on/off——装了就是常开。只有白名单 QQ 能用这些指令。

## 兜底

聊到 39 轮断了：**空闲 30 分钟且攒了 ≥10 轮**时自动提前整理（`idle_seconds` / `idle_min_turns`）。

## 配置 `data/memory_config.json`

| 字段 | 默认 | 说明 |
|---|---|---|
| `page_turns` | 20 | 多少轮触发一次页整理（0 = 关闭自动） |
| `book_pages` | 5 | 几次页整理 → 一次本整理 |
| `profile_books` | 5 | 几次本整理 → 一次棚整理 |
| `idle_seconds` / `idle_min_turns` | 1800 / 10 | 空闲兜底 |
| `keep_pages` | 2 | 页/本各留 2 格 |
| `unify_private_and_group` | true | 私聊与群合并成同一个人的记忆 |
| `group_memory_ids` | ["3447899870"] | 群聊里只记这些人的话 |
| `group_inject_level` | all | 群里注入级别 |
| `max_items` / `importance_quota` / `min_importance` | 25/15/10 等 | 上限与名额 |
| `inject` | 25/15/10 | 注入条数 |

## 迁移

- `memory_store_v4.json`（单文件 store）→ `mem_archive/<QQ>/memory.json`
- 旧的 `seg-*.json` 分片：只把**还没整理过**的那一个的轮次并入 `<QQ>.json`，其余（已整理过的）删掉
- 旧轮次没有 `src` 的自动补 `private`
