# 04 GitHub 集成与同步

## 1. 总体

- 所有 GitHub 调用都在 worker 里；API 进程不导入 `insights.sources`。
- 鉴权：`Authorization: Bearer <GITHUB_TOKEN>`。推荐 fine-grained token，Repository access 选 "Public repositories"，不加额外权限（fine-grained token 自带公开仓库只读权限，GraphQL 支持这种 token）。CI 数据：GitHub 的 check runs 接口支持 fine-grained token（公开仓库不需要额外权限），但本实现只采集 GitHub Actions 运行记录（§9）。dotnet/runtime 的主 CI 在 Azure Pipelines，在 GitHub 上表现为 check runs，本实现没有采集，所以 CI 数据不完整（默认 `CI_COMPLETE=false`，CI 相关假设的置信度封顶 0.5，`07` §4.3）。采集 check runs 是以后可以加的增强。
- 请求串行：worker `max_jobs = 1`，客户端内部再用 `asyncio.Lock` 保证同一时刻只有一个请求在途（避免触发二级限流）。

## 2. 领域模型（`insights/domain.py`）与适配器接口

### 2.1 数据类

```python
class EventKind(StrEnum):
    READY_FOR_REVIEW = "ready_for_review"
    CONVERT_TO_DRAFT = "convert_to_draft"
    REVIEW_REQUESTED = "review_requested"
    REVIEW_REQUEST_REMOVED = "review_request_removed"
    REVIEW = "review"
    REVIEW_DISMISSED = "review_dismissed"
    COMMIT = "commit"
    FORCE_PUSH = "force_push"
    COMMENT = "comment"
    LABELED = "labeled"
    UNLABELED = "unlabeled"
    CLOSED = "closed"
    REOPENED = "reopened"
    MERGED = "merged"
    CROSS_REFERENCED = "cross_referenced"

@dataclass(frozen=True, slots=True)
class RepoRef:
    owner: str
    name: str
    # property full_name -> f"{owner}/{name}"

@dataclass(frozen=True, slots=True)
class RepositoryInfo:
    full_name: str          # GitHub 返回的 nameWithOwner
    default_branch: str
    is_archived: bool

@dataclass(frozen=True, slots=True)
class Actor:
    login: str | None       # 已删除的账号为 None
    is_bot: bool

@dataclass(frozen=True, slots=True)
class Event:
    kind: EventKind
    occurred_at: datetime   # UTC
    actor: Actor
    payload: dict[str, Any] # 各 kind 的键见下表；只含 JSON 可序列化的值
    dedup_key: str

@dataclass(frozen=True, slots=True)
class PullRequestRecord:
    source_id: str
    number: int
    title: str
    body_excerpt: str       # body[:4000]
    url: str
    state: str              # OPEN | CLOSED | MERGED
    is_draft: bool
    author: Actor
    author_type: str        # User | Bot | Mannequin | Unknown
    author_association: str
    base_ref: str
    head_ref: str
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    merged_at: datetime | None
    merged_by: str | None
    merge_commit_oid: str | None
    additions: int
    deletions: int
    changed_files: int
    labels: tuple[str, ...]
    files: tuple[str, ...]
    files_truncated: bool
    events: tuple[Event, ...]   # 完整时间线，按 (occurred_at, kind, dedup_key) 排序

@dataclass(frozen=True, slots=True)
class PageResult:
    repository: RepositoryInfo
    prs: tuple[PullRequestRecord, ...]
    end_cursor: str | None
    has_next_page: bool
    oldest_updated_at: datetime | None   # 本页最小的 updatedAt
    newest_updated_at: datetime | None   # 本页最大的 updatedAt
    graphql_cost: int

@dataclass(frozen=True, slots=True)
class CiRun:                 # P1
    run_id: int
    workflow_name: str
    event: str
    head_sha: str
    status: str
    conclusion: str | None
    run_attempt: int
    created_at: datetime
    run_started_at: datetime | None
    updated_at: datetime
    pr_numbers: tuple[int, ...]

@dataclass(frozen=True, slots=True)
class OwnershipRule:         # P1
    source: str              # codeowners | area_owners
    pattern: str
    owners: tuple[str, ...]
    line_no: int
```

事件 `payload` 的键：

| kind | payload |
|---|---|
| `review` | `{"state": "APPROVED" \| "CHANGES_REQUESTED" \| "COMMENTED", "review_id": str, "dismissed": bool}`；`state` 是**提交时**的原始决定（§5.3） |
| `review_dismissed` | `{"review_id": str \| None, "review_author": str \| None, "previous_state": "APPROVED" \| "CHANGES_REQUESTED" \| None}` |
| `review_requested` | `{"reviewer": str \| None, "reviewer_type": "User" \| "Team" \| "Unknown"}` |
| `commit` | `{"oid": str, "authored_at": iso8601, "committed_at": iso8601, "reverts": [sha, ...]}` |
| `labeled` / `unlabeled` | `{"label": str}` |
| `cross_referenced` | `{"source_repo": str, "source_number": int, "source_state": str, "source_merged_at": iso8601 \| None, "source_author": str \| None, "will_close": bool}` |
| 其他 | `{}` |

### 2.2 时间字段

`commit` 事件的 `occurred_at` 取 `committedDate`（GitHub 不提供普通 push 的时间；这是已知近似，写进 README 的 Limitations）。其他事件取 `createdAt`；review 取 `submittedAt`。

### 2.3 `SourceAdapter`（`insights/sources/base.py`）

```python
class SourceAdapter(Protocol):
    async def pull_requests_page(
        self, repo: RepoRef, *, cursor: str | None, page_size: int, open_only: bool = False
    ) -> PageResult: ...
    async def ci_runs(
        self, repo: RepoRef, *, created_from: datetime, created_to: datetime
    ) -> list[CiRun]: ...                                   # P1
    async def ownership_rules(self, repo: RepoRef) -> list[OwnershipRule]: ...   # P1
```

这是唯一刻意保留的扩展点：接入新来源只需新增一个实现，同步和分析层不变。README 要说明这一点。

## 3. GraphQL 查询（`insights/sources/github/queries.py`，原样使用）

```graphql
fragment ActorFields on Actor {
  __typename
  login
}

fragment TimelineFields on PullRequestTimelineItems {
  __typename
  ... on Node { id }
  ... on ReadyForReviewEvent { createdAt actor { ...ActorFields } }
  ... on ConvertToDraftEvent { createdAt actor { ...ActorFields } }
  ... on ReviewRequestedEvent {
    createdAt
    actor { ...ActorFields }
    requestedReviewer { __typename ... on User { login } ... on Team { slug } }
  }
  ... on ReviewRequestRemovedEvent { createdAt actor { ...ActorFields } }
  ... on PullRequestReview { id state submittedAt author { ...ActorFields } }
  ... on ReviewDismissedEvent { createdAt actor { ...ActorFields } previousReviewState review { id author { ...ActorFields } } }
  ... on PullRequestCommit { commit { oid authoredDate committedDate messageHeadline messageBody } }
  ... on HeadRefForcePushedEvent { createdAt actor { ...ActorFields } }
  ... on IssueComment { createdAt author { ...ActorFields } }
  ... on LabeledEvent { createdAt actor { ...ActorFields } label { name } }
  ... on UnlabeledEvent { createdAt actor { ...ActorFields } label { name } }
  ... on ClosedEvent { createdAt actor { ...ActorFields } }
  ... on ReopenedEvent { createdAt actor { ...ActorFields } }
  ... on MergedEvent { createdAt actor { ...ActorFields } }
  ... on CrossReferencedEvent {
    createdAt
    willCloseTarget
    source {
      __typename
      ... on PullRequest { number state mergedAt author { ...ActorFields } repository { nameWithOwner } }
    }
  }
}

query PullRequestsPage($owner: String!, $name: String!, $pageSize: Int!, $cursor: String, $states: [PullRequestState!]) {
  rateLimit { cost remaining resetAt }
  repository(owner: $owner, name: $name) {
    nameWithOwner
    isArchived
    defaultBranchRef { name }
    pullRequests(first: $pageSize, after: $cursor, states: $states, orderBy: {field: UPDATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        id
        number
        title
        body
        url
        state
        isDraft
        createdAt
        updatedAt
        closedAt
        mergedAt
        additions
        deletions
        changedFiles
        baseRefName
        headRefName
        authorAssociation
        author { ...ActorFields }
        mergedBy { ...ActorFields }
        mergeCommit { oid }
        labels(first: 30) { nodes { name } }
        files(first: 100) { pageInfo { hasNextPage } nodes { path } }
        timelineItems(first: 100, itemTypes: [READY_FOR_REVIEW_EVENT, CONVERT_TO_DRAFT_EVENT, REVIEW_REQUESTED_EVENT, REVIEW_REQUEST_REMOVED_EVENT, PULL_REQUEST_REVIEW, REVIEW_DISMISSED_EVENT, PULL_REQUEST_COMMIT, HEAD_REF_FORCE_PUSHED_EVENT, ISSUE_COMMENT, LABELED_EVENT, UNLABELED_EVENT, CLOSED_EVENT, REOPENED_EVENT, MERGED_EVENT, CROSS_REFERENCED_EVENT]) {
          pageInfo { hasNextPage endCursor }
          nodes { ...TimelineFields }
        }
      }
    }
  }
}

query PullRequestTimeline($id: ID!, $cursor: String) {
  rateLimit { cost remaining resetAt }
  node(id: $id) {
    ... on PullRequest {
      timelineItems(first: 100, after: $cursor, itemTypes: [READY_FOR_REVIEW_EVENT, CONVERT_TO_DRAFT_EVENT, REVIEW_REQUESTED_EVENT, REVIEW_REQUEST_REMOVED_EVENT, PULL_REQUEST_REVIEW, REVIEW_DISMISSED_EVENT, PULL_REQUEST_COMMIT, HEAD_REF_FORCE_PUSHED_EVENT, ISSUE_COMMENT, LABELED_EVENT, UNLABELED_EVENT, CLOSED_EVENT, REOPENED_EVENT, MERGED_EVENT, CROSS_REFERENCED_EVENT]) {
        pageInfo { hasNextPage endCursor }
        nodes { ...TimelineFields }
      }
    }
  }
}
```

- 两个查询文本都需要包含两个 fragment 定义（拼接字符串常量即可）。
- `$states` 传 `null` 表示不过滤；开着的 PR 扫描传 `["OPEN"]`。
- 某个 PR 的 `timelineItems.pageInfo.hasNextPage` 为真时，用 `PullRequestTimeline` 按游标补齐，直到没有下一页；补齐完成前不得写入该 PR。
- `files` 超过 100 个时不补齐，只把 `files_truncated` 置真（大小统计用 `additions`、`deletions`、`changedFiles`，不受影响）。
- 成本估算：每页 25 个 PR，嵌套连接各一次请求，按 GitHub 规则约 1–2 点，远低于每小时 5,000 点。

## 4. 客户端（`insights/sources/github/client.py`）

### 4.1 基本设置

- `httpx.AsyncClient`，超时 `httpx.Timeout(connect=10, read=60, write=30, pool=10)`。
- 请求头：`Authorization: Bearer …`、`Accept: application/vnd.github+json`、`X-GitHub-Api-Version: 2022-11-28`、`User-Agent: delivery-insights/1.0`。
- 方法：`graphql(query: str, variables: dict) -> dict`、`rest_get(path: str, params: dict, *, accept: str | None = None) -> RestResponse`（`path` 只能是代码里的常量模板加上已校验的 owner/name，不接受完整 URL）。
- `sleep` 函数可注入（默认 `asyncio.sleep`），测试时替换为记录调用的假函数。

### 4.2 限流与重试

按 GitHub 官方建议的顺序处理：

1. 响应带 `retry-after` → 等待该秒数后重试。
2. `x-ratelimit-remaining` 为 `0` → 等到 `x-ratelimit-reset`（epoch 秒）之后 5 秒再重试。
3. 403 / 429 且以上都不满足（二级限流）→ 等 60 秒，之后按 120、240 秒指数退避，最多重试 3 次。
4. 502 / 503 / 504、网络超时 → 退避 2、4、8 秒，最多 3 次。
5. 401 → `GitHubAuthError`（不重试）。GraphQL `errors[].type == "NOT_FOUND"` → `GitHubNotFoundError`。其他 GraphQL 错误：消息包含 `timeout` 或 `Something went wrong` 视为暂时性错误（见 §4.3），否则 `GitHubQueryError`。
6. 每次 GraphQL 成功后读取 `rateLimit.remaining` 和 `resetAt`；`remaining < 200` 时主动等到 `resetAt` 之后 5 秒。
7. 单次请求累计等待超过 15 分钟 → `GitHubRateLimited`，任务失败，交给下一次定时同步。

每次等待都记一条日志（等待秒数、原因、剩余配额），不记请求头。

### 4.3 页大小自适应

`pull_requests_page` 遇到暂时性错误时，把本次的页大小减半（下限 5）用同一游标重试；成功后保持该页大小直到任务结束。下一个任务恢复到 `GRAPHQL_PAGE_SIZE`。

### 4.4 REST 条件请求

`rest_get` 默认使用 ETag：发送前从 Redis（`di:gh:etag:{sha256(完整 URL，含查询串)}`，`03` §3）取出 `etag`，带 `If-None-Match`；收到 304 时返回缓存的 body（304 不计入主限额）；收到 200 时更新缓存。

## 5. 规范化（`insights/sources/github/normalize.py`）

### 5.1 PR 字段

- `author` 为 `null`（已删除账号）时：`login=None`、`author_type="Unknown"`、`is_bot=False`。
- `author_type` 取 `author.__typename`（`User`、`Bot`、`Mannequin`，其他记为 `Unknown`）。
- `labels`、`files` 保持 GitHub 返回顺序，去重。

### 5.2 Bot 识别

`is_bot = __typename == "Bot" or login 以 "[bot]" 结尾 or login.lower().removesuffix("[bot]") in BOT_LOGINS`。

内置 `BOT_LOGINS`（小写）：`dependabot`、`renovate`、`github-actions`、`dotnet-maestro`、`dotnet-policy-service`、`msftbot`、`copilot`、`copilot-pull-request-reviewer`、`copilot-swe-agent`、`coderabbitai`、`azure-pipelines`、`codecov`、`mergify`、`pre-commit-ci`、`stale`，加上 `EXTRA_BOT_LOGINS`。

### 5.3 时间线映射

| `__typename` | EventKind | 说明 |
|---|---|---|
| `ReadyForReviewEvent` | `ready_for_review` | |
| `ConvertToDraftEvent` | `convert_to_draft` | |
| `ReviewRequestedEvent` | `review_requested` | reviewer 取 `User.login` 或 `Team.slug` |
| `ReviewRequestRemovedEvent` | `review_request_removed` | |
| `PullRequestReview` | `review` | `state == PENDING` 或 `submittedAt` 为空时丢弃；actor 为 review 作者。GitHub 的 `state` 是 review 的**当前**状态，被撤销的 review 显示为 `DISMISSED`：此时在同一 PR 的完整时间线中找 `review.id` 相同的 `ReviewDismissedEvent`，用它的 `previousReviewState` 作为 `payload.state`（提交时的决定），并置 `dismissed = true`；找不到对应的撤销事件时丢弃该 review 并记 debug 日志。这样重新回填也能还原"先批准、后被撤销"的历史 |
| `ReviewDismissedEvent` | `review_dismissed` | payload 记录 `review_id`、`review_author` 和 `previous_state`（取 `previousReviewState`） |
| `PullRequestCommit` | `commit` | actor 为 `Actor(None, False)`；从 `messageHeadline + messageBody` 中用 `This reverts commit ([0-9a-f]{7,40})` 提取 `reverts` |
| `HeadRefForcePushedEvent` | `force_push` | |
| `IssueComment` | `comment` | actor 为评论作者 |
| `LabeledEvent` / `UnlabeledEvent` | `labeled` / `unlabeled` | |
| `ClosedEvent` / `ReopenedEvent` / `MergedEvent` | `closed` / `reopened` / `merged` | |
| `CrossReferencedEvent` | `cross_referenced` | 只保留 `source.__typename == "PullRequest"` 的 |
| 其他 | 丢弃 | debug 日志 |

### 5.4 去重键与内容哈希

- `dedup_key = sha1(f"{kind}|{occurred_at.isoformat()}|{actor_login or ''}|{stable}").hexdigest()[:16]`，其中 `stable` 为时间线节点的全局 `id`（查询中的 `... on Node { id }`；review 的 `id` 也就是 `review_id`）。同一 PR 内 `dedup_key` 相同的事件只保留一条（`PullRequestTimeline` 补页可能与首页重叠）。不能用空串或只用时间和 actor：GitHub 在 push 后"撤销过时批准"时，会在同一秒、以同一 actor 为每个批准各生成一条撤销事件，它们必须都保留（`05` §2.5 第 22 条）。合成数据用 `f"syn-{PR 编号}-{事件序号}"` 作为 `stable`（`08` §2.1）。
- `content_hash` = 规范化 `PullRequestRecord`（转成 dict，键排序，时间转 ISO 字符串）的 orjson 字节的 sha256。

## 6. 同步任务（`insights/sync/jobs.py`、`worker.py`）

### 6.1 `WorkerSettings`

```python
class WorkerSettings:
    functions = [sync_repo, rederive_repo, precompute_snapshots, sync_ci_runs, sync_ownership]  # 后两个在 M8 加入
    cron_jobs = [
        cron(incremental_sync_all, minute=set(range(0, 60, settings.sync_interval_minutes))),
        cron(housekeeping, hour={3}, minute={17}),
    ]
    on_startup = startup     # 创建 engine、redis、GitHubClient；reconcile_tracked_repos；版本检查
    on_shutdown = shutdown
    max_jobs = 1
    job_timeout = 3 * 3600
    keep_result = 0          # 不保存任务结果，原因见下
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
```

- arq 在某个任务 ID 的结果仍被保存时，会拒绝再次入队同一 ID（`enqueue_job` 返回 `None`）。任务结果已经记录在 `sync_jobs` 表里，所以设 `keep_result = 0`，让 `_job_id` 去重只在任务排队或运行期间生效。
- 所有同步类任务都通过 §6.8 的 `enqueue_sync` 入队，任务函数签名统一为 `(ctx, repo_full_name, kind, job_id)`。

### 6.2 `startup`

1. `reconcile_tracked_repos`：`TRACKED_REPOS` 中的仓库 upsert 到 `repositories`（`tracked=True`），不在列表中的置 `tracked=False`（保留数据）。
2. 对 `tracked=True` 且回填未完成（`covered_since` 为空或晚于 `now - BACKFILL_DAYS`）的仓库调用 `enqueue_sync(kind="backfill")`。
3. **推导检查**：当前推导标识由 `insights.analytics.derive_key(LOCATION_DIMENSION, DIRECTORY_DEPTH)` 给出（`05` §1；即 `"{ANALYTICS_VERSION}|{THRESHOLDS_VERSION}|{LOCATION_DIMENSION}|depth={DIRECTORY_DEPTH}"`）。对 `tracked=True`、`covered_since` 非空且 `repositories.derived_key` 与当前标识不同（包括为空）的仓库（算法或阈值升级，位置维度、目录深度配置变了，或上一次重推导失败），调用 `enqueue_sync(kind="rederive")`。推导写入 `pr_facts` 时总是写当前标识；逐行的标识让中断后重跑的重推导可以跳过已完成的 PR。
4. 没有 `GITHUB_TOKEN` 时记录一条 error 日志，把所有仓库的 `last_sync_status` 设为 `missing_token`，并且不入队任何同步任务（`incremental_sync_all` 同样跳过同步）；第 3 步的重推导照常入队（它不访问 GitHub）。worker 继续运行，API 照常可用。

### 6.3 `sync_repo(ctx, repo_full_name, kind, job_id)`

1. 获取 Redis 锁 `di:lock:sync:{repo_lower}`（运行中每 10 分钟续期）；拿不到就把 `sync_jobs` 行标记为 `failed`（`error = "skipped: repository is locked by another job"`）并返回 `"skipped_locked"`。
2. 把 `job_id` 对应的 `sync_jobs` 行（入队时已创建，§6.8）更新为 `running`，写入 `started_at`；记 `job_start = now`。
3. **增量**（`sync_watermark` 非空时，**总是先做**，包括回填尚未完成的任务）：游标从 `None` 开始按 `updatedAt` 倒序翻页并保存，直到本页 `oldest_updated_at < sync_watermark - 10 分钟`（或没有下一页）；结束后 `sync_watermark` 更新为本次第一页的 `newest_updated_at`。先做增量，让回填期间的每个提交点都已包含任务开始前的最新数据。
4. **回填**（回填未完成时）：按阶段 `backfill_phases`（例如 7、30、180）从当前阶段继续：
   - 从 `backfill_cursor`（新仓库为 `None`）开始取页 → 规范化 → `store.save_page` → 更新 `backfill_cursor`；新仓库的第一页把 `newest_updated_at` 写入 `sync_watermark`；
   - 本页 `oldest_updated_at < now - target_days` 时，该阶段完成，`covered_since` 设为 `now - target_days`（截到分钟），进入下一阶段（游标保留，继续往更早翻）；`has_next_page` 为假时，`covered_since` 设为 `now - BACKFILL_DAYS`，回填完成；
   - 第一阶段（7 天）完成时，如果 `last_open_sweep_at` 为空，先执行一次开着的 PR 扫描（步骤 5），让风险 PR 从一开始就完整；
   - **每个阶段完成时**：执行一次**提交点收尾**（见下），其中一起提交 `covered_since`。这样 API 在长时间的首次回填期间就能用已完成的阶段回答请求（就绪条件见 `06` §5.1）。
5. **开着的 PR 扫描**：`last_open_sweep_at` 为空或早于 `OPEN_SWEEP_MINUTES` 时，用 `states=["OPEN"]` 翻完全部页并保存，更新 `last_open_sweep_at`。它能补上长期没有动静、`updatedAt` 早于覆盖范围的开着的 PR。
6. **结束**：执行一次提交点收尾（见下），其中一起提交 `last_sync_status = "ok"`。
7. 成功：`sync_jobs` 为 `succeeded`（附统计）；如果本次运行改变了 `data_version`，入队 `precompute_snapshots`（`_job_id=f"precompute:{repo_lower}"`）。worker 是串行的（`max_jobs = 1`），预计算在当前任务结束后才会运行；回填期间的请求由 API 按需计算。M8 之后再按需用 `enqueue_sync` 入队 `ci_runs`、`ownership`。
8. 失败：`GitHubAuthError` → `auth_error`；`GitHubNotFoundError` → `not_found`；其他 → `failed`。错误信息（异常类型和消息）截断到 500 字符后写入。始终释放锁。

**提交点收尾**（每个回填阶段完成时、任务成功结束时各执行一次；`last_synced_at` 只在这里写入）：

1. **收尾增量**：记 `t_c = now`；游标从 `None` 开始按 `updatedAt` 倒序翻页并保存（不改变 `backfill_cursor`），直到本页 `oldest_updated_at < t_prev - 10 分钟`（或没有下一页），其中 `t_prev` 是本任务上一次收尾增量的 `t_c`，没有则为 `job_start`；结束后 `sync_watermark` 更新为它第一页的 `newest_updated_at`。游标分页期间被更新的 PR 会移到列表前面，可能被正在进行的增量或回填跳过；收尾增量把它们补上（通常只有一页）。它自己运行的几秒内发生的更新留给下一轮。
2. 调用 `derive.link_repo(session, repo_id)`（处理该仓库全部 PR，`05` §4.3–§4.7）。
3. 在同一事务中提交 `last_synced_at = t_c`（"到这个时刻为止数据一致"，`05` §6.1）和本次要一起提交的字段；同时做**推导完整性检查**：该仓库不存在"推导未完成"的 PR（没有 `pr_facts` 行，或 `pr_facts.derive_key` 不等于当前标识）时，写入 `derived_key = 当前推导标识`；否则不写，并在事务提交后用 `enqueue_sync(kind="rederive")` 入队重推导。这个检查覆盖了首次回填中途配置变化、重推导在最后一步前失败等情况：只要还有旧标识的行，就不会发布快照。

### 6.4 `incremental_sync_all(ctx)`

对每个 `tracked=True` 的仓库调用 `enqueue_sync(kind="incremental")`（§6.8；同一仓库已有排队或运行中的同步时自动跳过；没有 token 时跳过）。然后做与 §6.2 第 3 步相同的推导检查，需要时入队 `rederive`（`_job_id` 去重；没有 token 时也做）：上一次重推导失败时，下一个定时周期会自动重试，API 不会一直停在 202。

### 6.5 `rederive_repo(ctx, repo_full_name, kind, job_id)`

开始时如果 `derived_key` 已等于当前标识、且没有推导未完成的 PR（§6.3 的推导完整性检查），直接成功返回。否则按 `pr_id` 键集分页、每批 500 个，选出推导未完成的 PR：`pull_requests LEFT JOIN pr_facts`，条件为 `pr_facts` 行不存在或 `derive_key IS DISTINCT FROM 当前标识`，且 `pr.id > 上一批最大 id`，按 `pr.id` 排序（不要用 OFFSET：已处理的行会离开结果集）。读取这些 PR 的事件、文件（M8 后含 CI 运行），重算 `pr_intervals` 和 `pr_facts`，每批单独提交。全部完成后调用 `derive.link_repo`，并在同一事务中写入 `repositories.derived_key = 当前推导标识`、`data_version + 1`（推导数据变了，旧快照不应再命中）。重推导期间 `derived_key` 仍是旧值，API 的就绪检查不通过、返回 202（`06` §5.1），所以逐批提交不会让快照读到新旧混合的推导结果。用于分析版本升级、位置维度或目录深度变化、owner 规则变化。`sync_jobs` 行的 `kind = "rederive"`。

### 6.6 `precompute_snapshots(ctx, repo_full_name)`

对 `PRECOMPUTE_DAYS` 中的每个 N：`to = 今天（UTC）`、`from = to - (N - 1)`；就绪条件满足时调用与 API 相同的快照服务（`insights/snapshot_service.py`，`06` §5.1）计算并保存，不满足就跳过。不写 `sync_jobs`。

### 6.7 `housekeeping(ctx)`

见 `03` §2.12。

### 6.8 入队辅助函数（`insights/sync/queue.py`）

API（手动同步）和 worker（启动、定时、同步后续任务）共用这一个函数。它只依赖数据库模型和 arq，**不导入** `insights.sources`。

```python
async def enqueue_sync(arq: ArqRedis, session: AsyncSession, repo_full_name: str, kind: str) -> tuple[SyncJob, bool]
```

| kind | arq 函数 | `_job_id` |
|---|---|---|
| `backfill`、`incremental`、`manual` | `sync_repo` | `sync:{repo_lower}` |
| `rederive` | `rederive_repo` | `rederive:{repo_lower}` |
| `ci_runs`（P1） | `sync_ci_runs` | `ci:{repo_lower}` |
| `ownership`（P1） | `sync_ownership` | `owners:{repo_lower}` |

1. `repositories` 中还没有该仓库时，按配置插入一行（与 `reconcile_tracked_repos` 相同的字段）。
2. 插入一行 `sync_jobs`（新 UUID，`status = "queued"`），提交。
3. `await arq.enqueue_job(function, repo_full_name, kind, str(job.id), _job_id=...)`。
4. 返回值不为 `None`：返回 `(新行, True)`。
5. 返回 `None`（同一仓库同类任务已在排队或运行）：删除刚插入的行，返回 `(同一 _job_id 前缀对应的 kind 中、该仓库最近一条 queued / running 的行，没有就取最近一条, False)`。

## 7. 存储（`insights/sync/store.py`）

`save_page(session, repo_id, page) -> SaveResult` 在**一个事务**里：

1. 批量查询本页 PR 现有的 `(number, id, content_hash)`。
2. 对新增或 `content_hash` 变化的 PR：upsert `pull_requests`（`ON CONFLICT (repo_id, number) DO UPDATE`），删除该 PR 的全部 `pr_events` 和 `pr_files` 后批量插入新的（时间线已完整，整体替换最简单、最一致）。哈希未变的 PR 跳过。
3. 有任何 PR 变化时，`repositories.data_version += 1`。
4. 对变化的 PR 调用 `derive.derive_prs(session, pr_ids)`（`05` §2–§3）。
5. 返回变化数、事件数等统计。

所有写入用批量语句（一次 `insert().values([...])` 或 executemany），不要逐行往返。

## 8. 冒烟命令（`insights/sources/github/smoke.py`）

`python -m insights.sources.github.smoke --repo OWNER/NAME --pages N`：只调用客户端和规范化，不写数据库，打印 `prs=… events=… rate_limit_remaining=…`。仓库参数用同一个正则校验。

## 9. GitHub Actions 运行记录（P1，M8）

- 任务 `sync_ci_runs(ctx, repo_full_name, kind, job_id)`：`CI_SOURCE == "actions"` 且距上次成功超过 1 小时时，在 `sync_repo` 结束后用 `enqueue_sync(kind="ci_runs")` 入队。
- 首次覆盖 `[covered_since, now]`，之后每次只拉最近 2 天。
- 按 UTC 自然日分窗请求：`GET /repos/{owner}/{repo}/actions/runs?created=YYYY-MM-DD&event=pull_request&per_page=100&page=N`（带 ETag）。**该接口在使用 `created`、`event` 等过滤参数时最多只返回 1,000 条**：当某天的 `total_count > 1000` 时，把这一天拆成 4 个 6 小时窗口，用 `created=YYYY-MM-DDTHH:MM:SSZ..YYYY-MM-DDTHH:MM:SSZ` 重新请求；仍超过 1,000 条时记录 warning 并接受截断。
- upsert 到 `workflow_runs`（按 `id`）。`pr_numbers` 取响应中 `pull_requests[].number`（来自 fork 的 PR 这里可能为空，所以映射时还要用 `head_sha` 匹配 PR 的 commit oid）。
- 存储后找出受影响的 PR（commit oid 或 PR 号匹配新增或变化的运行），重新推导，并在同一事务中把 `data_version` 加 1（`03` §1）。

## 10. 所有权规则（P1，M8，`ownership.py`）

- 任务 `sync_ownership(ctx, repo_full_name, kind, job_id)`：每天最多一次，在 `sync_repo` 结束后用 `enqueue_sync(kind="ownership")` 入队。
- **CODEOWNERS**：按 GitHub 的查找顺序依次请求 `.github/CODEOWNERS`、`CODEOWNERS`、`docs/CODEOWNERS`（`GET /repos/{owner}/{repo}/contents/{path}`，`Accept: application/vnd.github.raw+json`，带 ETag），第一个返回 200 的生效。逐行解析：去掉行内 `#` 注释（`\#` 为转义）、跳过空行；第一个 token 为模式，其余以 `@` 开头的 token 为 owner。按行号保存。
- **area owners**：请求 `AREA_OWNERS_PATH`（默认 `docs/area-owners.md`），404 时跳过。解析 Markdown 表格行：按 `|` 切分、去空白；第一个单元格匹配 `^area-[A-Za-z0-9._-]+$` 的行为一条规则；owner 为第 2、3 列中所有 `@[A-Za-z0-9._/-]+` token，去重并保持顺序。
- **CODEOWNERS** 规则内容（规范化后的哈希）变化时，位置可能变化（label 维度下没有 label 的 PR 也会回退到 CODEOWNERS）：在保存新规则的同一事务中把 `repositories.derived_key` 和该仓库全部 `pr_facts.derive_key` 置空（推导标识里不含规则内容，不置空的话 §6.5 会把所有 PR 当作已完成而跳过）、`data_version + 1`，再用 `enqueue_sync(kind="rederive")` 入队重推导。置空让 API 在重推导完成前返回 202，避免发布新旧规则混合的快照。
- 只有 **area owners** 变化时，位置不变，只影响构建快照时读取的 `owners_count`（`05` §6.2 第 7 项）：在保存规则的同一事务中 `data_version + 1` 即可，不重推导。
