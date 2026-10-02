# 03 数据模型

## 1. 原则

- 原始数据（`pull_requests`、`pr_events`、`pr_files`、`workflow_runs`、`ownership_rules`）只由同步写入。
- 推导数据（`pr_facts`、`pr_intervals`）只由推导步骤写入，可随时从原始数据重建。
- **任何**改变原始数据或推导数据的写入（`save_page`、`link_repo`、`rederive_repo`、P1 的 CI 运行记录与所有权规则）都在同一事务中把该仓库的 `data_version` 加 1：快照 ID 由它决定（`05` §12.3），漏加会让同一个 ID 对应不同的内容。唯一的例外是 `rederive_repo` 的逐批提交：批次提交时不加，整仓完成时加一次；期间 `derived_key` 不等于当前标识，API 不发布任何快照（`04` §6.5、`06` §5.1）。
- 快照和叙述不可变；数据变化时生成新的快照 ID，而不是修改旧快照。
- 所有时间列都是 `TIMESTAMPTZ`（UTC）。所有外键 `ON DELETE CASCADE`，除非下面另有说明。
- 用 SQLAlchemy 2.0 声明式模型实现，下面的 SQL 是规格；Alembic 自动生成后逐行对照检查。

## 2. 表结构

### 2.1 `repositories`

```sql
CREATE TABLE repositories (
  id                   SERIAL PRIMARY KEY,
  full_name            TEXT NOT NULL,                 -- 显示用，例如 "dotnet/runtime"
  full_name_lower      TEXT NOT NULL UNIQUE,          -- 比较用
  owner                TEXT NOT NULL,
  name                 TEXT NOT NULL,
  default_branch       TEXT,                          -- 首次同步时写入
  tracked              BOOLEAN NOT NULL DEFAULT TRUE, -- 是否在 TRACKED_REPOS 中
  covered_since        TIMESTAMPTZ,                   -- 所有 updatedAt >= 该时间的 PR 都已入库
  backfill_target_days INTEGER,                       -- 当前回填阶段的目标天数
  backfill_cursor      TEXT,                          -- 回填续传用的 GraphQL endCursor
  sync_watermark       TIMESTAMPTZ,                   -- 增量同步水位线：已处理的最大 updatedAt
  last_open_sweep_at   TIMESTAMPTZ,
  last_synced_at       TIMESTAMPTZ,                   -- "数据到此刻为止一致"：只在提交点收尾（回填阶段完成、同步任务成功）时写入，值为收尾增量的开始时间（04 §6.3）
  last_sync_status     TEXT NOT NULL DEFAULT 'never', -- never|ok|failed|auth_error|not_found|missing_token
  last_sync_error      TEXT,                          -- 简短错误信息（异常类型和消息，截断到 500 字符）
  data_version         BIGINT NOT NULL DEFAULT 0,     -- 数据变化时 +1
  derived_key          TEXT,                          -- 全部推导数据所用的推导标识（05 §1 的 derive_key()）；提交点收尾的推导完整性检查通过时或整仓重推导完成时写入（04 §6.3、§6.5），API 就绪检查要求它等于当前标识
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 2.2 `pull_requests`

```sql
CREATE TABLE pull_requests (
  id                 BIGSERIAL PRIMARY KEY,
  repo_id            INTEGER NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
  source_id          TEXT NOT NULL UNIQUE,      -- GitHub node id
  number             INTEGER NOT NULL,
  title              TEXT NOT NULL,             -- 只用于 UI 和 revert 识别，不发给 LLM
  body_excerpt       TEXT NOT NULL DEFAULT '',  -- 前 4,000 字符，只用于 revert 识别
  url                TEXT NOT NULL,
  state              TEXT NOT NULL,             -- OPEN | CLOSED | MERGED
  is_draft           BOOLEAN NOT NULL,
  author_login       TEXT,
  author_type        TEXT NOT NULL,             -- User | Bot | Mannequin | Unknown
  author_association TEXT NOT NULL,
  is_bot_author      BOOLEAN NOT NULL,
  base_ref           TEXT NOT NULL,
  head_ref           TEXT NOT NULL,
  created_at         TIMESTAMPTZ NOT NULL,
  updated_at         TIMESTAMPTZ NOT NULL,
  closed_at          TIMESTAMPTZ,
  merged_at          TIMESTAMPTZ,
  merged_by          TEXT,
  merge_commit_oid   TEXT,
  additions          INTEGER NOT NULL,
  deletions          INTEGER NOT NULL,
  changed_files      INTEGER NOT NULL,
  labels             TEXT[] NOT NULL DEFAULT '{}',
  files_truncated    BOOLEAN NOT NULL DEFAULT FALSE,
  content_hash       TEXT NOT NULL,             -- 规范化后的 PR + 事件 + 文件的 sha256
  synced_at          TIMESTAMPTZ NOT NULL,
  UNIQUE (repo_id, number)
);
CREATE INDEX ix_pr_repo_merged  ON pull_requests (repo_id, merged_at);
CREATE INDEX ix_pr_repo_created ON pull_requests (repo_id, created_at);
CREATE INDEX ix_pr_repo_updated ON pull_requests (repo_id, updated_at);
CREATE INDEX ix_pr_repo_state   ON pull_requests (repo_id, state);
CREATE INDEX ix_pr_merge_commit ON pull_requests (repo_id, merge_commit_oid);
```

### 2.3 `pr_events`

```sql
CREATE TABLE pr_events (
  id           BIGSERIAL PRIMARY KEY,
  pr_id        BIGINT NOT NULL REFERENCES pull_requests(id) ON DELETE CASCADE,
  kind         TEXT NOT NULL,          -- 见 04 §2.1 EventKind
  occurred_at  TIMESTAMPTZ NOT NULL,
  actor_login  TEXT,
  actor_is_bot BOOLEAN NOT NULL DEFAULT FALSE,
  payload      JSONB NOT NULL DEFAULT '{}',
  dedup_key    TEXT NOT NULL,
  UNIQUE (pr_id, dedup_key)
);
CREATE INDEX ix_events_pr_time ON pr_events (pr_id, occurred_at);
CREATE INDEX ix_events_kind_time ON pr_events (kind, occurred_at);
```

### 2.4 `pr_files`

```sql
CREATE TABLE pr_files (
  pr_id BIGINT NOT NULL REFERENCES pull_requests(id) ON DELETE CASCADE,
  path  TEXT NOT NULL,
  PRIMARY KEY (pr_id, path)
);
```

### 2.5 `pr_facts`（推导）

```sql
CREATE TABLE pr_facts (
  pr_id                     BIGINT PRIMARY KEY REFERENCES pull_requests(id) ON DELETE CASCADE,
  repo_id                   INTEGER NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
  number                    INTEGER NOT NULL,
  is_bot_author             BOOLEAN NOT NULL,
  is_backport               BOOLEAN NOT NULL,         -- base_ref != 仓库默认分支
  external_contributor      BOOLEAN NOT NULL,
  first_commit_at           TIMESTAMPTZ,
  ready_at                  TIMESTAMPTZ,
  first_response_at         TIMESTAMPTZ,
  first_review_at           TIMESTAMPTZ,
  first_approval_at         TIMESTAMPTZ,
  approved_at               TIMESTAMPTZ,              -- "已批准"条件第一次成立的时间
  merged_at                 TIMESTAMPTZ,
  closed_at                 TIMESTAMPTZ,              -- 只在关闭且未合并时有值
  end_at                    TIMESTAMPTZ,              -- merged_at 或最终 closed_at；开着为 NULL
  coding_hours              DOUBLE PRECISION,
  pickup_hours              DOUBLE PRECISION,
  review_hours              DOUBLE PRECISION,
  merge_hours               DOUBLE PRECISION,
  cycle_hours               DOUBLE PRECISION,
  review_rounds             INTEGER NOT NULL,
  feedback_before_approval  INTEGER NOT NULL,
  commits_after_first_review INTEGER NOT NULL,
  force_pushes_after_first_review INTEGER NOT NULL,
  updates_after_approval    INTEGER NOT NULL,         -- 批准后的 commit + force push 数（05 §3）
  distinct_approvers        INTEGER NOT NULL,
  second_approval_wait_hours DOUBLE PRECISION,
  merged_without_approval   BOOLEAN NOT NULL,
  review_requested_before_first_review BOOLEAN NOT NULL,
  human_reviews             INTEGER NOT NULL,
  size_lines                INTEGER NOT NULL,         -- additions + deletions
  size_bucket               TEXT NOT NULL,            -- XS | S | M | L | XL
  locations                 TEXT[] NOT NULL,          -- 按当前 LOCATION_DIMENSION 计算
  location_source           TEXT NOT NULL,            -- label | codeowners | directory | unclassified
  is_revert                 BOOLEAN NOT NULL DEFAULT FALSE,
  reverts_pr_id             BIGINT REFERENCES pull_requests(id) ON DELETE SET NULL,
  reverted_by_pr_id         BIGINT REFERENCES pull_requests(id) ON DELETE SET NULL,
  reverted_at               TIMESTAMPTZ,              -- revert PR 的 merged_at
  is_reland                 BOOLEAN NOT NULL DEFAULT FALSE,
  reland_of_pr_id           BIGINT REFERENCES pull_requests(id) ON DELETE SET NULL,
  superseded_by_pr_id       BIGINT REFERENCES pull_requests(id) ON DELETE SET NULL,
  close_class               TEXT,                     -- superseded | rejected | abandoned | no_review
  state_at_close            TEXT,                     -- 关闭时的等待状态
  late_rejection            BOOLEAN NOT NULL DEFAULT FALSE,
  ci_covered                BOOLEAN NOT NULL DEFAULT FALSE,
  author_open_prs_at_ready  INTEGER,                  -- P1
  ready_weekday             SMALLINT,                 -- 0=周一 … 6=周日（UTC）
  ready_hour                SMALLINT,                 -- 0–23（UTC）
  derive_key                TEXT,                     -- 04 §6.2："{ANALYTICS_VERSION}|{THRESHOLDS_VERSION}|{LOCATION_DIMENSION}|depth={DIRECTORY_DEPTH}"；owner 规则变化时置空（04 §10），表示需要重推导
  computed_at               TIMESTAMPTZ NOT NULL
);
CREATE INDEX ix_facts_repo_merged ON pr_facts (repo_id, merged_at);
CREATE INDEX ix_facts_repo_ready  ON pr_facts (repo_id, ready_at);
CREATE INDEX ix_facts_repo_end    ON pr_facts (repo_id, end_at);
```

### 2.6 `pr_intervals`（推导）

```sql
CREATE TABLE pr_intervals (
  id       BIGSERIAL PRIMARY KEY,
  pr_id    BIGINT NOT NULL REFERENCES pull_requests(id) ON DELETE CASCADE,
  repo_id  INTEGER NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
  seq      SMALLINT NOT NULL,         -- PR 内的顺序，从 0 开始
  state    TEXT NOT NULL,             -- coding | waiting_reviewer | waiting_author | waiting_ci | waiting_merge | closed（关闭后又重开之间，05 §2.2）
  start_at TIMESTAMPTZ NOT NULL,
  end_at   TIMESTAMPTZ,               -- NULL 表示推导时仍在进行（PR 开着）
  UNIQUE (pr_id, seq)
);
CREATE INDEX ix_intervals_repo_state_start ON pr_intervals (repo_id, state, start_at);
CREATE INDEX ix_intervals_pr ON pr_intervals (pr_id);
```

### 2.7 `workflow_runs`（P1，M1 建表）

```sql
CREATE TABLE workflow_runs (
  id             BIGINT PRIMARY KEY,   -- GitHub run id
  repo_id        INTEGER NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
  workflow_name  TEXT NOT NULL,
  event          TEXT NOT NULL,
  head_sha       TEXT NOT NULL,
  status         TEXT NOT NULL,
  conclusion     TEXT,
  run_attempt    INTEGER NOT NULL,
  created_at     TIMESTAMPTZ NOT NULL,
  run_started_at TIMESTAMPTZ,
  updated_at     TIMESTAMPTZ NOT NULL,
  pr_numbers     INTEGER[] NOT NULL DEFAULT '{}'
);
CREATE INDEX ix_runs_repo_sha     ON workflow_runs (repo_id, head_sha);
CREATE INDEX ix_runs_repo_created ON workflow_runs (repo_id, created_at);
```

### 2.8 `ownership_rules`（P1，M1 建表）

```sql
CREATE TABLE ownership_rules (
  id         BIGSERIAL PRIMARY KEY,
  repo_id    INTEGER NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
  source     TEXT NOT NULL,      -- codeowners | area_owners
  pattern    TEXT NOT NULL,      -- 路径模式（codeowners）或 area label（area_owners）
  owners     TEXT[] NOT NULL,    -- 原样保留 @user / @org/team
  line_no    INTEGER NOT NULL,   -- 文件中的顺序；CODEOWNERS 最后匹配的规则生效
  fetched_at TIMESTAMPTZ NOT NULL,
  UNIQUE (repo_id, source, line_no)
);
```

### 2.9 `snapshots`

```sql
CREATE TABLE snapshots (
  snapshot_id       TEXT PRIMARY KEY,   -- "s_" + 16 位十六进制
  params            JSONB NOT NULL,     -- 规范化后的参数
  repos             TEXT[] NOT NULL,
  period_from       DATE NOT NULL,
  period_to         DATE NOT NULL,
  data_versions     JSONB NOT NULL,     -- {"dotnet/runtime": 42}
  analytics_version TEXT NOT NULL,
  payload           JSONB NOT NULL,     -- 06 §4 的完整快照
  etag              TEXT NOT NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_snapshots_created ON snapshots (created_at);
```

### 2.10 `narratives`

```sql
CREATE TABLE narratives (
  id             BIGSERIAL PRIMARY KEY,
  snapshot_id    TEXT NOT NULL REFERENCES snapshots(snapshot_id) ON DELETE CASCADE,
  audience       TEXT NOT NULL,      -- director | manager
  lang           TEXT NOT NULL,      -- en | zh
  prompt_version TEXT NOT NULL,
  pack_hash      TEXT NOT NULL,      -- 证据包规范化 JSON 的 sha256 前 16 位（07 §9.2）；影响评分的配置（如 CI_COMPLETE）变化时自然换键
  model_id       TEXT NOT NULL,      -- generated_by = template 时为 "template"
  generated_by   TEXT NOT NULL,      -- llm | template
  payload        JSONB NOT NULL,     -- 06 §6 的完整响应
  etag           TEXT NOT NULL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (snapshot_id, audience, lang, prompt_version, model_id, pack_hash)
);
```

**持久化规则**：`generated_by = "llm"` 的结果写入；因为没有配置 Bedrock 而生成的模板结果也写入（`model_id = "template"`）。因为 LLM 调用失败而降级的模板结果**不写入 Postgres**，只在 Redis 缓存 5 分钟，之后的请求会重新尝试 LLM。

### 2.11 `sync_jobs`

```sql
CREATE TABLE sync_jobs (
  id          UUID PRIMARY KEY,
  repo_id     INTEGER NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
  kind        TEXT NOT NULL,   -- backfill | incremental | manual | rederive | ci_runs | ownership（04 §6.8）
  status      TEXT NOT NULL,   -- queued | running | succeeded | failed
  phase       TEXT,            -- 例如 "backfill:30d"
  stats       JSONB NOT NULL DEFAULT '{}',  -- prs_fetched, prs_changed, events, pages, graphql_cost
  error       TEXT,            -- 简短错误信息（异常类型和消息，截断到 500 字符）
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at  TIMESTAMPTZ,
  finished_at TIMESTAMPTZ
);
CREATE INDEX ix_sync_jobs_repo_created ON sync_jobs (repo_id, created_at DESC);
```

### 2.12 数据保留

worker 每天 03:17 UTC 运行 `housekeeping`：删除 7 天前创建的快照（叙述随之级联删除）和 30 天前结束的 `sync_jobs`。快照的 `created_at` 由快照服务按注入的 `now` 显式写入（Postgres 行和 Redis 哈希写同一个值），逻辑过期也用同一个 `now` 判断，不依赖数据库的 `DEFAULT now()`。dotnet/runtime 几乎每次同步都有数据变化，每次变化都会产生新的预计算快照，7 天的保留期把快照表的大小限制在可控范围内；之后再访问旧快照链接返回 404（写进 README 的 Operations）。

过期必须在所有存储上一致：

- `housekeeping` 删除快照行的同时，删除这些快照的 Redis 键（`di:snap:{id}`、`di:rows:{id}`，以及用 `SCAN` 匹配的 `di:narr:{id}:*`）。
- 读取快照时，无论命中 Redis 还是 Postgres，`created_at` 早于 7 天的一律按不存在处理（Redis 哈希里同时保存 `created_at`）；从 Postgres 回填 Redis 时，过期时间取 24 小时与"距离逻辑过期的剩余时间"中的较小者。
- 写叙述时如果遇到外键错误（快照刚好被清理），按快照不存在返回 404，不返回 500。

## 3. Redis 键（全部通过 `insights/redis.py` 的函数生成，前缀 `di:`）

| 键 | 类型 | 过期 | 用途 |
|---|---|---|---|
| `di:snap:{snapshot_id}` | hash `{etag, body, created_at}`（body 为规范化 JSON 字节） | 24 小时 | 快照缓存；`snapshot_id` 可由参数和数据状态直接算出（`05` §12.3），不需要单独的索引键 |
| `di:rows:{snapshot_id}` | bytes（orjson 行列表） | 1 小时 | `/v1/insights/delivery/prs` 的明细行（`06` §5.2） |
| `di:narr:{snapshot_id}:{audience}:{lang}:{prompt_version}:{model_id}:{pack_hash}` | hash `{etag, body}` | 24 小时；失败降级的模板 300 秒 | 叙述缓存 |
| `di:lock:sync:{repo_lower}` | string（job id） | 2 小时，运行中每 10 分钟续期 | 同一仓库同时只有一个同步 |
| `di:lock:narr:{snapshot_id}:{audience}:{lang}` | string（随机 token） | 180 秒 | 防止同一叙述并发调用 LLM（生成有 150 秒的总时限，`07` §9.2） |
| `di:cooldown:sync:{repo_lower}` | string | `MANUAL_SYNC_COOLDOWN_SECONDS` | 手动同步冷却 |
| `di:rl:{client_ip}:{epoch_minute}` | int | 70 秒 | API 限流计数 |
| `di:gh:etag:{sha256(完整 URL，含查询串)}` | hash `{etag, body}` | 7 天 | REST 条件请求缓存（`04` §4.4） |

arq 使用自己的键（默认 `arq:` 前缀），不要手动操作。

`snapshot_id`、`params_hash` 与 `versions_hash` 的计算见 `05-analytics.md` §12.3。
