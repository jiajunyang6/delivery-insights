# 09 Frontend (P1, M11)

## 1. Scope and principles

- Read-only single page: choose repository/period → efficiency, bottlenecks, risks, narrative. English UI text; narrative follows lang.
- No router/state/UI libraries; React hooks only, about ten components.
- Render server text as **plain text**; prohibit dangerouslySetInnerHTML.
- No frontend unit tests (low styling return per design); gates npm run typecheck/build.

## 2. Stack and layout

Major-version ranges locked by package-lock.json: react@^18/react-dom@^18/recharts@^2; development typescript@^5/vite@^5/@vitejs/plugin-react/@types/react/@types/react-dom.

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
    ├── App.tsx            # Parameters, snapshot, loading/error state
    ├── api.ts             # fetch, problem+json, 202 polling, AbortController
    ├── types.ts           # 06-compatible TypeScript types, used fields only
    ├── format.ts          # Number/share/hour formatting; safe links
    ├── styles.css
    └── components/
        ├── Controls.tsx         # Repository, period, audience, lang
        ├── Headline.tsx         # Headline, as_of, freshness
        ├── KpiGrid.tsx          # Efficiency and quality guardrail cards
        ├── TimeLedgerChart.tsx  # Stacked ledger bars
        ├── Bottlenecks.tsx      # Findings
        ├── ReviewQueueChart.tsx # Weekly inflow/outflow/queue
        ├── LocationsTable.tsx
        ├── AtRiskTable.tsx      # Snapshot top 20, load all
        └── NarrativePanel.tsx   # Narrative, citation tags, hypotheses, evidence
```

package.json scripts: dev=vite, build=vite build, typecheck=tsc --noEmit, preview=vite preview.

## 3. Page and interactions

### 3.1 Controls and URL

- Repositories: GET /api/v1/repos items[].repo; default first; show sync status beside options unless ok.
- Presets Last 7 days/Last 30 days (default)/Last 90 days; UTC today, to=today/from=to-(N-1), matching precompute/cache. Two custom date inputs; disable query if from>to.
- audience: Director/Manager, default Manager; lang: EN/Chinese, default EN.
- Synchronize repo/from/to/audience/lang into URL via URLSearchParams/history.replaceState; shareable links; read on load.

### 3.2 Loading insights

- GET /api/v1/insights/delivery?repo=…&from=…&to=….
- **200**: render snapshot.
- **202**: one row per unready repository: never_synced "Not synced yet"; backfill "Backfilling history: covered since {covered_since}, needs {required_since}"; open_sweep "Scanning open pull requests"; rederive "Recomputing derived data after a configuration or version change"; stale "Sync has not reached the requested period yet"; include job phase. Retry-After polling, default 30s/minimum 5s; stop after five minutes and suggest later refresh.
- **problem+json**: error strip with title/detail/request_id.
- AbortController cancels requests on parameter changes.

### 3.3 Sections, top to bottom

| Section | Content | Director | Manager |
|---|---|---|---|
| Headline | headline/as_of/freshness time/status; no comparison notice; if period incomplete and to before today UTC: "Data synced through {as_of}; the rest of the period is not covered yet". Today always incomplete: show as_of only | ✓ | ✓ |
| Efficiency | cycle p50/p90, effective throughput, merged within N days, waiting share labeled of cycle time, waste, review rounds/concentration, revert rate. Current/previous/change arrow colored by desirable direction; not significant and insufficient sample notices | ✓ | ✓ |
| Quality guardrail | Cycle/revert change side by side; highlight non-ok verdict | ✓ | ✓ |
| Ledger | Previous/Current horizontal stacked bars for four waiting shares; tooltip PR-hours; unavailable-CI notice | ✓ | ✓ |
| Bottlenecks | Cards: rank/severity/title/impact hours/share/evidence label:value/recommendation/what-if, e.g. pickup cap 8h → cycle median −11%. Director top three. Empty with <20 merges: "Too few merged PRs for bottleneck findings; see at-risk PRs" | ✓ | ✓ |
| Review queue | Weekly inflow/outflow bars plus open_at_week_end line, Recharts ComposedChart | | ✓ |
| Locations | Location/merged/pickup/vs rest/reviewer wait/inflow/outflow/risks/owners | | ✓ |
| Risks | Snapshot PR #number title/author (deleted user if null)/state/age vs threshold/severity. Load all calls /api/v1/insights/delivery/prs?…&at_risk=true&limit=50, follows next_cursor | | ✓ |
| Narrative | §3.4 | ✓ | ✓ |

### 3.4 Narrative panel

- After snapshot load or audience/lang change, request /api/v1/snapshots/{snapshot_id}/narrative?audience=…&lang=…. First generation may take tens of seconds; show Generating narrative….
- Split text with /(\[E\d+\])/; ordinary text nodes, citation buttons E1; clicking highlights/scrolls to evidence entry.
- Evidence list: label/value/previous/change/ref JSON Pointer/example PR links.
- Hypothesis cards: title/location/statement citation tags/confidence number+band+bar with "Evidence-strength score, not a calibrated probability"; chain symptom→stage→location→mechanism, counter-evidence, ruled-out alternatives (e.g. H_pr_size_growth ruled out by E30,E31), open alternatives with no data/insufficient sample/below threshold/not in top 3 labels; action/verification; show original band/reason after downgrade; source=llm labeled Outside the hypothesis library.
- abstained=true: Signals are insufficient for a root cause.
- Footer: Generated by LLM ({model}) · prompt {prompt_version} · validation {validation}; template: Template narrative ({fallback_reason}).

## 4. Security

- Text-only server strings; never concatenate HTML.
- Only https://github.com/ URLs become anchors with target=_blank rel=noopener noreferrer; others text (format.ts safeGithubUrl).
- nginx security headers (§6).

## 5. API client (`api.ts`)

- `const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api"`.
- fetchJson<T>(path,signal) returns {status,data,headers}; problem+json throws ApiProblem with title/detail/status/request_id.
- loadInsights(params,signal,onPending): 202 polling (§3.2).
- Browser HTTP cache using response Cache-Control/ETag; no additional frontend cache.

## 6. Build and deployment

`vite.config.ts`:

```ts
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://localhost:8000", changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, "") } },
  },
});
```

`nginx.conf`:

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

- Trailing proxy_pass / strips /api; proxy_read_timeout 180s covers first narrative generation (`07` §9.2).
- Recharts requires style-src 'unsafe-inline'.

Dockerfile: nginx non-root, port 8080:

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

Compose web maps 5173:8080 (`02` §6). Same-origin /api needs no CORS; CORS_ORIGINS remains for direct cross-origin API calls.
