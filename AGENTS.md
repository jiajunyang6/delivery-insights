# AGENTS.md — 给执行开发的 LLM Agent

你要从零实现 **Delivery Insights**：一个用 Python 编写的 Web 服务。它从 GitHub 同步 PR 协作数据，为工程管理者（managers / directors）回答"交付卡在哪、为什么、先修什么"，并通过两个 HTTP endpoint 对外提供：

1. `GET /v1/insights/delivery`：指定周期内的团队效率和瓶颈分析，数字全部由代码计算。
2. `GET /v1/snapshots/{snapshot_id}/narrative`：基于同一份快照，由 LLM（AWS Bedrock 上的 Claude Sonnet 4.6）写出简短叙述，附根因假设、置信度和证据链。

本计划是你唯一的需求来源。计划用中文写；**代码、注释、日志、API 文本、README 一律用英文**。

---

## 1. 阅读顺序

先完整读一遍下面的文件，再开始写代码：

| 文件 | 内容 |
|---|---|
| `docs/plan/00-overview.md` | 产品、范围（P0/P1）、架构、技术栈、仓库结构、术语 |
| `docs/plan/01-milestones.md` | **执行主线**：按顺序的里程碑、任务、完成标准（DoD） |
| `docs/plan/02-config-and-infra.md` | 配置项、依赖、日志、Docker、Compose、Makefile、CI |
| `docs/plan/03-data-model.md` | Postgres 表结构、索引、Redis 键 |
| `docs/plan/04-github-sync.md` | GitHub 客户端、GraphQL 查询、限流、同步任务 |
| `docs/plan/05-analytics.md` | 状态机、指标公式、瓶颈分析、快照组装 |
| `docs/plan/06-api.md` | REST 约定、每个 endpoint 的参数和返回结构 |
| `docs/plan/07-narrative.md` | 证据包、假设库、置信度、prompt、校验器 |
| `docs/plan/08-eval-harness.md` | 合成场景评估 |
| `docs/plan/09-frontend.md` | React 单页 |
| `docs/plan/10-testing.md` | 测试策略和必须覆盖的用例 |
| `docs/plan/11-readme-and-submission.md` | README 和提交说明的写法 |
| `docs/plan/12-acceptance-checklist.md` | 最终验收清单 |

## 2. 执行规则

1. **严格按 `01-milestones.md` 的顺序执行**。P0 里程碑（M0–M7）全部完成并通过 DoD 之后，才开始 P1（M8–M11），最后做 M12 收尾。
2. 每个里程碑结束时，**逐条执行 DoD 里的命令**，全部通过才算完成。不要跳过，不要只"看起来对"。
3. 每个里程碑完成后提交一次 git commit，使用 Conventional Commits（例如 `feat(sync): incremental GitHub sync`）。
4. 规格冲突时的优先级：`06-api.md`（对外契约）> `05-analytics.md` / `07-narrative.md`（算法）> 其他文件。实现时发现计划本身有错或无法实现，选择**更简单、更安全**的做法，并在 `docs/DECISIONS.md` 里记一条：日期、问题、决定、原因。
5. 外部 API（GitHub、Bedrock）的字段以官方文档为准。如果某个字段在真实调用中报错，查官方文档修正，并记入 `docs/DECISIONS.md`。不要猜测、不要编造字段。

## 3. 质量门槛（每个里程碑都要过）

```bash
make lint        # ruff check + ruff format --check + mypy（strict）
make test-unit   # 不依赖 Docker 的单元测试
make test        # 单元 + 集成测试（集成测试用 testcontainers，需要 Docker）
```

- 类型：`src/` 下所有代码通过 `mypy --strict`。
- 风格：ruff 默认规则加 `02-config-and-infra.md` 里列出的规则集，行宽 100。
- **不留死代码**：没有被调用的函数、注释掉的代码、无用依赖、`TODO` 占位实现都不允许出现在最终提交里。
- **宁小勿散**：评审"宁可看到一个做得好的小代码库，也不要一个松散的大代码库"。不要引入计划外的框架或抽象（例如 Celery、Kafka、通用插件系统、ORM 之外的再一层仓储模式）。唯一刻意保留的扩展点是 `SourceAdapter`。
- 纯函数优先：分析层（`insights/analytics/`）和叙述的评分、校验逻辑必须是不依赖 I/O 的纯函数，方便测试。
- 确定性：相同输入必须产生完全相同的快照 JSON（包括 bootstrap 等随机过程，用固定种子）。

## 4. 安全规则（不可违反）

- token 只从环境变量读取（`pydantic.SecretStr`），**永远不要**写进代码、测试、日志、异常信息或提交历史。`.env` 必须在 `.gitignore` 里。
- 所有外部输入（查询参数、路径参数）都按 `06-api.md` 的白名单正则校验。GitHub 的 base URL 只来自配置，**绝不**由请求参数拼出（防 SSRF）。
- SQL 只用 SQLAlchemy 的参数化语句，不拼接字符串。
- 发给 LLM 的内容只有证据包里的结构化数字、ID、位置名和仓库名（位置名先按 `07-narrative.md` §2.3 清洗）；**不包含** PR 标题、描述、评论等自由文本，也不包含任何用户名。
- 前端只按纯文本渲染叙述，不使用 `dangerouslySetInnerHTML`。
- 容器以非 root 用户运行。

## 5. 凭证与真实数据

- 开发和测试默认**不需要**任何凭证：GitHub 用 respx 模拟，LLM 用 `FakeLLMClient`，数据用合成数据。
- 如果环境里有 `GITHUB_TOKEN` 和 `AWS_BEARER_TOKEN_BEDROCK`，执行各里程碑 DoD 中标注"需要凭证"的冒烟步骤。没有就跳过，并把这些步骤写进最终报告的"待人工验证"部分。
- 不要因为缺少凭证而停下，也不要伪造"已验证"。

## 6. 什么时候停下来问人

只有以下情况才停下来等待人的输入：

- 完成全部工作后，需要人用真实凭证做最终验证；
- 提交物准备好之后（`11` §9），由人填写 README "AI assistance" 一节中的 `<confirm: …>` 占位符，并由人推送或上传；
- 遇到会改变对外契约（`06-api.md`）或产品范围的决策，而计划没有覆盖。

其他问题按第 2 节第 4 条自行决定并记录。

## 7. 最终交付

完成 M12 后，在对话中输出一份最终报告，包含：

1. 完成了哪些里程碑，每个里程碑的 DoD 是否全部通过；
2. 测试与 eval 的结果摘要（命令和关键数字）；
3. `docs/DECISIONS.md` 中偏离计划的条目摘要；
4. 待人工验证的步骤（例如需要真实凭证的冒烟测试、核对 "AI assistance"、推送或上传提交物）；
5. 提交物检查结果（`11` §9：工作区是否干净、压缩包的绝对路径、三条检查的输出）；
6. 已知问题。

## 8. 禁止事项

- 不做个人生产力指标、个人排行榜。个人只出现在 review 负载分布和风险 PR 列表里。
- 不让 LLM 计算数字或决定置信度。
- 不在 API 请求路径上调用 GitHub（API 只读本地数据库和 Redis）。
- 不在请求时自动拉取任意仓库或整个组织；只处理白名单（`TRACKED_REPOS`）里的仓库。
- 不实现 P2 范围的功能（见 `00-overview.md`）。
- 不自行发布或提交作业：不创建远程仓库、不 `git push` 到任何远程、不上传或发送压缩包。M12 只准备提交物并检查（`11` §9），推送、上传和发送由人完成。
- README 的 "AI assistance" 一节不编造：只写自己能确认的事实，没用过的工具、没做过的验证不能写成做过；关于人做了什么的内容保留 `<confirm: …>` 占位符，不替人填写（`11` §7.5）。
