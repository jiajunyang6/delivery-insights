#!/usr/bin/env bash
set -euo pipefail
python3 - <<'PY'
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.parse import urlencode

base = os.environ.get("API", "http://localhost:8000").rstrip("/")
repo = os.environ.get("REPO", "dotnet/runtime")

def get(path):
    with tempfile.TemporaryDirectory(prefix="delivery-smoke-") as tmp:
        header_path = Path(tmp) / "headers"
        result = subprocess.run(
            ["curl", "--silent", "--show-error", "--max-time", "185",
             "--dump-header", str(header_path), "--write-out", "\n%{http_code}", base + path],
            check=True, capture_output=True, text=True,
        )
        body, status = result.stdout.rsplit("\n", 1)
        headers = dict(line.lower().split(":", 1) for line in header_path.read_text().splitlines()
                       if ":" in line)
        payload = json.loads(body)
        print(path.split("?")[0], status)
        return int(status), payload, headers

for endpoint in ("/healthz", "/readyz", "/v1/repos"):
    status, payload, _ = get(endpoint)
    if status != 200:
        raise SystemExit(f"Failed dependency/status check: {status}")
    if endpoint == "/v1/repos":
        print(json.dumps(payload, ensure_ascii=False))
today = dt.datetime.now(dt.timezone.utc).date()
query = urlencode({"repo": repo, "from": str(today-dt.timedelta(days=6)), "to": str(today)})
deadline = time.monotonic()+300
while True:
    status, snapshot, headers = get("/v1/insights/delivery?" + query)
    if status == 200:
        break
    remaining = deadline-time.monotonic()
    if status != 202 or remaining <= 0:
        raise SystemExit(f"Insight unavailable: HTTP {status}, {snapshot.get('type', 'pending')}")
    time.sleep(min(remaining, max(1, int(headers.get("retry-after", "30")))))
sid = snapshot["snapshot_id"]
print("snapshot_id:", sid, "merged_prs:", snapshot["meta"]["sample"]["merged_prs"])
status, narrative, _ = get(f"/v1/snapshots/{sid}/narrative?audience=director&lang=en")
if status != 200:
    raise SystemExit(f"Narrative failed: HTTP {status}")
print("generated_by:", narrative["meta"]["generated_by"],
      "validation:", narrative["meta"]["validation"])
PY
