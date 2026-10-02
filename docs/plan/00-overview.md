# 00 总览

## 1. 产品

**Delivery Insights** 从 GitHub 同步 PR 协作数据，为工程管理者回答四个问题：工作卡在哪、为什么慢、风险在哪、该介入什么。

核心指标是 **PR 的交付周期以及其中"在等谁"**：一个改动从开始写代码到合并用了多久，其中多少时间在等 reviewer、等作者修改、等 CI、等合并。选择它的理由（README 中要用英文写清楚）：

- 对应管理者的决策：把周期拆到等待状态和位置（area / 目录）后，每个瓶颈都对应一个具体动作。
- GitHub 上信号最丰富：PR 时间线同时记录作者、reviewer、CI 和合并动作。
- 业界通用：对应 DORA 的 Lead Time for Changes；revert 率对应变更失败率。
- 难以被刷：缩短等待只能靠改善流程；revert 率作为护栏，防止靠放松 review 提速。

输出分两部分：

- **团队效率（结果）**：交付得快不快、稳不稳、多少投入被浪费，和上一周期比如何。
- **瓶颈分析（原因）**：时间花在哪 → 卡在哪 → 为什么卡 → 影响多大、先修什么。

headline 把两部分连成一句话：效率怎么变了 → 主要瓶颈 → 建议 → 预期收益。

Demo 仓库：**`dotnet/runtime`**（大型、活跃、用 `area-*` label 划分负责领域）。

## 2. 范围

### P0（必做）

1. GitHub 同步：GraphQL 回填（7 → 30 → 180 天分段）和增量同步，写入 Postgres（arq worker）。
2. 推导：每个 PR 的"在等谁"状态区间、阶段时长、PR 级事实；关闭 PR 分类；revert / reland / 被替代链；bot 和 backport 识别。
3. 效率指标：有效吞吐、交付周期（p50/p90）与 N 天内合并比例、等待占比（占整个交付周期）、浪费率、返工率、review 负载集中度、revert 护栏。
4. 瓶颈：时间账、review 队列流入流出、按位置定位（默认 `area-*` label，回退到目录）、合并阻塞拆解、累计等待排序（Pareto）、what-if、风险 PR（历史 p85）、瓶颈转移、发现（findings）规则引擎、headline。
5. Endpoint 1：快照、Redis 缓存、ETag/304、problem+json、202 异步流程、PR 明细接口、仓库与同步任务接口、健康检查。
6. Endpoint 2：证据包、确定性置信度、Bedrock 调用、校验器、重试与模板兜底、缓存。
7. 核心测试、Docker Compose 一键启动、README（60 秒快速开始）。

### P1（加分，P0 完成后做）

1. Eval harness（`make eval`）。
2. React 前端单页。
3. CODEOWNERS 和 `docs/area-owners.md` 解析，位置级 owner 数量。
4. CI 等待：GitHub Actions 运行记录的排队、运行时长和 flaky 重跑（dotnet/runtime 的主 CI 在 Azure Pipelines，以 check runs 的形式出现在 GitHub 上，本版不采集；CI 数据不全时 CI 假设的置信度封顶）。
5. 原因分析（drivers）：指派、review 轮次代价、作者并行 PR、提交时机、最慢 10% 的特征。
6. 生存分析（Kaplan–Meier）和可预测性指标。
7. 叙述的 director / manager 两个版本（与 M7 一起实现）。

### P2（不做，只在 README 的 "Not done" 中说明）

合并到发布的等待、AI 发起的 PR 对 review 的影响、依赖等待（stacked PR、blocked label）、累积流图数据、按工作时间计算、第二个数据源适配器、扩展假设库、webhook 实时更新、API 用户认证。

**暂缓**（设计文档提到、本版不做，各在 `docs/DECISIONS.md` 记一条，并写进 README 的 "Not done"）：被替代链和 revert / reland 链的链级交付时长（本版只建立链接，`05` §4.5）；用真实历史回测阈值、校准置信度档位（`08` §5）。

### 非目标

- 个人生产力指标、个人排行榜、团队之间横向排名。
- 在请求时调用 GitHub。
- 让 LLM 计算数字。

## 3. 架构

```
                 ┌──────────────┐   GraphQL + REST (read-only token)
 GitHub API ◀────┤ worker (arq) │  backfill / incremental / open-PR sweep
                 └──────┬───────┘
                        │ upsert raw PRs, events, files (+ CI runs, ownership: P1)
                        ▼
                 ┌──────────────┐  derive (pure functions):
                 │  Postgres    │  pr_intervals (who-are-we-waiting-on), pr_facts
                 └──────┬───────┘
                        │ read only
  Browser ──▶ web ──▶ ┌─┴────────────┐     ┌──────────┐
  (React)   (nginx)   │ api (FastAPI)├────▶│  Redis   │ snapshot cache, locks,
  curl ─────────────▶ └─┬────────────┘     └──────────┘ rate limits, arq queue
                        │ snapshot ──▶ evidence pack ──▶ Bedrock (Claude Sonnet 4.6)
                        │                                   │
                        └──────────── validator ◀───────────┘ (retry once, else template)
```

要点：

- API **只读** Postgres 和 Redis；所有 GitHub 调用都在 worker 里。
- 每个 PR 的状态区间和事实在同步时推导一次，请求时只做聚合，所以请求很快。
- 快照不可变：由参数和数据版本决定 ID，叙述挂在快照下面，保证叙述和数字来自同一份数据。

## 4. 技术栈（写最低版本，由 uv 锁定具体版本）

| 层 | 选择 |
|---|---|
| 语言 | Python 3.12 |
| API | FastAPI ≥ 0.115、uvicorn[standard] ≥ 0.30、Pydantic ≥ 2.8、pydantic-settings ≥ 2.4 |
| 存储 | Postgres 16、SQLAlchemy[asyncio] ≥ 2.0.30、asyncpg ≥ 0.29、Alembic ≥ 1.13 |
| 缓存与队列 | Redis 7、redis-py ≥ 5.0（`redis.asyncio`）、arq ≥ 0.26 |
| 上游客户端 | httpx ≥ 0.27 |
| LLM | boto3 ≥ 1.40（`bedrock-runtime` Converse API，Bedrock API key 认证） |
| 计算 | numpy ≥ 2.0 |
| 其他 | orjson ≥ 3.10、structlog ≥ 24.1、pathspec ≥ 0.12（P1，CODEOWNERS 匹配） |
| 测试与质量 | pytest ≥ 8、pytest-asyncio ≥ 0.23、respx ≥ 0.21、testcontainers[postgres,redis] ≥ 4.4、ruff ≥ 0.5、mypy ≥ 1.10 |
| 前端（P1） | Node 20、React 18、Vite 5、TypeScript 5、Recharts 2 |
| 部署 | Docker、Docker Compose v2、nginx（前端静态文件和 `/api` 反向代理） |

不要使用 pandas（numpy 足够，镜像更小）。

## 5. 仓库结构

```
delivery-insights/
├── AGENTS.md  CLAUDE.md  README.md  Makefile
├── docker-compose.yml  .env.example  .gitignore  .dockerignore
├── .github/workflows/ci.yml
├── scripts/smoke.sh               # 端到端冒烟（02 §7）
├── docs/
│   ├── plan/                      # 本计划
│   └── DECISIONS.md               # 实现中偏离计划的记录
├── backend/
│   ├── pyproject.toml  uv.lock  Dockerfile  alembic.ini
│   ├── migrations/                # Alembic（async 模板）
│   ├── src/insights/
│   │   ├── __init__.py            # __version__
│   │   ├── main.py                # create_app()
│   │   ├── config.py              # Settings
│   │   ├── logging.py             # structlog JSON 日志
│   │   ├── db/
│   │   │   ├── engine.py          # async engine / session factory
│   │   │   └── models.py          # SQLAlchemy 模型
│   │   ├── redis.py               # Redis 连接、键名函数
│   │   ├── domain.py              # 领域数据类（与来源无关）
│   │   ├── snapshot_service.py    # 快照服务：就绪检查、缓存、计算、持久化（API 与 worker 共用，06 §5.1）
│   │   ├── sources/
│   │   │   ├── base.py            # SourceAdapter Protocol
│   │   │   └── github/
│   │   │       ├── client.py      # httpx 客户端：GraphQL、REST、限流、ETag
│   │   │       ├── queries.py     # GraphQL 查询文本
│   │   │       ├── normalize.py   # GitHub JSON → 领域数据类
│   │   │       ├── adapter.py     # GitHubAdapter
│   │   │       ├── ownership.py   # P1：CODEOWNERS、area-owners 解析
│   │   │       └── smoke.py       # 冒烟命令（需要凭证）
│   │   ├── sync/
│   │   │   ├── worker.py          # arq WorkerSettings
│   │   │   ├── jobs.py            # arq 任务函数
│   │   │   ├── queue.py           # enqueue_sync（API 与 worker 共用，不导入 sources；04 §6.8）
│   │   │   ├── store.py           # upsert 与读取
│   │   │   ├── invariants.py      # 不变式检查命令（读库，调用 timeline.check_invariants）
│   │   │   └── derive.py          # 推导编排（调用 analytics 的纯函数）
│   │   ├── analytics/
│   │   │   ├── thresholds.py      # 所有阈值常量
│   │   │   ├── timeline.py        # 状态机 → 区间（纯函数）
│   │   │   ├── facts.py           # PR 级事实（纯函数）
│   │   │   ├── classify.py        # bot、backport、关闭分类、revert/reland/被替代（纯函数）
│   │   │   ├── stats.py           # 分位数、bootstrap、KM（纯函数）
│   │   │   ├── dataset.py         # 从数据库加载某周期所需数据
│   │   │   ├── efficiency.py
│   │   │   ├── bottlenecks.py
│   │   │   ├── findings.py
│   │   │   ├── drivers.py         # P1
│   │   │   ├── ci.py              # P1
│   │   │   ├── snapshot.py        # 组装快照、headline、规范化 JSON、哈希
│   │   │   └── rows.py            # PR 明细行（/v1/insights/delivery/prs）
│   │   ├── narrative/
│   │   │   ├── evidence.py        # 证据包
│   │   │   ├── hypotheses.py      # 假设库与评分
│   │   │   ├── prompt.py          # system prompt、tool schema
│   │   │   ├── llm.py             # LLMClient Protocol、BedrockClient、FakeLLMClient
│   │   │   ├── validator.py
│   │   │   ├── template.py        # 模板叙述
│   │   │   └── service.py         # 生成、校验、重试、兜底、缓存
│   │   └── api/
│   │       ├── deps.py  errors.py  middleware.py  params.py
│   │       ├── schemas.py         # Pydantic 响应模型（与 06-api.md 一致）
│   │       ├── caching.py         # ETag 工具
│   │       └── routes/  health.py  insights.py  snapshots.py  repos.py  sync_jobs.py
│   ├── eval/insights_eval/        # generator.py、pipeline.py、scenarios.py（M5）；stub_llm.py、metrics.py、run.py（M10）
│   └── tests/  unit/  integration/  fixtures/  golden/
└── frontend/                      # P1
    ├── package.json  vite.config.ts  tsconfig.json  index.html
    ├── Dockerfile  nginx.conf
    └── src/
```

`eval/` 作为独立包 `insights_eval` 放在 `backend/eval/insights_eval/`，在 `pyproject.toml` 中与 `insights` 一起打包（见 02）。

## 6. 术语

| 术语 | 含义 |
|---|---|
| ready_at | PR 进入可 review 状态的时间：非 draft 创建则为 `created_at`；draft 创建则为第一次 `ReadyForReviewEvent` |
| 阶段（stage） | coding（首个 commit → ready）、pickup（ready → 首次人工 review）、review（首次 review → approve）、merge（approve → 合并） |
| 等待状态（ledger state） | ready 之后每段时间只归入一个：`waiting_reviewer`、`waiting_author`、`waiting_ci`、`waiting_merge`；ready 之前为 `coding`；关闭后又重开之间的关闭期为 `closed`，不属于任何等待状态 |
| 人工 review | 非作者、非 bot 账号提交的、状态为 APPROVED / CHANGES_REQUESTED / COMMENTED 的 review |
| 位置（location） | 瓶颈定位维度的取值，例如 `area-System.Net.Http`、`dir:src/coreclr` |
| 快照（snapshot） | 某组仓库、某周期、某数据版本下计算出的完整 insight，不可变 |
| data_version | 每个仓库的数据版本号，同步改变了数据时加 1 |
| as_of | 快照的"观察时刻"：`min(to 的次日零点, 各仓库 last_synced_at 的最小值)`（`05` §6.1）；`period.complete` 表示请求的周期是否被完整观察到 |
| coverage | 仓库已同步覆盖的最早时间 `covered_since` |
| 证据包（evidence pack） | 由快照生成、交给 LLM 的结构化证据 |
| 假设库（hypothesis library） | 预定义的根因假设，每个都要求效率侧"症状"和瓶颈侧"机制" |

## 7. 全局约定

- **时间**：全部用带时区的 UTC `datetime`。周期 `[from, to]` 按天，对应半开区间 `[from 00:00Z, to+1 00:00Z)`。时长单位为小时（`float`），API 输出保留 2 位小数。
- **时间口径**：UTC 自然时间，不扣除周末和节假日（取舍写进 README）。
- **统计范围**：只统计目标分支为仓库默认分支的 PR；backport、bot 作者的 PR 和从未 ready 的 draft 不计入流程指标，只在 `meta.excluded` 中计数。
- **团队层面**：个人只出现在 `review_load.distribution` 和风险 PR 的 `author` 字段。
- **确定性**：bootstrap 的随机种子由快照参数哈希得出；所有列表都有明确的排序规则（见各规格）。
- **时间可注入**：领域代码不直接调用 `datetime.now()`。API 通过依赖 `insights.api.deps.get_now` 获取当前时间，worker 任务和服务函数接收 `now` 参数，测试和合成数据因此可以固定"今天"。
- **版本号**：`insights.analytics.ANALYTICS_VERSION = "1.0.0"`，`thresholds.THRESHOLDS_VERSION = "1.0.0"`，`narrative.prompt.PROMPT_VERSION = "v1"`。修改算法、阈值或 prompt 时递增，旧快照和叙述不再命中。
