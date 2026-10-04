> Historical design input used to generate the first implementation. The code, README, NOTES and docs/REFERENCE.md are authoritative where they differ.

# 03 Data model

## 1. Principles

- Only synchronization writes raw data (`pull_requests`, `pr_events`, `pr_files`, `workflow_runs`, `ownership_rules`).
- Only derivation writes `pr_facts` / `pr_intervals`; they can always be rebuilt from raw data.
- **Every** write changing raw or derived data (`save_page`, `link_repo`, `rederive_repo`, P1 CI/ownership) increments repository `data_version` in the same transaction. Snapshot IDs depend on it (`05` §12.3); omitting it lets one ID refer to different content. Exception: rederivation batches do not increment it; increment once on repository completion. During rederivation `derived_key` differs from current identity, so the API publishes no snapshots (`04` §6.5, `06` §5.1).
- Snapshots and narratives are immutable; changes create new snapshot IDs rather than modify old snapshots.
- All timestamps are `TIMESTAMPTZ` (UTC). Foreign keys use `ON DELETE CASCADE` unless specified otherwise.
- Implement SQLAlchemy 2.0 declarative models; the SQL below is the specification. Inspect generated Alembic migrations line by line.

## 2. Tables

### 2.1 `repositories`

```sql
CREATE TABLE repositories (
  id                   SERIAL PRIMARY KEY,
  full_name            TEXT NOT NULL,                 -- Display name, e.g. "dotnet/runtime"
  full_name_lower      TEXT NOT NULL UNIQUE,          -- Comparison key
  owner                TEXT NOT NULL,
  name                 TEXT NOT NULL,
  default_branch       TEXT,                          -- Set on first synchronization
  tracked              BOOLEAN NOT NULL DEFAULT TRUE, -- Present in TRACKED_REPOS
  covered_since        TIMESTAMPTZ,                   -- All PRs with updatedAt >= this time are stored
  backfill_target_days INTEGER,                       -- Current backfill stage target
  backfill_cursor      TEXT,                          -- GraphQL endCursor for resume
  sync_watermark       TIMESTAMPTZ,                   -- Maximum processed updatedAt
  last_open_sweep_at   TIMESTAMPTZ,
  last_synced_at       TIMESTAMPTZ,                   -- Consistent through this time; published only at checkpoint finalization (completed stage/successful job), using catch-up incremental start time (04 §6.3)
  last_sync_status     TEXT NOT NULL DEFAULT 'never', -- never|ok|failed|auth_error|not_found|missing_token
  last_sync_error      TEXT,                          -- Exception type/message, capped at 500 characters
  data_version         BIGINT NOT NULL DEFAULT 0,     -- Increment when data changes
  derived_key          TEXT,                          -- Identity for all derived data (05 §1 derive_key()); set after checkpoint completeness checks or full rederivation (04 §6.3, §6.5); API requires current identity
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
  title              TEXT NOT NULL,             -- UI/revert detection only; never sent to LLM
  body_excerpt       TEXT NOT NULL DEFAULT '',  -- First 4,000 characters; revert detection only
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
  content_hash       TEXT NOT NULL,             -- SHA256 of normalized PR + events + files
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
  kind         TEXT NOT NULL,          -- EventKind in 04 §2.1
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

### 2.5 `pr_facts` (derived)

```sql
CREATE TABLE pr_facts (
  pr_id                     BIGINT PRIMARY KEY REFERENCES pull_requests(id) ON DELETE CASCADE,
  repo_id                   INTEGER NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
  number                    INTEGER NOT NULL,
  is_bot_author             BOOLEAN NOT NULL,
  is_backport               BOOLEAN NOT NULL,         -- base_ref != repository default branch
  external_contributor      BOOLEAN NOT NULL,
  first_commit_at           TIMESTAMPTZ,
  ready_at                  TIMESTAMPTZ,
  first_response_at         TIMESTAMPTZ,
  first_review_at           TIMESTAMPTZ,
  first_approval_at         TIMESTAMPTZ,
  approved_at               TIMESTAMPTZ,              -- First time approval condition holds
  merged_at                 TIMESTAMPTZ,
  closed_at                 TIMESTAMPTZ,              -- Only when closed without merging
  end_at                    TIMESTAMPTZ,              -- merged_at or final closed_at; NULL if open
  coding_hours              DOUBLE PRECISION,
  pickup_hours              DOUBLE PRECISION,
  review_hours              DOUBLE PRECISION,
  merge_hours               DOUBLE PRECISION,
  cycle_hours               DOUBLE PRECISION,
  review_rounds             INTEGER NOT NULL,
  feedback_before_approval  INTEGER NOT NULL,
  commits_after_first_review INTEGER NOT NULL,
  force_pushes_after_first_review INTEGER NOT NULL,
  updates_after_approval    INTEGER NOT NULL,         -- Post-approval commits + force pushes (05 §3)
  distinct_approvers        INTEGER NOT NULL,
  second_approval_wait_hours DOUBLE PRECISION,
  merged_without_approval   BOOLEAN NOT NULL,
  review_requested_before_first_review BOOLEAN NOT NULL,
  human_reviews             INTEGER NOT NULL,
  size_lines                INTEGER NOT NULL,         -- additions + deletions
  size_bucket               TEXT NOT NULL,            -- XS | S | M | L | XL
  locations                 TEXT[] NOT NULL,          -- Based on current LOCATION_DIMENSION
  location_source           TEXT NOT NULL,            -- label | codeowners | directory | unclassified
  is_revert                 BOOLEAN NOT NULL DEFAULT FALSE,
  reverts_pr_id             BIGINT REFERENCES pull_requests(id) ON DELETE SET NULL,
  reverted_by_pr_id         BIGINT REFERENCES pull_requests(id) ON DELETE SET NULL,
  reverted_at               TIMESTAMPTZ,              -- Revert PR merged_at
  is_reland                 BOOLEAN NOT NULL DEFAULT FALSE,
  reland_of_pr_id           BIGINT REFERENCES pull_requests(id) ON DELETE SET NULL,
  superseded_by_pr_id       BIGINT REFERENCES pull_requests(id) ON DELETE SET NULL,
  close_class               TEXT,                     -- superseded | rejected | abandoned | no_review
  state_at_close            TEXT,                     -- Waiting state at closure
  late_rejection            BOOLEAN NOT NULL DEFAULT FALSE,
  ci_covered                BOOLEAN NOT NULL DEFAULT FALSE,
  author_open_prs_at_ready  INTEGER,                  -- P1
  ready_weekday             SMALLINT,                 -- 0=Monday … 6=Sunday (UTC)
  ready_hour                SMALLINT,                 -- 0–23(UTC)
  derive_key                TEXT,                     -- 04 §6.2: "{ANALYTICS_VERSION}|{THRESHOLDS_VERSION}|{LOCATION_DIMENSION}|depth={DIRECTORY_DEPTH}"; cleared after owner-rule changes (04 §10) to require rederivation
  computed_at               TIMESTAMPTZ NOT NULL
);
CREATE INDEX ix_facts_repo_merged ON pr_facts (repo_id, merged_at);
CREATE INDEX ix_facts_repo_ready  ON pr_facts (repo_id, ready_at);
CREATE INDEX ix_facts_repo_end    ON pr_facts (repo_id, end_at);
```

### 2.6 `pr_intervals` (derived)

```sql
CREATE TABLE pr_intervals (
  id       BIGSERIAL PRIMARY KEY,
  pr_id    BIGINT NOT NULL REFERENCES pull_requests(id) ON DELETE CASCADE,
  repo_id  INTEGER NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
  seq      SMALLINT NOT NULL,         -- Per-PR sequence starting at 0
  state    TEXT NOT NULL,             -- coding | waiting_reviewer | waiting_author | waiting_ci | waiting_merge | closed (temporary closure before reopen, 05 §2.2)
  start_at TIMESTAMPTZ NOT NULL,
  end_at   TIMESTAMPTZ,               -- NULL if still active at derivation time (PR open)
  UNIQUE (pr_id, seq)
);
CREATE INDEX ix_intervals_repo_state_start ON pr_intervals (repo_id, state, start_at);
CREATE INDEX ix_intervals_pr ON pr_intervals (pr_id);
```

### 2.7 `workflow_runs` (P1; table created in M1)

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

### 2.8 `ownership_rules` (P1; table created in M1)

```sql
CREATE TABLE ownership_rules (
  id         BIGSERIAL PRIMARY KEY,
  repo_id    INTEGER NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
  source     TEXT NOT NULL,      -- codeowners | area_owners
  pattern    TEXT NOT NULL,      -- Path pattern (codeowners) or area label (area_owners)
  owners     TEXT[] NOT NULL,    -- Preserve @user / @org/team verbatim
  line_no    INTEGER NOT NULL,   -- File order; last matching CODEOWNERS rule wins
  fetched_at TIMESTAMPTZ NOT NULL,
  UNIQUE (repo_id, source, line_no)
);
```

### 2.9 `snapshots`

```sql
CREATE TABLE snapshots (
  snapshot_id       TEXT PRIMARY KEY,   -- "s_" + 16 hex digits
  params            JSONB NOT NULL,     -- Normalized parameters
  repos             TEXT[] NOT NULL,
  period_from       DATE NOT NULL,
  period_to         DATE NOT NULL,
  data_versions     JSONB NOT NULL,     -- {"dotnet/runtime": 42}
  analytics_version TEXT NOT NULL,
  payload           JSONB NOT NULL,     -- Full snapshot from 06 §4
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
  lang           TEXT NOT NULL,      -- en for new requests; legacy rows are not served by prompt v6
  prompt_version TEXT NOT NULL,
  pack_hash      TEXT NOT NULL,      -- First 16 hex digits of canonical evidence-pack JSON SHA256 (07 §9.2); scoring configuration changes such as CI_COMPLETE change the key automatically
  model_id       TEXT NOT NULL,      -- "template" when generated_by = template
  generated_by   TEXT NOT NULL,      -- llm | template
  payload        JSONB NOT NULL,     -- Full response from 06 §6
  etag           TEXT NOT NULL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (snapshot_id, audience, lang, prompt_version, model_id, pack_hash)
);
```

**Persistence**: store `generated_by = "llm"` results and templates generated because Bedrock is unconfigured (`model_id = "template"`). Templates used after LLM failure are **not persisted in Postgres**; cache them in Redis for five minutes, then retry the LLM on later requests.

### 2.11 `sync_jobs`

```sql
CREATE TABLE sync_jobs (
  id          UUID PRIMARY KEY,
  repo_id     INTEGER NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
  kind        TEXT NOT NULL,   -- backfill | incremental | manual | rederive | ci_runs | ownership(04 §6.8)
  status      TEXT NOT NULL,   -- queued | running | succeeded | failed
  phase       TEXT,            -- Example: "backfill:30d"
  stats       JSONB NOT NULL DEFAULT '{}',  -- prs_fetched, prs_changed, events, pages, graphql_cost
  error       TEXT,            -- Exception type/message, capped at 500 characters
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at  TIMESTAMPTZ,
  finished_at TIMESTAMPTZ
);
CREATE INDEX ix_sync_jobs_repo_created ON sync_jobs (repo_id, created_at DESC);
```

### 2.12 Retention

Worker `housekeeping` runs daily at 03:17 UTC, deleting snapshots older than seven days (cascading narratives) and sync jobs finished over 30 days ago. The snapshot service explicitly writes the same injected `now` to Postgres and Redis `created_at`, and uses it for logical expiry rather than database `DEFAULT now()`. Frequent dotnet/runtime changes create new precomputed snapshots; seven-day retention bounds the table. Expired snapshot links return 404 (document under README Operations).

Expiry must be consistent across stores:

- When deleting snapshot rows, `housekeeping` also deletes `di:snap:{id}`, `di:rows:{id}`, and `di:narr:{id}:*` discovered with `SCAN`.
- Redis/Postgres reads treat `created_at` older than seven days as missing (Redis retains this timestamp). Postgres-to-Redis refill TTL is the lesser of 24 hours and remaining logical lifetime.
- Narrative writes that encounter a foreign-key error because a snapshot was just deleted return 404, not 500.

## 3. Redis keys (all built by `insights/redis.py`; `di:` prefix)

| Key | Type | Expiry | Purpose |
|---|---|---|---|
| `di:snap:{snapshot_id}` | hash `{etag, body, created_at}` (canonical JSON bytes) | 24 hours | Snapshot cache; ID computed directly from parameters/data state (`05` §12.3), no separate index key |
| `di:rows:{snapshot_id}` | bytes (orjson row list) | 1 hour | `/v1/insights/delivery/prs` details (`06` §5.2) |
| `di:narr:{snapshot_id}:{audience}:{lang}:{prompt_version}:{model_id}:{pack_hash}` | hash `{etag, body}` | 24 hours; failure fallback 300 seconds | Narrative cache |
| `di:lock:sync:{repo_lower}` | string (job ID) | 2 hours, renewed every 10 minutes while running | One synchronization per repository |
| `di:lock:narr:{snapshot_id}:{audience}:{lang}` | string (random token) | 180 seconds | Prevent concurrent LLM calls for one narrative; generation deadline 150 seconds (`07` §9.2) |
| `di:cooldown:sync:{repo_lower}` | string | `MANUAL_SYNC_COOLDOWN_SECONDS` | Manual synchronization cooldown |
| `di:rl:{client_ip}:{epoch_minute}` | int | 70 seconds | API rate counter |
| `di:gh:etag:{sha256(full URL including query string)}` | hash `{etag, body}` | 7 days | REST conditional requests (`04` §4.4) |

arq manages its own keys (default `arq:` prefix); do not manipulate them manually.

See `05-analytics.md` §12.3 for `snapshot_id`, `params_hash`, and `versions_hash`.
