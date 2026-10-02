# 01 里程碑（执行主线）

按顺序执行。每个里程碑都列出：目标、任务、需要写的测试、完成标准（DoD）和注意事项。DoD 里的命令必须逐条执行并通过。标注"需要凭证"的步骤，在没有对应环境变量时跳过并记入最终报告。

| 里程碑 | 内容 | 范围 |
|---|---|---|
| M0 | 脚手架、配置、日志、健康检查、容器 | P0 |
| M1 | 数据模型与迁移 | P0 |
| M2 | GitHub 客户端与规范化 | P0 |
| M3 | 同步 worker | P0 |
| M4 | 推导：状态机、PR 事实、分类与关联 | P0 |
| M5 | 分析与快照 | P0 |
| M6 | API：Endpoint 1 与配套接口 | P0 |
| M7 | 叙述：Endpoint 2（含 director / manager 两个版本） | P0（版本区分属 P1，一并实现） |
| M8 | CI 等待与 owner 解析 | P1 |
| M9 | Drivers、生存分析、可预测性 | P1 |
| M10 | Eval harness | P1 |
| M11 | 前端 | P1 |
| M12 | 收尾：README、安全与性能检查、验收 | P0 |

---

## M0 脚手架与工程基础

**目标**：一个能启动的空服务，质量门槛和容器都能跑通。

**任务**

1. 按 `00-overview.md` §5 创建目录。按 `02-config-and-infra.md` §2 写 `backend/pyproject.toml`，执行 `cd backend && uv lock`。
2. `insights/config.py`：按 `02` §1 实现 `Settings`（pydantic-settings），提供 `get_settings()`（`functools.lru_cache`）。
3. `insights/logging.py`：按 `02` §4 配置 structlog JSON 日志。
4. `insights/db/engine.py`：创建 async engine 和 `async_sessionmaker`；`insights/redis.py`：创建 `redis.asyncio.Redis`。两者都在 FastAPI lifespan 中创建、在关闭时释放，并通过依赖注入提供给路由。`insights/api/deps.py` 提供 `get_settings`、`get_session`、`get_redis`、`get_now`（返回 `datetime.now(UTC)`，测试中覆盖，见 `00` §7）。
5. `insights/api/errors.py`：problem+json 异常体系与处理器（`06-api.md` §2.3）；`insights/api/middleware.py`：请求 ID 与访问日志中间件（限流在 M6 加）。
6. `insights/api/routes/health.py`：`GET /healthz`（不检查依赖，返回 `{"status": "ok"}`）、`GET /readyz`（检查 `SELECT 1` 和 Redis `PING`，任一失败返回 503 problem+json）。
7. `insights/main.py`：`create_app()` 组装以上内容；`app = create_app()` 供 uvicorn 使用。
8. 按 `02` §5–§8 写 `backend/Dockerfile`、`docker-compose.yml`（此时只有 `postgres`、`redis`、`api`）、`Makefile`、`.env.example`、`.gitignore`、`.dockerignore`、`.github/workflows/ci.yml`。
9. 创建 `docs/DECISIONS.md`（标题和表头即可）。

**测试**：`tests/unit/test_health.py`（用依赖覆盖模拟数据库和 Redis 成功、失败两种情况）、`tests/unit/test_logging.py`（JSON 格式与字段，见 `02` §4）。

**DoD**

```bash
make lint && make test-unit
cp .env.example .env
docker compose up --build -d
curl -s localhost:8000/healthz                                   # {"status":"ok"}
curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/readyz   # 200
curl -s localhost:8000/openapi.json | python -c "import sys,json; print(json.load(sys.stdin)['info']['title'])"
docker compose down -v
```

**注意**：`/healthz` 和 `/readyz` 不加 `/v1` 前缀，不受限流影响。

---

## M1 数据模型与迁移

**目标**：完整的表结构和迁移，所有后续里程碑都基于它。

**任务**

1. `insights/db/models.py`：按 `03-data-model.md` §2 实现全部表（包括 P1 才使用的 `workflow_runs`、`ownership_rules`，提前建好避免以后改表）。
2. `cd backend && uv run alembic init -t async migrations`，配置 `env.py` 从 `Settings.database_url` 读取连接串、`target_metadata = Base.metadata`。
3. 生成初始迁移 `0001_initial`，**逐行检查**自动生成的结果与 `03` 一致（索引、唯一约束、外键 `ON DELETE CASCADE`、`server_default`）。
4. `insights/redis.py`：按 `03` §3 实现键名函数（不要在代码各处手写键名字符串）。
5. `docker-compose.yml` 加入 `migrate` 服务（`alembic upgrade head`），`api` 依赖它成功完成。

**测试**：`tests/integration/test_migrations.py`（testcontainers Postgres）：`upgrade head` 后检查全部表和关键索引存在；`downgrade base` 成功。

**DoD**

```bash
make lint && make test
docker compose up --build -d && docker compose ps -a  # migrate 状态为 exited (0)，api healthy
docker compose exec postgres psql -U insights -d insights -c '\dt'
docker compose down -v
```

---

## M2 GitHub 客户端与规范化

**目标**：可靠地从 GitHub 读取 PR 数据并转成与来源无关的领域数据。

**任务**

1. `insights/domain.py`：按 `04-github-sync.md` §2 定义领域数据类（`frozen=True, slots=True` 的 dataclass）。
2. `insights/sources/base.py`：`SourceAdapter` Protocol（`04` §2.3）。
3. `insights/sources/github/queries.py`：**原样**放入 `04` §3 的 GraphQL 查询文本。
4. `insights/sources/github/client.py`：按 `04` §4 实现 GraphQL / REST 调用、限流等待、重试、页大小自适应、ETag 条件请求。
5. `insights/sources/github/normalize.py`：按 `04` §5 把 GraphQL 节点转成领域对象，包括 bot 识别、revert 提交解析、事件去重键。
6. `insights/sources/github/adapter.py`：`GitHubAdapter` 实现 `SourceAdapter`。
7. `insights/sources/github/smoke.py`：命令行冒烟（`python -m insights.sources.github.smoke --repo OWNER/NAME --pages N`），打印 PR 数、事件数、剩余配额；不打印 token。

**测试**：`tests/unit/test_github_client.py`、`tests/unit/test_normalize.py`（用例见 `10-testing.md` §3）。GraphQL 响应夹具放在 `tests/fixtures/github/`，手写，结构与 `04` §3 的查询完全对应。

**DoD**

```bash
make lint && make test-unit
# 需要凭证：
cd backend && uv run python -m insights.sources.github.smoke --repo dotnet/runtime --pages 1
#   期望：打印 "prs=25 events=... rate_limit_remaining=..."，没有 GraphQL errors
```

**注意**：真实调用如果报某个字段不存在，查 GitHub GraphQL 文档修正查询，同时更新夹具和 `docs/DECISIONS.md`。

---

## M3 同步 worker

**目标**：后台按阶段回填、增量同步、扫描开着的 PR，数据可重复写入不重复。

**任务**

1. `insights/sync/store.py`：按 `04` §7 实现按页事务化 upsert（PR、事件、文件），基于 `content_hash` 判断是否变化，变化时递增仓库 `data_version`。`04` §6.3、§7 中对 `derive.derive_prs` 和 `derive.link_repo` 的调用在 M4 实现推导时一并接入，M3 的同步只写原始数据。
2. `insights/sync/jobs.py`：按 `04` §6 实现 `sync_repo`（已有 `sync_watermark` 时每个任务先做增量、再继续回填；回填阶段 7 → 30 → `BACKFILL_DAYS`，可从游标续传；第一阶段完成后先做一次开着的 PR 扫描；每个阶段完成时和任务成功结束时执行"提交点收尾"：收尾增量 → 提交 `covered_since`、`last_synced_at`，`04` §6.3；其中的 `link_repo` 和推导完整性检查在 M4 接入）、`incremental_sync_all`（定时）、`open_pr_sweep`、`reconcile_tracked_repos`（启动时根据 `TRACKED_REPOS` 更新 `repositories.tracked`，为没有覆盖的仓库入队回填）。`insights/sync/queue.py`：`enqueue_sync`（`04` §6.8），所有同步类任务都经由它入队。`precompute_snapshots` 在 M6 接入，M3 先不入队。
3. `insights/sync/worker.py`：arq `WorkerSettings`（`04` §6.1），`max_jobs = 1`。
4. 每个任务写 `sync_jobs` 记录（queued → running → succeeded / failed，记录阶段、统计、错误摘要）。用 Redis 锁保证同一仓库同时只有一个同步任务。
5. `docker-compose.yml` 加入 `worker` 服务（`arq insights.sync.worker.WorkerSettings`）。

**测试**：`tests/integration/test_sync.py`（testcontainers + respx，用例见 `10` §4）。

**DoD**

```bash
make lint && make test
# 需要凭证：
docker compose up --build -d
sleep 180
docker compose exec postgres psql -U insights -d insights -c \
  "select full_name, covered_since, data_version, last_sync_status from repositories"
#   期望：dotnet/runtime 的 covered_since 约等于 now() - 7 days 或更早，data_version >= 1
docker compose exec postgres psql -U insights -d insights -c "select count(*) from pull_requests"
```

---

## M4 推导：状态机、PR 事实、分类与关联

**目标**：把原始事件变成每个 PR 的等待状态区间和事实，这是所有指标的基础，必须做对、测透。

**任务**

1. `insights/analytics/timeline.py`：按 `05-analytics.md` §2 实现状态机（纯函数），输出连续、不重叠的区间。
2. `insights/analytics/facts.py`：按 `05` §3 计算 PR 级事实。
3. `insights/analytics/classify.py`：按 `05` §4 实现 bot / backport / 外部贡献者识别、关闭分类、revert / reland / 被替代链接。
4. `insights/sync/derive.py`：每页 upsert 后为变化的 PR 重算区间和事实——`pr_intervals` 整体替换该 PR 的旧行，`pr_facts` 用 upsert 且冲突时不覆盖关联字段（`05` §3），每行写入当前的推导标识（`insights.analytics.derive_key(...)`，`05` §1）；提交点收尾（回填每个阶段完成时、整次同步结束时）跑一次仓库级关联（`05` §4.3–§4.7：匹配逻辑是纯函数 `classify.link_prs`，`derive.link_repo` 负责读写），并做推导完整性检查：全部 PR 都已是当前标识时写入 `repositories.derived_key`，否则入队重推导（`04` §6.3）。
5. 推导标识：worker 启动时和每个定时周期，对 `covered_since` 非空、且 `repositories.derived_key` 与当前推导标识不同（包括为空）的仓库，用 `enqueue_sync(kind="rederive")` 入队整仓重推导（`04` §6.2 第 3 步、§6.4；没有 token 时也入队）；`rederive_repo` 按 `pr_id` 键集分页、逐批提交、跳过已是当前标识的 PR，全部完成后在同一事务中写入 `derived_key` 并递增 `data_version`（`04` §6.5）。
6. `insights/sync/invariants.py`：命令行检查不变式（读库后调用纯函数 `timeline.check_invariants`，`05` §2.6；放在 `sync/` 是为了不破坏 analytics 层不做 I/O 的边界），`python -m insights.sync.invariants --repo OWNER/NAME`，打印违规数并以非零退出码表示失败。

**测试**：`tests/unit/test_timeline.py`（`05` §2.5 的全部 22 个用例，外加随机不变式测试）、`tests/unit/test_facts.py`、`tests/unit/test_classify.py`，用例见 `10` §3；集成测试验证同步后 `pr_intervals`、`pr_facts` 有数据且不变式成立，以及 `10` §4 中标"M4 起"的推导标识用例。

**DoD**

```bash
make lint && make test
# 需要凭证（M3 的数据已同步）：
docker compose exec api python -m insights.sync.invariants --repo dotnet/runtime   # 0 violations
```

---

## M5 分析与快照

**目标**：从 `pr_facts` / `pr_intervals` 算出完整快照，结果确定、可复现。

**任务**

1. `insights/analytics/thresholds.py`：`05` §1 的全部阈值常量。
2. `insights/analytics/stats.py`：分位数（含最小样本）、种子化 bootstrap（`05` §5）。
3. `insights/analytics/dataset.py`：按周期加载数据（`05` §6），返回不可变的内存结构；只查需要的列。
4. `insights/analytics/efficiency.py`、`bottlenecks.py`（包括 `05` §9.12 的变化归因）、`findings.py`：按 `05` §7–§11 实现。
5. `insights/analytics/snapshot.py`：按 `05` §12 和 `06-api.md` §4 组装快照、生成 headline、规范化 JSON、计算 `snapshot_id` 和 ETag；`insights/analytics/rows.py`：PR 明细行（`05` §17）。
6. `backend/eval/insights_eval/generator.py`、`pipeline.py`、`scenarios.py`：按 `08-eval-harness.md` §2–§3 实现合成数据生成器、"记录 → 快照"的纯函数流水线和场景定义（此处先用于黄金测试和 M7 的模板测试，M10 复用）。

**测试**：各模块单元测试（`10` §3，包括 `test_rows.py`）；黄金测试：种子 42 的合成数据集生成的快照与 `tests/golden/snapshot_seed42.json` 完全一致（设置 `UPDATE_GOLDEN=1` 时重写黄金文件）；确定性测试：同一输入构建两次，字节完全相同。

**DoD**

```bash
make lint && make test
```

**注意**：黄金文件第一次生成后要**人工核对**几个关键数字（例如时间账总和等于各 PR 区间之和），再提交。

---

## M6 API：Endpoint 1 与配套接口

**目标**：可直接用 curl 调用的完整 REST API，HTTP 语义正确。

**任务**

1. `insights/api/params.py`：参数解析与校验（`06` §3）。
2. `insights/api/schemas.py`：与 `06` §4–§7 完全一致的 Pydantic 响应模型。
3. `insights/api/caching.py`：ETag 生成与 `If-None-Match` 处理（`06` §2.4）。
4. 快照服务 `insights/snapshot_service.py`：解析仓库 → 检查白名单 → 就绪检查（`06` §5.1 第 4 步的五个条件和 `reason`；不满足时返回 202 或 503，**不入队**）→ 计算 `data_freshness`、`as_of` 和 `snapshot_id` → 查 Redis / Postgres → 未命中时用 `asyncio.to_thread` 计算并持久化，记录 `snapshot_computed` 日志（`duration_ms`、`load_ms`、`compute_ms`、`merged_prs`）（`06` §5.1）。
5. 路由：`insights.py`（`/v1/insights/delivery`、`/v1/insights/delivery/prs`）、`snapshots.py`（`/v1/snapshots/{id}`）、`repos.py`（`GET /v1/repos`、`POST /v1/repos/{owner}/{name}/sync`，后者调用 `enqueue_sync`；arq 连接池在 lifespan 中创建，经依赖 `get_arq` 注入，`06` §5.6）、`sync_jobs.py`。
6. 中间件加入基于 Redis 的限流（`06` §2.6）；CORS 只允许 `CORS_ORIGINS`。
7. worker 接入 `precompute_snapshots`（`04` §6.6）：每次改变了 `data_version` 的同步任务结束后入队（`04` §6.3 第 7 步）。
8. worker 接入 `housekeeping`（`04` §6.7）：按 `03` §2.12 删除过期快照、它们的 Redis 键和旧的 `sync_jobs`；快照读取路径按 7 天逻辑过期处理（Redis 哈希保存 `created_at`，回填 Redis 时截短过期时间）。
9. 在 README 中先写好 quickstart 和 curl 示例（M12 再完善）。

**测试**：`tests/integration/test_api.py`（用例见 `10` §4）。

**DoD**

```bash
make lint && make test
# 需要凭证：按 06-api.md §8 的 curl 示例逐条执行，结果与描述一致（200 / 304 / 422 / 403）
```

---

## M7 叙述：Endpoint 2

**目标**：LLM 只负责叙述；数字、置信度、校验全部由代码掌控；任何失败都能降级。

**任务**

1. `insights/narrative/evidence.py`：证据包（`07` §2）。
2. `insights/narrative/hypotheses.py`：假设库与评分（`07` §3–§4）。
3. `insights/narrative/prompt.py`：system prompt、tool schema、用户消息模板（`07` §5），常量 `PROMPT_VERSION = "v1"`。
4. `insights/narrative/llm.py`：`LLMClient` Protocol、`BedrockClient`（boto3 Converse，`asyncio.to_thread`）、`FakeLLMClient`（测试用，可编排返回值和异常）（`07` §6）。
5. `insights/narrative/validator.py`（`07` §7）、`template.py`（`07` §8）、`service.py`（`07` §9）。
6. 路由 `GET /v1/snapshots/{snapshot_id}/narrative`，支持 `audience=director|manager`、`lang=en|zh`（`06` §6）。

**测试**：`test_evidence.py`、`test_hypotheses.py`（必须包含 `07` §4.5 的示例：0.78、有反证 0.63、无机制信号不输出、数据不全封顶 0.5）、`test_validator.py`、`test_template.py`（模板必须通过校验器）、`test_llm.py`、`test_narrative_service.py`；集成测试用 `FakeLLMClient` 走通 endpoint（`10` §4）。

**DoD**

```bash
make lint && make test
# 无 Bedrock key：叙述 endpoint 返回 200，meta.generated_by == "template"
# 需要凭证（Bedrock）：dotnet/runtime 最近 30 天快照的叙述返回 meta.generated_by == "llm"，meta.validation == "passed"
```

**P0 完成检查**：执行 `12-acceptance-checklist.md` 中标注 P0 的条目，全部通过后再进入 P1。

---

## M8（P1）CI 等待与 owner 解析

**任务**

1. 客户端与同步：按 `04` §9 同步 GitHub Actions 运行记录（按天分窗，单窗超过 1,000 条时再拆分），写入 `workflow_runs`，并映射到 PR。
2. 状态机：把 CI 运行区间接入 `waiting_ci`（`05` §2.4），受影响的 PR 重推导。
3. `insights/analytics/ci.py`：CI 排队、运行时长、flaky 重跑、覆盖率（`05` §13），写入 `bottleneck_analysis.ci`；配置 `CI_COMPLETE`（`02` §1）。CI 数据不完整（覆盖率低于 0.5 或 `CI_COMPLETE=false`）时，CI 相关假设置信度封顶 0.5（`07` §4.3）。
4. `insights/sources/github/ownership.py`：CODEOWNERS 与 `docs/area-owners.md` 解析（`04` §10），位置回退链：label → CODEOWNERS → 目录（`05` §4.2），`owners_count` 填充（`05` §9.1）。

**测试**：`test_ci.py`、`test_ownership.py`，集成同步测试覆盖运行记录映射。

**DoD**：`make lint && make test`；快照包含 `bottleneck_analysis.ci` 和 `time_ledger.ci_coverage`；需要凭证时 dotnet/runtime 的 area 位置有 `owners_count`。

---

## M9（P1）Drivers、生存分析、可预测性

**任务**：按 `05` §14–§16 实现 `drivers.py`、`stats.kaplan_meier`、可预测性指标，写入快照的 `drivers`、`efficiency.survival`、`efficiency.predictability`；证据包加入对应条目（`07` §2.1 表中标 P1 的行）。

**测试**：KM 用手算示例验证（`10` §3）；drivers 各项用小数据集验证；更新黄金文件并在提交信息中说明。

**DoD**：`make lint && make test`。

---

## M10（P1）Eval harness

**任务**：按 `08-eval-harness.md` 实现场景、运行器、指标、`StubLLMClient`、报告和对比；`make eval`、`make eval-offline`。

**DoD**

```bash
make eval-offline        # 退出码 0，打印各场景结果表
# 需要凭证（Bedrock）：
make eval                # 达到 08 §6 的门槛，退出码 0
```

---

## M11（P1）前端

**任务**：按 `09-frontend.md` 实现单页、`frontend/Dockerfile`、`frontend/nginx.conf`；`docker-compose.yml` 加入 `web` 服务（主机端口 5173）。

**DoD**

```bash
cd frontend && npm ci && npm run typecheck && npm run build && cd ..
docker compose up --build -d
curl -s -o /dev/null -w '%{http_code}\n' localhost:5173/             # 200
curl -s localhost:5173/api/healthz                                  # {"status":"ok"}
```

人工检查（需要凭证或合成数据）：页面能选择日期范围、显示效率指标、时间账、瓶颈、风险 PR 和叙述。

---

## M12 收尾

**任务**

1. 按 `11-readme-and-submission.md` 完成 README（英文）与提交说明，包括作业要求的 "AI assistance"（`11` §7.5）和 "With one more day"（`11` §7.6）两节。"AI assistance" 只起草：工具、用途和验证方式必须如实填写，标明需要提交人核对后定稿，不得写没做过的验证。
2. 复查 `docs/DECISIONS.md`，确保每条偏离都有原因；暂缓项（置信度未用真实数据校准、链级交付时长）各有一条。
3. 安全复查：执行 `12` 中的安全条目（含 `git log -p | grep` 检查 token 模式）。
4. 性能检查：执行 `12` 中的性能条目。
5. 逐条完成 `12-acceptance-checklist.md`（G6 留到第 6 步）。
6. 最后一步，准备提交物（`11` §9）：README 和 DECISIONS 已定稿并提交、工作区干净、历史中没有密钥；生成包含 `.git` 的 `../delivery-insights.tar.gz` 并执行三条检查（G6）。之后如果又有提交，重新打包。**不要**创建远程仓库、推送或上传，这一步由人完成（`AGENTS.md` §8）。
7. 输出 `AGENTS.md` §7 要求的最终报告：写出压缩包的绝对路径；在"待人工验证"中列出 README 中留给提交人的 `<confirm: …>` 占位符，以及推送或上传提交物。

**DoD**：`12-acceptance-checklist.md` 全部勾选（需要凭证或需要人完成的条目可标"待人工验证"）。
