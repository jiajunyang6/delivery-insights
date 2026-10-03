# 06 API 契约

本文件是对外契约，优先级最高（`AGENTS.md` §2）。字段名、状态码、头部和错误类型必须与本文件一致；Pydantic 响应模型放在 `insights/api/schemas.py`。

## 1. 总则

- 业务接口都在 `/v1` 下；健康检查 `/healthz`、`/readyz` 不带前缀。
- 请求和响应都是 UTF-8 JSON，字段名 `snake_case`。时间格式 `YYYY-MM-DDTHH:MM:SSZ`（UTC），日期格式 `YYYY-MM-DD`。数字的取整规则见 `05` §12.2。
- OpenAPI：`/openapi.json`，交互文档 `/docs`。`info.title = "Delivery Insights API"`，`info.version = insights.__version__`。
- API 进程从不调用 GitHub，也不导入 `insights.sources`。它读取 Postgres 和 Redis；写入只有快照、叙述和缓存，以及手动同步时的 `sync_jobs` 行和 arq 任务（`04` §6.8）。

| 方法与路径 | 用途 | 状态码 | 缓存 |
|---|---|---|---|
| `GET /v1/insights/delivery` | 指定周期的 insight 快照（Endpoint 1） | 200、202、304、403、422、503 | `private, max-age=60` + ETag |
| `GET /v1/insights/delivery/prs` | PR 明细：阶段与等待时长，可筛选、分页 | 200、202、403、422、503 | `private, max-age=60` |
| `GET /v1/snapshots/{snapshot_id}` | 按 ID 读取不可变快照 | 200、304、404、422 | `private, max-age=86400, immutable` + ETag |
| `GET /v1/snapshots/{snapshot_id}/narrative` | LLM 叙述（Endpoint 2） | 200、304、404、422 | 见 §5.4 |
| `GET /v1/repos` | 白名单仓库及同步状态 | 200 | `no-store` |
| `POST /v1/repos/{owner}/{name}/sync` | 手动触发一次同步 | 202、403、422、429、503 | `no-store` |
| `GET /v1/sync-jobs/{job_id}` | 同步任务状态 | 200、404、422 | `no-store` |
| `GET /healthz` | 存活检查（不检查依赖） | 200 | `no-store` |
| `GET /readyz` | 就绪检查（Postgres、Redis） | 200、503 | `no-store` |

所有 `/v1` 接口都可能返回 429（限流，§2.6）和 500（未处理异常，§2.3）。

## 2. 通用约定

### 2.1 请求 ID

- 请求头 `X-Request-ID` 匹配 `^[A-Za-z0-9._-]{1,64}$` 时沿用，否则生成 `uuid4().hex`。
- 响应头始终带 `X-Request-ID`；它同时写进访问日志（structlog contextvars）和 problem 响应的 `request_id` 字段。

### 2.2 序列化

- 快照接口直接返回规范化字节（`05` §12.2），不再经过 Pydantic 序列化，保证 ETag 与字节对应。路由仍声明 `response_model=Snapshot`，让 OpenAPI 文档完整（FastAPI 对直接返回的 `Response` 不做二次校验）。测试用 `Snapshot.model_validate` 校验结构。
- 其他接口用 Pydantic 模型序列化（`model_config = ConfigDict(extra="forbid")`）。
- 不启用 GZip 中间件（避免 ETag 与传输字节不一致；本地服务不需要）。

### 2.3 错误：problem+json（RFC 9457）

所有 4xx/5xx（304 除外）返回 `Content-Type: application/problem+json`：

```json
{
  "type": "/problems/invalid-parameter",
  "title": "Invalid parameter",
  "status": 422,
  "detail": "'from' must not be later than 'to'.",
  "instance": "/v1/insights/delivery",
  "request_id": "6f1c0e8a9b2d4c7e8f0a1b2c3d4e5f60",
  "errors": [{"param": "from", "message": "must not be later than 'to'"}]
}
```

| `type` | status | title | 何时使用 |
|---|---|---|---|
| `/problems/invalid-parameter` | 422 | Invalid parameter | 参数、路径参数或游标不合法；`errors` 列出每个问题 |
| `/problems/not-tracked` | 403 | Repository not tracked | 仓库或组织不在 `TRACKED_REPOS` 中；扩展字段 `repos` 列出未跟踪的仓库名（已通过白名单正则） |
| `/problems/not-found` | 404 | Not found | 快照、同步任务不存在；未知路由 |
| `/problems/method-not-allowed` | 405 | Method not allowed | 方法不支持 |
| `/problems/rate-limited` | 429 | Too many requests | API 限流（§2.6），带 `Retry-After` |
| `/problems/sync-cooldown` | 429 | Sync recently requested | 手动同步冷却中，带 `Retry-After` |
| `/problems/data-unavailable` | 503 | Data unavailable | 所需周期没有数据，且同步无法进行（`missing_token`、`auth_error`、`not_found`）；扩展字段 `repos` 列出各仓库的 `last_sync_status` |
| `/problems/dependency-unavailable` | 503 | Dependency unavailable | Postgres 或 Redis 不可用 |
| `/problems/internal-error` | 500 | Internal server error | 未处理异常；`detail` 固定为 `"An unexpected error occurred."` |

规则：

- `detail` 和 `errors[].message` **不回显参数原值**（防日志注入和反射内容）。只有已经通过白名单正则的仓库名可以出现在扩展字段里。
- 不暴露堆栈、SQL、上游响应体或异常原文；堆栈和异常信息只写进服务端日志（日志不记录请求头和完整的上游响应体，`02` §4）。
- 实现：`insights/api/errors.py` 定义 `ProblemError(status, type_slug, title, detail, errors=None, headers=None, extensions=None)` 和处理器；同时接管 `RequestValidationError`（转成 422，`errors[].param` 取 `loc` 的最后一段）、Starlette 的 404/405 和未处理异常。

### 2.4 缓存与条件请求

- **ETag**：强 ETag，`'"' + sha256(响应字节).hexdigest()[:32] + '"'`。快照的 ETag 在生成时计算并保存（`snapshots.etag`、Redis 哈希的 `etag` 字段），之后直接复用。
- **If-None-Match**：支持逗号分隔的多个值、`W/` 前缀（弱比较：去掉 `W/` 后比较）和 `*`。匹配时返回 304，响应头只带 `ETag`、`Cache-Control`、`X-Request-ID`（以及 insights 的 `X-Snapshot-Id`、`Content-Location`），无响应体。
- `GET /v1/insights/delivery`：先算出 `snapshot_id`，如果 Redis 里已有该快照，用 `HGET etag` 先比较，命中就直接 304，不读响应体。
- 各接口的 `Cache-Control` 见 §1 的表格。

### 2.5 分页（只用于 `/v1/insights/delivery/prs`）

- 参数：`limit`（整数 1–200，默认 50）、`cursor`（不透明字符串）。
- 响应：`{"items": [...], "next_cursor": "…" | null, "total": 123, ...}`。
- 游标 = base64url（无填充）编码的 `orjson.dumps({"v": 1, "sid": snapshot_id, "o": offset, "f": filters_hash})`；`filters_hash = sha256(规范化的筛选参数)[:12]`。
- 解码时依次校验：匹配 `^[A-Za-z0-9_-]{1,512}$` → base64url 解码 → JSON 对象 → 键和类型正确 → `o >= 0`。任一失败返回 422（`param = "cursor"`）。
- `sid` 与本次请求算出的 `snapshot_id` 不同（数据已经更新），或 `f` 与本次筛选参数不符：返回 422，`message = "cursor is no longer valid; restart from the first page"`。

### 2.6 限流

- 只作用于 `/v1/*`。固定窗口：`INCR di:rl:{client_ip}:{epoch_minute}`，第一次计数时 `EXPIRE 70`；超过 `RATE_LIMIT_PER_MINUTE` 返回 429 `/problems/rate-limited`，`Retry-After` = 距下一分钟的秒数（至少 1）。
- 每个 `/v1` 响应带 `X-RateLimit-Limit`、`X-RateLimit-Remaining`。
- `client_ip` 取 `request.client.host`。不自行解析 `X-Forwarded-For`；uvicorn 的 `--proxy-headers` 默认只信任 `127.0.0.1` 的代理头，所以经由前端 nginx 的请求共享 nginx 的 IP、共用一个限流桶。这是本地部署可以接受的取舍，写进 README。
- Redis 不可用时**放行**（记录 warning），不因限流器故障拒绝服务。

### 2.7 CORS 与安全响应头

- CORS：只允许 `CORS_ORIGINS`；方法 `GET`、`POST`；允许请求头 `If-None-Match`、`Content-Type`、`X-Request-ID`；暴露响应头 `ETag`、`Retry-After`、`Location`、`Content-Location`、`X-Request-ID`、`X-Snapshot-Id`；`allow_credentials=False`。
- 所有响应加 `X-Content-Type-Options: nosniff`。

### 2.8 依赖故障

| 故障 | 处理 |
|---|---|
| Postgres 不可用（连接错误、超时） | 503 `/problems/dependency-unavailable` |
| Redis 不可用：缓存读写 | 记录 warning，跳过缓存继续（计算或读 Postgres） |
| Redis 不可用：限流 | 放行（§2.6） |
| Redis 不可用：手动同步入队 | 503 `/problems/dependency-unavailable` |
| GitHub 不可用 | 不影响 API；`meta.data_freshness` 显示最后同步时间和状态 |
| Bedrock 不可用 | 叙述降级为模板，仍返回 200（`07` §9） |

## 3. 参数

### 3.1 仓库与组织

```python
REPO_RE  = r"^(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/(?P<name>(?!\.{1,2}$)[A-Za-z0-9._-]{1,100})$"
OWNER_RE = r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$"
NAME_RE  = r"^(?!\.{1,2}$)[A-Za-z0-9._-]{1,100}$"
```

- `repo`：可重复（`?repo=a/b&repo=c/d`），每个值匹配 `REPO_RE`；按小写去重，保留请求中的第一次出现。
- `org`：匹配 `OWNER_RE`，解析为 `TRACKED_REPOS` 中 owner 相同（不区分大小写）的全部仓库。
- `repo` 与 `org` **必须且只能提供一个**，否则 422。
- 解析后仓库数超过 `MAX_REPOS_PER_REQUEST`：422。
- 白名单判断以配置 `TRACKED_REPOS` 为准（不依赖 `repositories` 表是否已有行）。有任何仓库不在白名单：403 `/problems/not-tracked`；`org` 没有对应的白名单仓库：403。
- 路径参数 `{owner}`、`{name}` 分别匹配 `OWNER_RE`、`NAME_RE`。
- 返回和快照中的仓库名使用 `TRACKED_REPOS` 中的原始写法。

### 3.2 日期

- `from`、`to`：严格匹配 `^\d{4}-\d{2}-\d{2}$` 后用 `date.fromisoformat` 解析。
- 默认：`to` = 今天（UTC），`from` = `to - 29 天`（30 天周期）。
- 规则（全部违反项一起放进 `errors`）：`from <= to`；`(to - from).days + 1 <= 366`；`to <= 今天（UTC）`；`from >= 今天 - BACKFILL_DAYS 天`（更早的数据不会被同步）。
- 对比周期自动取紧邻的上一个等长周期（`05` §6.1），不接受参数。

### 3.3 其他参数

| 参数 | 适用接口 | 取值 | 默认 |
|---|---|---|---|
| `status` | `/prs` | `merged`、`closed`、`open` | `merged` |
| `at_risk` | `/prs` | `true`、`false` | `false`；为 `true` 时只返回风险 PR，且 `status` 必须是 `open` 或省略（省略时视为 `open`） |
| `state` | `/prs` | `waiting_reviewer`、`waiting_author`、`waiting_ci`、`waiting_merge` | 无；只能与 `status=open`（或 `at_risk=true`）一起用，否则 422 |
| `location` | `/prs` | 匹配 `^[^\x00-\x1f\x7f]{1,200}$`，与 PR 的 `locations` 精确匹配 | 无 |
| `limit`、`cursor` | `/prs` | §2.5 | |
| `audience` | narrative | `director`、`manager` | `manager` |
| `lang` | narrative | `en`、`zh` | `en` |
| `snapshot_id` | 路径 | `^s_[0-9a-f]{16}$` | |
| `job_id` | 路径 | 规范 UUID（小写、带连字符） | |

未知的查询参数忽略。

## 4. 快照结构（`Snapshot`）

快照由 `05` 中的纯函数计算，结构如下。除特别说明外，"上一周期"指 `period.compared_to`；`comparison_available = false` 时所有上一周期字段为 `null`。所有列表的排序规则见对应的 `05` 小节。

### 4.1 顶层

| 字段 | 类型 | 说明 |
|---|---|---|
| `snapshot_id` | string | `05` §12.3 |
| `repos` | string[] | 排序后的仓库显示名 |
| `period` | object | `{"from", "to", "days", "complete", "compared_to": {"from", "to"}}`；`complete = (as_of == to_excl)`，为 `false` 表示同步只到 `as_of`，请求周期的末尾还没有被观察到（`05` §6.1）；`to` 是今天时周期还没结束，`complete` 总是 `false` |
| `as_of` | timestamp | `05` §6.1 |
| `headline` | string | `05` §11 |
| `efficiency` | object | §4.4 |
| `time_ledger` | object | §4.5 |
| `bottlenecks` | Finding[] | 排序后的发现，§4.6；当前周期合并 PR 少于 20 个时为空数组（`05` §11） |
| `bottleneck_analysis` | object | §4.7 |
| `drivers` | object \| null | P1，§4.12；P0 为 `null` |
| `at_risk_prs` | AtRiskPr[] | §4.8，最多 `AT_RISK_MAX_ITEMS` 条 |
| `at_risk_summary` | object | §4.8 |
| `waste`、`rework`、`guardrail` | object | §4.9 |
| `trend` | object | §4.10 |
| `signals` | object | §4.11 |
| `series` | object | §4.11 |
| `per_repo` | object[] \| null | 单仓库时为 `null`，§4.11 |
| `links` | object | `{"self": "/v1/snapshots/{id}", "narrative": "/v1/snapshots/{id}/narrative"}` |
| `meta` | object | §4.3 |

### 4.2 Metric 对象

所有"指标"都用同一个结构，当前值与上一周期值由同一个函数计算：

```json
{
  "value": 41.25,
  "unit": "hours",
  "n": 512,
  "previous": 35.1,
  "n_previous": 498,
  "change_abs": 6.15,
  "change_rel": 0.1752,
  "significant": true,
  "status": "ok",
  "extra": {}
}
```

| 字段 | 说明 |
|---|---|
| `value` | 当前值；样本不足时为 `null` |
| `unit` | `hours`、`minutes`、`count`、`share`（0–1）、`ratio`、`lines`、`rounds`、`coefficient`、`change`（相对变化，0.25 表示 +25%） |
| `n`、`n_previous` | 计算该值使用的样本数（比率为分母） |
| `previous` | 上一周期的值；不可用或样本不足为 `null` |
| `change_abs` | `value - previous`；任一为空则 `null` |
| `change_rel` | `change_abs / previous`；`previous` 为 0 或空则 `null` |
| `significant` | `05` §10；未计算（不适用、缺上一周期、样本不足）为 `null` |
| `status` | `ok` 或 `insufficient_sample`（`value` 为 `null` 时） |
| `extra` | 指标特有字段，例如 `{"k": 2}`、`{"n_days": 3}`、`{"events": 3, "denominator": 41}`；没有时为 `{}` |

### 4.3 `meta`

```json
{
  "schema_version": "1",
  "analytics_version": "1.0.0",
  "thresholds_version": "1.0.0",
  "location_dimension": "label:area-",
  "time_basis": "utc_wall_clock",
  "comparison_available": true,
  "ci_source": "actions",
  "data_freshness": [
    {"repo": "dotnet/runtime", "data_version": 1234, "covered_since": "2026-04-05T09:00:00Z",
     "last_synced_at": "2026-10-02T09:45:12Z", "last_sync_status": "ok"}
  ],
  "sample": {"merged_prs": 812, "closed_unmerged_prs": 141, "ready_prs": 968,
             "open_prs_at_as_of": 655, "human_reviews": 3120},
  "excluded": {"bot_prs": 214, "backport_prs": 97, "never_ready_drafts": 23},
  "location_sources": {"label": 702, "codeowners": 0, "directory": 96, "unclassified": 14}
}
```

- `data_freshness`：每个仓库一项，按仓库名排序；这一列表同时是 `versions_hash` 的输入（`05` §12.3）。
- `sample`：`merged_prs` = 当前周期合并的流程 PR 数；`closed_unmerged_prs` = 当前周期关闭未合并的流程 PR 数；`ready_prs` = `ready_at` 落在当前周期的流程 PR 数；`open_prs_at_as_of` = `as_of` 时"开着"（`05` §2.7，处于关闭期的不算）的流程 PR 数；`human_reviews` = 当前周期流程 PR 上的人工 review 数。
- `excluded`：当前周期合并或关闭的 bot 作者 PR、backport PR 数，以及当前周期关闭的、从未 ready 的 draft PR 数（它们不在 review 流程中，`05` §4.1）；一个 PR 只计入一类，顺序为 bot → backport → never_ready_draft（`05` §6.2 第 5 项）。
- `location_sources`：当前周期合并的流程 PR 按 `location_source` 计数。
- `ci_source`：配置 `CI_SOURCE`。

### 4.4 `efficiency`

键与 `05` §7 一一对应，值都是 Metric：

```json
{
  "merged_prs": Metric, "effective_throughput": Metric,
  "cycle_time_p50_hours": Metric, "cycle_time_p90_hours": Metric,
  "stage_p50_hours": {"coding": Metric, "pickup": Metric, "review": Metric, "merge": Metric},
  "merged_within_n_days": Metric, "waiting_share": Metric, "waste_share": Metric,
  "avg_review_rounds": Metric, "post_review_commit_share": Metric,
  "review_concentration_top_k": Metric, "revert_rate": Metric, "pr_size_p50_lines": Metric,
  "predictability": null, "survival": null
}
```

`predictability`、`survival` 在 P1（M9）填充，结构见 §4.12。

### 4.5 `time_ledger`

```json
{
  "scope": "merged_prs",
  "merged_prs": 812, "previous_merged_prs": 798,
  "total_pr_hours": 29012.5, "previous_total_pr_hours": 25110.0,
  "states": {
    "waiting_reviewer": {"pr_hours": 12185.3, "previous_pr_hours": 8790.1, "share": 0.42, "previous_share": 0.3501, "change_pp": 6.99},
    "waiting_author":   {"pr_hours": 9284.0, "previous_pr_hours": 9541.8, "share": 0.32, "previous_share": 0.38, "change_pp": -6.0},
    "waiting_ci":       {"pr_hours": 0.0, "previous_pr_hours": 0.0, "share": 0.0, "previous_share": 0.0, "change_pp": 0.0},
    "waiting_merge":    {"pr_hours": 7543.2, "previous_pr_hours": 6778.1, "share": 0.26, "previous_share": 0.2699, "change_pp": -0.99}
  },
  "ci_coverage": 0.0,
  "ci_data_available": false
}
```

定义见 `05` §8。`change_pp` 保留 2 位小数。

### 4.6 `bottlenecks`（Finding）

```json
{
  "id": "review_capacity:area-System.Net.Http",
  "rank": 1,
  "type": "review_capacity",
  "severity": "medium",
  "title": "First-review wait concentrated in area-System.Net.Http",
  "location": "area-System.Net.Http",
  "impact_pr_hours": 2460.5,
  "impact_share": 0.0848,
  "evidence": [
    {"label": "First-review wait vs rest of repo", "value": 2.4, "unit": "ratio",
     "ref": "/bottleneck_analysis/locations/0/pickup_ratio_vs_rest"},
    {"label": "Share of reviewer-waiting time", "value": 0.2019, "unit": "share",
     "ref": "/bottleneck_analysis/locations/0/waiting_reviewer_share"}
  ],
  "recommendation": "Add reviewers or code owners for area-System.Net.Http, enable team auto-assignment, and set a one-business-day first-review SLA.",
  "what_if": {"stage": "pickup", "location": "area-System.Net.Http", "target_hours": 8.0, "affected_prs": 37,
              "cycle_p50_before_hours": 41.25, "cycle_p50_after_hours": 36.9, "change_rel": -0.1055}
}
```

- `type` ∈ `review_capacity`、`review_queue_growth`、`review_concentration`、`merge_blocked`、`ci_wait`（P1）、`rework_high`、`waste_high`、`quality_guardrail`、`external_contributor_wait`；规则见 `05` §11。
- `severity` ∈ `high`、`medium`、`low`；`location` 没有时为 `null`；`impact_share = impact_pr_hours / time_ledger.total_pr_hours`（总量为 0 时为 0）。
- `evidence[].ref` 是指向本快照内字段的 JSON Pointer（RFC 6901）；指向 Metric 时指向对象本身。
- `what_if` 结构与 §4.7 的 `what_if` 条目相同，没有时为 `null`。

### 4.7 `bottleneck_analysis`

```json
{
  "review_queue": {
    "weeks": [{"week_start": "2026-09-03", "days": 4, "inflow": 120, "outflow": 98, "open_at_week_end": 410}],
    "weeks_total": 5,
    "weeks_inflow_exceeds_outflow": 4,
    "open_growth_rel": 0.2195,
    "net_inflow_share": 0.20
  },
  "locations": [
    {"location": "area-System.Net.Http", "merged_prs": 41, "pickup_p50_hours": 30.5, "pickup_ratio_vs_rest": 2.4,
     "waiting_reviewer_pr_hours": 2460.5, "previous_waiting_reviewer_pr_hours": 1102.0, "waiting_reviewer_share": 0.2019,
     "inflow": 52, "outflow": 40, "at_risk_prs": 6, "owners_count": null}
  ],
  "merge_blockers": {"approved_merged_prs": 640, "second_approval_share": 0.31, "second_approval_wait_p50_hours": 5.2,
                     "post_approval_update_share": 0.22, "ci_after_approval_p50_hours": null},
  "pareto": [{"cause": "waiting_reviewer", "location": "area-System.Net.Http", "pr_hours": 2460.5, "share": 0.0848}],
  "what_if": [{"stage": "pickup", "location": null, "target_hours": 8.0, "affected_prs": 301,
               "cycle_p50_before_hours": 41.25, "cycle_p50_after_hours": 33.1, "change_rel": -0.1976}],
  "review_load": {"reviewers": 85, "reviews": 3120,
                  "distribution": [{"reviewer": "octocat", "reviews": 240, "share": 0.0769}]},
  "ci": null
}
```

- `review_queue`：`05` §9.2；`net_inflow_share` 为本期 `(Σinflow − Σoutflow) / Σinflow`（无 inflow 时为 `null`，可以为负）；存量与存量增长仅作本期集合图表，不触发发现。`locations`：`05` §9.1；`merge_blockers`：`05` §9.3；`pareto`：`05` §9.4（`location` 只在 `cause = waiting_reviewer` 时有值）；`what_if`：`05` §9.5 的全仓库条目（`location = null`）；`review_load`：`05` §9.8；`ci`：P1，本文件 §4.12。

### 4.8 `at_risk_prs`、`at_risk_summary`

```json
{
  "repo": "dotnet/runtime", "number": 108123, "title": "…", "url": "https://github.com/dotnet/runtime/pull/108123",
  "author": "octocat", "state": "waiting_reviewer", "age_hours": 212.4,
  "threshold_hours": 70.1, "critical_threshold_hours": 160.3, "severity": "critical",
  "baseline_source": "90d", "locations": ["area-System.Net.Http"], "external_contributor": true, "size_lines": 84
}
```

`author` 为 `string | null`（作者账号已删除时为 `null`）。`at_risk_summary = {"total": 61, "critical": 14, "by_state": {"waiting_reviewer": 38, "waiting_author": 15, "waiting_ci": 0, "waiting_merge": 8}}`，`by_state` 固定包含四个等待状态。定义见 `05` §9.6。

### 4.9 `waste`、`rework`、`guardrail`

```json
"waste": {"closed_unmerged": 141, "by_class": {"superseded": 30, "rejected": 41, "abandoned": 38, "no_review": 32},
          "lost_while_waiting": 44, "late_rejections": 9, "wasted_review_share": 0.071, "wasted_pr_hours": 5210.4},
"rework": {"reverts": 6, "revert_prs": 6, "relanded": 3,
           "revert_chains": [{"original": {"number": 107001, "url": "…"}, "revert": {"number": 107050, "url": "…"},
                              "reland": null, "exposure_hours": 30.2, "revert_pr_cycle_hours": 2.1}]},
"guardrail": {"cycle_time_p50_change_rel": 0.1752, "revert_rate": 0.0074, "previous_revert_rate": 0.005,
              "revert_rate_change_pp": 0.24, "verdict": "ok"}
```

定义见 `05` §9.9。`revert_prs` = 当前周期合并的 revert PR 数。`verdict` ∈ `ok`、`watch`、`tradeoff_suspected`。

### 4.10 `trend`

```json
{
  "bottleneck_shift": "waiting_reviewer share +7.0pp vs previous period",
  "attribution": {
    "basis": "mean_hours_per_merged_pr",
    "cycle_mean_hours": {"current": 57.83, "previous": 52.47, "change": 5.36},
    "total_increase_hours": 5.89,
    "total_decrease_hours": 0.52,
    "states": {
      "coding":           {"current": 22.1, "previous": 21.0, "change": 1.1, "share_of_increase": 0.1868, "share_of_decrease": 0.0},
      "waiting_reviewer": {"current": 15.01, "previous": 11.02, "change": 3.99, "share_of_increase": 0.678, "share_of_decrease": 0.0},
      "waiting_author":   {"current": 11.43, "previous": 11.96, "change": -0.52, "share_of_increase": 0.0, "share_of_decrease": 1.0}
    },
    "locations": [{"location": "area-System.Net.Http", "change": 1.65, "share_of_reviewer_increase": 0.3946, "share_of_increase": 0.2675}],
    "large_prs": {"current": 14.2, "previous": 10.0, "change": 4.2, "share_of_increase": 0.7831}
  }
}
```

`states` 包含 `coding` 和四个等待状态（示例只列三个）。示例与 §4.5、§4.7 一致：等待状态的 `current` / `previous` 是时间账的 PR-小时除以合并 PR 数（812、798），位置的 `change` = 2460.5 / 812 − 1102.0 / 798；示例假设各位置正变化之和 `gross` = 4.18（位置划分了全部 reviewer 等待，所以 `gross` 不小于 reviewer 阶段的增长 3.99），于是 `share_of_reviewer_increase` = 1.65 / 4.18、`share_of_increase` = 0.678 × 0.3946；示例中 `cycle_mean_hours` 恰好等于各分量之和，真实数据里关闭期和没有 commit 的 PR 会让两者略有差别。定义见 `05` §9.7 和 `05` §9.12；不可计算时 `attribution` 为 `null`。

### 4.11 `signals`、`series`、`per_repo`

- `signals`：`{"large_pr_share", "merged_without_approval_share", "fast_large_approval_share", "external_pickup_ratio", "at_risk_reviewer_top_location_share"}`，值都是 Metric（`05` §9.10）。
- `series`：`{"current": Week[], "previous": Week[]}`；`Week = {"week_start", "days", "merged", "cycle_p50_hours", "pickup_p50_hours", "pr_size_p50_lines", "waiting_reviewer_share", "waiting_ci_share", "reverts"}`（`05` §9.11）。上一周期不可用时 `previous` 为 `[]`。
- `per_repo`：多仓库时每个仓库一项 `{"repo", "merged_prs", "cycle_time_p50_hours", "pickup_p50_hours", "waiting_share"}`（值为数字或 `null`，不是 Metric），按仓库名排序。

### 4.12 P1 字段

| 路径 | 结构 | 来源 |
|---|---|---|
| `bottleneck_analysis.ci` | `{"source", "coverage", "queue_p50_minutes": Metric, "run_p50_minutes": Metric, "rerun_rate": Metric, "flaky_rerun_rate": Metric, "runs_per_pr_p50": Metric, "top_workflows": [{"workflow_name", "runs", "run_p50_minutes", "rerun_rate"}]}` | `05` §13 |
| `bottleneck_analysis.locations[].owners_count` | 整数或 `null` | `05` §9.1 |
| `drivers` | `{"assignment", "review_round_cost", "author_wip", "submit_timing", "slowest_decile"}` | `05` §14 |
| `efficiency.predictability` | `{"within_hist_p85": Metric, "weekly_throughput_cv": Metric}` | `05` §16 |
| `efficiency.survival` | `{"current": KM, "previous": KM \| null}`，`KM = {"n", "events", "median_hours", "s_at_hours": {"24", "72", "168", "336"}}` | `05` §15 |

`drivers` 各子项结构：

```json
{
  "assignment": {"assigned": {"n": 512, "pickup_p50_hours": 14.0}, "unassigned": {"n": 300, "pickup_p50_hours": 31.0}, "ratio": 2.21},
  "review_round_cost": {"buckets": [{"rounds": "0", "n": 210, "cycle_p50_hours": 20.1}, {"rounds": "1", "n": 330, "cycle_p50_hours": 38.0},
                                    {"rounds": "2", "n": 160, "cycle_p50_hours": 61.2}, {"rounds": "3+", "n": 112, "cycle_p50_hours": 99.0}],
                        "hours_per_extra_round": 23.1, "re_review_wait_p50_hours": 18.0, "first_pickup_p50_hours": 14.6},
  "author_wip": {"buckets": [{"wip": "0", "n": 300, "waiting_author_p50_hours": 10.0}, {"wip": "1-2", "n": 400, "waiting_author_p50_hours": 14.0},
                             {"wip": "3+", "n": 112, "waiting_author_p50_hours": 25.0}], "spearman": 0.21},
  "submit_timing": {"by_weekday": [{"weekday": 0, "n": 140, "pickup_p50_hours": 12.0}],
                    "by_hour_block": [{"hours": "00-05", "n": 90, "pickup_p50_hours": 20.0}]},
  "slowest_decile": {"n": 81, "features": [{"feature": "size_lines_p50", "slowest": 610.0, "rest": 90.0, "ratio": 6.78}]}
}
```

`slowest_decile.features[].feature` ∈ `size_lines_p50`、`external_share`、`multi_location_share`、`review_rounds_p50`、`unrequested_share`。样本不足的值为 `null`。

### 4.13 完整示例

`backend/tests/golden/snapshot_seed42.json` 是权威示例（M5 生成）。README 中放一个截断的真实示例（M12）。

## 5. Endpoint 详细说明

### 5.1 `GET /v1/insights/delivery`

参数：`repo`（可重复）或 `org`；`from`、`to`。

处理流程（`insights/snapshot_service.py`，worker 的预计算复用同一个服务）：

1. 解析并校验参数（§3）→ 422。
2. 解析仓库、检查白名单 → 403 / 422。
3. 开启一个 `REPEATABLE READ` 只读事务，第 3–9 步的数据库读取都在其中进行（与 `05` §6.2 的数据集加载是同一个事务，保证 `snapshot_id` 和快照内容来自同一份数据）；写入快照用另一个短事务。读取这些仓库的 `repositories` 行；表中还没有的白名单仓库视为"从未同步"（`covered_since = null`、`last_sync_status = "never"`、`data_version = 0`）。
4. **就绪检查**：先算出 `as_of = min(to_excl, 各仓库 last_synced_at 的最小值)`（`05` §6.1）。每个仓库依次检查下列条件，第一个不满足的条件就是它的 `reason`：

   | 条件 | 不满足时的 `reason` |
   |---|---|
   | `covered_since` 和 `last_synced_at` 都非空 | `never_synced` |
   | `covered_since <= from_dt` | `backfill` |
   | `last_open_sweep_at` 非空 | `open_sweep` |
   | `derived_key` 等于当前推导标识（`insights.analytics.derive_key(...)`，`04` §6.2；API 和 worker 调用同一个函数） | `rederive` |
   | `last_synced_at > from_dt`（数据已同步到请求周期之内，从而 `as_of > from_dt`） | `stale` |

   前三个条件在首次回填的第一阶段完成时同时满足（`04` §6.3）。算法版本或位置配置变化后，整仓重推导完成之前是 `rederive`，不会用旧的推导结果发布标着新配置的快照。不就绪时：
   - 有任一未就绪仓库的 `reason` 需要访问 GitHub（`never_synced`、`backfill`、`open_sweep`、`stale`）且 `last_sync_status ∈ {missing_token, auth_error, not_found}` → 503 `/problems/data-unavailable`（同步无法继续）；`reason = rederive` 不访问 GitHub，没有 token 也会完成，按下一条返回 202；
   - 否则返回 **202**（§7.1），`Retry-After: 30`；`Location` 指向第一个未就绪仓库最近的 `queued` / `running` 任务（`reason = rederive` 时是重推导任务，其他是同步任务，`/v1/sync-jobs/{id}`），没有则不带 `Location`。本接口**不入队**任何任务（GET 无副作用；worker 启动和定时任务会继续回填）。
5. 计算 `data_freshness`、`params_hash`、`versions_hash`、`snapshot_id`（`05` §12.3）。
6. 条件请求快速路径：Redis `HMGET di:snap:{id} etag created_at` 命中、未逻辑过期（`03` §2.12）且与 `If-None-Match` 匹配 → 304。
7. Redis `HGETALL di:snap:{id}` 命中且未逻辑过期 → 200。已逻辑过期的 Redis 命中视为未命中，继续第 8 步（第 9 步会覆盖这个哈希）。
8. Postgres `snapshots` 命中（且未逻辑过期，`03` §2.12）→ 用规范化序列化得到字节，ETag 用表中的 `etag` → 回填 Redis（过期时间取 24 小时与逻辑过期剩余时间中的较小者）→ 200（再检查一次 `If-None-Match`）。
9. 未命中（包括 Postgres 中只有已逻辑过期的行）：`dataset.load_dataset`（异步）→ `asyncio.to_thread(build_snapshot, ...)` → 规范化字节和 ETag → `INSERT ...（created_at = 注入的 now）ON CONFLICT (snapshot_id) DO UPDATE SET created_at = EXCLUDED.created_at`（内容由 ID 决定、不会变化；刷新 `created_at` 让刚返回的快照 ID 在 7 天内可读）→ Redis `HSET`（含 `created_at`）+ `EXPIRE 86400` → 200。记录一条 `snapshot_computed` 日志（`snapshot_id`、`duration_ms`、`load_ms`、`compute_ms`、`merged_prs`）。并发请求同一快照时可能重复计算一次，结果相同，由 `ON CONFLICT` 去重（不加锁，取舍写进 DECISIONS）。

200 响应头：`ETag`、`Cache-Control: private, max-age=60`、`Content-Location: /v1/snapshots/{id}`、`X-Snapshot-Id`。

### 5.2 `GET /v1/insights/delivery/prs`

参数：与 §5.1 相同的 `repo`/`org`、`from`、`to`，加上 `status`、`at_risk`、`state`、`location`、`limit`、`cursor`（§3.3）。

1. 第 1–5 步与 §5.1 相同（包括 202、403、422、503）。
2. 行数据：Redis `di:rows:{snapshot_id}`（orjson 列表，1 小时）；未命中时加载数据集并用 `analytics/rows.py` 的纯函数 `build_pr_rows(dataset)` 生成全部行，写入 Redis。
3. 按 `status` 取总体（`05` §17）：`merged` = 当前周期合并；`closed` = 当前周期关闭未合并；`open` = 在 `as_of` 时开着（`05` §2.7：已 ready、尚未结束、且不处于关闭后又重开之间的关闭期；从未 ready 的 draft 不算）。`at_risk=true` 只保留 `at_risk` 非空的行；`state`（当前所在等待状态）、`location` 再筛选。只包含流程 PR（`05` §4.1）。
4. 排序（固定）：`merged` 按 `cycle_hours` 降序；`closed` 按 `closed_at` 降序；`open` 按 `current_state_age_hours` 降序；`at_risk=true` 按 `age_hours / threshold_hours` 降序。并列时按 `(repo, number)` 升序。
5. 分页（§2.5），响应见 §7.2。

### 5.3 `GET /v1/snapshots/{snapshot_id}`

Redis → Postgres 读取；不存在返回 404（包括被 `housekeeping` 清理的快照，以及创建已超过 7 天、但 Redis 里还没过期的快照，`03` §2.12）。响应头：`ETag`、`Cache-Control: private, max-age=86400, immutable`。支持 304。

### 5.4 `GET /v1/snapshots/{snapshot_id}/narrative`

参数：`audience`、`lang`。流程见 `07` §9，响应见 §6。

- 快照不存在：404（包括写叙述时因为快照刚被清理而遇到外键错误的情况）。
- 响应头：`ETag`；持久化的叙述（LLM 结果，或因未配置 Bedrock 生成的模板）`Cache-Control: private, max-age=3600`；因 LLM 失败临时降级的模板 `Cache-Control: no-store`。支持 304。
- LLM 调用失败、校验失败都返回 200（模板），不返回 5xx；只有 Postgres 不可用时返回 503。

### 5.5 `GET /v1/repos`

返回 `{"items": [RepoStatus]}`（§7.3），包含 `TRACKED_REPOS` 中的全部仓库（按配置顺序）；表中没有行的仓库各字段为 `null`、`last_sync_status = "never"`、`data_version = 0`。

### 5.6 `POST /v1/repos/{owner}/{name}/sync`

1. 校验路径参数（422）、白名单（403）。
2. 冷却：`SET di:cooldown:sync:{repo_lower} 1 NX EX MANUAL_SYNC_COOLDOWN_SECONDS`；已存在 → 429 `/problems/sync-cooldown`，`Retry-After` = 剩余秒数（`TTL`，至少 1）。
3. 调用 `insights/sync/queue.py` 的 `enqueue_sync(..., kind="manual")`（`04` §6.8）。arq 连接池在 lifespan 中用 `arq.create_pool(RedisSettings.from_dsn(REDIS_URL))` 创建，通过依赖 `get_arq` 注入：
   - 新任务入队 → 202，body 为新的 SyncJob（`status = "queued"`）；
   - 同一仓库已有排队或运行中的同步 → 202，body 为现有任务（不新建）。
4. 响应头：`Location: /v1/sync-jobs/{id}`、`Cache-Control: no-store`。Redis 不可用 → 503。

### 5.7 `GET /v1/sync-jobs/{job_id}`

返回 SyncJob（§7.4）；不存在 404；`job_id` 不是 UUID 返回 422。

### 5.8 `GET /healthz`、`GET /readyz`

- `/healthz`：`{"status": "ok"}`，不访问依赖。
- `/readyz`：执行 `SELECT 1` 和 Redis `PING`（各 2 秒超时）。都成功：`{"status": "ready", "checks": {"postgres": "ok", "redis": "ok"}}`；任一失败：503 `/problems/dependency-unavailable`，扩展字段 `checks` 标明哪个失败（值为 `ok` 或 `error`，不含异常原文）。
- 两者都不限流、不记访问日志（避免健康检查刷屏）。

## 6. 叙述响应（`Narrative`）

```json
{
  "snapshot_id": "s_3f9a0c1d2e4b5a67",
  "audience": "manager",
  "lang": "en",
  "narrative": "Median cycle time rose 18% to 41.3h [E1]. …",
  "abstained": false,
  "abstain_reason": null,
  "hypotheses": [
    {
      "id": "H_review_capacity",
      "source": "library",
      "title": "Limited review capacity",
      "location": "area-System.Net.Http",
      "statement": "Limited review capacity in area-System.Net.Http is likely the main cause of the slower cycle time [E1][E15][E53].",
      "confidence": 0.79,
      "confidence_level": "high",
      "confidence_basis": {
        "signal_agreement": 0.8, "signals_present": 4, "signals_total": 5,
        "effect_size": 1.0, "persistence": 0.8, "weeks_holding": "4/5",
        "sample_adequacy": 1.0, "sample_size": 790,
        "localization": 0.27, "counter_evidence": 0, "covers_both_parts": true,
        "raw_score": 0.79, "cap": null, "cap_reason": null, "llm_downgrade": null
      },
      "evidence_chain": [
        {"step": "symptom", "evidence": ["E1", "E18"]},
        {"step": "stage", "evidence": ["E15", "E37"]},
        {"step": "location", "evidence": ["E51", "E53"]},
        {"step": "mechanism", "evidence": ["E22", "E26"]}
      ],
      "counter_evidence": [],
      "alternatives_ruled_out": [{"hypothesis": "H_pr_size_growth", "evidence": ["E30", "E31"]}],
      "alternatives_open": [{"hypothesis": "H_ci_bottleneck", "reason": "no_data"}],
      "action": "Add reviewers or code owners for area-System.Net.Http and enable team auto-assignment.",
      "verify_next": "Two weeks after adding reviewers, check whether the first-review wait in area-System.Net.Http has dropped."
    }
  ],
  "evidence": [
    {"id": "E1", "key": "cycle_time_p50", "label": "Median cycle time", "unit": "hours",
     "value": 41.25, "previous": 35.1, "change_abs": 6.15, "change_rel": 0.1752, "change_pp": null,
     "significant": true, "n": 512, "side": "efficiency", "baseline": "previous_period", "location": null,
     "ref": "/efficiency/cycle_time_p50_hours", "examples": []}
  ],
  "links": {"snapshot": "/v1/snapshots/s_3f9a0c1d2e4b5a67"},
  "meta": {
    "generated_by": "llm",
    "model": "us.anthropic.claude-sonnet-4-6",
    "prompt_version": "v1",
    "validation": "passed",
    "attempts": 1,
    "fallback_reason": null,
    "violations": [],
    "confidence_method": "deterministic-v1",
    "pack_hash": "9c1d0e2f3a4b5c6d",
    "generated_at": "2026-10-02T09:47:03Z"
  }
}
```

| 字段 | 说明 |
|---|---|
| `narrative` | 叙述正文，带 `[E#]` 引用；纯文本 |
| `abstained`、`abstain_reason` | 没有满足门槛的假设时为 `true`；原因 ∈ `no_comparison`、`insufficient_signal`（`07` §4.2） |
| `hypotheses[]` | 组装完成（包括 LLM 的下调）后按最终 `confidence` 降序排列，同分时库内假设在前、再按 ID 升序；备选假设只考虑没有输出、且至少一个症状信号出现的库内假设：`alternatives_ruled_out` 只列出现了反证、或可评估机制信号全部未出现的，其余列在 `alternatives_open`（`[{"hypothesis", "reason"}]`，`reason` ∈ `no_data`、`insufficient_sample`、`below_threshold`、`not_selected`，分类顺序见 `07` §3.3；`H_llm` 的两个列表为空）；`source` ∈ `library`、`llm`；`confidence_level` ∈ `high`、`medium`、`low`；`evidence_chain[].step` ∈ `symptom`、`stage`、`location`、`mechanism`（LLM 提出的假设只有一个 `cited` 步骤）；`action`、`verify_next` 由代码模板生成，LLM 假设为 `null`。字段定义见 `07` §3–§4 |
| `evidence[]` | 叙述、假设、证据链、反证和备选假设中引用到的全部证据条目，按 ID 数字升序；`ref` 是指向快照的 JSON Pointer；`examples` 是最多 3 个 PR 链接（`07` §2.1） |
| `meta.generated_by` | `llm` 或 `template` |
| `meta.model` | Bedrock 推理配置 ID；模板为 `"template"` |
| `meta.validation` | `passed`（最终输出通过校验）、`failed`（两次都未通过，已降级）、`not_run`（没有调用 LLM 或调用失败） |
| `meta.attempts` | 实际调用 LLM 的次数（0–2） |
| `meta.fallback_reason` | `null`、`llm_disabled`、`llm_error`、`validation_failed`、`llm_busy` |
| `meta.violations` | 校验失败时的违规代码列表（例如 `["V5:number_not_in_evidence"]`），不含 LLM 原文 |
| `meta.pack_hash` | 证据包规范化 JSON 的哈希（前 16 位），是叙述缓存身份的一部分：影响证据或评分的配置变化后自动换键（`07` §9.2） |

## 7. 其他响应结构

### 7.1 Pending（202）

```json
{
  "status": "pending",
  "detail": "Data for the requested period is still being synced.",
  "retry_after_seconds": 30,
  "repos": [
    {"repo": "dotnet/runtime", "covered_since": "2026-09-25T10:00:00Z", "required_since": "2026-09-03T00:00:00Z",
     "open_sweep_done": true, "last_sync_status": "ok", "reason": "backfill",
     "job": {"id": "0f8c…", "status": "running", "phase": "backfill:30d", "url": "/v1/sync-jobs/0f8c…"}}
  ]
}
```

`repos` 只列未就绪的仓库；`reason` 见 §5.1 第 4 步（`never_synced`、`backfill`、`open_sweep`、`rederive`、`stale`）；`job` 与 `Location` 的选择规则相同：`reason = rederive` 时为该仓库最近的排队或运行中重推导任务，其他为最近的排队或运行中同步任务，没有则为 `null`。

### 7.2 PR 明细（`PrPage`、`PrRow`）

```json
{
  "snapshot_id": "s_3f9a0c1d2e4b5a67",
  "as_of": "2026-10-02T09:45:12Z",
  "status": "merged",
  "total": 812,
  "items": [
    {
      "repo": "dotnet/runtime", "number": 108001, "title": "…", "url": "https://github.com/dotnet/runtime/pull/108001",
      "author": "octocat", "status": "merged",
      "created_at": "…", "ready_at": "…", "merged_at": "…", "closed_at": null,
      "size_lines": 120, "size_bucket": "M", "locations": ["area-System.Net.Http"], "external_contributor": false,
      "is_revert": false, "reverted": false, "close_class": null, "review_rounds": 1, "human_reviews": 3,
      "cycle_hours": 52.1,
      "stage_hours": {"coding": 10.2, "pickup": 20.5, "review": 15.0, "merge": 6.4},
      "ledger_hours": {"waiting_reviewer": 25.0, "waiting_author": 10.5, "waiting_ci": 0.0, "waiting_merge": 6.4},
      "current_state": null, "current_state_age_hours": null, "at_risk": null
    }
  ],
  "next_cursor": "eyJ2IjoxLCJzaWQiOi…"
}
```

- `author`：`string | null`（作者账号已删除时为 `null`）。
- `ledger_hours`：`[ready_at, min(end_at, as_of)]` 内各状态时长；`coding` 不在其中（在 `stage_hours.coding`）。
- `current_state`、`current_state_age_hours`：只对 `status = open` 的行有值（`as_of` 时所在区间）。
- `at_risk`：风险 PR 为 `{"severity", "threshold_hours", "critical_threshold_hours", "baseline_source"}`，否则 `null`。
- `reverted`：`reverted_at` 非空且早于 `as_of`。

### 7.3 `RepoStatus`

```json
{
  "repo": "dotnet/runtime", "default_branch": "main",
  "covered_since": "2026-04-05T09:00:00Z", "backfill_target_days": 180, "backfill_complete": true,
  "sync_watermark": "2026-10-02T09:40:00Z", "last_synced_at": "2026-10-02T09:45:12Z",
  "last_open_sweep_at": "2026-10-02T09:30:00Z", "last_sync_status": "ok", "last_sync_error": null,
  "data_version": 1234, "latest_job": SyncJob | null
}
```

`backfill_complete = covered_since 非空 且 covered_since <= now - BACKFILL_DAYS 天`。

### 7.4 `SyncJob`

```json
{
  "id": "0f8c2a3e-…", "repo": "dotnet/runtime", "kind": "manual", "status": "running", "phase": "incremental",
  "stats": {"prs_fetched": 75, "prs_changed": 12, "events": 640, "pages": 3, "graphql_cost": 4},
  "error": null, "created_at": "…", "started_at": "…", "finished_at": null,
  "url": "/v1/sync-jobs/0f8c2a3e-…"
}
```

## 8. curl 示例（M6 DoD 逐条执行）

```bash
API=http://localhost:8000
TO=$(python3 -c 'import datetime as d; print(d.datetime.now(d.timezone.utc).date())')
FROM=$(python3 -c 'import datetime as d; print(d.datetime.now(d.timezone.utc).date() - d.timedelta(days=29))')

# 1. 仓库与同步状态 → 200
curl -s "$API/v1/repos" | python3 -m json.tool

# 2. 最近 30 天的 insight → 200（回填未完成时 202，带 Retry-After 和 Location）
curl -si "$API/v1/insights/delivery?repo=dotnet/runtime&from=$FROM&to=$TO" | head -20

# 3. 条件请求 → 304（用 GET 并只看响应头；FastAPI 的 GET 路由不响应 HEAD）
ETAG=$(curl -s -D - -o /dev/null "$API/v1/insights/delivery?repo=dotnet/runtime&from=$FROM&to=$TO" | awk -F': ' 'tolower($1)=="etag"{print $2}' | tr -d '\r')
curl -s -o /dev/null -w '%{http_code}\n' -H "If-None-Match: $ETAG" "$API/v1/insights/delivery?repo=dotnet/runtime&from=$FROM&to=$TO"

# 4. 参数错误 → 422（problem+json，不回显原值）
curl -s "$API/v1/insights/delivery?repo=../../etc&from=$FROM&to=$TO"
curl -s "$API/v1/insights/delivery?repo=dotnet/runtime&from=$TO&to=$FROM"
curl -s "$API/v1/insights/delivery?repo=dotnet/runtime&org=dotnet"

# 5. 不在白名单 → 403
curl -s -o /dev/null -w '%{http_code}\n' "$API/v1/insights/delivery?repo=torvalds/linux&from=$FROM&to=$TO"

# 6. 按 ID 读取快照 → 200，Cache-Control 含 immutable
SID=$(curl -s "$API/v1/insights/delivery?repo=dotnet/runtime&from=$FROM&to=$TO" | python3 -c 'import sys,json; print(json.load(sys.stdin)["snapshot_id"])')
curl -s -D - -o /dev/null "$API/v1/snapshots/$SID" | grep -i -E '^(etag|cache-control)'

# 7. 风险 PR 明细 → 200
curl -s "$API/v1/insights/delivery/prs?repo=dotnet/runtime&from=$FROM&to=$TO&at_risk=true&limit=5" | python3 -m json.tool

# 8. 手动同步 → 202；立即再次触发 → 429
curl -si -X POST "$API/v1/repos/dotnet/runtime/sync" | head -5
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$API/v1/repos/dotnet/runtime/sync"

# 9. 叙述 → 200（未配置 Bedrock 时 meta.generated_by == "template"）
curl -s "$API/v1/snapshots/$SID/narrative?audience=director&lang=zh" | python3 -m json.tool
```
