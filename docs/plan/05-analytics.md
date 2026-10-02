# 05 分析算法

本文件定义所有计算。除 `dataset.py` 外全部是纯函数：输入不可变数据，输出新的数据，不做 I/O。所有时长单位为小时（`float`），所有时间为 UTC。

## 1. 阈值（`insights/analytics/thresholds.py`）

```python
THRESHOLDS_VERSION = "1.0.0"

MIN_SAMPLES_P50 = 20
MIN_SAMPLES_P90 = 30
MIN_SAMPLES_LOCATION_P50 = 10
MIN_SAMPLES_WEEKLY_P50 = 5
MIN_RATE_DENOMINATOR = 30
MIN_RATE_EVENTS = 5
CHANGE_MIN_RELATIVE = 0.10
BOOTSTRAP_ITERATIONS = 1000
BOOTSTRAP_CI = 0.90

N_DAYS_MERGED = 3
MIN_LOCATION_PRS = 10
MAX_LOCATIONS_IN_SNAPSHOT = 15
DIRECTORY_LOCATIONS_PER_PR = 3

AT_RISK_WARNING_PERCENTILE = 85
AT_RISK_CRITICAL_PERCENTILE = 95
AT_RISK_BASELINE_DAYS = 90
AT_RISK_FALLBACK_DAYS = 180
AT_RISK_MIN_BASELINE = 30
AT_RISK_DEFAULT_HOURS = {"waiting_reviewer": 48.0, "waiting_author": 72.0, "waiting_ci": 6.0, "waiting_merge": 24.0}
AT_RISK_MAX_ITEMS = 20

WHAT_IF_TARGET_HOURS = {"pickup": 8.0, "merge": 8.0, "ci": 2.0}   # ci 在 P1 生效
REVIEW_CONCENTRATION_TOP_K = 2
LARGE_PR_LINES = 500
FAST_APPROVAL_MINUTES = 10
FAST_APPROVAL_MIN_LINES = 300
LATE_REJECTION_DAYS = 14
LATE_REJECTION_ROUNDS = 2
SUPERSEDE_WINDOW_DAYS = 14
SIZE_BUCKETS = ((10, "XS"), (100, "S"), (500, "M"), (1000, "L"))   # 其余为 "XL"
CI_COVERAGE_MIN = 0.5

# findings 规则（§11）
REVIEW_CAPACITY_PICKUP_RATIO = 1.5
REVIEW_CAPACITY_MIN_WAIT_SHARE = 0.15
REVIEW_CAPACITY_HIGH_WAIT_SHARE = 0.30
QUEUE_GROWTH_WEEK_SHARE = 0.5
QUEUE_GROWTH_MIN_RELATIVE = 0.20
QUEUE_GROWTH_HIGH_RELATIVE = 0.50
CONCENTRATION_SHARE = 0.60
CONCENTRATION_HIGH_SHARE = 0.75
MERGE_BLOCKED_SHARE = 0.15
MERGE_BLOCKED_HIGH_SHARE = 0.30
MERGE_BLOCKED_P50_HOURS = 24.0
CI_WAIT_SHARE = 0.15
CI_WAIT_HIGH_SHARE = 0.30
REWORK_ROUNDS = 2.5
REWORK_POST_REVIEW_SHARE = 0.5
WASTE_SHARE = 0.15
WASTE_HIGH_SHARE = 0.25
LOST_WHILE_WAITING_MIN = 5
GUARDRAIL_REVERT_RATE_DELTA = 0.01   # revert 率绝对上升 0.01，即 1 个百分点
EXTERNAL_PICKUP_RATIO = 2.0
```

`insights/analytics/__init__.py` 定义 `ANALYTICS_VERSION = "1.0.0"` 和推导标识函数 `derive_key(location_dimension: str, directory_depth: int) -> str`，返回 `f"{ANALYTICS_VERSION}|{THRESHOLDS_VERSION}|{location_dimension}|depth={directory_depth}"`。worker 写 `pr_facts.derive_key`、`repositories.derived_key` 和 API 的就绪检查（`06` §5.1）都只调用这个函数，不各自拼字符串。

## 2. 状态机（`insights/analytics/timeline.py`）

### 2.1 输入与输出

```python
@dataclass(frozen=True, slots=True)
class Interval:
    state: str                    # coding | waiting_reviewer | waiting_author | waiting_ci | waiting_merge | closed
    start_at: datetime
    end_at: datetime | None       # None 表示仍在进行

@dataclass(frozen=True, slots=True)
class TimelineResult:
    ready_at: datetime | None
    intervals: tuple[Interval, ...]
    approved_at: datetime | None  # 第一次进入 waiting_merge 的时间
    review_rounds: int            # 因 reviewer 反馈而进入 waiting_author 的次数（见 §2.4）
    state_at_end: str | None      # 关闭（未合并）前最后的等待状态

def build_timeline(pr: PrInput, events: Sequence[Event], ci_intervals: Sequence[tuple[datetime, datetime]], now: datetime) -> TimelineResult
```

`PrInput` 包含：`author_login`、`created_at`、`is_draft`（当前）、`merged_at`、`closed_at`、`state`。`ci_intervals` 在 P0 永远为空（M8 接入）。

### 2.2 定义

- **作者身份**：`author_key = author_login.lower()`；`author_login` 为空（账号已删除）时 `author_key = None`，表示作者未知。所有"是否作者本人"的比较都用 `author_key`，为 `None` 时一律判为"不是作者"，不调用 `.lower()`。
- **人工账号**：`login` 非空、`is_bot` 为假，且 `author_key` 为 `None` 或 `login.lower() != author_key`。
- **reviewer 反馈**：人工账号的 `review` 事件，`state ∈ {CHANGES_REQUESTED, COMMENTED}`。
- **批准**：人工账号的 `review` 事件，`state == APPROVED`。
- **作者更新**：以下任一事件：任意 `commit`；任意 `force_push`；作者本人的 `comment`；作者本人的 `review`（任意状态，代表作者回复）；作者本人触发的 `review_requested`（重新请求 review）。作者未知时只有 `commit` 和 `force_push` 算作者更新。
- **ready_at**：
  - 令 `first_ready` = 第一个 `ready_for_review` 的时间，`first_convert` = 第一个 `convert_to_draft` 的时间。
  - 若 `first_ready` 存在，且 `first_convert` 不存在或 `first_ready < first_convert`：PR 以 draft 创建，`ready_at = first_ready`。
  - 否则若当前 `is_draft` 为真且没有任何 `convert_to_draft`：以 draft 创建且从未 ready，`ready_at = None`。
  - 否则：`ready_at = created_at`。
- **end_at**：已合并为 `merged_at`；已关闭未合并为最终的 `closed_at`；开着为 `None`。`horizon = end_at or now`。
- **关闭期**：只有**配对**的关闭才算：一个 `closed` 事件，且在它之后、`horizon` 之前有 `reopened` 事件，则从该 `closed` 到其后第一个 `reopened` 记为一个 `closed` 区间。没有配对的 `closed` 事件（最终关闭；或合并时伴随的关闭事件，即使它比 `merged_at` 早一两秒）忽略。`closed` 区间不属于任何等待状态，不计入按区间累计的时长（时间账、`waiting_share`、归因的各分量、明细的 `ledger_hours`），只用于判断某个时点 PR 是否开着。里程碑之间的时长（`cycle_hours` 和 §3 的 `pickup_hours` 等阶段时长）是两个时间点之差，按定义包含关闭期；这类 PR 很少，取舍写进 DECISIONS。
- **first_commit_at**：所有 `commit` 事件 `payload.authored_at` 的最小值；没有 commit 时为 `None`。

### 2.3 状态变量与判定

按时间应用事件后维护：

- `latest_review: dict[reviewer_lower, (state, review_id)]`：每个 reviewer 最新的**决定性** review，只记人工账号的 `APPROVED` / `CHANGES_REQUESTED`。`COMMENTED` 不改变它（与 GitHub 一致：之后的评论不会撤销批准或清除修改请求）。review 事件的 `payload.state` 是提交时的原始决定（被撤销的 review 已由规范化还原，`04` §5.3），所以批准在被撤销之前照常生效。`review_dismissed` 时，只有该 reviewer 当前记录的 `review_id` 等于事件的 `payload.review_id` 才删除（事件没有 `review_id` 时按 `payload.review_author` 删除）：撤销只作用于对应的那条 review，不影响该 reviewer 之后提交的新决定。
- `last_feedback_at`：最近一次 reviewer 反馈（`CHANGES_REQUESTED` 或 `COMMENTED`）的时间。
- `last_author_update_at`：最近一次作者更新的时间。
- `draft`：`convert_to_draft` 置真，`ready_for_review` 置假。
- `paused`：**配对的** `closed`（§2.2）置真，`reopened` 置假；没有配对的 `closed` 不改变状态。

派生：

```text
outstanding_changes = any(state == "CHANGES_REQUESTED" for state, _ in latest_review.values())
has_approval        = any(state == "APPROVED" for state, _ in latest_review.values())
approved            = has_approval and not outstanding_changes
awaiting_author     = last_feedback_at is not None and (last_author_update_at is None or last_feedback_at > last_author_update_at)
ci_running(t)       = 存在 ci 区间 [s, e) 使 s <= t < e
```

判定（优先级从上到下）：

```text
if paused:            closed
elif draft:           waiting_author
elif approved:        waiting_merge
elif awaiting_author: waiting_author
elif ci_running(t):   waiting_ci
else:                 waiting_reviewer
```

### 2.4 算法

```text
ready_at = compute_ready_at(...)
coding_start = min(first_commit_at, ready_at or horizon) if first_commit_at else created_at
if ready_at is None:
    return 一个 coding 区间 [coding_start, end_at]（end_at 可为 None）
if coding_start < ready_at: 加入 coding 区间 [coding_start, ready_at]
用所有 occurred_at <= ready_at 的事件初始化状态变量（draft 期间的 review 也计入），然后令 draft = False
boundaries = 所有 occurred_at 在 (ready_at, horizon) 内的事件时间，加上落在该范围内的 ci 区间起止点，去重排序
current = evaluate(ready_at); seg_start = ready_at
for t in boundaries:
    应用所有 occurred_at == t 的事件（同一时刻按 kind 的固定顺序：closed, reopened, merged, convert_to_draft,
      ready_for_review, review_dismissed, review, review_requested, review_request_removed, commit, force_push,
      comment, labeled, unlabeled, cross_referenced）
    new = evaluate(t)
    if new == waiting_author and current != waiting_author and not draft and 本时刻应用的事件中有 reviewer 反馈:
        review_rounds += 1        # 重新打开、撤销 draft 等原因进入 waiting_author 不算一轮
    if new != current:
        输出区间 [seg_start, t]
        current = new; seg_start = t
输出区间 [seg_start, end_at]（PR 开着时 end_at 为 None）
合并相邻的同状态区间，删除长度为 0 的区间
approved_at = 第一个 waiting_merge 区间的 start_at
state_at_end = 已关闭未合并时，最后一个区间的 state
```

`horizon` 当刻的事件（合并、最终关闭）不进入循环，所以最终的 `merged`/`closed` 不会产生 `closed` 区间；`closed` 区间只表示关闭后又重开之间的时间，因此永远不会是最后一个区间。

### 2.5 边界情况（每条都要有单元测试）

1. 非 draft 创建、一次 approve 后合并：`waiting_reviewer → waiting_merge`。
2. changes requested → 作者 push → 再 approve：`waiting_reviewer → waiting_author → waiting_reviewer → waiting_merge`，`review_rounds = 1`。
3. 作者只回复评论（comment），没有 push：`waiting_author → waiting_reviewer`。
4. 作者重新请求 review：同 3。
5. draft 创建 → ready：ready 之前为 `coding`。
6. ready 之后又转回 draft：draft 期间为 `waiting_author`。
7. approve 被 dismiss：撤销之前为 `waiting_merge`，撤销后回到 `waiting_reviewer`（或 `waiting_author`）。
8. 两个 reviewer：A 要求修改、B 批准：未批准；作者 push 后 A 仍是 CHANGES_REQUESTED，状态为 `waiting_reviewer`。
9. 未经批准直接合并：没有 `waiting_merge`，`approved_at = None`。
10. 关闭未合并、从未有 review：只有 `waiting_reviewer`，`state_at_end = waiting_reviewer`。
11. 关闭后重新打开再合并：关闭期间是一个 `closed` 区间，不计入任何等待状态（时间账不含这段时间）；观察时点落在关闭期内时，该 PR 当时不算开着。
12. bot 的 review 和作者自己的 review 都不算 reviewer 反馈或批准。
13. draft 创建从未 ready 就被关闭：只有一个 `coding` 区间。
14. 首个 commit 晚于 ready（PR 先开、后提交）：没有 `coding` 区间。
15. 开着的 PR：最后一个区间 `end_at = None`。
16. CI 区间（M8）：在 `waiting_reviewer` 时段内 CI 运行，切分出 `waiting_ci`；`waiting_author` 和 `waiting_merge` 优先于 CI。
17. reviewer 先批准、之后又留了一条 `COMMENTED` review：仍然是已批准（`waiting_merge`），`review_rounds` 不增加（`approved` 优先于 `awaiting_author`）。
18. 上游形态的撤销：review 在 t1 提交、上游 `state = DISMISSED`、撤销事件的 `previousReviewState = APPROVED`、t2 撤销：t1–t2 为 `waiting_merge`，t2 之后回到 `waiting_reviewer`；同一份数据重复同步，区间完全相同。
19. 同一 reviewer 先批准（r1）、再批准（r2），随后 r1 被撤销：仍为已批准。
20. 作者账号已删除（`author_login` 为空）：有效 reviewer 的 review 照常计入，不报错；作者的评论无法识别，只有 commit 和 force push 算作者更新。
21. 已合并的 PR 有一个比 `merged_at` 早 1 秒、之后没有 `reopened` 的 `closed` 事件：不产生 `closed` 区间，最后一个区间是 `waiting_merge`（或合并前所处的等待状态），不变式成立。
22. push 后两位 reviewer 的批准被同时撤销（同一秒、同一 actor 的两个 `review_dismissed`，`review_id` 不同）：两条撤销都生效（规范化不能把它们去重成一条，`04` §5.4），回到 `waiting_reviewer`。

### 2.6 不变式（纯函数 `timeline.check_invariants(result, pr) -> list[str]`；命令行入口见 `01` M4，测试也断言）

1. 区间按 `start_at` 排序，互不重叠，首尾相接（关闭期由 `closed` 区间表示，没有空隙）。
2. 已结束的 PR：ready 之后各区间（含 `closed`）首尾相接地覆盖 `[ready_at, end_at)`，非 `closed` 区间的时长之和 = `(end_at - ready_at) - closed 区间时长之和`，精确到秒。
3. `coding` 区间只出现在 `ready_at` 之前。
4. 没有 `start_at >= end_at` 的区间；只有最后一个区间的 `end_at` 可以为 `None`，且仅当 PR 开着；最后一个区间不是 `closed`。

### 2.7 按时点（as_of）使用区间

状态机是因果的（时刻 t 的状态只取决于 t 之前的事件），所以同步时算一次的区间可以回答任意历史时点的问题：

- 截断：区间视为 `[start_at, min(end_at or +∞, as_of))`，`start_at >= as_of` 的丢弃。
- 时点状态：包含 `as_of` 的区间（`start_at <= as_of < end_at` 或 `end_at` 为空）。
- **时点开着**：`ready_at <= t`、（`end_at` 为空或 `end_at > t`），且包含 `t` 的区间是等待状态（不是 `closed`）。§9.2 的队列、§9.6 的风险候选、§17 的 `open` 总体和 `meta.sample.open_prs_at_as_of` 都用这一个判定函数。

## 3. PR 事实（`insights/analytics/facts.py`）

`compute_facts(pr, events, timeline, *, default_branch, location_rules, now) -> PrFacts`，字段与 `03` §2.5 一一对应：

| 字段 | 计算 |
|---|---|
| `first_commit_at` | §2.2 |
| `ready_at` | `timeline.ready_at` |
| `human_reviews` | 人工 review 事件数（APPROVED / CHANGES_REQUESTED / COMMENTED，含之后被撤销的 review：它们确实发生过） |
| `first_review_at` | 人工 review 事件的最早时间（包括 ready 之前） |
| `first_response_at` | `min(first_review_at, 人工 comment 的最早时间)` |
| `first_approval_at` | 批准事件的最早时间 |
| `approved_at` | `timeline.approved_at` |
| `end_at`、`merged_at`、`closed_at` | `closed_at` 只在已关闭未合并时有值 |
| `coding_hours` | `ready_at` 和 `first_commit_at` 都存在时为 `max(0, ready_at - first_commit_at)`，否则 `None` |
| `pickup_hours` | `first_review_at` 和 `ready_at` 都存在时为 `max(0, first_review_at - ready_at)`，否则 `None` |
| `review_hours` | `approved_at - first_review_at`（两者都存在时），否则 `None` |
| `merge_hours` | `merged_at - approved_at`（两者都存在时），否则 `None` |
| `cycle_hours` | 已合并时为 `merged_at - (first_commit_at or created_at)`，否则 `None` |
| `review_rounds` | `timeline.review_rounds` |
| `feedback_before_approval` | `first_approval_at` 之前的 reviewer 反馈数（没有批准时为全部反馈数） |
| `commits_after_first_review` | `first_review_at` 之后的 commit 数（没有 review 时为 0） |
| `force_pushes_after_first_review` | 同上，force push |
| `updates_after_approval` | `approved_at` 之后、合并或关闭之前的 commit 与 force push 总数（没有 `approved_at` 时为 0） |
| `distinct_approvers` | 不同的批准者人数 |
| `second_approval_wait_hours` | 第二位不同批准者的首次批准时间 − `first_approval_at`；不足两人为 `None` |
| `merged_without_approval` | 已合并且 `approved_at` 为 `None` |
| `review_requested_before_first_review` | 存在早于 `first_review_at`（没有 review 时为任意时间）的 `review_requested` |
| `size_lines`、`size_bucket` | `additions + deletions`，按 `SIZE_BUCKETS` |
| `ready_weekday`、`ready_hour` | 由 `ready_at`（UTC）得出 |
| `state_at_close` | `timeline.state_at_end` |
| `ci_covered` | `len(ci_intervals) > 0` |
| `is_bot_author`、`is_backport`、`external_contributor`、`locations`、`location_source` | §4 |
| revert / reland / 被替代 / `close_class` / `late_rejection` / `author_open_prs_at_ready`（下称"关联字段"） | 只由仓库级关联写入（§4.3–§4.7）。`compute_facts` 给新行填默认值；`derive_prs` 更新已有行时**不覆盖**关联字段（upsert 的 `ON CONFLICT DO UPDATE` 不包含这些列），避免两次关联之间出现被清空的中间状态 |

所有时长都由 `timedelta.total_seconds() / 3600` 得到，不做取整（输出时才取整）。

## 4. 分类与关联（`insights/analytics/classify.py`）

### 4.1 统计范围

- `is_bot_author`：来自规范化。
- `is_backport`：`base_ref != 仓库默认分支`（默认分支未知时为假）。
- **流程 PR**：`not is_bot_author and not is_backport and ready_at is not None`（从未 ready 的 draft 没有进入 review 流程）。除 `meta.excluded` 的计数（`bot_prs`、`backport_prs`、`never_ready_drafts`）外，所有指标只用流程 PR。
- `external_contributor`：`author_association ∈ {CONTRIBUTOR, FIRST_TIME_CONTRIBUTOR, FIRST_TIMER, NONE}`。

### 4.2 位置（location）

按 `LOCATION_DIMENSION` 和回退链计算 `locations` 与 `location_source`：

1. `label:<prefix>`：label 中以前缀开头（不区分大小写，保留原文）的去重排序列表，例如 `["area-System.Net.Http"]`。没有时回退。
2. `codeowners`（P1，存在规则时）：对每个文件取**最后一条匹配**的 CODEOWNERS 规则（pathspec `gitwildmatch`），位置为 `"codeowners:" + rule.pattern`，去重排序。没有匹配时回退。
3. `directory`：每个文件取前 `DIRECTORY_DEPTH` 段路径，位置为 `"dir:" + 前缀`（根目录文件为 `"dir:/"`），按文件数取前 `DIRECTORY_LOCATIONS_PER_PR` 个，并按文件数降序、名称升序排列。
4. 都没有：`["unclassified"]`。

当 `LOCATION_DIMENSION` 为 `directory` 时直接从第 3 步开始；为 `codeowners` 时从第 2 步开始。

### 4.3 关闭分类（已关闭未合并的流程 PR）

按顺序判断，命中即停：

1. **superseded**：本 PR 的时间线上有 `cross_referenced` 事件（GitHub 把交叉引用记在**被引用**的 PR 上，`source` 是提到它的 PR），来源 PR 在同一仓库、已合并、来源作者与本 PR 作者相同（比较 `author_key`，任一方作者未知时不判定）、合并时间 ∈ `[created_at, closed_at + SUPERSEDE_WINDOW_DAYS]`；或者同一作者（作者已知）、相同 `head_ref` 的另一个 PR 在 `closed_at` 之后 `SUPERSEDE_WINDOW_DAYS` 天内创建并已合并。来源 PR 的状态、作者和合并时间以数据库中该 PR 的当前记录为准（事件 payload 里的 `source_state` 是抓取时的快照，可能已过时）；来源 PR 不在库中时才使用 payload。`superseded_by_pr_id` 取其中最早合并的。
2. **no_review**：`human_reviews == 0`。
3. **rejected**：存在由人工账号（非作者）触发的最终 `closed` 事件。
4. **abandoned**：其余（作者自己关闭，或 bot 关闭）。

`late_rejection = close_class == "rejected" and (closed_at - ready_at >= LATE_REJECTION_DAYS 天 or review_rounds >= LATE_REJECTION_ROUNDS)`。

分析时派生：`lost_while_waiting = close_class ∈ {no_review, abandoned} and state_at_close == "waiting_reviewer"`。

### 4.4 revert 识别（仓库级关联 `link_repo`）

PR 是 **revert PR**，当满足任一：

- 标题匹配 `^Revert\s+"(?P<title>.+)"\s*$`；
- `body_excerpt` 匹配 `(?m)^Reverts\s+(?P<repo>[\w.-]+/[\w.-]+)#(?P<num>\d+)\b`；
- 标题以 `Revert`（不区分大小写）开头，且至少一个 commit 的 `payload.reverts` 非空。

原 PR 的解析顺序（命中即停）：

1. body 中 `Reverts owner/repo#N`，且 `owner/repo` 与本仓库相同（不区分大小写）→ PR N；
2. commit 的 `reverts` SHA → 本仓库中 `merge_commit_oid` 或任一 commit oid 以该 SHA 开头、且早于 revert PR 创建时间合并的 PR；
3. 标题捕获的 `title` → 本仓库默认分支上、早于 revert PR 创建时间合并的、标题完全相同的最近一个 PR。

找到原 PR 且 revert PR 已合并时：原 PR `reverted_by_pr_id = revert.id`、`reverted_at = revert.merged_at`；revert PR `is_revert = True`、`reverts_pr_id = original.id`。revert PR 未合并时只标 `is_revert`。

如果原 PR 本身就是 revert PR（"Revert "Revert "X"""），则本 PR 视为 **reland**：`is_reland = True`、`reland_of_pr_id = original.reverts_pr_id`，不再作为 revert 处理。

### 4.5 reland 识别

标题匹配 `^(Reland|Re-land|Reapply|Re-apply)\b`（不区分大小写）时 `is_reland = True`。`reland_of_pr_id` 按顺序：标题或正文中第一个 `#N` 且 N 是已被 revert 的 PR；标题中引号内的文字等于某个已被 revert 的 PR 的标题；关键字之后去掉引号的文字等于某个已被 revert 的 PR 的标题。

**暂缓（写进 DECISIONS 和 README 的 Not done）**：设计文档要求被替代链、revert / reland 链"从链上第一个 PR 开始计时、算到最终合入为止"的真实交付时长。本版只建立链接字段、按单个 PR 计算 cycle；链接字段已足够以后补上链级时长。

### 4.6 作者并行 PR（P1，M9）

`author_open_prs_at_ready`：对每个有 `ready_at` 的流程 PR，统计同一作者的其他流程 PR 中，在该时刻 `ready_at_other <= t < (end_at_other or +∞)` 的个数。按作者分组、排序后扫描，复杂度 O(n log n)，不要做 O(n²) 两两比较。作者未知（`author_key` 为 `None`）的 PR 不参与，值为 `None`。

### 4.7 实现与运行时机

- 匹配逻辑是纯函数 `classify.link_prs(prs: Sequence[LinkInput], *, repo_full_name: str, default_branch: str) -> dict[int, LinkResult]`：输入每个 PR 的编号、作者、标题、`body_excerpt`、`head_ref`、`base_ref`、创建 / 合并 / 关闭时间、`merge_commit_oid`、commit oid 与 `reverts`、`cross_referenced` 事件和已算好的 `PrFacts` 字段；输出 §4.3–§4.6 的全部关联字段（`close_class`、`late_rejection`、revert / reland / 被替代、`author_open_prs_at_ready`）。eval 直接调用它（`08` §2.5）。
- `sync/derive.py` 的 `link_repo(session, repo_id)` 负责 I/O：在每次同步任务结束（以及 `rederive_repo` 结束）时运行，**每次处理该仓库的全部 PR**（数量受回填保留期限制，dotnet/runtime 约为数千个，一次性读入所需的列：编号、作者、标题、`body_excerpt`、`head_ref`、时间、`merge_commit_oid`、commit oid 与 `reverts`、`cross_referenced` 事件、相关 `pr_facts` 字段），调用 `link_prs`，只批量更新关联字段发生变化的行；有任何行变化时，在同一事务中把 `repositories.data_version` 加 1（快照 ID 依赖它，`05` §12.3）。全量处理避免了"只看最近变化的 PR"时漏掉旧 PR 的关联。

## 5. 统计工具（`insights/analytics/stats.py`）

- `percentile(values, q, min_samples) -> float | None`：样本数不足返回 `None`；否则 `numpy.percentile(values, q, method="linear")`。
- `bootstrap_diff(current, previous, statistic, seed) -> tuple[float, float]`：用 `numpy.random.default_rng(seed)`，各自有放回抽样 `BOOTSTRAP_ITERATIONS` 次，向量化计算统计量之差，返回 `BOOTSTRAP_CI` 区间。`statistic` 支持中位数、指定分位数、均值、比值（对 PR 级的分子分母成对重抽样）。
- `seed_for(params_hash, metric_name) -> int`：`int.from_bytes(sha256(f"{params_hash}:{metric_name}").digest()[:8], "big")`。
- `kaplan_meier(...)`：P1，见 §15。

（设计文档提到的 Wilson 区间只用于离线校准比率阈值，不在运行时使用，因此不实现。）

## 6. 数据加载与周期（`insights/analytics/dataset.py`）

### 6.1 周期

- 当前周期：`from_dt = from 00:00Z`，`to_excl = (to + 1 天) 00:00Z`，长度 `L` 天。
- 上一周期：`[from_dt - L 天, from_dt)`。
- `as_of = min(to_excl, 所有相关仓库 last_synced_at 的最小值)`，无论 `to` 是否早于今天：同步停滞时，历史周期也只能观察到真实的采集水位线（`last_synced_at` 是最近一次"提交点收尾"中收尾增量的开始时间，表示"到这个时刻为止数据一致"，见 `04` §6.3）。就绪检查保证它非空且 `as_of > from_dt`（`06` §5.1）。`period.complete = (as_of == to_excl)`，表示请求的周期是否被完整观察到。上一周期的观察时刻为 `from_dt`。
- `end_dt = min(to_excl, as_of)`（即 `as_of`）：§7 起所有"当前周期内"的判定（合并、关闭、ready、review 事件、周的裁剪、cohort）都用 `[from_dt, end_dt)`。`to` 是今天时，`as_of` 之后才写入的变化（例如同步任务进行中抓到的新合并）不计入，避免同一个 PR 既算"已合并"又算"`as_of` 时开着"。上一周期为 `[from_dt - L 天, from_dt)`，观察时刻 `from_dt`。
- `comparison_available`：所有仓库的 `covered_since <= 上一周期起点`。不满足时所有 `previous` 相关字段为 `None`。

### 6.2 加载内容

在**一个 `REPEATABLE READ` 只读事务**中执行下列查询（worker 可能正在逐页写入，同一快照的所有查询必须看到同一份数据），得到不可变的 `Dataset` 后交给纯函数计算（计算部分在 `asyncio.to_thread` 中运行）：

1. 流程 PR 的 `pr_facts`（以及 `pull_requests` 的 `number`、`title`、`url`、`author_login`、`is_draft`、`created_at`），条件为 `ready_at < to_excl` 且（`end_at IS NULL` 或 `end_at >= 上一周期起点`）。
2. 上述 PR 的 `pr_intervals`。
3. at-risk 基线：`end_at ∈ [from_dt - AT_RISK_FALLBACK_DAYS, as_of)` 的已完成等待区间的 `(repo, state, end_at, 时长)`（起点取 `from_dt` 而不是 `as_of`，上一周期的风险 PR 以 `from_dt` 为观察时刻，也需要它之前 180 天的基线）。
4. 两个周期内人工 review 事件的 `(reviewer_lower, occurred_at, pr_id)`（流程 PR）。
5. 被排除的计数：当前周期内合并或关闭的 bot PR、backport PR 数量，以及当前周期内关闭的、从未 ready 的 draft 数量（`meta.excluded`，`06` §4.3）。一个 PR 只计入一类，顺序为 bot → backport → never_ready_draft。
6. 仓库元数据：默认分支、`data_version`、`covered_since`、`last_synced_at`。
7. P1：映射到这些 PR 的 CI 运行记录；位置的 owner 数量。
8. P1：可预测性基线用的历史合并 PR：流程 PR 中 `merged_at ∈ [from_dt - L 天 - 90 天, from_dt)` 的 `(merged_at, cycle_hours)`（不受第 1 项过滤条件的限制，§16）。

## 7. 效率指标（`insights/analytics/efficiency.py`）

记号：`M` = 当前周期内合并的流程 PR（`merged_at ∈ [from_dt, end_dt)`）；`M_prev` 同理（`[from_dt - L 天, from_dt)`）；`Cl` = 最终状态为关闭未合并、且最终 `closed_at ∈ [from_dt, end_dt)` 的流程 PR。按最终结果判定：周期内关闭、之后又重开的 PR 不计入（它后来交付了或仍在进行），一个 PR 最多计一次；这样浪费率衡量的是"没有交付的工作"。所有指标用 `Metric` 结构（`06` §4.2）输出，当前值与上一周期值用同一函数计算。

| 指标 | 定义 | 最小样本 | 显著性 |
|---|---|---|---|
| `merged_prs` | `|M|` | 无 | 不计算（`null`） |
| `effective_throughput` | `|M| - |{p ∈ M : p.reverted_at < as_of}| - |{p ∈ M : p.is_revert}|` | 无 | 不计算（`null`） |
| `cycle_time_p50_hours` / `p90` | `M` 的 `cycle_hours` 分位数 | 20 / 30 | bootstrap 中位数 / p90 差 |
| `stage_p50_hours.{coding,pickup,review,merge}` | `M` 中对应字段非空者的中位数 | 20 | bootstrap |
| `merged_within_n_days` | cohort `C` = `ready_at ∈ [from_dt, min(to_excl, as_of) - N 天]` 的流程 PR（保证每个 PR 都有完整的 N 天观察期）；值为其中 `merged_at - ready_at <= N 天` 的比例；`extra.n_days = N` | 比率规则 | bootstrap 均值 |
| `waiting_share` | 等待占**整个周期**的比例（设计文档：流动效率的近似）：`Σ_M (waiting_reviewer + waiting_ci + waiting_merge) / Σ_M (coding_hours + 四个等待状态时长之和)`。`coding_hours` 为空按 0，`closed` 区间不计入分子和分母。注意时间账（§8）的占比只看 ready 之后的时间，两者分母不同 | 20 | bootstrap 比值（按 PR 成对重抽样） |
| `waste_share` | `D = M ∪ Cl`；`(|Cl 中非 superseded| + |M 中已被 revert|) / |D|` | 比率规则 | bootstrap 均值 |
| `avg_review_rounds` | `M` 的 `review_rounds` 均值 | 20 | bootstrap 均值 |
| `post_review_commit_share` | `M` 中 `commits_after_first_review > 0` 的比例 | 比率规则 | bootstrap 均值 |
| `review_concentration_top_k` | 周期内人工 review 事件中，review 数最多的 `K` 人所占比例；`extra.k = K` | review 数 ≥ 30 | 不计算 |
| `revert_rate` | `|{p ∈ M : reverted_at < as_of}| / |M|` | 比率规则 | bootstrap 均值 |
| `pr_size_p50_lines` | `M` 的 `size_lines` 中位数 | 20 | bootstrap |
| `predictability` | P1，§16 | | |

**比率规则**：分母 ≥ `MIN_RATE_DENOMINATOR` 且事件数 ≥ `MIN_RATE_EVENTS`，否则 `value = None`、`status = "insufficient_sample"`，并在 `extra` 中给出 `{"events": k, "denominator": n}`。

**显著性**（§10）：bootstrap 差值的 90% 区间不含 0，且 `|change_rel| >= CHANGE_MIN_RELATIVE`。

## 8. 时间账

范围为 `M`，每个 PR 取 `[ready_at, merged_at]` 内的等待区间（`coding` 和 `closed` 区间不在其中）：

- `merged_prs = |M|`，`previous_merged_prs = |M_prev|`。
- 每个等待状态 `pr_hours` = 所有 PR 该状态时长之和；`total_pr_hours` = 四个状态之和；`share = pr_hours / total_pr_hours`（总量为 0 时为 0）。
- `previous_pr_hours`、`previous_total_pr_hours`、`previous_share` 用 `M_prev` 同样计算；`change_pp = (share - previous_share) * 100`。上一周期不可用时这些字段为 `null`。
- `ci_coverage` = `M` 中 `ci_covered` 为真的比例；`ci_data_available = CI_SOURCE != "none" and ci_coverage > 0`（P0 永远为假）。
- 输出结构见 `06` §4.5。

## 9. 瓶颈分析（`insights/analytics/bottlenecks.py`）

### 9.1 位置统计

对每个位置 ℓ：`M_ℓ = {p ∈ M : ℓ ∈ p.locations}`，权重 `w_p = 1 / len(p.locations)`（让加总可加）。

| 字段 | 定义 |
|---|---|
| `merged_prs` | `|M_ℓ|` |
| `pickup_p50_hours` | `M_ℓ` 的 pickup 中位数（≥ `MIN_SAMPLES_LOCATION_P50`） |
| `pickup_ratio_vs_rest` | `pickup_p50(M_ℓ) / pickup_p50(M - M_ℓ)`，任一缺失为 `None` |
| `waiting_reviewer_pr_hours` | `Σ w_p × p 的 waiting_reviewer 时长` |
| `previous_waiting_reviewer_pr_hours` | 同样的定义用于 `M_prev` 中带有 ℓ 的 PR；上一周期不可用时为 `null` |
| `waiting_reviewer_share` | `waiting_reviewer_pr_hours` / 时间账 `waiting_reviewer.pr_hours`（分母为 0 时为 0） |
| `inflow` / `outflow` | ℓ 的流程 PR 中 `ready_at` / 有效首次 review 时间 `fr`（§9.2）落在当前周期的数量 |
| `at_risk_prs` | §9.6 中位于 ℓ 的风险 PR 数 |
| `owners_count` | P1：label 位置取 area owner 数；codeowners 位置取规则 owner 数；其他为 `None` |

`merged_prs < MIN_LOCATION_PRS` 的位置合并为 `"other"`：`merged_prs`、`inflow`、`outflow`、`at_risk_prs` 在这些位置的 PR **去重并集**上重新计数（同时属于多个被合并小位置的 PR 只算一次）；按 `1/k` 分摊的 `waiting_reviewer_pr_hours`、`previous_waiting_reviewer_pr_hours` 可以直接相加；中位数和比值在并集上重算；`owners_count = null`。其余位置按 `waiting_reviewer_pr_hours` 降序、名称升序排序，保留前 `MAX_LOCATIONS_IN_SNAPSHOT` 个，之后的也并入 `"other"`；`"other"`（存在时）固定放在最后。多仓库时位置名为 `"{repo}:{location}"`。

### 9.2 review 队列（周粒度）

周从周一开始，裁剪到 `[from_dt, end_dt)`。令每个 PR 的"有效首次 review 时间" `fr = max(first_review_at, ready_at)`（draft 期间就被 review 的 PR 在 ready 时刻离开队列）；没有 review 时 `fr` 为空。对每周 `[ws, we)`，在流程 PR（含未合并）上：

- `inflow` = `ready_at ∈ [ws, we)` 的数量；
- `outflow` = `fr ∈ [ws, we)` 的数量；
- `open_at_week_end` = 在 `we` 时刻"开着"（§2.7，处于 `closed` 区间的不算）且（`fr` 为空或 `fr >= we`）的数量。

每周输出 `week_start`（裁剪后的起始日期）和 `days`（该周在周期内的天数）。

- `weeks_total` = 周数；`weeks_inflow_exceeds_outflow` = `inflow > outflow` 的周数；
- `open_growth_rel` = 最后一周的 `open_at_week_end` / 第一周的 `open_at_week_end` − 1（第一周为 0 时为 `null`）。

### 9.3 合并阻塞

在 `M` 中有 `approved_at` 的 PR 上（合并等待的中位数就是 `efficiency.stage_p50_hours.merge`，这里不重复）：

- `approved_merged_prs`：这些 PR 的数量；
- `second_approval_share`：`distinct_approvers >= 2` 的比例；`second_approval_wait_p50_hours`：其 `second_approval_wait_hours` 的中位数（≥ 10 个样本）；
- `post_approval_update_share`：`updates_after_approval > 0` 的比例（批准后又有 commit 或 force push，作为 rebase / 冲突的近似）；
- `ci_after_approval_p50_hours`：P1，每个 PR 在 `[approved_at, merged_at]` 内与 CI 区间重叠的时长的中位数。因为 `waiting_merge` 优先于 CI，这部分在状态区间里看不到，要直接用 CI 区间计算。P0 为 `None`。

比例的分母小于 10 时为 `None`。

### 9.4 影响排序（Pareto）

条目：`waiting_reviewer` 按位置拆分（取 §9.1 排名前 5 的位置，各自一条；其余合为位置 `"other"`），加上 `waiting_author`、`waiting_ci`、`waiting_merge` 各一条。字段 `cause`、`location`、`pr_hours`、`share`（占时间账总量），按 `pr_hours` 降序。

### 9.5 What-if

对阶段 `s ∈ {pickup, merge}`（M8 后加 `ci`）和目标 `T = WHAT_IF_TARGET_HOURS[s]`：

```text
对每个 p ∈ M（cycle_hours 非空）：
    x = p 在阶段 s 的时长（pickup → pickup_hours；merge → merge_hours；ci → p 的 waiting_ci 总时长），缺失视为 0
    new_cycle = cycle_hours - max(0, x - T)
affected_prs = x > T 的数量
cycle_p50_before / cycle_p50_after = 两组 cycle 的中位数（最小样本 20）
change_rel = after / before - 1；before 或 after 为 None、或 before 为 0 时 change_rel = None
change_rel 为 None 的 what-if 不输出（列表里没有这一项，发现的 what_if 为 None）
```

位置级 what-if（供 review_capacity 发现使用）：只对 `ℓ ∈ p.locations` 的 PR 截断 pickup，其余不变，同样输出全体 `M` 的中位数变化。

### 9.6 风险 PR（at-risk）

1. 候选：在 `as_of` 时"开着"（§2.7）的流程 PR；当时处于 `closed` 区间的 PR 不是候选。`as_of` 为当前时刻（`to` 是今天）时，排除当前 `is_draft` 为真的 PR。
2. 取 `as_of` 时所在区间：状态 `s`、`age_hours = as_of - interval.start_at`。
3. 阈值：按 `(repo, state)` 分组，每个 PR 用**自己仓库**的基线（多仓库查询也一样）。基线 = 同一仓库、状态 `s` 的已完成区间中 `end_at ∈ [as_of - 90 天, as_of)` 的时长；数量 ≥ 30 时用其 p85 / p95，`baseline_source = "90d"`；否则用 180 天窗口（`"180d"`）；再不够用 `AT_RISK_DEFAULT_HOURS[s]` 作为 p85、两倍作为 p95（`"default"`）。
4. `age_hours > p85` 为风险；`> p95` 时 `severity = "critical"`，否则 `"warning"`。
5. 按 `age_hours / threshold_hours` 降序、`(repo, number)` 升序排序；快照的 `at_risk_prs` 保留前 `AT_RISK_MAX_ITEMS` 个（字段见 `06` §4.8，`threshold_hours` 为 p85，`critical_threshold_hours` 为 p95）；`at_risk_summary = {total, critical, by_state}`，`by_state` 固定包含四个等待状态（没有的为 0）。完整列表通过 `/v1/insights/delivery/prs?at_risk=true` 获取。

### 9.7 趋势

输出到快照的 `trend`（`06` §4.10）：

- 各状态占比的变化直接看 `time_ledger.states[*].change_pp`，不重复输出。
- `bottleneck_shift`：上一周期可用时，取 `change_pp` 最大且 ≥ 5 的状态，输出 `f"{state} share {change_pp:+.1f}pp vs previous period"`（例如 `"waiting_ci share +7.2pp vs previous period"`），否则 `None`。
- `attribution`：§9.12。

### 9.8 review 负载

`reviewers` = 周期内做过人工 review 的人数；`reviews` = 人工 review 总数；`distribution` = review 数前 10 的 `{reviewer, reviews, share}`，按 review 数降序、登录名升序。集中度指标在 `efficiency.review_concentration_top_k`。**这是快照中出现个人的两处之一（另一处是风险 PR 的 `author`），证据包不使用它们。**

### 9.9 浪费、返工与护栏

- `waste`：`closed_unmerged = |Cl|`；`by_class`（四类计数）；`lost_while_waiting`；`late_rejections`；`wasted_review_share` = 周期内落在 `Cl` 中非 superseded PR 上的人工 review 数 / 周期内全部人工 review 数（分母 < 30 时为 `None`）；`wasted_pr_hours` = 这些 PR 与 `M` 中已被 revert 的 PR 的时间账总时长。
- `rework`：`reverts` = `M` 中在 `as_of` 前被 revert 的数量；`revert_prs` = `M` 中 `is_revert` 的数量；`relanded` = 前者中已有合并的 reland PR 的数量；`revert_chains` = 按 revert 合并时间降序的最近 10 条 `{original, revert, reland, exposure_hours, revert_pr_cycle_hours}`，其中 `exposure_hours = revert.merged_at - original.merged_at`，`revert_pr_cycle_hours = revert.merged_at - revert.created_at`，PR 用 `{number, url}` 表示。
- `guardrail`：`cycle_time_p50_change_rel`（`efficiency.cycle_time_p50_hours.change_rel`）、`revert_rate`、`previous_revert_rate`（同 §7 的当前值和上一周期值）、`revert_rate_change_pp`（两者之差 × 100）、`verdict`：
  - `"tradeoff_suspected"`：cycle p50 显著下降且 `change_rel <= -0.10`，并且 `revert_rate - previous_revert_rate >= GUARDRAIL_REVERT_RATE_DELTA`（两个值都满足比率规则）；
  - `"watch"`：只满足 revert 率上升条件；
  - 其他（包括任一 revert 率为 `None`）为 `"ok"`。

### 9.10 假设用信号（`signals`）

| 字段 | 定义 |
|---|---|
| `large_pr_share` | `M` 中 `size_lines >= LARGE_PR_LINES` 的比例 |
| `merged_without_approval_share` | `M` 中 `merged_without_approval` 的比例 |
| `fast_large_approval_share` | `M` 中 `size_lines >= FAST_APPROVAL_MIN_LINES`、`first_approval_at - ready_at <= FAST_APPROVAL_MINUTES` 分钟、`feedback_before_approval == 0` 的比例 |
| `external_pickup_ratio` | 外部贡献者与内部成员的 pickup 中位数之比（两组各 ≥ 10 个样本） |
| `at_risk_reviewer_top_location_share` | 状态为 `waiting_reviewer` 的风险 PR 中，按位置计数（有多个位置的 PR 每个位置各计 1 次），最大计数 / 这些风险 PR 的个数；不足 4 个时为 `None` |

均为 `Metric`，含上一周期对比（PR 级比例用 `M_prev`；风险 PR 相关的用 `from_dt` 时点的风险 PR）。

### 9.11 周序列（`series`）

当前周期和上一周期各一组（周的切分同 §9.2），每周按 `merged_at` 归属合并的流程 PR：`week_start`、`days`、`merged`（合并数）、`cycle_p50_hours`、`pickup_p50_hours`、`pr_size_p50_lines`（三者每周 ≥ `MIN_SAMPLES_WEEKLY_P50` 个样本，否则 `None`）、`waiting_reviewer_share`、`waiting_ci_share`（该周合并 PR 的时间账占比；合并数为 0 时为 `None`）、`reverts`（该周合并、且在 `as_of` 前被 revert 的数量）。叙述用它计算效应大小和持续性（`07` §4.1）。上一周期不可用时 `previous` 为空列表。

### 9.12 变化归因（`trend.attribution`）

中位数不可加，归因用"每个合并 PR 的平均小时数"（设计文档：归因用均值或累计 PR-小时）：

- 分量 `c ∈ {coding, waiting_reviewer, waiting_author, waiting_ci, waiting_merge}`：`current_c` = `M` 中该分量时长之和 / `|M|`（`coding` 用 `coding_hours`，空值按 0；等待状态用时间账的 `pr_hours`）；`previous_c` 同样用 `M_prev`；`change_c = current_c - previous_c`。
- `cycle_mean_hours = {current: M 的 cycle_hours 均值, previous: M_prev 的均值, change}`。
- `total_increase_hours = Σ_c max(0, change_c)`；`total_decrease_hours = Σ_c max(0, -change_c)`。
- `share_of_increase_c = max(0, change_c) / total_increase_hours`；`share_of_decrease_c = max(0, -change_c) / total_decrease_hours`（分母为 0 时为 0）。
- `locations`：与 `bottleneck_analysis.locations` 同序、同长。每项 `change = waiting_reviewer_pr_hours / |M| - previous_waiting_reviewer_pr_hours / |M_prev|`；令 `gross = Σ_ℓ max(0, change_ℓ)`（各位置正变化之和）：
  - `share_of_reviewer_increase = max(0, change) / gross`（reviewer 等待的增长内部有多少落在 ℓ；`gross` 为 0 时为 0）；
  - `share_of_increase = states.waiting_reviewer.share_of_increase × share_of_reviewer_increase`（把 reviewer 阶段的**净**正增长按各位置的正变化比例分摊，即"新增时间里有多少是 ℓ 的 reviewer 等待"）。各位置之和等于 `states.waiting_reviewer.share_of_increase`，不会超过 1；等待只是在位置之间转移、reviewer 阶段净增长为 0 时，各位置都为 0。
- `large_prs`：`current` = `M` 中 `size_lines >= LARGE_PR_LINES` 的 PR 的 `cycle_hours` 之和 / `|M|`，`previous` 同样用 `M_prev`；`change`；`share_of_increase = min(1, max(0, change) / cycle_mean_hours.change)`（`cycle_mean_hours.change <= 0` 时为 0）。
- 上一周期不可用，或 `|M|`、`|M_prev|` 小于 `MIN_SAMPLES_P50` 时，`attribution = None`。
- 小时保留 2 位，share 保留 4 位；结构见 `06` §4.10。

## 10. 周期对比与显著性

- 上一周期用完全相同的函数计算。
- `change_abs = value - previous`；`change_rel = change_abs / previous`（`previous` 为 0 或缺失时为 `None`）。
- 显著性只对 §7 表中标注 bootstrap 的指标计算：90% 区间不含 0 且 `|change_rel| >= 0.10`；种子用 `seed_for(params_hash, metric_name)`。

## 11. 发现（findings）与 headline（`insights/analytics/findings.py`）

规则引擎的输出写入快照的 `bottlenecks` 数组（字段见 `06` §4.6）。每条规则产出 0 或 1 条发现（`review_capacity` 每个位置最多 1 条，不含 `other`）。`evidence` 是 `{label, value, unit, ref}` 列表，`ref` 为指向快照内字段的 JSON Pointer，`value` 为该处的值（指向 Metric 时取其 `value`）。条件中引用的值为 `None` 时视为不满足。`|M| < MIN_SAMPLES_P50` 时不产出任何发现（`bottlenecks = []`）：小样本只列具体案例（风险 PR），不下"主要瓶颈"之类的统计结论（设计文档）。

| 类型（id） | 条件 | 严重程度 | `impact_pr_hours` | 建议（英文模板） |
|---|---|---|---|---|
| `review_capacity:{location}` | 位置 `pickup_ratio_vs_rest >= 1.5`、`waiting_reviewer_share >= 0.15`、`merged_prs >= 10` | share ≥ 0.30 为 high，否则 medium | 该位置 `waiting_reviewer_pr_hours` | "Add reviewers or code owners for {location}, enable team auto-assignment, and set a one-business-day first-review SLA." |
| `review_queue_growth` | `weeks_inflow_exceeds_outflow / 周数 >= 0.5` 且最后一周 `open_at_week_end` 比第一周增长 ≥ 20% | 增长 ≥ 50% 为 high | 时间账 `waiting_reviewer.pr_hours` | "Review demand exceeds capacity: rebalance review load or temporarily limit work in progress until the queue stops growing." |
| `review_concentration` | `review_concentration_top_k >= 0.60` | ≥ 0.75 为 high | 0 | "Spread reviews through a rotation or CODEOWNERS so a few reviewers are not a single point of failure." |
| `merge_blocked` | `waiting_merge.share >= 0.15` 或 `stage_p50_hours.merge >= 24` | share ≥ 0.30 为 high | `waiting_merge.pr_hours` | `second_approval_share >= 0.5` 时："Review whether two approvals are needed for low-risk changes."；否则："Reduce post-approval rebase friction, for example with a merge queue." |
| `ci_wait`（P1） | `ci_data_available` 且 `waiting_ci.share >= 0.15` | ≥ 0.30 为 high | `waiting_ci.pr_hours` | `queue_p50_minutes >= run_p50_minutes` 时："Add CI capacity or reduce queued jobs."，否则："Speed up the slowest workflows."；`flaky_rerun_rate >= 0.10` 时再追加 " Fix flaky tests that pass only on rerun." |
| `rework_high` | `avg_review_rounds >= 2.5` 或 `post_review_commit_share >= 0.5` | medium | `waiting_author.pr_hours` | "Agree on the approach before coding (issue or design note) and keep PRs small to cut review rounds." |
| `waste_high` | `waste_share >= 0.15` 或 `lost_while_waiting >= 5` | `waste_share >= 0.25` 为 high | `wasted_pr_hours` | "Review late rejections and PRs lost while waiting for review; align on scope earlier and triage stale PRs." |
| `quality_guardrail` | `guardrail.verdict != "ok"` | `tradeoff_suspected` 为 high，否则 medium | 0 | "Faster delivery may be costing quality: check whether review depth dropped before pushing speed further." |
| `external_contributor_wait` | `external_pickup_ratio >= 2.0` | medium | 外部 PR 的 `waiting_reviewer` 时长之和 | "Set up a triage rotation so community PRs get a first review sooner." |

标题与证据（`{i}` 为该位置在 `bottleneck_analysis.locations` 中的下标）：

| 类型 | `title` | `evidence`（label → ref） |
|---|---|---|
| `review_capacity` | `First-review wait concentrated in {location}` | `First-review wait vs rest of repo` → `/bottleneck_analysis/locations/{i}/pickup_ratio_vs_rest`；`Share of reviewer-waiting time` → `/bottleneck_analysis/locations/{i}/waiting_reviewer_share`；`Median first-review wait` → `/bottleneck_analysis/locations/{i}/pickup_p50_hours` |
| `review_queue_growth` | `Review queue is growing` | `Weeks with inflow above outflow` → `/bottleneck_analysis/review_queue/weeks_inflow_exceeds_outflow`；`Open queue growth` → `/bottleneck_analysis/review_queue/open_growth_rel` |
| `review_concentration` | `Reviews concentrated on a few people` | `Share of reviews by top K reviewers` → `/efficiency/review_concentration_top_k` |
| `merge_blocked` | `Approved PRs wait long to merge` | `Share of PR time waiting to merge` → `/time_ledger/states/waiting_merge/share`；`Median approval-to-merge time` → `/efficiency/stage_p50_hours/merge`；`Share with a second approval` → `/bottleneck_analysis/merge_blockers/second_approval_share` |
| `ci_wait` | `CI waiting is a large share of PR time` | `Share of PR time waiting on CI` → `/time_ledger/states/waiting_ci/share`；`Median CI queue time` → `/bottleneck_analysis/ci/queue_p50_minutes`；`Median CI run time` → `/bottleneck_analysis/ci/run_p50_minutes` |
| `rework_high` | `High rework after review` | `Average review rounds` → `/efficiency/avg_review_rounds`；`Share with commits after first review` → `/efficiency/post_review_commit_share` |
| `waste_high` | `Significant work never ships` | `Waste share` → `/efficiency/waste_share`；`PRs lost while waiting for review` → `/waste/lost_while_waiting` |
| `quality_guardrail` | `Speed may be costing quality` | `Median cycle time change` → `/guardrail/cycle_time_p50_change_rel`；`Revert rate` → `/guardrail/revert_rate` |
| `external_contributor_wait` | `External contributors wait longer for a first review` | `First-review wait, external vs internal` → `/signals/external_pickup_ratio` |

- `id`：`{type}` 或 `{type}:{location}`（只有 `review_capacity` 带位置）。
- `impact_share = impact_pr_hours / time_ledger.total_pr_hours`（总量为 0 时为 0）。
- 排序（设计文档：按累计等待决定先修什么）：`impact_pr_hours` 降序 → 严重程度（high > medium > low）→ `id` 升序；`rank` 从 1 开始。
- `what_if`：`review_capacity` 用位置级 pickup what-if；`merge_blocked` 用 merge what-if；`ci_wait` 用 ci what-if；其他为 `None`。

**headline**（英文，确定性模板）：

1. 效率（按顺序取第一个适用的）：
   - cycle p50 显著变化：`"Median cycle time rose|fell {|change_rel|×100:.0f}% ({previous:.1f}h → {value:.1f}h)"`；
   - 有值、有 `change_rel` 但不显著：`"Median cycle time is {value:.1f}h ({change_rel×100:+.0f}% vs previous period, not significant)"`；
   - 有值但没有上一周期（`previous` 或 `change_rel` 为 `None`）：`"Median cycle time is {value:.1f}h (no previous period to compare)"`；
   - 没有值：`"Not enough merged PRs for a reliable cycle time"`。
2. 主要瓶颈（存在发现时）：`"; the main bottleneck is " + 第一条发现的标题（首字母小写）`；若第一条是 `review_capacity` 或 `review_queue_growth`，追加 `" ({waiting_reviewer.share×100:.0f}% of PR time waits on reviewers)"`。
3. 预期收益（第一条发现的 `what_if` 非空、且其 `change_rel` 非空时）：`". Capping {stage} at {target:.0f}h would cut median cycle time by about {|change_rel|×100:.0f}%"`。
4. 以句号结尾。

## 12. 快照组装（`insights/analytics/snapshot.py`）

### 12.1 结构

严格按 `06` §4 组装（顶层键见 `06` §4.1，`meta` 各字段的定义见 `06` §4.3）。多个仓库时 `per_repo` 每个仓库一项（对该仓库单独计算的 `merged_prs`、`cycle_time_p50_hours`、`pickup_p50_hours`、`waiting_share`，都是数字），单仓库时为 `null`。P1 段尚未实现时为 `null`。

纯函数入口：`build_snapshot(dataset: Dataset, *, params: SnapshotParams) -> dict[str, Any]`，返回可直接规范化序列化的字典（不含 `snapshot_id` 以外的随机或时间相关字段）。

### 12.2 取整与序列化

- 小时保留 2 位小数；比例（`_share`、`_rate`、`waiting_share` 等 0–1 值）保留 4 位；倍数（`ratio`）保留 2 位；计数为整数。
- 时间输出为 `YYYY-MM-DDTHH:MM:SSZ`。
- 规范化 JSON：`orjson.dumps(obj, option=orjson.OPT_SORT_KEYS)`。所有列表都有明确排序（见各节），集合一律转为排序后的列表。

### 12.3 ID 与 ETag

```text
canonical(x)  = orjson.dumps(x, option=orjson.OPT_SORT_KEYS)；sha256(...) 取 hexdigest
params        = {"repos": 排序后的小写仓库名, "from": ISO 日期, "to": ISO 日期,
                 "location_dimension": ..., "directory_depth": ..., "ci_source": ...,
                 "analytics_version": ..., "thresholds_version": ...}
params_hash   = sha256(canonical(params))[:16]
versions_hash = sha256(canonical(meta.data_freshness))[:16]
snapshot_id   = "s_" + sha256(f"{params_hash}|{versions_hash}|{as_of_iso}")[:16]
etag          = '"' + sha256(payload_bytes)[:32] + '"'
```

- `meta.data_freshness`（`06` §4.3）包含每个仓库的 `data_version`、`covered_since`、`last_synced_at`、`last_sync_status`。快照里显示的这些字段因此也是 ID 的输入：同样的参数、数据状态和观察时刻永远得到同样的快照 ID 和字节；任何一项变化都会得到新的 ID，旧快照不会被改写。
- `snapshot_id` 只依赖参数和 `repositories` 表中的几列，不读取快照就能算出，所以 Redis 和 Postgres 都直接按 `snapshot_id` 查找（`06` §5.1），不需要额外的索引键。
- 位置维度、目录深度或算法、阈值版本变化后，worker 会整仓重推导并递增 `data_version`（`04` §6.5）。重推导完成之前 `repositories.derived_key` 与当前推导标识不一致，API 返回 202（`06` §5.1），不会用旧的推导结果发布标着新配置的快照。

## 13. CI 指标（P1，`insights/analytics/ci.py`）

输出到 `bottleneck_analysis.ci`（结构见 `06` §4.12）。在映射到流程 PR 的运行记录上（周期按 `created_at` 归属）：

- `queue_p50_minutes` = `run_started_at - created_at` 的中位数；
- `run_p50_minutes` = 已完成运行的 `updated_at - run_started_at` 的中位数；
- `rerun_rate` = `run_attempt > 1` 的比例；`flaky_rerun_rate` = `run_attempt > 1` 且 `conclusion == "success"` 的比例；
- `runs_per_pr_p50`；`coverage` = `time_ledger.ci_coverage`；
- `top_workflows` = 按总运行分钟数前 5 的 `{workflow_name, runs, run_p50_minutes, rerun_rate}`；
- 全部带上一周期对比。CI 区间（用于状态机）= 每个映射运行的 `[created_at, updated_at)` 的并集（等 CI 包括排队时间）。

## 14. Drivers（P1，`insights/analytics/drivers.py`）

| 字段 | 定义 |
|---|---|
| `assignment` | `assigned` = `M` 中 `review_requested_before_first_review` 为真的组的 `{n, pickup_p50_hours}`；`unassigned` = 为假的组；`ratio = unassigned.pickup_p50_hours / assigned.pickup_p50_hours` |
| `review_round_cost` | `buckets`：按 `review_rounds` 分桶 `"0"`、`"1"`、`"2"`、`"3+"` 的 `{rounds, n, cycle_p50_hours}`；`hours_per_extra_round` = 相邻桶中位数之差的中位数；`re_review_wait_p50_hours` = 紧跟在 `waiting_author` 区间之后的 `waiting_reviewer` 区间时长的中位数（作者更新后等下一次 review）；`first_pickup_p50_hours` = `stage_p50_hours.pickup` 的值 |
| `author_wip` | `buckets`：按 `author_open_prs_at_ready` 分桶 `"0"`、`"1-2"`、`"3+"` 的 `{wip, n, waiting_author_p50_hours}`（每个 PR 的 `waiting_author` 总时长的中位数）；`spearman` = 两者的 Spearman 相关系数（numpy：对秩求 Pearson） |
| `submit_timing` | `by_weekday`：按 `ready_weekday`（0–6）的 `{weekday, n, pickup_p50_hours}`；`by_hour_block`：按 `ready_hour` 分 `"00-05"`、`"06-11"`、`"12-17"`、`"18-23"`（UTC）的 `{hours, n, pickup_p50_hours}` |
| `slowest_decile` | `M` 中 `cycle_hours` 最慢的 10%（向上取整）与其余对比：`{n, features: [{feature, slowest, rest, ratio}]}`，`feature` 依次为 `size_lines_p50`、`external_share`、`multi_location_share`（≥ 2 个位置）、`review_rounds_p50`、`unrequested_share`（`review_requested_before_first_review` 为假）；`ratio = slowest / rest`（`rest` 为 0 时为 `None`）；`|M| < 50` 时整个子项为 `None` |

输出到快照的 `drivers`（结构见 `06` §4.12）。分桶或分组样本少于 10 时对应的中位数为 `None`，不要报错。

## 15. 生存分析（P1）

- cohort：`ready_at ∈ [from_dt, end_dt)` 的流程 PR，观察时刻 `as_of`；上一周期 cohort 为 `ready_at` 落在上一周期的流程 PR，观察时刻 `from_dt`。
- 时间：在观察时刻前合并的为 `merged_at - ready_at`（事件）；否则在 `min(观察时刻, closed_at) - ready_at` 处删失。
- Kaplan–Meier：按时间升序，`S(t) = Π (1 - d_i / n_i)`（同一时刻先处理事件再处理删失）。
- 每个 cohort 输出 `KM = {n, events, median_hours, s_at_hours}`：`median_hours` 为第一个 `S(t) <= 0.5` 的 t（不存在为 `None`），`s_at_hours` 为 24、72、168、336 小时处的 S（键为字符串）。
- 快照：`efficiency.survival = {current: KM, previous: KM | None}`（`06` §4.12）；cohort 少于 20 个 PR 时对应 KM 为 `None`。

## 16. 可预测性（P1）

- `within_hist_p85`：基线 = `merged_at ∈ [from_dt - 90 天, from_dt)` 的流程 PR 的 cycle p85（≥ 30 个样本）；值 = `M` 中 `cycle_hours <= 基线 p85` 的比例（稳定时约为 0.85）。上一周期值用 `M_prev` 和 `[prev_from - 90 天, prev_from)` 的基线。数据来自 §6.2 第 8 项；基线窗口的起点早于 `covered_since` 时基线不可计算：`value = None`、`status = "insufficient_sample"`、`extra.reason = "baseline_not_covered"`。
- `weekly_throughput_cv`：周期内完整周（7 天）的周合并数的总体标准差 / 均值（完整周 ≥ 4 时计算）；上一周期同样计算。
- 两者都是 Metric（`unit` 分别为 `share`、`coefficient`），不计算显著性；快照路径 `efficiency.predictability`。

## 17. PR 明细行（`insights/analytics/rows.py`）

`build_pr_rows(dataset: Dataset) -> list[dict[str, Any]]`，供 `/v1/insights/delivery/prs` 使用（字段见 `06` §7.2）。只包含流程 PR，三类总体：

- `merged`：`M` 中的 PR；
- `closed`：`Cl` 中的 PR；
- `open`：在 `as_of` 时"开着"（§2.7）的 PR。

每行的 `ledger_hours` 为 `[ready_at, min(end_at, as_of)]` 内各等待状态的时长；`open` 行的 `current_state`、`current_state_age_hours` 来自 `as_of` 时所在区间；`at_risk` 使用与 §9.6 相同的候选规则（包括"`as_of` 为当前时刻时排除当前是 draft 的 PR"）和阈值函数（同一次计算的基线），所以 `at_risk` 非空的行数等于 `at_risk_summary.total`。函数返回全部行，不截断；筛选、排序和分页在 API 层按 `06` §5.2 完成。
