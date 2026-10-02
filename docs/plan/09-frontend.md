# 09 前端（P1，M11）

## 1. 范围与原则

- 一个只读单页：选仓库和周期 → 看效率、瓶颈、风险 PR 和叙述。界面文字用英文；叙述语言跟随 `lang` 切换。
- 不引入路由、状态管理库、UI 组件库；只用 React 自带的 hooks。代码量控制在小规模（约 10 个组件）。
- 所有服务端文本按**纯文本**渲染，禁止 `dangerouslySetInnerHTML`。
- 不写前端单元测试（设计文档：前端样式投入产出比低）；质量门槛是 `npm run typecheck` 和 `npm run build`。

## 2. 技术与目录

依赖（写主版本范围，由 `package-lock.json` 锁定）：`react@^18`、`react-dom@^18`、`recharts@^2`；开发依赖 `typescript@^5`、`vite@^5`、`@vitejs/plugin-react`、`@types/react`、`@types/react-dom`。

```
frontend/
├── package.json          # scripts: dev, build, typecheck, preview
├── package-lock.json
├── tsconfig.json         # "strict": true, "noUncheckedIndexedAccess": true
├── vite.config.ts
├── index.html
├── Dockerfile
├── nginx.conf
├── .dockerignore         # node_modules, dist
└── src/
    ├── main.tsx
    ├── App.tsx            # 状态：参数、快照、加载状态、错误
    ├── api.ts             # fetch 封装、problem+json、202 轮询、AbortController
    ├── types.ts           # 与 06 一致的 TypeScript 类型（只写用到的字段）
    ├── format.ts          # 数字、比例、小时格式化；安全链接判断
    ├── styles.css
    └── components/
        ├── Controls.tsx         # 仓库、周期、audience、lang
        ├── Headline.tsx         # headline、as_of、数据新鲜度
        ├── KpiGrid.tsx          # 效率指标卡片 + 质量护栏卡片
        ├── TimeLedgerChart.tsx  # 时间账堆叠条形图
        ├── Bottlenecks.tsx      # 发现列表
        ├── ReviewQueueChart.tsx # 每周流入、流出、积压
        ├── LocationsTable.tsx
        ├── AtRiskTable.tsx      # 快照中的前 20 条 + 加载全部
        └── NarrativePanel.tsx   # 叙述、引用标签、假设、证据
```

`package.json` 脚本：`"dev": "vite"`、`"build": "vite build"`、`"typecheck": "tsc --noEmit"`、`"preview": "vite preview"`。

## 3. 页面与交互

### 3.1 控件与 URL

- 仓库：`GET /api/v1/repos` 的 `items[].repo`，默认第一个；同步状态不是 `ok` 时在选项旁显示状态。
- 周期：预设 `Last 7 days`、`Last 30 days`（默认）、`Last 90 days`，按 **UTC** 今天计算：`to = 今天`，`from = to - (N - 1)`（与 worker 预计算的周期一致，能直接命中缓存）；另有两个日期输入框做自定义周期，`from > to` 时禁用查询。
- `audience`：`Director` / `Manager`（默认 Manager）；`lang`：`EN` / `中文`（默认 EN）。
- 参数同步到 URL 查询串（`repo`、`from`、`to`、`audience`、`lang`，用 `URLSearchParams` 和 `history.replaceState`），链接可以直接分享；页面加载时从 URL 读取。

### 3.2 加载 insight

- 请求 `GET /api/v1/insights/delivery?repo=…&from=…&to=…`。
- **200**：渲染快照。
- **202**：显示同步进度：每个未就绪仓库按 `reason` 显示一行说明（`never_synced` "Not synced yet"；`backfill` "Backfilling history: covered since {covered_since}, needs {required_since}"；`open_sweep` "Scanning open pull requests"；`rederive` "Recomputing derived data after a configuration or version change"；`stale` "Sync has not reached the requested period yet"）和任务阶段；按 `Retry-After`（缺省 30 秒，最少 5 秒）自动重试，累计 5 分钟后停止并提示稍后刷新。
- **problem+json**：显示错误条（`title`、`detail`、`request_id`）。
- 参数变化时用 `AbortController` 取消进行中的请求。

### 3.3 区块（自上而下）

| 区块 | 内容 | Director | Manager |
|---|---|---|---|
| Headline | `headline`；`as_of`；`meta.data_freshness`（最后同步时间、状态）；`comparison_available = false` 时提示"no previous period to compare"；`period.complete = false` 且 `to` 早于 UTC 今天时提示"Data synced through {as_of}; the rest of the period is not covered yet"（`to` 是今天时周期还没结束，`complete` 总是 `false`，只显示 `as_of`） | ✓ | ✓ |
| 效率指标 | 卡片：cycle time p50（附 p90）、effective throughput、merged within N days、waiting share（标注 "of cycle time"）、waste share、avg review rounds、review concentration、revert rate；每张卡片显示值、上一周期值、变化箭头（按"越低越好 / 越高越好"着色）、`significant = false` 时显示 "not significant"、`status = insufficient_sample` 时显示 "insufficient sample" | ✓ | ✓ |
| 质量护栏 | cycle time 变化与 revert 率变化并排；`verdict` 不是 `ok` 时高亮 | ✓ | ✓ |
| 时间账 | 两行（Previous、Current）的水平堆叠条形图，四个等待状态的占比；tooltip 显示 PR-小时；`ci_data_available = false` 时注明 "CI data not available" | ✓ | ✓ |
| 瓶颈 | `bottlenecks` 卡片：排名、严重程度、标题、影响（PR-小时和占比）、证据（label: value）、建议、what-if（例如 "Capping pickup at 8 h → median cycle time −11%"）。Director 只显示前 3 条。数组为空且合并 PR 少于 20 个时显示 "Too few merged PRs for bottleneck findings; see at-risk PRs" | ✓ | ✓ |
| review 队列 | 每周 inflow / outflow 柱状图 + `open_at_week_end` 折线（Recharts `ComposedChart`） | | ✓ |
| 位置 | 表格：location、merged PRs、pickup p50、vs rest、reviewer-wait share、inflow/outflow、at-risk、owners | | ✓ |
| 风险 PR | 快照的 `at_risk_prs`：PR（`#number title`）、author（`null` 时显示 "deleted user"）、state、age vs threshold、severity；"Load all" 调用 `GET /api/v1/insights/delivery/prs?…&at_risk=true&limit=50`，按 `next_cursor` 继续加载 | | ✓ |
| 叙述 | 见 §3.4 | ✓ | ✓ |

### 3.4 叙述面板

- 快照加载后，或 `audience` / `lang` 变化时，请求 `GET /api/v1/snapshots/{snapshot_id}/narrative?audience=…&lang=…`。首次生成可能需要几十秒，显示 "Generating narrative…"。
- 正文：用 `/(\[E\d+\])/` 切分；普通文本作为文本节点，引用渲染为按钮标签（`E1`）。点击标签时在下方证据列表中高亮并滚动到该条目。
- 证据列表：`label`、`value`、`previous`、变化、`ref`（JSON Pointer 文本）、示例 PR 链接。
- 假设卡片：标题和位置、`statement`（同样渲染引用标签）、置信度（数值 + 档位 + 简单进度条，旁边注明 "Evidence-strength score, not a calibrated probability"）、证据链（`symptom → stage → location → mechanism`，每步一组标签）、反证、已排除的备选假设（"H_pr_size_growth ruled out by E30, E31"）、未能判断的备选假设（`alternatives_open`："H_ci_bottleneck not assessed: no data"，原因文字 `insufficient_sample` "insufficient sample"、`no_data` "no data"、`below_threshold` "below threshold"、`not_selected` "not in the top 3"）、建议动作、验证方式；LLM 下调过的显示原档位和理由；`source = "llm"` 的标注 "Outside the hypothesis library"。
- `abstained = true`：显示 "Signals are insufficient for a root cause"。
- 页脚：`Generated by LLM ({model}) · prompt {prompt_version} · validation {validation}`；模板时显示 `Template narrative ({fallback_reason})`。

## 4. 安全

- 只按文本渲染服务端字符串；不拼接 HTML。
- 链接：只有以 `https://github.com/` 开头的 URL 渲染为 `<a href=… target="_blank" rel="noopener noreferrer">`，其他一律显示为文本（`format.ts` 中的 `safeGithubUrl`）。
- nginx 设置安全响应头（§6）。

## 5. API 客户端（`api.ts`）

- `const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api"`。
- `fetchJson<T>(path, signal)` 返回 `{status, data, headers}`；`Content-Type` 为 `application/problem+json` 时抛出带 `title`、`detail`、`status`、`request_id` 的 `ApiProblem`。
- `loadInsights(params, signal, onPending)`：处理 202 轮询（§3.2）。
- 依赖浏览器的 HTTP 缓存（快照响应带 `Cache-Control` 和 `ETag`），不另做前端缓存。

## 6. 构建与部署

`vite.config.ts`：

```ts
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://localhost:8000", changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, "") } },
  },
});
```

`nginx.conf`：

```nginx
server {
  listen 8080;
  root /usr/share/nginx/html;

  add_header X-Content-Type-Options "nosniff" always;
  add_header Referrer-Policy "no-referrer" always;
  add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'" always;

  location /api/ {
    proxy_pass http://api:8000/;
    proxy_set_header Host $host;
    proxy_set_header X-Request-ID $request_id;
    proxy_read_timeout 180s;
  }

  location / {
    try_files $uri /index.html;
  }
}
```

- `proxy_pass` 末尾的 `/` 去掉 `/api` 前缀；`proxy_read_timeout 180s` 覆盖叙述首次生成时的等待（`07` §9.2）。
- `style-src 'unsafe-inline'` 是 Recharts 的内联样式所需。

`Dockerfile`（nginx 以非 root 用户运行，监听 8080）：

```dockerfile
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
```

Compose 的 `web` 服务映射 `5173:8080`（`02` §6）。同源访问 `/api`，不需要 CORS；`CORS_ORIGINS` 只为直接从其他来源调用 API 的场景保留。
