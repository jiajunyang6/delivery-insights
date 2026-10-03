# AGENTS.md — Instructions for the implementation agent

Implement **Delivery Insights**, a Python web service that synchronizes GitHub PR collaboration data and helps engineering managers and directors understand where delivery is stuck, why, and what to fix first. It exposes two HTTP endpoints:

1. `GET /v1/insights/delivery`: period-based team efficiency and bottleneck analysis; code computes every number.
2. `GET /v1/snapshots/{snapshot_id}/narrative`: a short narrative from the same snapshot, written by Claude Sonnet 4.6 on AWS Bedrock, with root-cause hypotheses, confidence, and evidence chains.

The plan is the requirements source. Documentation, code, comments, logs, API text, and README use **English**. Narrative output is English only; the API accepts only `lang=en`.

## 1. Reading order

Read these files fully before writing code:

| File | Content |
|---|---|
| `docs/plan/00-overview.md` | Product, P0/P1 scope, architecture, stack, layout, terminology |
| `docs/plan/01-milestones.md` | Execution sequence: milestones, tasks, definitions of done |
| `docs/plan/02-config-and-infra.md` | Configuration, dependencies, logs, Docker, Compose, Makefile, CI |
| `docs/plan/03-data-model.md` | Postgres schema, indexes, Redis keys |
| `docs/plan/04-github-sync.md` | GitHub client, GraphQL, limits, synchronization |
| `docs/plan/05-analytics.md` | State machine, metrics, bottlenecks, snapshots |
| `docs/plan/06-api.md` | REST conventions, parameters, response contracts |
| `docs/plan/07-narrative.md` | Evidence, hypotheses, confidence, prompt, validation |
| `docs/plan/08-eval-harness.md` | Synthetic evaluation |
| `docs/plan/09-frontend.md` | React single-page UI |
| `docs/plan/10-testing.md` | Strategy and required cases |
| `docs/plan/11-readme-and-submission.md` | README and submission notes |
| `docs/plan/12-acceptance-checklist.md` | Final acceptance checklist |

## 2. Execution rules

1. Follow `01-milestones.md` in order: complete P0 M0–M7 and their DoDs before P1 M8–M11, then M12.
2. Execute every milestone DoD command; all must pass. Appearance alone is not completion.
3. Commit each completed milestone with Conventional Commits, such as `feat(sync): incremental GitHub sync`.
4. Resolve specification conflicts in this order: `06-api.md` (external contract) > `05-analytics.md` / `07-narrative.md` (algorithms) > other files. If the plan is incorrect or infeasible, choose the simpler, safer approach and record date, issue, decision, and reason in `docs/DECISIONS.md`.
5. Official GitHub/Bedrock documentation governs upstream fields. If a real call rejects a field, consult the official documentation, fix it, and record the decision. Never invent fields.

## 3. Quality gates for every milestone

```bash
make lint        # ruff check + ruff format --check + strict mypy
make test-unit   # Unit tests without Docker
make test        # Unit + Testcontainers integration tests; Docker required
```

- All `src/` code passes `mypy --strict`.
- Ruff uses its defaults plus `02-config-and-infra.md` rules, line length 100.
- No dead code, commented-out implementations, unused dependencies, or TODO placeholders in final delivery.
- Keep the codebase small and cohesive. Do not add unplanned frameworks or abstractions such as Celery, Kafka, a generic plugin system, or another repository layer above the ORM. `SourceAdapter` is the only deliberate extension point.
- Analytics and narrative scoring/validation are pure functions without I/O.
- Identical input produces identical snapshot JSON, including fixed-seed random/bootstrap computations.

## 4. Security rules

- Read tokens only from environment variables via `pydantic.SecretStr`. Never put them in code, tests, logs, exceptions, or Git history. Ignore `.env`.
- Validate query/path inputs with the allowlist patterns in `06-api.md`. GitHub base URLs come only from configuration, never request input.
- Use SQLAlchemy parameter binding, never concatenate SQL.
- Send the LLM only structured evidence numbers, IDs, sanitized locations (`07-narrative.md` §2.3), and repository names. No PR titles, bodies, comments, or usernames.
- Render narrative as plain text; never use `dangerouslySetInnerHTML`.
- Run containers as non-root.

## 5. Credentials and real data

- Default development/tests need no credentials: respx GitHub mocks, `FakeLLMClient`, synthetic data.
- If `GITHUB_TOKEN` / `AWS_BEARER_TOKEN_BEDROCK` are available, execute applicable credentialed smoke steps. Otherwise skip and list them under pending human verification in the final report.
- Do not stop because credentials are missing or claim unperformed verification.

## 6. When to ask the human

Wait for human input only when:

- All work is complete and final verification needs real credentials;
- The submission is prepared (`11` §9), and the submitter must fill README AI-assistance `<confirm: …>` placeholders and push/upload;
- An uncovered decision would change the external contract or product scope.

Resolve other issues using §2 rule 4 and record the decision.

## 7. Final delivery report

After M12, report:

1. Completed milestones and whether each DoD passed;
2. Test/eval commands and key results;
3. Deviations summarized from `docs/DECISIONS.md`;
4. Pending human checks, including credentials, AI disclosure, push/upload;
5. Submission audit (`11` §9): clean status, absolute archive path, all three check outputs;
6. Known issues.

## 8. Prohibitions

- No individual productivity metrics/leaderboards; individuals appear only in review-load distribution and at-risk PRs.
- The LLM never calculates numbers or decides confidence.
- No GitHub on the API request path; requests read local DB/Redis.
- No request-triggered arbitrary repository/organization discovery; process `TRACKED_REPOS` only.
- No P2 features (`00-overview.md`).
- Do not publish or submit: no remote creation, `git push`, archive upload, or sending. M12 only prepares/audits; the human submits.
- AI disclosure is factual: confirmed agent work/checks only, no invented tool use or verification. Human actions remain `<confirm: …>` placeholders for the submitter, never filled by the agent (`11` §7.5).
