# 12 验收清单

## 0. 用法

- 每一项都要实际执行"验证"中的步骤，通过后勾选。不能"看起来对"就勾。
- 标签：`[P0]` 必做；`[P1]` 加分；`[凭证]` 需要真实的 `GITHUB_TOKEN` 和 / 或 `AWS_BEARER_TOKEN_BEDROCK`（没有时标"待人工验证"，写进最终报告）；`[M12]` 只在 M12 检查（README、最终报告）。
- **M7 的 P0 检查**：执行所有 `[P0]` 且不带 `[M12]` 的条目。**M12**：执行全部条目。
- 下面命令默认在仓库根目录执行；`API=http://localhost:8000`，`TO`、`FROM` 的写法见 `06` §8。

## A. 功能

- [ ] **A1** `[P0]` 从干净的克隆开始：`cp .env.example .env` → `docker compose up --build -d`，`migrate` 退出码 0，`api` healthy，`worker` 运行中。验证：`docker compose ps -a`（`migrate` 已退出，不加 `-a` 看不到）。
- [ ] **A2** `[P0]` `/healthz` 返回 `{"status":"ok"}`；`/readyz` 返回 200；`docker compose stop redis` 后 `/readyz` 返回 503 problem（`checks.redis = "error"`），再 `docker compose start redis` 恢复。
- [ ] **A3** `[P0][凭证]` worker 回填 dotnet/runtime：`GET /v1/repos` 中 `covered_since` 依次推进（7 天 → 30 天 → 180 天），`last_open_sweep_at` 在第一阶段后出现，`last_sync_status = "ok"`。
- [ ] **A4** `[P0][凭证]` 最近 7、30、90 天的 insight 都返回 200，包含 `headline`、`efficiency`、`time_ledger`、`bottlenecks`、`bottleneck_analysis`、`at_risk_prs`、`waste`、`rework`、`guardrail`、`trend`、`meta`；带 `If-None-Match` 再请求返回 304。
- [ ] **A5** `[P0]` 未就绪时返回 202 + `Retry-After` + Pending 结构，`repos[].reason` 覆盖 `never_synced`、`backfill`、`open_sweep`、`rederive`、`stale` 五种情况（`test_api.py` 覆盖；有凭证时在首次回填期间手动请求 90 天周期确认）。
- [ ] **A6** `[P0][凭证]` `06` §8 的 9 组 curl 全部符合描述。
- [ ] **A7** `[P0][凭证]` `/v1/insights/delivery/prs?…&at_risk=true` 的 `total` 等于快照的 `at_risk_summary.total`；`status=merged` 的 `total` 等于 `meta.sample.merged_prs`。
- [ ] **A8** `[P0]` 没有 Bedrock key 时叙述返回 200，`meta.generated_by = "template"`、`fallback_reason = "llm_disabled"`，并写入 `narratives`（`model_id = "template"`）。
- [ ] **A9** `[P0][凭证]` 有 Bedrock key 时 dotnet/runtime 最近 30 天的叙述 `meta.generated_by = "llm"`、`meta.validation = "passed"`；任选叙述中 3 个数字，按 `evidence[].ref` 在快照中找到对应值。
- [ ] **A10** `[P0]` 手动同步返回 202 + `Location`；冷却期内再次触发返回 429 + `Retry-After`。
- [ ] **A11** `[P0]` `org=dotnet` 与 `repo=dotnet/runtime`（只跟踪这一个仓库时）返回同一个 `snapshot_id`。
- [ ] **A12** `[P0]` `/docs` 列出 `06` §1 表中的全部 endpoint，响应模型完整。

## B. 正确性

- [ ] **B1** `[P0]` `make test` 全部通过，包括 `05` §2.5 的全部 22 个状态机用例、黄金测试、确定性测试（含打乱输入顺序）。
- [ ] **B2** `[P0][凭证]` `docker compose exec api python -m insights.sync.invariants --repo dotnet/runtime` 输出 0 个违规。
- [ ] **B3** `[P0][凭证]` 对账：从 `/prs?status=merged` 任选 3 个 PR，在 GitHub 页面核对 ready 时间、首次人工 review 时间、合并时间（误差 1 分钟内），把 PR 编号和结论写进最终报告。
- [ ] **B4** `[P0][凭证]` 时间账总量 `time_ledger.total_pr_hours` 与 `/prs?status=merged` 全部行的 `ledger_hours` 之和一致（差异只来自取整，相对误差 < 0.1%）。用一个临时脚本分页拉取后求和，不提交该脚本。
- [ ] **B5** `[P0]` 样本不足的指标返回 `value = null`、`status = "insufficient_sample"`（单元测试覆盖）。
- [ ] **B6** `[P0]` 黄金文件首次生成后人工核对过关键数字（`01` M5 的注意事项），提交信息中有说明。

## C. 代码质量

- [ ] **C1** `[P0]` `make lint` 通过（ruff check、ruff format --check、mypy strict）。
- [ ] **C2** `[P0]` 没有死代码和占位：`grep -rnE "TODO|FIXME|XXX|NotImplementedError" backend/src frontend/src` 无结果；没有注释掉的代码；没有未使用的依赖。
- [ ] **C3** `[P0]` 纯函数分层：`grep -rnE "^(from|import) (insights\.db|insights\.redis|httpx|boto3|sqlalchemy|redis)" backend/src/insights/analytics backend/src/insights/narrative` 只命中 `analytics/dataset.py`、`narrative/service.py`、`narrative/llm.py`。
- [ ] **C4** `[P0]` 依赖与 `02` §2 一致；多出的依赖在 `docs/DECISIONS.md` 中有理由。
- [ ] **C5** `[P0]` 每个里程碑一个 Conventional Commit（`git log --oneline`）。
- [ ] **C6** `[P0]` `docs/DECISIONS.md` 的每一条都有原因。

## D. 安全

- [ ] **D1** `[P0]` 提交历史中没有 token：`git log -p --all -- . ':(exclude)docs/plan' | grep -nE 'gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AWS_BEARER_TOKEN_BEDROCK=[A-Za-z0-9]'` 无结果（计划文档本身含有这些模式的说明文字，所以排除；测试中的假 token 必须在运行时拼接生成，例如 `"ghp_" + "x" * 36`，源码里不出现完整形态）。
- [ ] **D2** `[P0]` `git check-ignore -q .env && echo ignored` 输出 `ignored`；`.env.example` 中 token 为空。
- [ ] **D3** `[P0][凭证]` 完成一次同步和一次叙述后，日志中没有 token 原文：`docker compose logs api worker | grep -cF "$(grep '^GITHUB_TOKEN=' .env | cut -d= -f2-)"` 输出 0；Bedrock key 同理。
- [ ] **D4** `[P0]` 非法的仓库名、日期、游标返回 422，未跟踪仓库返回 403，响应不回显参数原值（测试 + curl）。
- [ ] **D5** `[P0]` 没有 SSRF 路径：没有任何参数接受 URL 或主机名；GitHub 地址只来自 `Settings`；`rest_get` 拒绝完整 URL（测试）。
- [ ] **D6** `[P0]` SQL 全部参数化：检查 `grep -rn "text(" backend/src` 的每处用法只使用绑定参数。
- [ ] **D7** `[P0]` 证据包和 LLM 请求不含 PR 标题、正文、登录名（`test_security.py`）；位置名清洗生效（`test_evidence.py`）。
- [ ] **D8** `[P0]` 500 响应只含固定文本，没有堆栈和异常消息（测试）。
- [ ] **D9** `[P0]` 容器以非 root 运行：`docker compose exec api id -u` 和 `docker compose exec worker id -u` 输出 `10001`；`[P1]` `docker compose exec web id -u` 输出 `101`。
- [ ] **D10** `[P1]` 前端没有 `dangerouslySetInnerHTML`（`grep -rn dangerouslySetInnerHTML frontend/src` 无结果）；只有 `https://github.com/` 链接可点击。

## E. 性能

- [ ] **E1** `[P0][凭证]` dotnet/runtime 90 天快照冷计算 < 3 秒：快照服务对每次计算记录 `snapshot_computed` 日志（`duration_ms`、`load_ms`、`compute_ms`、`merged_prs`），取 worker 预计算 90 天周期的那一条。
- [ ] **E2** `[P0][凭证]` 缓存命中的 insight 请求 p95 < 300 毫秒：

  ```bash
  for i in $(seq 50); do curl -s -o /dev/null -w '%{time_total}\n' "$API/v1/insights/delivery?repo=dotnet/runtime&from=$FROM&to=$TO"; done | sort -n | sed -n '48p'
  ```

- [ ] **E3** `[P0]` 请求路径上没有 GitHub 调用：`test_security.py` 中的导入检查通过（API 进程不加载 `insights.sources`）。
- [ ] **E4** `[P0]` 同步使用批量写入和 GraphQL 分页（代码检查：没有逐行 INSERT 循环）；`[凭证]` 回填期间日志中的 `rate_limit_remaining` 保持在 1,000 以上。
- [ ] **E5** `[P0]` 分析层没有对 PR 的 O(n²) 循环（代码检查；作者并行 PR 用排序加扫描）。
- [ ] **E6** `[P0]` CPU 密集的快照计算和 boto3 调用都通过 `asyncio.to_thread` 执行（代码检查）。

## F. 可运维性

- [ ] **F1** `[P0][M12]` 按 README 的 Quickstart 从干净克隆逐字执行成功。
- [ ] **F2** `[P0]` 日志为 JSON，API 请求带 `request_id`、`method`、`path`、`status`、`duration_ms`；worker 日志带 `job`、`repo`、`phase`。
- [ ] **F3** `[P0]` 不设置 `GITHUB_TOKEN` 启动：API 正常，`/v1/repos` 显示 `missing_token`，insight 返回 503 `data-unavailable`，`detail` 说明原因。
- [ ] **F4** `[P0][凭证]` `make smoke` 通过。
- [ ] **F5** `[P0][凭证]` `docker compose restart worker` 后回填从游标继续，不重复抓取已完成的阶段（看 `sync_jobs.phase` 和日志）。

## G. 判断力与文档

- [ ] **G1** `[P0][M12]` README 包含 `11` §2 的全部章节；没有未经验证的数字；命令与 Makefile、Compose 一致。
- [ ] **G2** `[P0][M12]` "Key trade-offs"、"Things deliberately not done"、"Beyond the brief"、"Known limitations" 四节齐全，内容与实现一致（没做的不写进 Beyond the brief）；置信度写明是确定性的、未经真实数据校准的证据强度分数（`11` §2 第 6 项、§7.1），暂缓项（真实数据校准、链级交付时长）出现在 Not done 中。
- [ ] **G3** `[P0][M12]` 输出 `AGENTS.md` §7 要求的最终报告。
- [ ] **G4** `[P0][M12]` README 有 "AI assistance" 小节（`11` §7.5）：`<agent: …>` 占位符都已换成 agent 能确认的真实内容（工具和模型、写了什么、实际执行过的命令和结果、没有执行的检查），`grep -n '<agent:' README.md` 无结果；关于人的 `<confirm: …>` 占位符原样保留给提交人，并逐条列进最终报告的"待人工验证"。agent 没有替人声称做过任何决定或审阅。
- [ ] **G5** `[P0][M12]` README 有 "With one more day" 小节（`11` §7.6）：最多 5 条、按影响排序、与 Not done 和 Known limitations 一致，已完成的项不在其中。
- [ ] **G6** `[P0][M12]` 提交物检查（`11` §9，M12 的最后一步）：`git status --porcelain` 为空；使用压缩包时按 `11` §9 从全新克隆打包到 `../delivery-insights.tar.gz`，三条检查分别输出 `has .git`、`no local files`、`no local path`（包中有 `.git`，没有 `.env`、`node_modules`、`.venv`，不含本机路径），最终报告写出压缩包的绝对路径；使用公开仓库时由人推送，之后在未登录的环境执行 `git ls-remote <url>` 成功。agent 没有执行任何推送、上传或发送（`AGENTS.md` §8）。

## H. P1 加分项

- [ ] **H1** `[P1]` `make eval-offline` 达到 `08` §6 的全部门槛，退出码 0。
- [ ] **H2** `[P1][凭证]` `make eval` 达到全部门槛；把指标表写进最终报告。
- [ ] **H3** `[P1]` 前端：`npm ci && npm run typecheck && npm run build` 通过；`curl -s localhost:5173/api/healthz` 返回 `{"status":"ok"}`；人工检查：切换 audience 时区块变化、引用标签可点击并高亮证据、202 时显示同步进度。
- [ ] **H4** `[P1][凭证]` dotnet/runtime 的 area 位置有 `owners_count`；`LOCATION_DIMENSION=codeowners` 时位置来自 CODEOWNERS 规则（重启后触发 `rederive_repo`）。
- [ ] **H5** `[P1]` 快照包含 `bottleneck_analysis.ci` 和 `time_ledger.ci_coverage`；默认 `CI_COMPLETE=false` 时 CI 假设的置信度不超过 0.5。
- [ ] **H6** `[P1]` 快照包含 `drivers`、`efficiency.survival`、`efficiency.predictability`；黄金文件已更新且提交信息说明原因。
- [ ] **H7** `[P1]` director / manager × en / zh 四种叙述都能通过校验（`test_template.py`、eval）。
