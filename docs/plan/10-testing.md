# 10 测试

## 1. 策略

测试集中在最容易算错、出错代价最高的地方：等待状态记账、时间归属、分类规则、统计口径、LLM 输出校验和 HTTP 契约。LLM 本身的效果由 eval harness 评估（`08`）。

| 层 | 位置 | 依赖 | 命令 |
|---|---|---|---|
| 单元 | `backend/tests/unit/` | 无（不需要 Docker、网络、凭证） | `make test-unit` |
| 集成 | `backend/tests/integration/`，标记 `@pytest.mark.integration` | testcontainers 的 Postgres 16、Redis 7 | `make test` |
| 黄金 | `backend/tests/golden/` | 合成数据（`08` §2） | 包含在单元测试中 |
| Eval | `backend/eval/insights_eval/` | stub 或 Bedrock | `make eval-offline` / `make eval` |

## 2. 约定与工具

- `pytest-asyncio`（`asyncio_mode = "auto"`）；`respx` 模拟 GitHub，`respx.mock(assert_all_mocked=True)`，确保没有真实网络请求。
- **时间可注入**：领域代码不直接调用 `datetime.now()`。API 通过依赖 `insights.api.deps.get_now` 获取当前时间，worker 任务和服务函数接收 `now` 参数。测试覆盖 `get_now`，把"今天"固定为合成日历的 `2026-03-02`，这样合成数据能通过 `06` §3.2 的日期校验。
- GitHub 客户端的 `sleep` 可注入（`04` §4.1），测试用记录调用参数的假函数，不真的等待。
- LLM 用 `FakeLLMClient`（`07` §6.4）；Bedrock 客户端用 `botocore.stub.Stubber`。
- 夹具：GraphQL 响应 JSON 放在 `tests/fixtures/github/`，按 `04` §3 的查询结构手写（每个文件注明覆盖的场景）。
- 集成测试的 `tests/integration/conftest.py`：
  - 会话级启动 `PostgresContainer("postgres:16-alpine")` 和 `RedisContainer("redis:7-alpine")`，执行一次 `alembic upgrade head`；
  - 每个测试前 `TRUNCATE` 所有表（`RESTART IDENTITY CASCADE`）并 `FLUSHDB`；
  - `app` 夹具：`create_app()` + 依赖覆盖（数据库、Redis、`get_now`、`Settings`）；客户端用 `httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")`；
  - 辅助函数 `seed_synthetic(session, spec, seed)`：写入 `repositories` 行（`synthetic/repo`，`covered_since` = 合成历史起点，`last_open_sweep_at` = `last_synced_at` = `as_of`，`derived_key` = 当前推导标识，状态 `ok`），把 `insights_eval.generator` 的记录按 50 条一页交给 `store.save_page`，最后调用 `derive.link_repo`。设置 `TRACKED_REPOS=synthetic/repo`。

## 3. 单元测试用例

每个文件至少覆盖下列用例；用例名用英文、描述行为（例如 `test_reply_without_push_returns_to_waiting_reviewer`）。

**`test_config.py`**：列表字段解析（空白、空项）；非法仓库名、`SYNC_INTERVAL_MINUTES` 不整除 60、`OPEN_SWEEP_MINUTES` 不是其倍数、`BACKFILL_DAYS` 越界时启动报错；`backfill_phases`（`BACKFILL_DAYS=30` 时为 `[7, 30]`）；`SecretStr` 的 `repr` 不含原文；空的 `GITHUB_TOKEN=` 得到 `None`（`env_ignore_empty`）；`llm_enabled`。

**`test_logging.py`**：输出是一行合法 JSON 且包含 `timestamp`、`level`、`event`、`logger`；标准库 logging 的记录输出同样的 JSON；`httpx` 等第三方 logger 的级别为 `WARNING`。

**`test_health.py`**：`/healthz` 200；`/readyz` 在数据库或 Redis 失败时返回 503 problem，`checks` 标明失败项且不含异常原文。

**`test_github_client.py`**：
- GraphQL 成功并返回 `rateLimit`；
- `retry-after` 头 → 按秒数等待后重试；
- `x-ratelimit-remaining: 0` → 等到 `x-ratelimit-reset + 5` 秒；
- 二级限流（403 无上述头）→ 60、120、240 秒退避，第 4 次失败抛 `GitHubRateLimited`；
- 502 / 超时 → 2、4、8 秒退避；
- 401 → `GitHubAuthError`，不重试；
- GraphQL `NOT_FOUND` → `GitHubNotFoundError`；
- `Something went wrong` → 页大小减半（25 → 12 → 6 → 5，不低于 5）用同一游标重试；
- `rateLimit.remaining < 200` → 主动等待到 `resetAt + 5`；
- 累计等待超过 15 分钟 → `GitHubRateLimited`；
- REST：第二次请求带 `If-None-Match`，收到 304 时返回缓存的 body；
- `rest_get` 传入完整 URL（`https://evil.example/...`）时抛 `ValueError`；
- 成功和 401 路径的日志、`GitHubAuthError` 的文本中都不出现 token（`caplog`；客户端不记录请求头）。

**`test_normalize.py`**：`__typename == "Bot"`、`[bot]` 后缀、内置名单、`EXTRA_BOT_LOGINS`；`PENDING` review 被丢弃；上游 `state = DISMISSED` 的 review：时间线中有 `review.id` 相同的 `ReviewDismissedEvent` 时 `payload.state` = 它的 `previousReviewState`、`dismissed = true`，没有时该 review 被丢弃；`ReviewDismissedEvent` 的 `review_id`、`review_author`、`previous_state`；`dedup_key` 用时间线节点 `id`：同一秒、同一 actor 的两条撤销事件得到两个不同的 `dedup_key`，都被保留；PR 作者为 `null` 时 `login = None`、`author_type = "Unknown"`；commit 消息解析 `reverts`；非 PR 来源的交叉引用被丢弃；已删除账号；`dedup_key` 稳定且重复事件去重；`content_hash` 与事件输入顺序无关；`files_truncated`；`body_excerpt` 截断到 4,000 字符。

**`test_timeline.py`**：`05` §2.5 的全部 22 个用例；`05` §2.7 的"时点开着"判定（`ready_at` 当刻开着、`end_at` 当刻不开着、关闭期内不开着、从未 ready 的 draft 不开着）和截断；外加随机测试：用固定种子生成 200 条随机但合法的事件序列（包含关闭后重开、撤销 review、作者为空），断言 `05` §2.6 的四条不变式全部成立。

**`test_facts.py`**：各阶段时长（含 `max(0, …)` 截断）；`review_rounds`；`feedback_before_approval`；`commits_after_first_review`；`updates_after_approval`（批准后、合并前的 commit 与 force push；没有批准为 0）；`second_approval_wait_hours`；作者为空时不报错；`merged_without_approval`；`review_requested_before_first_review`；`size_bucket` 边界（9/10、99/100、499/500、999/1000）；`ready_weekday`、`ready_hour`；`first_response_at` 取评论和 review 中较早者。

**`test_classify.py`**：位置回退链（label 前缀不区分大小写；CODEOWNERS 最后匹配的规则生效；目录前 3 个按文件数排序；`unclassified`）；四种关闭分类各一例，以及优先级（同时满足 superseded 和 no_review 时为 superseded）；`late_rejection`；revert 识别（标题、正文 `Reverts owner/repo#N`、commit SHA 前缀、标题匹配回退）；跨仓库的 `Reverts other/repo#N` 不链接；revert 未合并只标 `is_revert`；revert 的 revert 视为 reland；`Reland` / `Reapply` 标题；被替代（交叉引用、同 `head_ref`）；任一方作者未知时两条被替代规则都不判定；流程 PR 排除从未 ready 的 draft；`author_open_prs_at_ready`（P1；作者未知时为 `None`）。

**`test_stats.py`**：分位数最小样本；bootstrap 同种子结果相同、明显差异判为显著、无差异判为不显著；`seed_for` 稳定；Kaplan–Meier 手算示例（P1：5 个样本、2 个删失，逐点核对 S(t) 和中位数）。

**`test_efficiency.py`**：每个指标一个小数据集手算核对；比率规则（分母 29 / 事件 4 时为 `insufficient_sample`）；`merged_within_n_days` 的 cohort 截止到 `min(to_excl, as_of) - N`；`as_of = min(to_excl, last_synced_at)`：`to` 早于今天但同步停在周期内时 `as_of = last_synced_at`、`period.complete = false`，`as_of` 之后才合并或关闭的 PR 不计入当前周期（`05` §6.1 的 `end_dt`）；`waiting_share` 的分母是整个周期（手算：coding 10 h、waiting_reviewer 20 h、waiting_author 5 h、waiting_merge 5 h → 25 / 40 = 0.625），`coding_hours` 为空按 0，`closed` 区间不计入；`Cl` 按最终结果判定（周期内关闭、之后重开的 PR 不计入）；从未 ready 的 draft 不进入任何指标、计入 `meta.excluded.never_ready_drafts`；`effective_throughput` 扣除被 revert 的和 revert PR；`comparison_available = false` 时上一周期字段为空；P1：`within_hist_p85` 的基线窗口早于 `covered_since` 时 `extra.reason = "baseline_not_covered"`，历史数据只来自 `05` §6.2 第 8 项（不受第 1 项过滤）。

**`test_bottlenecks.py`**：时间账各状态之和等于各 PR 区间之和（`closed` 区间不计入）；多位置 PR 按 `1/k` 加权；小位置并入 `other`：同时属于两个小位置的 PR 在 `merged_prs`、`inflow`、`outflow`、`at_risk_prs` 中只计一次，分摊的小时直接相加，中位数在并集上重算；review 队列的周裁剪和 `open_at_week_end`（关闭期内的 PR 不计入）；Pareto 排序；what-if 截断公式，`cycle_p50_before` 为 `None`（样本不足）或 0 时 `change_rel = None` 且该项不输出；风险 PR 的三级基线（90 天、180 天、默认值）和 p85 / p95，两个仓库同一状态的基线不同时各自的 PR 用自己仓库的阈值，关闭期内的 PR 不是候选；as-of 截断；`bottleneck_shift` 阈值；归因（`05` §9.12）：状态的 `share_of_increase` 之和不超过 1、无增长时为 0；位置的 `share_of_increase` 之和等于 `states.waiting_reviewer.share_of_increase`（误差 1e-4），等待只在位置之间转移（reviewer 净增长为 0）时各位置为 0，手工小数据集（输入写在测试里）：位置 A 的 `change = 3`、位置 B 的 `change = -1`、`states.waiting_reviewer.share_of_increase = 0.5` → A 的 `share_of_reviewer_increase = 1`、`share_of_increase = 0.5`，B 都为 0；求和类断言用取整前的值。

**`test_findings.py`**：每条规则的触发 / 不触发边界；`|M| < 20` 时 `bottlenecks = []` 且 headline 没有瓶颈和收益子句；排序（影响降序 → 严重程度 → ID）；`impact_share`；headline 的四种效率句式、带与不带瓶颈和 what-if 的组合；第一条发现的 `what_if` 为 `None` 时没有收益子句。

**`test_snapshot.py`**：黄金测试（§5）；确定性测试（§5）；`snapshot_id`、`params_hash`、`versions_hash` 稳定且随输入变化；`period.complete`；`insights.analytics.derive_key("label:area-", 3)` 的输出格式固定；取整规则；`Snapshot.model_validate(payload)` 通过（结构与 `06` §4 一致）；所有 `bottlenecks[].evidence[].ref` 都能在快照中解析。

**`test_rows.py`**：`build_pr_rows` 的三种 `status`（`open` 使用 `05` §2.7 的判定，关闭期内的 PR 不在其中）、`at_risk`（多仓库时按各自仓库的基线，非空行数等于 `at_risk_summary.total`）、`ledger_hours` 截断到 `as_of` 且不含关闭期、`current_state`；作者账号已删除的 PR 行 `author = null`。

**`test_params.py`**：仓库正则的合法 / 非法样例（`a/b`、`dotnet/runtime`、`-a/b`、`a/..`、`a/b/c`、超长 owner、`a b/c`）；`repo` 与 `org` 互斥；重复 `repo` 去重；超过 `MAX_REPOS_PER_REQUEST`；四条日期规则；默认日期；游标的各种非法形态；`state` 与 `status` 的组合规则。

**`test_caching.py`**：`If-None-Match` 的多值、`W/` 前缀、`*`；ETag 计算。

**`test_evidence.py`**：每个证据条目的 `ref` 都能在快照中解析；值为 `null` 的条目不进入证据包；位置条目 ID 分配（跳过 `other`）；位置名清洗（含空格或换行的位置名被替换为 `location-k`，且证据包任何地方都不再出现原文）；证据包中不含 PR 标题和登录名（对黄金快照逐个检查）；显著点的排序和两条矛盾规则；证据包字节确定，`pack_hash` 稳定；M8 之后：`ci_slowdown` 场景切换 `ci_complete` 时 `pack_hash` 改变。

**`test_hypotheses.py`**：`07` §4.5 的全部示例（0.78 / high；0.63 / medium；无机制不输出；CI 上限 0.5 / low；六个档位边界）；可评估信号的分母规则；P0（没有 CI 数据）且 E1 显著上升时，H_ci_bottleneck 进入 `alternatives_open` 且 `reason = "no_data"`；两侧按信号角色判定（症状算效率侧、机制算瓶颈侧，与条目的 `side` 字段无关）；`comparison_available = false` 时 `no_comparison`；备选假设按 `07` §3.3 的六个分支各一例（`no_data`、`insufficient_sample`、反证 → 排除、机制全部未出现 → 排除、`below_threshold`、`not_selected`），证据包的 `ruled_out` 只含被排除的；候选最多 3 个且排序正确。

**`test_validator.py`**：每条规则至少一个失败样例和一个通过样例：Schema 错误；句数越界；zh 没有中文、en 含中文；不存在的引用；某句没有引用；数字比对（`41.25` → "41 h"、"41.3 hours" 通过，"42 h" 不通过；`change_rel = 0.1752` → "18%"、"17.5%" 通过；`share = 0.4213` → "42%" 通过；小时 → 天 "1.7 days"（41.25/24）通过；分钟 → 小时通过；区间写法 "from 35.1 to 41.3 h" 中的 35.1 按 hours 通过；"3 different areas" 中的 3 按 plain 处理；`p50`、`E12` 不被当成数字；千分位 "1,234"）；V5 的反例：数字在证据包里、但不在本句引用的条目中 → `number_not_in_evidence`；`n = 61` 写成 "61 hours"、`share = 0.4213` 写成 "42 hours" → `unit_mismatch`；`change_rel = 0.1752` 写成 "fell 18%" 或 "下降了 18%" → `direction_mismatch`，句中同时有上升词和下降词时不检查方向；`period.days` 在任何句子中可用；`weeks_holding` 只在引用了该候选证据链 ID 的句子中可用；label 中的数字（"500 or more lines"）只在引用该条目时可用；`statement` 中的数字同样逐句检查；E5 的 "merged within 3 days [E5]" 和 "在 3 天内合并 [E5]" 通过（`extra.n_days` 同时算 days）；方向按 `07` §7 第 6 步选条目：句子引用 E1 和 E19、用 "decreased" 报告 E19 的变化（`change_pp` 数字）、E1 只出现数值时，只检查 E19；E1 上升时 "Median cycle time fell to 41.2 h [E1]" → `direction_mismatch`（只写新值也要检查）；"2.4x vs. the rest" 不会被切成两句；V7b：任何文本含 `clearly`、`证明了`、"一定会" → `overclaim`，"一定程度上""不一定""没有证据证明"不算；最高档位为 medium 时正文因果句用 `likely` 或没有档位措辞 → `overclaim`，用 `may` 通过；证据包只有 low 候选、输出省略了全部假设时，正文出现 "is likely the main cause" → `overclaim`，而 "The signals are not strong enough to support a root cause [E1]." 通过；V12：无候选时正文出现 "rose because of …" → `abstain`，含 `not enough` 的句子中的因果措辞允许；未知假设 ID；漏掉 high 候选；引用不在证据链内；有反证但没引用；措辞与档位不符（medium 里出现 "likely"；zh medium 写成 "很可能"）；非法下调（向上或同级）；库外假设缺少一侧显著证据、无候选时出现库外假设；包含登录名或 `@handle`；无候选时输出了假设或缺少"信号不足"。

**`test_template.py`**：对黄金快照和 `08` §3 中非 CI 的四个场景快照（M8 之后再加 `ci_slowdown`），四种 `audience × lang` 组合生成的模板都通过校验器；无候选时包含"信号不足"的句子；有候选时 manager 至少 3 句；S1 的四种变体各一例：显著变化用 S1、`change_rel` 非空但不显著用 S1-ns、没有上一周期（`build_snapshot_from_repo(..., covered_since=当前周期起点)`，`08` §2.5）用 S1-np 且 `abstain_reason = "no_comparison"`、E1 不在证据包中用 S1-na，都通过校验器。

**`test_llm.py`**：用 `Stubber` 断言 Converse 请求的 `modelId`、`toolConfig.toolChoice`、`inferenceConfig`、`system`；解析 `toolUse`；`ThrottlingException`、读超时被映射为 `LLMUnavailable`，日志只含错误代码。

**`test_narrative_service.py`**（只测 `generate`；`NarrativeService` 的缓存、锁和持久化由 `test_api.py` 的叙述用例覆盖）：第一次通过；第一次失败、第二次通过，且第二次请求的最后一条消息是 `status = "error"` 的 `toolResult`；两次都失败 → 模板、`validation = "failed"`、`persist = False`、`violations` 有代码；`LLMUnavailable` → 模板、`fallback_reason = "llm_error"`；`llm=None` → 模板、`persist = True`；下调生效后 `confidence = 0.74` / `0.5`；下调后按最终置信度重排（原第一个候选从 high 降到 low 后排到 medium 候选之后）；`alternatives_open` 来自代码；`H_llm` 固定 0.35；`evidence` 只包含被引用的条目；`meta.pack_hash` 等于证据包规范化字节哈希的前 16 位。

## 4. 集成测试用例

**`test_migrations.py`**：`upgrade head` 后全部表、唯一约束和关键索引存在；`downgrade base` 成功。

**`test_sync.py`**（respx 模拟 GitHub + 真实 Postgres / Redis；M3 先实现不带标记的用例，标"M4 起""M6 起""P1"的用例在对应里程碑补上）：
- 新仓库回填：分阶段推进 `covered_since`（7 → 30 → `BACKFILL_DAYS`），游标续传；第一阶段完成后执行开着的 PR 扫描；每个阶段完成和任务结束时执行提交点收尾（`04` §6.3）：收尾增量补上分页期间被更新的 PR（夹具：某个 PR 在回填翻页过程中 `updatedAt` 变新，只出现在收尾增量的第一页，最终入库的是新内容），`last_synced_at` = 收尾增量的开始时间；回填未完成的后续任务先做增量再继续回填；（M6 起）改变了 `data_version` 的任务结束后入队预计算；
- 同样的响应再同步一次：`data_version` 不变，没有重复行；
- 某个 PR 内容变化：该 PR 的事件和文件被整体替换，`data_version + 1`；（M4 起）`pr_intervals` / `pr_facts` 被重算，关联字段不被清空；
- 时间线超过 100 条时用 `PullRequestTimeline` 补齐后才写入；
- 增量同步在 `sync_watermark - 10 分钟` 处停止；
- 开着的 PR 扫描补上 `updatedAt` 早于覆盖范围的 PR；
- 锁：同一仓库第二个任务返回 `skipped_locked`；
- 401 → `last_sync_status = "auth_error"`，`sync_jobs.error` 为截断后的简短错误信息、不含 token；没有 token → `missing_token`；
- `enqueue_sync` 去重：同一仓库已有排队任务时返回现有任务、不新建行；
- 同步后 `insights.sync.invariants` 的检查（`timeline.check_invariants`）返回 0 个违规（M4 起断言）；
- 被撤销的 review（M4 起）：夹具中 review 的上游 `state = DISMISSED`、时间线中有对应的撤销事件 → 入库的 `payload.state = "APPROVED"`、`dismissed = true`，区间与 `05` §2.5 第 18 条一致；同样的响应再同步一次，区间不变；
- 推导标识（M4 起）：新仓库回填的第一阶段完成后 `derived_key` 等于当前标识；库中还有旧标识的 `pr_facts` 行（模拟首次回填中途改了配置）时，阶段完成不写 `derived_key` 并入队 `rederive`；改变 `LOCATION_DIMENSION` 后执行 `startup` → 入队 `rederive`；`rederive_repo` 在第一批提交后抛异常 → `derived_key` 仍是旧值，下一次 `incremental_sync_all` 重新入队 `rederive`（没有 token 时也是）；重跑跳过已是当前标识的 PR，完成后 `derived_key` 更新且 `data_version + 1`；`derived_key` 已是当前标识时 `rederive_repo` 直接返回、`data_version` 不变；
- `housekeeping`（M6 起）：删除 7 天前的快照（叙述级联删除）、它们的 Redis 键（`di:snap`、`di:rows`、`di:narr:{id}:*`）和 30 天前结束的 `sync_jobs`，较新的保留；
- P1：Actions 运行记录的分窗（某天 `total_count > 1000` 时拆成 4 个窗口）、按 `head_sha` 映射到 PR、受影响 PR 重推导；CODEOWNERS 与 area-owners 解析和入库；CODEOWNERS 变化时 `repositories.derived_key` 和该仓库的 `pr_facts.derive_key` 被置空并入队 `rederive`，完成后位置更新；只有 area owners 变化时只 `data_version + 1`、不重推导。

**`test_api.py`**（用 `seed_synthetic` 准备数据）：
- insights 200：响应头 `ETag`、`Cache-Control`、`Content-Location`、`X-Snapshot-Id`；结构通过 `Snapshot.model_validate`；第二次请求命中缓存且字节相同；
- 带 `If-None-Match` → 304，无响应体；
- 未就绪 → 202，`Retry-After`、`Location`（有运行中任务时）、Pending 结构；五种 `reason` 各一例（`never_synced`；`covered_since > from_dt` → `backfill`；`last_open_sweep_at` 为空 → `open_sweep`；`derived_key` 过时 → `rederive`，`Location` 指向重推导任务；`last_synced_at <= from_dt` → `stale`）；`missing_token` 且未就绪原因需要访问 GitHub → 503 `data-unavailable`；`missing_token` 但只差 `rederive` → 202，`Location` 指向重推导任务；
- `last_synced_at` 早于 `to_excl` 时快照的 `as_of = last_synced_at`、`period.complete = false`；
- 403 未跟踪仓库、`org` 没有对应仓库；
- 422：每条参数规则各一例，响应为 problem+json、`errors[].param` 正确、`detail` 不含参数原值；
- `/v1/snapshots/{id}`：200 + `immutable`；未知 ID 404；非法 ID 422；把注入的 `get_now` 往后移 8 天 → 404（Redis 里仍有键时也是），叙述同样 404；同一快照 ID 在 Redis 和 Postgres 中都只有已过期的数据时，insights 请求把它们当作未命中、重新计算并刷新两处的 `created_at`，之后按 ID 读取为 200；
- `/prs`：作者账号已删除的 PR 行 `author = null`，响应正常（不是 500）；三种 `status`、`at_risk=true` 与快照的 `at_risk_summary.total` 一致、`state`、`location` 筛选、分页拼起来等于全集且无重复、数据更新后旧游标返回 422；
- `/v1/repos`：包含配置中尚无数据库行的仓库（`never`）；
- 手动同步：202 + `Location`；冷却期内 429 + `Retry-After`；已有排队任务时返回现有任务；
- `/v1/sync-jobs/{id}`：200、404、非 UUID 422；
- 限流：把 `RATE_LIMIT_PER_MINUTE` 设为 3，第 4 次 429 且带 `Retry-After`；`/healthz` 不受影响；
- 叙述：`FakeLLMClient` 成功 → 200、`generated_by = "llm"`、持久化；同一请求第二次不再调用 LLM；`If-None-Match` → 304；LLM 两次校验失败 → 200 模板、`Cache-Control: no-store`、未写入 `narratives`；未配置 LLM → 模板且持久化为 `model_id = "template"`；快照不存在 404；写入叙述时快照刚被删除（外键错误）→ 404 而不是 500；Redis 键和 `narratives` 行包含 `pack_hash`，且等于响应的 `meta.pack_hash`；
- 每个响应都有 `X-Request-ID`；合法的传入值被沿用，含换行或超长的被替换；
- CORS：允许的来源有 `Access-Control-Allow-Origin`，其他来源没有。

## 5. 黄金测试与确定性

- 黄金文件 `tests/golden/snapshot_seed42.json`：`generate(ScenarioSpec.baseline(), seed=42)` → `build_snapshot_from_repo` 的规范化字节（`08` §2.5）。测试逐字节比较；不一致时打印第一个不同的 JSON Pointer 和两边的值。
- 重新生成：`UPDATE_GOLDEN=1 uv run pytest tests/unit/test_snapshot.py -k golden`。只有在有意修改算法或阈值时才重新生成，并在提交信息中说明原因（同时递增 `ANALYTICS_VERSION` 或 `THRESHOLDS_VERSION`）。
- 确定性：同一输入构建两次字节相同；把输入记录的顺序打乱后构建，字节仍然相同（验证所有列表都有明确排序）。

## 6. 安全测试（`test_security.py`，单元为主，必要时用集成夹具）

- token 不出现在日志、异常响应和 `sync_jobs.error` 中（检查 401、超时等错误路径：代码不记录请求头，也不把 token 拼进异常信息）。测试里的假 token 在运行时拼接生成（例如 `"ghp_" + "x" * 36`），源码中不出现完整的 token 形态，以免触发 `12` D1 的历史扫描。
- 未处理异常返回 500 problem，`detail` 为固定文本，不含异常消息。
- `location` 参数为 `' OR 1=1 --` 时正常返回空结果（参数化查询 / 内存筛选），不是 500。
- 证据包和 LLM 请求中不含 PR 标题、正文、登录名（`FakeLLMClient` 记录的请求逐字检查）。
- API 进程不导入 GitHub 客户端：`python -c "import insights.main, sys; assert not [m for m in sys.modules if m.startswith('insights.sources')]"` 作为测试执行。

## 7. 不测什么

- 前端样式和交互（设计取舍）；Recharts、FastAPI、arq 等第三方库本身。
- 真实 GitHub 和 Bedrock：由冒烟命令（`04` §8、`scripts/smoke.sh`）和 `make eval` 覆盖，需要凭证，不进 CI。
- 压力测试：M12 只做 `12` 中的简单性能检查。

不设覆盖率百分比门槛；以上列出的用例必须全部存在并通过。
