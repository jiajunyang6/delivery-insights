# 08 Eval harness（P1，M10；生成器、流水线和场景定义在 M5 实现）

## 1. 目的

用已知答案的合成场景评估 Endpoint 2：数字是否全部来自证据、引用是否有效、植入的根因能否被识别、没有信号时是否正确地不下结论、"较高"档是否可信。修改 prompt、假设库、阈值或更换模型之前都要跑一遍。

- `make eval-offline`：用 `StubLLMClient`（§8），不需要任何凭证，可以放进 CI；检验确定性部分（证据包、假设评分、校验器、模板）。
- `make eval`：用真实的 Bedrock（需要 `AWS_BEARER_TOKEN_BEDROCK`），检验 LLM 的表现。

eval 不访问数据库、Redis 或 GitHub：合成数据 → 纯函数推导 → 快照 → `narrative.service.generate`（`07` §9.1）。

## 2. 合成数据生成器（`insights_eval/generator.py`）

### 2.1 接口

```python
@dataclass(frozen=True, slots=True)
class ScenarioSpec:
    name: str
    pickup_mult: Mapping[str, float]          # 按 area 的首次 review 等待倍数（只作用于当前周期）
    arrival_mult: Mapping[str, float]         # 按 area 的 PR 到达率倍数（当前周期）
    area_reviewers: Mapping[str, int]         # 当前周期每个 area 可用的 reviewer 人数（默认 4）
    size_mult: float                          # PR 大小中位数倍数（当前周期）
    first_approval_bonus: float               # 首次 review 直接批准的概率增量（当前周期）
    rubber_stamp_large_share: float           # 当前周期大 PR 被"10 分钟内无评论批准"的比例
    revert_rate: tuple[float, float]          # (上一周期, 当前周期) 的 revert 概率
    ci_enabled: bool
    ci_queue_mult: float                      # 当前周期 CI 排队时长倍数
    ci_run_mult: float                        # 当前周期 CI 运行时长倍数
    flaky_rate: tuple[float, float]           # (上一周期, 当前周期)
    reviewers_wait_for_ci: bool

    @classmethod
    def baseline(cls) -> "ScenarioSpec": ...  # 所有倍数为 1、增量为 0、revert (0.02, 0.02)、ci_enabled False

@dataclass(frozen=True, slots=True)
class SyntheticRepo:
    repo: str                                 # "synthetic/repo"
    default_branch: str                       # "main"
    records: tuple[PullRequestRecord, ...]
    ci_runs: tuple[CiRun, ...]
    period_from: date
    period_to: date
    as_of: datetime

def generate(spec: ScenarioSpec, seed: int) -> SyntheticRepo
```

- 唯一的随机源：`numpy.random.default_rng(seed)`；按"天 → 当天第几个 PR"的固定顺序生成，保证同一种子得到完全相同的数据。
- 生成的是领域对象（`04` §2.1），事件的 `dedup_key` 用 `insights.sources.github.normalize` 中的同一个函数计算，`stable` 用 `f"syn-{PR 编号}-{该 PR 内的事件序号}"`（`04` §5.4；review 事件的 `review_id`、撤销事件引用的 `review_id` 取对应 review 的这个值）。
- 对数正态时长记作 `LN(m, s)` = `m * exp(s * z)`，`z ~ N(0, 1)`，单位小时（另有说明除外）。

### 2.2 日历（固定，不依赖当前日期）

| 名称 | 区间（UTC，含首尾日） |
|---|---|
| 历史（风险基线用） | 2025-09-08 → 2025-12-07 |
| 上一周期 | 2025-12-08 → 2026-01-18（6 周，周一开始） |
| 当前周期 | 2026-01-19 → 2026-03-01（6 周，周一开始） |
| `as_of` | 2026-03-02T00:00:00Z（= 当前周期 `to` 的次日零点） |

### 2.3 人员与位置

- area label：`area-A` … `area-E`，权重 0.30、0.25、0.20、0.15、0.10。90% 的 PR 带一个 area label，5% 带两个，5% 不带（文件路径仍在对应目录下，用于目录回退）。
- 文件：1–5 个，路径 `src/{A..E}/file{k}.cs`。
- 作者：内部 `dev01`–`dev40`（`MEMBER`）；外部 `ext01`–`ext30`（`CONTRIBUTOR`），占 20%。
- reviewer：每个 area 4 人（`rev-a1`…`rev-a4` 等），另有跨 area 的 `rev-x1`…`rev-x4`，承担 20% 的 review。
- 5% 的 PR 由 `dependabot[bot]` 发起（`author_type = "Bot"`，一次批准后合并）；3% 的 PR `base_ref = "release/9.0"`（backport）。

### 2.4 单个 PR 的生成

1. **到达**：工作日每天 `Poisson(15 × arrival_mult[area])`，周末 `Poisson(5)`；`created_at` 在当天均匀分布。PR 编号从 1000 递增；标题 `Change {number} in {area}`。
2. **大小**：`size = max(1, round(LN(80, 1.1) × size_mult))`，`additions = round(0.7 × size)`，`deletions = size - additions`，`changed_files = max(1, size // 40)`。
3. **draft 与 ready**：15% 以 draft 创建，`ready = created + LN(10, 0.8)`（生成 `ready_for_review` 事件）；首个 commit 的 `authored_at = created - LN(4, 1.0)`。非 draft：`ready = created`，`authored_at = created - LN(12, 1.0)`。commit 的 `committed_at = authored_at + LN(0.5, 0.5)`（但不早于 `created`）。
4. **请求 review**：70% 的 PR 在 ready 时由作者请求一名 reviewer（`review_requested`）。
5. **首次 review**：`first_review = ready + LN(6 × area_mult × pickup_mult[area], 1.0)`，基础 `area_mult` 为 A 1.0、B 1.2、C 0.9、D 1.1、E 1.0。reviewer 80% 取本 area 可用的 reviewer（人数 `area_reviewers[area]`），20% 取跨 area reviewer（`review_capacity` 场景中 area-B 只用本 area reviewer）。`reviewers_wait_for_ci` 为真时，`first_review = max(first_review, 最新 CI 结束 + LN(0.5, 0.5))`。
6. **首次结论**：直接批准的概率按大小：`< 100` 行 0.55，`100–499` 行 0.35，`≥ 500` 行 0.20，再加 `first_approval_bonus`（上限 0.95）。否则 60% `CHANGES_REQUESTED`、40% `COMMENTED`。当前周期 `size >= 500` 的 PR 中有 `rubber_stamp_large_share` 比例改为：ready 后 `LN(0.1, 0.3)` 小时内直接 `APPROVED`、没有任何评论。
7. **返工轮次**：每次非批准的 review 后，作者在 `LN(8, 1.0)` 后回应（80% 新 commit，20% 评论回复），reviewer 在 `LN(5, 1.0)` 后再次 review；第 k 次再 review 的批准概率 `min(0.95, 0.6 + 0.1k)`；最多 5 轮，第 5 轮必定批准。
8. **第二位批准者**：30% 的 PR 在首次批准后 `LN(4, 1.0)` 获得另一位 reviewer 的批准。
9. **合并**：批准后 `LN(3, 1.0)` 合并（`merged` 事件，`merged_by` 为最后一位 reviewer）；10% 在批准后、合并前有一次 commit（rebase）。
10. **结局**：85% 合并；10% 关闭未合并，其中 30% 无人 review 就被作者关闭（ready 后 `LN(120, 0.6)`）、30% 在 `CHANGES_REQUESTED` 后被 reviewer 关闭、25% 在等作者修改时被作者关闭、15% 被替代（作者关闭，同一作者的新 PR 在关闭前后 1 天内创建并最终合并；**旧 PR** 的时间线上有一个 `cross_referenced` 事件，`source` 为新 PR——GitHub 把交叉引用记在被引用的 PR 上）；5% 到 `as_of` 仍开着（在某个等待状态停止推进）。
11. **revert 与 reland**：合并的 PR 以 `revert_rate`（按合并时间所在周期取值）被 revert：另一位开发者在合并后 `LN(30, 0.8)` 创建 `Revert "{原标题}"`，正文 `Reverts synthetic/repo#{原编号}`，3 小时内批准并合并。被 revert 的 PR 中 50% 在 revert 后 `LN(72, 0.5)` 有 `Reland "{原标题}"` PR，按普通流程合并。
12. **CI**（`ci_enabled` 为真时）：PR ready 后 1 分钟有一次运行（对应最新 commit），之后 ready 之后的每个 commit 在 `committed_at + 1 分钟` 各有一次运行。排队 `LN(10/60, 0.8) × ci_queue_mult`，运行 `LN(1.0, 0.5) × ci_run_mult`；以 `flaky_rate` 的概率重跑：`run_attempt = 2`、最终成功、`updated_at` 再加一次运行时长。`head_sha` 为对应 commit 的 oid，`pr_numbers = (number,)`。倍数只作用于当前周期创建的运行。
13. 生成的事件时间不得晚于 `as_of`；晚于的事件丢弃（对应"到 as_of 仍开着"）。

### 2.5 从记录到快照（`insights_eval/pipeline.py`）

```python
def build_snapshot_from_repo(
    syn: SyntheticRepo, *, location_dimension: str = "label:area-", covered_since: datetime | None = None,
) -> dict[str, Any]
```

按与线上相同的顺序调用纯函数：`timeline.build_timeline` → `facts.compute_facts` → `classify.link_prs` → 构造 `analytics.dataset.Dataset`（与 `load_dataset` 返回的类型相同；`data_freshness` 用 `data_version = 1`、`covered_since = covered_since or 历史起点`、`last_synced_at = as_of`、`last_sync_status = "ok"`；传入当前周期起点时 `comparison_available = false`（记录本身不过滤），供 `test_template.py` 测试"没有上一周期"的模板）→ `analytics.snapshot.build_snapshot`。返回解析后的快照字典。M5 的黄金测试使用 `generate(ScenarioSpec.baseline(), seed=42)`。

## 3. 场景（`insights_eval/scenarios.py`，M5 实现）

除列出的字段外都取 `ScenarioSpec.baseline()` 的值。场景定义在 M5 与生成器一起实现（M7 的 `test_template.py` 要用到非 CI 的四个场景）；`ci_slowdown` 依赖 M8 的 CI 推导，在 M8 之后才参与测试和 eval。

| 场景 | 当前周期的变化 | 期望 |
|---|---|---|
| `review_capacity` | `pickup_mult = {"area-B": 4.0}`；`arrival_mult = {"area-B": 1.4}`；`area_reviewers = {"area-B": 1}` | 第一个假设 `H_review_capacity`，`location = "area-B"` |
| `ci_slowdown` | `ci_enabled = True`（两个周期都有 CI）；`ci_queue_mult = 6.0`；`ci_run_mult = 2.0`；`flaky_rate = (0.03, 0.15)`；`reviewers_wait_for_ci = True`（两个周期都为真） | 第一个假设 `H_ci_bottleneck`（eval 中 `ci_complete = True`） |
| `pr_size_growth` | `size_mult = 3.0` | 第一个假设 `H_pr_size_growth` |
| `quality_tradeoff` | `pickup_mult` 所有 area 0.4；`first_approval_bonus = 0.35`；`rubber_stamp_large_share = 0.5`；`revert_rate = (0.02, 0.09)` | 第一个假设 `H_quality_tradeoff` |
| `no_signal` | 无变化 | 不输出假设（`abstained = true`） |

植入的效应要足够明显：如果 `make eval-offline` 达不到门槛，先检查生成器是否按本节实现、假设判定是否按 `07` §3 实现；**不要**为了通过 eval 去改 `07` 的阈值。确实需要调整时记入 `docs/DECISIONS.md`。

## 4. 运行器（`insights_eval/run.py`）

```bash
python -m insights_eval.run --llm {stub,bedrock} [--seeds 101,202] [--scenarios review_capacity,...] [--out reports]
```

- 默认种子 `101,202`（42 留给黄金测试）；默认全部 5 个场景；每个场景 × 种子跑两个组合：`director/en`、`manager/zh`。默认共 20 次。
- 每次：`generate` → `build_snapshot_from_repo` → `narrative.service.generate(snapshot, audience, lang, llm, ci_complete=True, now=固定时间)`。
- `--llm bedrock`：用 `BedrockClient`（读取 `AWS_REGION`、`BEDROCK_MODEL_ID`）；没有 `AWS_BEARER_TOKEN_BEDROCK` 时打印 `AWS_BEARER_TOKEN_BEDROCK is not set` 并以退出码 2 结束。顺序执行，不并发。
- `--llm stub`：用 `StubLLMClient`。
- `--out` 默认 `backend/reports`（相对 `backend/` 即 `reports/`，已在 `.gitignore` 和 `.dockerignore` 中）。

## 5. 指标（`insights_eval/metrics.py`）

| 指标 | 定义 |
|---|---|
| `first_attempt_valid_rate` | 调用了 LLM 的运行中，第 1 次输出就通过校验的比例 |
| `numeric_consistency` | `generated_by = "llm"` 的运行中，对最终响应重新执行 V5（`07` §7：逐句、只用本句引用的条目、单位类别和变化方向）全部通过的比例 |
| `citation_validity` | `generated_by = "llm"` 的运行中，对最终响应重新执行 V4、V6 全部通过的比例 |
| `hedge_consistency` | `generated_by = "llm"` 的运行中，对最终响应（按最终档位）重新执行 V7、V7b、V12 全部通过的比例 |
| `root_cause_hit_rate` | 有期望假设的场景中，响应 `hypotheses` 的第一项（按 `07` §9.3 下调后重排的顺序）ID 等于期望（`review_capacity` 还要求 `location` 等于期望）的比例 |
| `abstention_rate` | `no_signal` 场景中 `abstained = true` 且 `hypotheses` 为空的比例 |
| `high_precision` | 所有运行中 `confidence_level = "high"` 的假设里，ID 等于该场景期望假设的比例（`no_signal` 中的 high 一律算错）；没有 high 时为 `null` |
| `fallback_rate` | `generated_by = "template"` 的运行比例 |
| 校准表（只报告） | 每个档位：假设数、命中数、命中率；目标 high ≥ 80%、medium ≥ 60%、low ≥ 40%（设计文档的校准方法） |

校准表只说明评分在**合成场景**（答案已知、效应明显）上的表现，不等于在真实仓库上校准过。置信度是确定性的证据强度分数，不是概率；用真实历史回测阈值、用人工标注校准档位是暂缓项，写进 DECISIONS 和 README 的 trade-offs 与 Not done（`11` §7.1、§7.2）。

"重新执行"指用最终响应（不是 LLM 原始输出）和同一证据包再跑一次对应规则，防止组装环节引入错误。

## 6. 门槛

| 指标 | 门槛 |
|---|---|
| `first_attempt_valid_rate` | ≥ 0.90 |
| `numeric_consistency` | = 1.00 |
| `citation_validity` | = 1.00 |
| `hedge_consistency` | = 1.00 |
| `root_cause_hit_rate` | ≥ 0.80 |
| `abstention_rate` | ≥ 0.80 |
| `high_precision` | ≥ 0.80（为 `null` 时视为通过并打印警告） |
| `fallback_rate` | ≤ 0.10 |

任一门槛未达到时以退出码 1 结束。stub 模式使用同样的门槛（模板必然通过校验，所以前两项应为 1.0）。

## 7. 报告与对比

- 写入 `reports/eval-{YYYYMMDDTHHMMSSZ}-{llm}.json`：`started_at`、`llm`、`model`、`prompt_version`、`analytics_version`、`seeds`、`runs[]`（每次运行的 `scenario`、`seed`、`audience`、`lang`、`expected`、`generated_by`、`validation`、`attempts`、`fallback_reason`、`violations`、`top_hypothesis`、`top_level`、`abstained`、`hit`、`duration_ms`、`input_tokens`、`output_tokens`）、`metrics`、`gates`（每项 `value`、`threshold`、`passed`）、`passed`。
- 控制台打印每次运行一行的结果表和指标表；如果 `reports/` 中有同一 `llm` 模式的上一份报告，再打印各指标的变化。
- 报告不包含叙述全文以外的任何敏感信息（合成数据本身不敏感）；叙述全文可以放进 `runs[].narrative` 方便人工抽查。

## 8. `StubLLMClient`（`insights_eval/stub_llm.py`）

实现 `LLMClient` 协议（`07` §6.1），`model_id = "stub"`。`submit` 从用户消息中解析证据包 JSON，调用 `narrative.template` 生成与模板相同的正文和假设说明，按工具输入的格式返回（`narrative`、`hypotheses[].id`、`hypotheses[].statement`）。它让 offline eval 覆盖"证据包 → 校验 → 组装"的完整路径，结果完全确定。
