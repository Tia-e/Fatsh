# mem · 页 / 本 / 棚 三层记忆（v4，轮驱动）

全部逻辑落在插件内，**不读 sqlite**——AstrBot 的 `conversations` 表只是"当前上下文窗口"（`max_turns=40`，每次写入砍最老一轮），不是聊天记录库。

## 存储布局：一个人一个目录

```
data/mem_archive/
└── 3447899870/               ← 目录名就是 QQ 号
    ├── memory.json           ← 这个人的 页/本/棚 + 分片指针（key/qq 也写在里面）
    ├── seg-0001.json         ← 装 page_turns 轮对话，每轮标来源
    ├── seg-0002.json
    └── …
```

每个 `seg-xxxx.json`：

```json
{"seq":1,"key":"person:3447899870","qq":"3447899870","opened_at":…,"closed_at":null,
 "turns":[{"ts":…,"user":"…","reply":"…","src":"private"},
          {"ts":…,"user":"…","reply":"…","src":"group"}]}
```

- `src` 标来源：`private`（私聊）/ `group`（群聊）
- 整理时素材行也会带来源：`[11-01 17:40|群] 用户：…`
- 页条目里会记 `sources`（这一页吸过哪些来源）
- 分片写满就关掉、立刻新开下一个；目录最多留 `archive_keep`（默认 10）个，超了删最早的

## 记忆按「人」存（私聊 + 群互通）

```
person:3447899870   ← 私聊和群里都由这个人的话累积到同一个目录
```

- 私聊：`qq-snowluma:FriendMessage:3447899870` → `person:3447899870`
- 群聊：**只有** `group_memory_ids` 里的人（默认主人 3447899870）说的话进记忆；群里别人说什么都不记录、也不注入
- 群里的注入级别 `group_inject_level`：`all`（页+本+棚）/ `profile`（只给棚）/ `off`（不注入）

旧数据会自动迁移：`memory_store_v4.json` 和哈希名目录 → `mem_archive/<QQ>/`，分片和页都保留。

## 三层怎么滚

```
每 N 轮（默认 40）        → 整理「页」：重写这一页，冻结页只作参考
页被整理 5 次             → 整理「本」：吸当前页 → 页冻结、开新页
本被整理 5 次             → 整理「棚」：吸当前本 → 本冻结、开新本
```

- 存储永远只有 **页 2 格 / 本 2 格 / 棚 1 份**（当前 + 冻结）
- 上限：页 25 条、本 15 条、棚 10 条（都是"上限"不是目标）
- 名额分配（importance）：页 5 分不限 / 4 分 ≤12 / 3 分 ≤8，1-2 分只补位；本、棚只收 ≥3 分
- 注入只带当前页/当前本/棚；冻结的只做整理参考

## 条目格式

```json
{"content":"第一人称一句话，≤40字","type":"who|like|deal|rel","importance":1-5}
```

- **没有 id**：靠内容前 20 字去重
- **importance 决定顺序**：存储、注入、`/mem show` 一律按 5→1 排序；超上限丢最低的

## 为什么 40 轮

实测 ≈167 tokens/轮（60-70 字/轮），16k 减去固定开销（~3k）后：

```
16k 全用 ≈ 77 轮 ｜ 66% ≈ 45 轮 ｜ 一半 ≈ 30 轮
```

默认 40 轮 ≈ 6.7k tokens，正好 66%，也和内核 `max_turns=40` 对齐。换 ctx 就改 `/mem page`。

## 指令

```
/mem                 状态（页/本/棚、本页轮数、分片数、来源统计）
/mem show            看现在会注入的记忆
/mem clear           清掉这个人的页/本/棚（分片保留）
/mem page 0-100      每页多少轮（默认 40；0 = 不自动分页）
/mem gen             立刻用当前分片整理一次（当前分片空了就用最近一个有内容的分片）
```

**没有 on/off**——装了插件就是常开。只有白名单 QQ 能用这些指令。

## 兜底

聊到 39 轮就断了：**空闲 30 分钟且攒了 ≥10 轮**时自动提前关分片并整理（`idle_seconds` / `idle_min_turns`）。

## 配置 `data/memory_config.json`

| 字段 | 默认 | 说明 |
|---|---|---|
| `page_turns` | 40 | 每页轮数（0 = 关闭自动分页） |
| `book_pages` | 5 | 几次页整理 → 一次本整理 |
| `profile_books` | 5 | 几次本整理 → 一次棚整理 |
| `archive_keep` | 10 | 每人目录最多留几个分片 |
| `idle_seconds` / `idle_min_turns` | 1800 / 10 | 空闲兜底 |
| `keep_pages` | 2 | 页/本各留 2 格 |
| `unify_private_and_group` | true | 私聊与群合并成同一个人的记忆 |
| `group_memory_ids` | ["3447899870"] | 群聊里只记这些人的话 |
| `group_inject_level` | all | 群里注入级别 |
| `max_items` / `importance_quota` / `min_importance` | 25/15/10 等 | 上限与名额 |
| `inject` | 25/15/10 | 注入条数 |
