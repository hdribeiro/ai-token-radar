#!/usr/bin/env python3
"""Daily fetcher for AI Token Radar.

Pulls OpenRouter Data API + public models list and the Artificial Analysis free
endpoint, and stores dated raw snapshots plus an upserted rankings table.
Keys come only from environment variables (GitHub Actions secrets).
Standard library only.
"""
import csv
import datetime as dt
import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
RAW = os.path.join(DATA, "raw")
TODAY = dt.datetime.now(dt.timezone.utc).date()
UA = "ai-token-radar/1.0 (private dashboard)"

OR_KEY = os.environ.get("OPENROUTER_API_KEY", "")
AA_KEY = os.environ.get("AA_API_KEY", "")

errors = []


def get(url, headers=None, params=None, retries=3):
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    h = {"User-Agent": UA, "Accept": "application/json"}
    h.update(headers or {})
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read()[:300].decode("utf-8", "replace")
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            raise RuntimeError(f"HTTP {e.code} for {url.split('?')[0]}: {body}")
        except urllib.error.URLError as e:
            if attempt < retries - 1:
                time.sleep(5)
                continue
            raise RuntimeError(f"URL error for {url.split('?')[0]}: {e}")


def save_raw(source, payload, gz=False):
    d = os.path.join(RAW, source)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{TODAY.isoformat()}.json" + (".gz" if gz else ""))
    data = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if gz:
        with gzip.GzipFile(path, "wb", mtime=0) as f:
            f.write(data)
    else:
        with open(path, "wb") as f:
            f.write(data)
    return path


def step(name, fn):
    try:
        fn()
        print(f"ok   {name}")
    except Exception as e:  # keep other sources running
        errors.append(f"{name}: {e}")
        print(f"FAIL {name}: {e}", file=sys.stderr)


# ---------------- OpenRouter ----------------
def or_headers():
    if not OR_KEY:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    return {"Authorization": f"Bearer {OR_KEY}"}


def or_rankings():
    path = os.path.join(DATA, "rankings_daily.csv")
    rows = {}
    meta_log = []
    if os.path.exists(path):
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                rows[(r["date"], r["model_permaslug"])] = r["total_tokens"]
        # refresh trailing window: in-progress days get restated
        windows = [(TODAY - dt.timedelta(days=14), TODAY)]
    else:
        windows = []
        start = dt.date(2025, 1, 1)
        while start <= TODAY:
            end = min(start + dt.timedelta(days=360), TODAY)
            windows.append((start, end))
            start = end + dt.timedelta(days=1)
    for s, e in windows:
        p = get("https://openrouter.ai/api/v1/datasets/rankings-daily", or_headers(),
                {"start_date": s.isoformat(), "end_date": e.isoformat()})
        meta_log.append(p.get("meta", {}))
        fetched_dates = {r["date"] for r in p.get("data", [])}
        # drop stale rows for days we just re-fetched, then insert fresh ones
        rows = {k: v for k, v in rows.items() if k[0] not in fetched_dates}
        for r in p.get("data", []):
            rows[(r["date"], r["model_permaslug"])] = str(r["total_tokens"])
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "model_permaslug", "total_tokens"])
        for (d, m) in sorted(rows):
            w.writerow([d, m, rows[(d, m)]])
    save_raw("openrouter_rankings_meta", meta_log)


def or_models():
    p = get("https://openrouter.ai/api/v1/models")  # public endpoint
    save_raw("openrouter_models", p, gz=True)


def or_classifications():
    p = get("https://openrouter.ai/api/v1/classifications/task", or_headers(), {"window": "7d"})
    save_raw("openrouter_classifications", p)


def or_apps():
    p = get("https://openrouter.ai/api/v1/datasets/app-rankings", or_headers(),
            {"sort": "popular", "limit": 50})
    save_raw("openrouter_apps", p)


def or_benchmarks():
    out = {}
    for task in ("intelligence", "coding", "agentic"):
        out[task] = get("https://openrouter.ai/api/v1/benchmarks", or_headers(),
                        {"source": "artificial-analysis", "task_type": task, "max_results": 100})
    save_raw("openrouter_benchmarks_aa", out)


# ---------------- Artificial Analysis ----------------
def aa_models():
    if not AA_KEY:
        raise RuntimeError("AA_API_KEY not set")
    url = "https://artificialanalysis.ai/api/v2/language/models/free"
    pages = []
    params = {}
    for _ in range(50):
        p = get(url, {"x-api-key": AA_KEY}, params or None)
        pages.append(p)
        pag = p.get("pagination") or {}
        nxt = pag.get("next") or pag.get("next_url")
        cursor = pag.get("next_cursor") or pag.get("cursor_next")
        if isinstance(nxt, str) and nxt.startswith("http"):
            url, params = nxt, {}
        elif cursor:
            params = {"cursor": cursor}
        elif pag.get("has_more") and "page" in pag:
            params = {"page": int(pag["page"]) + 1}
        else:
            break
    save_raw("aa_models", pages, gz=True)


if __name__ == "__main__":
    step("openrouter rankings", or_rankings)
    step("openrouter models", or_models)
    step("openrouter classifications", or_classifications)
    step("openrouter apps", or_apps)
    step("openrouter benchmarks (AA via OR)", or_benchmarks)
    step("artificial analysis models", aa_models)
    status = {"run_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
              "errors": errors}
    with open(os.path.join(DATA, "last_run.json"), "w") as f:
        json.dump(status, f, indent=1)
    # fail the job only if every source failed
    sys.exit(1 if len(errors) == 6 else 0)
