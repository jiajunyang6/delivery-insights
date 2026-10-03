# 04 GitHub integration and synchronization

## 1. Overview

- All GitHub calls run in the worker; the API process does not import `insights.sources`.
- Authentication: `Authorization: Bearer <GITHUB_TOKEN>`. Recommend a fine-grained token with Repository access "Public repositories", no extra permissions (public read access is built in and GraphQL supports it). GitHub check-runs API supports fine-grained tokens without extra permissions on public repositories, but this implementation collects only GitHub Actions runs (§9). dotnet/runtime's main Azure Pipelines CI appears as check runs and is not collected; CI is incomplete (`CI_COMPLETE=false` by default, CI-hypothesis confidence capped at 0.5, `07` §4.3). Check-run collection is a future enhancement.
- Serial requests: worker `max_jobs = 1`; client `asyncio.Lock` allows one request in flight to avoid secondary limits.

## 2. Domain model (`insights/domain.py`) and adapter interface

### 2.1 Dataclasses

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
    full_name: str          # GitHub nameWithOwner
    default_branch: str
    is_archived: bool

@dataclass(frozen=True, slots=True)
class Actor:
    login: str | None       # None for deleted accounts
    is_bot: bool

@dataclass(frozen=True, slots=True)
class Event:
    kind: EventKind
    occurred_at: datetime   # UTC
    actor: Actor
    payload: dict[str, Any] # Keys by kind below; JSON-serializable values only
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
    events: tuple[Event, ...]   # Complete timeline, sorted by (occurred_at, kind, dedup_key)

@dataclass(frozen=True, slots=True)
class PageResult:
    repository: RepositoryInfo
    prs: tuple[PullRequestRecord, ...]
    end_cursor: str | None
    has_next_page: bool
    oldest_updated_at: datetime | None   # Minimum updatedAt on page
    newest_updated_at: datetime | None   # Maximum updatedAt on page
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

Event `payload` keys:

| kind | payload |
|---|---|
| `review` | `{"state": "APPROVED" \| "CHANGES_REQUESTED" \| "COMMENTED", "review_id": str, "dismissed": bool}`; state is the original decision **at submission** (§5.3) |
| `review_dismissed` | `{"review_id": str \| None, "review_author": str \| None, "previous_state": "APPROVED" \| "CHANGES_REQUESTED" \| None}` |
| `review_requested` | `{"reviewer": str \| None, "reviewer_type": "User" \| "Team" \| "Unknown"}` |
| `commit` | `{"oid": str, "authored_at": iso8601, "committed_at": iso8601, "reverts": [sha, ...]}` |
| `labeled` / `unlabeled` | `{"label": str}` |
| `cross_referenced` | `{"source_repo": str, "source_number": int, "source_state": str, "source_merged_at": iso8601 \| None, "source_author": str \| None, "will_close": bool}` |
| Other | `{}` |

### 2.2 Time fields

Commit events use `committedDate` as `occurred_at` (GitHub does not expose ordinary push time; document this approximation in README Limitations). Other events use `createdAt`; reviews use `submittedAt`.

### 2.3 `SourceAdapter`(`insights/sources/base.py`)

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

This is the only deliberate extension point: another source needs one adapter implementation, leaving sync/analytics unchanged. Explain in README.

## 3. GraphQL queries (`insights/sources/github/queries.py`; use verbatim)

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

- Include both fragment definitions in both query strings (concatenate constants).
- `$states=null` means no filtering; open sweeps use `["OPEN"]`.
- If a PR's `timelineItems.pageInfo.hasNextPage` is true, fetch `PullRequestTimeline` cursor pages until complete; do not persist the PR before then.
- Do not fetch files beyond 100; set `files_truncated=true`. Size uses additions/deletions/changedFiles and is unaffected.
- Estimated cost: 25 PRs per page, one request per nested connection, approximately 1–2 GitHub points, far below 5,000/hour.

## 4. Client (`insights/sources/github/client.py`)

### 4.1 Basics

- `httpx.AsyncClient`, `httpx.Timeout(connect=10, read=60, write=30, pool=10)`.
- Headers: `Authorization: Bearer …`, `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`, `User-Agent: delivery-insights/1.0`.
- Methods: `graphql(query: str, variables: dict) -> dict`, `rest_get(path: str, params: dict, *, accept: str | None = None) -> RestResponse`. Paths use code constants and validated owner/name only; reject full URLs.
- Injectable sleep (default `asyncio.sleep`); tests use a recording fake.

### 4.2 Rate limits and retries

Follow GitHub's recommended order:

1. `retry-after` present → wait its seconds, then retry.
2. `x-ratelimit-remaining=0` → wait until `x-ratelimit-reset` (epoch seconds) plus five seconds.
3. Other 403/429 (secondary limit) → wait 60, 120, 240 seconds; at most three retries.
4. 502/503/504 or network timeout → 2, 4, 8 seconds; at most three retries.
5. 401 → `GitHubAuthError`, no retry. GraphQL `errors[].type == "NOT_FOUND"` → `GitHubNotFoundError`. Other GraphQL messages containing `timeout` or `Something went wrong` are transient (§4.3); otherwise `GitHubQueryError`.
6. After successful GraphQL calls, read `rateLimit.remaining`/`resetAt`; below 200 proactively wait until reset plus five seconds.
7. Accumulated waiting above 15 minutes per request → `GitHubRateLimited`; fail job and defer to next scheduled sync.

Log every wait (seconds, reason, quota remaining), never request headers.

### 4.3 Adaptive page size

On transient `pull_requests_page` failure, halve page size (minimum five) and retry the same cursor. Keep the successful reduced size through the job; next job restores `GRAPHQL_PAGE_SIZE`.

### 4.4 Conditional REST requests

`rest_get` uses ETags by default: read Redis `di:gh:etag:{sha256(full URL including query)}` (`03` §3), send `If-None-Match`; on 304 return cached body (not counted against primary quota); on 200 refresh cache.

## 5. Normalization (`insights/sources/github/normalize.py`)

### 5.1 PR fields

- Null author (deleted account): `login=None`, `author_type="Unknown"`, `is_bot=False`.
- `author_type` uses `author.__typename`: User, Bot, Mannequin; otherwise Unknown.
- Deduplicate labels/files while preserving GitHub order.

### 5.2 Bot detection

`is_bot = __typename == "Bot" or login.endswith("[bot]") or login.lower().removesuffix("[bot]") in BOT_LOGINS`.

Built-in lowercase `BOT_LOGINS`: `dependabot`, `renovate`, `github-actions`, `dotnet-maestro`, `dotnet-policy-service`, `msftbot`, `copilot`, `copilot-pull-request-reviewer`, `copilot-swe-agent`, `coderabbitai`, `azure-pipelines`, `codecov`, `mergify`, `pre-commit-ci`, `stale`; extend with `EXTRA_BOT_LOGINS`.

### 5.3 Timeline mapping

| `__typename` | EventKind | Description |
|---|---|---|
| `ReadyForReviewEvent` | `ready_for_review` | |
| `ConvertToDraftEvent` | `convert_to_draft` | |
| `ReviewRequestedEvent` | `review_requested` | Reviewer is `User.login` or `Team.slug` |
| `ReviewRequestRemovedEvent` | `review_request_removed` | |
| `PullRequestReview` | `review` | Discard PENDING/null submittedAt. Actor is review author. GitHub state is **current**; DISMISSED reviews require matching `ReviewDismissedEvent` by review ID in the complete timeline. Use previousReviewState as submitted payload.state, set dismissed=true. If unmatched, discard with debug log. This reconstructs approval followed by dismissal even after backfill |
| `ReviewDismissedEvent` | `review_dismissed` | Payload: review_id, review_author, previous_state (previousReviewState) |
| `PullRequestCommit` | `commit` | Actor `Actor(None, False)`; extract reverts with `This reverts commit ([0-9a-f]{7,40})` from messageHeadline + messageBody |
| `HeadRefForcePushedEvent` | `force_push` | |
| `IssueComment` | `comment` | Actor is comment author |
| `LabeledEvent` / `UnlabeledEvent` | `labeled` / `unlabeled` | |
| `ClosedEvent` / `ReopenedEvent` / `MergedEvent` | `closed` / `reopened` / `merged` | |
| `CrossReferencedEvent` | `cross_referenced` | Keep only source.__typename == PullRequest |
| Other | Discard | Debug log |

### 5.4 Deduplication and content hashing

- `dedup_key = sha1(f"{kind}|{occurred_at.isoformat()}|{actor_login or ''}|{stable}").hexdigest()[:16]`; stable is the node's global ID (`... on Node { id }`; review ID equals review_id). Keep one event per dedup key per PR; timeline pagination can overlap. Do not use empty stable or just time/actor: automatic stale-approval dismissals can share both, and every dismissal must survive (`05` §2.5 case 22). Synthetic stable is `f"syn-{pr_number}-{event_index}"` (`08` §2.1).
- `content_hash`: SHA256 of orjson normalized PullRequestRecord dictionary with sorted keys and ISO timestamps.

## 6. Synchronization jobs (`insights/sync/jobs.py`, `worker.py`)

### 6.1 `WorkerSettings`

```python
class WorkerSettings:
    functions = [sync_repo, rederive_repo, precompute_snapshots, sync_ci_runs, sync_ownership]  # Last two added in M8
    cron_jobs = [
        cron(incremental_sync_all, minute=set(range(0, 60, settings.sync_interval_minutes))),
        cron(housekeeping, hour={3}, minute={17}),
    ]
    on_startup = startup     # Create engine/redis/GitHubClient; reconcile repos; check versions
    on_shutdown = shutdown
    max_jobs = 1
    job_timeout = 3 * 3600
    keep_result = 0          # Do not retain arq results; see below
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
```

- arq rejects duplicate job IDs while results are retained (`enqueue_job` returns None). Results already live in sync_jobs; `keep_result=0` limits deduplication to queued/running jobs.
- All sync-related jobs use §6.8 enqueue_sync and signature `(ctx, repo_full_name, kind, job_id)`.

### 6.2 `startup`

1. `reconcile_tracked_repos`: upsert TRACKED_REPOS with tracked=True; mark others False, retaining data.
2. Enqueue backfill for tracked repositories whose coverage is null or later than now - BACKFILL_DAYS.
3. **Derivation check**: current identity is `insights.analytics.derive_key(LOCATION_DIMENSION, DIRECTORY_DEPTH)` (`05` §1): `"{ANALYTICS_VERSION}|{THRESHOLDS_VERSION}|{LOCATION_DIMENSION}|depth={DIRECTORY_DEPTH}"`. For covered tracked repositories with different/null repositories.derived_key (version/config changes or failed rederivation), enqueue rederive. Facts always store current identity; row-level identities allow interrupted jobs to skip completed PRs.
4. Without GITHUB_TOKEN, log error, mark all repositories missing_token, and skip synchronization queues (including scheduled incremental). Still enqueue step-3 rederivation, which needs no GitHub access. Worker/API remain available.

### 6.3 `sync_repo(ctx, repo_full_name, kind, job_id)`

1. Acquire `di:lock:sync:{repo_lower}`, renewed every 10 minutes. On contention mark sync_jobs failed with `error = "skipped: repository is locked by another job"`, return `"skipped_locked"`.
2. Update the pre-created job row (§6.8) to running, set started_at; capture job_start=now.
3. **Incremental first**, always when sync_watermark exists, including unfinished backfill: paginate updatedAt descending from cursor None until oldest_updated_at < sync_watermark - 10 minutes or no next page. Publish first-page newest_updated_at as watermark. Each backfill checkpoint then includes pre-job updates.
4. **Backfill**, if unfinished: resume current backfill_phases stage (e.g. 7, 30, 180):
   - Fetch from saved backfill_cursor (None for new repo) → normalize → store.save_page → save cursor. First page of a new repo initializes watermark from newest_updated_at.
   - When oldest_updated_at < now - target_days, publish covered_since=now - target_days truncated to minutes; advance stage, retaining cursor. At end of pages, set coverage to now - BACKFILL_DAYS and complete backfill.
   - After first (seven-day) stage, if no last_open_sweep_at, perform step-5 open sweep so risk data is complete immediately.
   - **Every stage completion** runs **checkpoint finalization** below, publishing covered_since with it. The API can serve completed stages during a long initial backfill (`06` §5.1).
5. **Open sweep**: when last_open_sweep_at is null/older than OPEN_SWEEP_MINUTES, paginate all states=["OPEN"], save, update sweep time. Includes inactive open PRs with updatedAt before coverage.
6. **Finish**: checkpoint finalization also publishes last_sync_status="ok".
7. Success: mark sync_jobs succeeded with stats. If data_version changed, enqueue precompute_snapshots with `_job_id=f"precompute:{repo_lower}"`. Serial max_jobs=1 means precompute runs after sync; API computes on demand during backfill. From M8 enqueue ci_runs/ownership via enqueue_sync when needed.
8. Failure: GitHubAuthError → auth_error; GitHubNotFoundError → not_found; others → failed. Store exception type/message capped at 500 characters; always release lock.

**Checkpoint finalization** (each completed stage and successful job end; the only writer of last_synced_at):

1. **Catch-up incremental**: capture t_c=now. Paginate updatedAt descending from None without changing backfill_cursor, until oldest_updated_at < t_prev - 10 minutes or no next page; t_prev is this job's previous catch-up t_c, else job_start. Set watermark from first-page newest_updated_at. Updated PRs can move ahead of in-progress cursor scans; catch-up retrieves them, usually in one page. Updates during catch-up itself wait until next cycle.
2. Call derive.link_repo(session, repo_id) over all repository PRs (`05` §4.3–§4.7).
3. In one transaction publish last_synced_at=t_c (consistent through this point, `05` §6.1) plus checkpoint fields; verify **derivation completeness**: only set derived_key=current when no PR lacks facts or current fact identity. Otherwise retain old key and enqueue rederive after commit. Covers mid-backfill config changes and rederivation failure before final publication; any old row blocks snapshots.

### 6.4 `incremental_sync_all(ctx)`

For each tracked repository enqueue incremental (§6.8; skip queued/running syncs and missing tokens). Also run §6.2 step-3 derivation checks and enqueue rederive as needed (job-ID dedup; even without token). Failed rederivation retries on the next scheduled cycle rather than leaving API permanently at 202.

### 6.5 `rederive_repo(ctx, repo_full_name, kind, job_id)`

If repository identity is current and all PRs pass completeness (§6.3), succeed immediately. Otherwise select unfinished PRs via pull_requests LEFT JOIN pr_facts: missing facts or derive_key IS DISTINCT FROM current, pr.id > last batch maximum, ordered by ID, 500 per batch. No OFFSET: processed rows leave the result set. Load events/files and, from M8, CI; recompute intervals/facts, commit each batch. Finally link repository and publish current repositories.derived_key plus data_version+1 together. Until then the old key makes API return 202 (`06` §5.1), preventing mixed derivations in snapshots. Handles algorithm/location/depth/owner changes; job kind=rederive.

### 6.6 `precompute_snapshots(ctx, repo_full_name)`

For each PRECOMPUTE_DAYS N: to=today UTC, from=to-(N-1). If ready, compute/persist through shared insights/snapshot_service.py (`06` §5.1); otherwise skip. No sync_jobs row.

### 6.7 `housekeeping(ctx)`

See `03` §2.12.

### 6.8 Enqueue helper (`insights/sync/queue.py`)

Shared by API manual sync and worker startup/schedule/follow-ups. Depends only on database models and arq; **does not import** insights.sources.

```python
async def enqueue_sync(arq: ArqRedis, session: AsyncSession, repo_full_name: str, kind: str) -> tuple[SyncJob, bool]
```

| kind | arq function | `_job_id` |
|---|---|---|
| `backfill`, `incremental`, `manual` | `sync_repo` | `sync:{repo_lower}` |
| `rederive` | `rederive_repo` | `rederive:{repo_lower}` |
| `ci_runs`(P1) | `sync_ci_runs` | `ci:{repo_lower}` |
| `ownership`(P1) | `sync_ownership` | `owners:{repo_lower}` |

1. If repository absent, insert from configuration using reconcile_tracked_repos fields.
2. Insert sync_jobs with new UUID/status=queued and commit.
3. `await arq.enqueue_job(function, repo_full_name, kind, str(job.id), _job_id=...)`.
4. Non-None result: return (new row, True).
5. None (same repository/job class queued or running): delete new row; return (latest queued/running repository row among kinds sharing the _job_id prefix, else latest row, False).

## 7. Storage (`insights/sync/store.py`)

`save_page(session, repo_id, page) -> SaveResult` in **one transaction**:

1. Bulk-read existing (number, id, content_hash) for page PRs.
2. New/changed content_hash: upsert pull_requests (`ON CONFLICT (repo_id, number) DO UPDATE`), replace all events/files with bulk inserts (complete timeline replacement is simplest). Skip unchanged hashes.
3. If any PR changed, increment repositories.data_version.
4. Derive changed PRs with derive.derive_prs(session, pr_ids) (`05` §2–§3).
5. Return changed/event counts and other stats.

Use batch statements (`insert().values([...])` or executemany), never per-row round trips.

## 8. Smoke CLI (`insights/sources/github/smoke.py`)

`python -m insights.sources.github.smoke --repo OWNER/NAME --pages N`: client/normalization only, no database writes; print prs/events/quota. Validate repository with the shared regex.

## 9. GitHub Actions runs (P1, M8)

- sync_ci_runs(ctx, repo_full_name, kind, job_id): when CI_SOURCE="actions" and last success was over an hour ago, enqueue ci_runs after sync_repo.
- First run covers [covered_since, now]; later runs fetch only the last two days.
- Request UTC-day windows: `GET /repos/{owner}/{repo}/actions/runs?created=YYYY-MM-DD&event=pull_request&per_page=100&page=N` with ETag. **Filtering by created/event limits results to 1,000**. For total_count>1000 split into four six-hour windows using created=YYYY-MM-DDTHH:MM:SSZ..YYYY-MM-DDTHH:MM:SSZ; if still above 1,000 warn and accept truncation.
- Upsert workflow_runs by id. pr_numbers comes from pull_requests[].number; fork responses can omit it, so also map head_sha to PR commit OIDs.
- Find PRs affected by new/changed runs via commit OID or number; rederive and increment data_version in the same transaction (`03` §1).

## 10. Ownership rules (P1, M8, `ownership.py`)

- sync_ownership(ctx, repo_full_name, kind, job_id): enqueue ownership after sync_repo at most once daily.
- **CODEOWNERS**: request .github/CODEOWNERS, CODEOWNERS, docs/CODEOWNERS in GitHub order (`GET /repos/{owner}/{repo}/contents/{path}`, `Accept: application/vnd.github.raw+json`, ETag); first 200 wins. Strip inline # comments (escaped \# preserved), skip blanks; first token is pattern, remaining @ tokens are owners. Save line order.
- **Area owners**: fetch AREA_OWNERS_PATH (default docs/area-owners.md); skip 404. Split Markdown rows by | and trim cells; first cell matching ^area-[A-Za-z0-9._-]+$ defines a rule. Deduplicate @ tokens matching @[A-Za-z0-9._/-]+ from columns two/three, preserving order.
- **CODEOWNERS** normalized hash changes can alter locations, including unlabeled PRs in label mode. In the rule-write transaction clear repositories.derived_key and all repository pr_facts.derive_key (identity omits rule contents, so retaining row keys would make §6.5 skip everything), increment data_version; enqueue rederive. Clearing blocks API with 202 until rules are fully applied.
- **Area owners only**: locations unchanged; snapshot owners_count changes (`05` §6.2 item 7). Increment data_version in the rule-write transaction; no rederivation.
