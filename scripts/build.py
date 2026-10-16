#!/usr/bin/env python3
"""Build dist/radar.json for the AI Token Radar dashboard from data/.

All aggregation happens here so the dashboard only renders. Standard library only.
Tolerant of missing sources: each section is built independently.
"""
import csv
import datetime as dt
import glob
import gzip
import json
import os
import re
import statistics
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
RAW = os.path.join(DATA, "raw")
STATIC = os.path.join(DATA, "static")
DIST = os.path.join(ROOT, "dist")
PRICE_INDEX_DIR = os.environ.get("PRICE_INDEX_DIR", "")
TODAY = dt.datetime.now(dt.timezone.utc).date()
BLEND_IN, BLEND_OUT = 3, 1  # Artificial Analysis / Epoch convention: 3:1 input:output

warnings = []


def load_json(path):
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def latest(source):
    files = sorted(glob.glob(os.path.join(RAW, source, "*.json*")))
    if not files:
        return None, None
    f = files[-1]
    date = os.path.basename(f).split(".")[0]
    return date, load_json(f)


def blended(inp, out):
    if inp is None or out is None:
        return None
    return (BLEND_IN * inp + BLEND_OUT * out) / (BLEND_IN + BLEND_OUT)


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def week_start(d):
    return d - dt.timedelta(days=d.weekday())


def rnd(x, n=4):
    return None if x is None else round(x, n)


# ---------------- static ----------------
LABS = load_json(os.path.join(STATIC, "labs.json"))["labs"]
OPEN_NAME_HINT = re.compile(r"gemma|gpt-oss|llama|qwen|deepseek|mixtral|mistral-(small|nemo|7b)|phi-|olmo|hermes|glm|kimi|minimax", re.I)

# ---------------- OpenRouter models ----------------
models_date, models_payload = latest("openrouter_models")
models = (models_payload or {}).get("data", []) if isinstance(models_payload, dict) else []
by_slug = {}
for m in models:
    for key in (m.get("canonical_slug"), m.get("id")):
        if key:
            by_slug.setdefault(key.split(":")[0], m)

# AA scores via OpenRouter benchmarks (keyed by OpenRouter permaslug)
bench_date, bench_payload = latest("openrouter_benchmarks_aa")
aa_by_slug = {}
if isinstance(bench_payload, dict):
    for task, p in bench_payload.items():
        for it in (p or {}).get("data", []):
            slug = (it.get("model_permaslug") or "").split(":")[0]
            m = by_slug.get(slug)
            keys = {slug} | ({m.get("canonical_slug"), m.get("id")} if m else set())
            for k in filter(None, keys):
                rec = aa_by_slug.setdefault(k, {})
                for f in ("intelligence_index", "coding_index", "agentic_index"):
                    if it.get(f) is not None:
                        rec[f] = it[f]


def model_price(m):
    pr = (m or {}).get("pricing") or {}
    p, c = fnum(pr.get("prompt")), fnum(pr.get("completion"))
    if p is None or c is None or p < 0 or c < 0:
        return None, None
    return p * 1e6, c * 1e6


def clean_name(m, fallback):
    n = (m or {}).get("name") or fallback
    return n.split(": ", 1)[1] if ": " in n else n


meta_cache = {}


def meta(permaslug):
    if permaslug in meta_cache:
        return meta_cache[permaslug]
    base, _, variant = permaslug.partition(":")
    author = base.split("/")[0]
    lab = LABS.get(author, {"lab": author, "origin": "Other", "default_open": None})
    m = by_slug.get(base)
    if m is not None:
        is_open = bool(m.get("hugging_face_id"))
    elif OPEN_NAME_HINT.search(base):
        is_open = True
    else:
        is_open = lab.get("default_open")
    pin, pout = model_price(m)
    aa = aa_by_slug.get(base) or (aa_by_slug.get(m.get("id")) if m else None) or {}
    created = m.get("created") if m else None
    res = {
        "base": base, "variant": variant or None, "author": author,
        "lab": lab["lab"], "origin": lab.get("origin", "Other"),
        "open": is_open, "name": clean_name(m, base.split("/")[-1]),
        "listed": m is not None,
        "in": rnd(pin), "out": rnd(pout), "blended": rnd(blended(pin, pout)),
        "created": dt.datetime.fromtimestamp(created, dt.timezone.utc).date().isoformat() if created else None,
        "intelligence": aa.get("intelligence_index"), "coding": aa.get("coding_index"),
        "agentic": aa.get("agentic_index"),
    }
    meta_cache[permaslug] = res
    return res


def segment(md):
    if md["open"] is None:
        return "Unclassified"
    if md["open"]:
        return "Open · China" if md["origin"] == "China" else "Open · US, EU & other"
    return "Closed · US labs" if md["origin"] == "US" else "Closed · other"


out = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
       "blend_convention": "Blended price = (3 x input + 1 x output) / 4, USD per million tokens",
       "sources": {}}

# ---------------- Rankings ----------------
rank_path = os.path.join(DATA, "rankings_daily.csv")
daily = defaultdict(dict)  # date -> permaslug -> tokens
if os.path.exists(rank_path):
    with open(rank_path, newline="") as f:
        for r in csv.DictReader(f):
            daily[dt.date.fromisoformat(r["date"])][r["model_permaslug"]] = int(r["total_tokens"])
rank_meta_date, rank_meta = latest("openrouter_rankings_meta")
as_of = None
if rank_meta:
    as_of = max((m.get("as_of") or "" for m in rank_meta), default=None)
out["sources"]["openrouter_rankings"] = {
    "citation": f"Source: OpenRouter (openrouter.ai/rankings), as of {as_of}. Licensed under CC BY 4.0.",
    "as_of": as_of, "first_date": min(daily).isoformat() if daily else None,
    "last_date": max(daily).isoformat() if daily else None,
    "caveat": "OpenRouter-routed public traffic only: excludes labs' own APIs, consumer apps, private and zero-retention traffic. Token counts use each provider's tokenizer.",
}

if daily:
    complete_days = sorted(d for d in daily if d < TODAY)
    last_day = complete_days[-1]

    def window_totals(end, days):
        tot = defaultdict(int)
        for i in range(days):
            d = end - dt.timedelta(days=i)
            for s, t in daily.get(d, {}).items():
                tot[s] += t
        return tot

    # weekly mix (avg tokens per day in week)
    seg_week = defaultdict(lambda: defaultdict(int))
    lab_week = defaultdict(lambda: defaultdict(int))
    week_days = defaultdict(set)
    lab_total_90 = defaultdict(int)
    price_num = defaultdict(float)
    price_den = defaultdict(int)
    price_den_all = defaultdict(int)
    for d in complete_days:
        w = week_start(d)
        week_days[w].add(d)
        for s, t in daily[d].items():
            if s == "other":
                seg_week[w]["Long tail (outside top 50)"] += t
                lab_week[w]["Long tail (outside top 50)"] += t
                continue
            md = meta(s)
            seg_week[w][segment(md)] += t
            lab_week[w][md["lab"]] += t
            if d > last_day - dt.timedelta(days=90):
                lab_total_90[md["lab"]] += t
            price_den_all[w] += t
            if md["variant"] == "free":
                continue
            if md["blended"] is not None:
                price_num[w] += md["blended"] * t
                price_den[w] += t
    top_labs = [l for l, _ in sorted(lab_total_90.items(), key=lambda x: -x[1])[:10]]
    weeks = sorted(week_days)
    seg_names = ["Closed · US labs", "Closed · other", "Open · China", "Open · US, EU & other",
                 "Unclassified", "Long tail (outside top 50)"]
    mix = []
    for w in weeks:
        n = len(week_days[w])
        row = {"week": w.isoformat(), "days": n}
        for sname in seg_names:
            row[sname] = round(seg_week[w].get(sname, 0) / n)
        labs_row = {}
        other_labs = 0
        for l, t in lab_week[w].items():
            if l in top_labs or l.startswith("Long tail"):
                labs_row[l] = round(t / n)
            else:
                other_labs += t
        labs_row["Other labs"] = round(other_labs / n)
        row["labs"] = labs_row
        row["usage_weighted_blended_usd_per_m"] = rnd(price_num[w] / price_den[w]) if price_den[w] else None
        row["price_coverage_of_classified_tokens"] = rnd(price_den[w] / price_den_all[w], 3) if price_den_all[w] else None
        mix.append(row)
    out["weekly"] = {"segments": seg_names, "top_labs": top_labs + ["Other labs", "Long tail (outside top 50)"], "rows": mix}

    # leaderboard: last 7 complete days vs previous 7, variants merged into base model
    cur, prev = window_totals(last_day, 7), window_totals(last_day - dt.timedelta(days=7), 7)
    cur30 = window_totals(last_day, 30)
    total_cur = sum(cur.values())
    agg = {}
    for src, tgt in ((cur, "t7"), (prev, "p7"), (cur30, "t30")):
        for s, t in src.items():
            if s == "other":
                continue
            md = meta(s)
            a = agg.setdefault(md["base"], {**{k: md[k] for k in ("base", "name", "lab", "origin", "open", "created", "intelligence", "coding", "agentic")}, "t7": 0, "p7": 0, "t30": 0, "free7": 0, "in": None, "out": None, "blended": None})
            a[tgt] += t
            if tgt == "t7" and md["variant"] == "free":
                a["free7"] += t
            if md["variant"] != "free" and md["blended"] is not None:
                a["in"], a["out"], a["blended"] = md["in"], md["out"], md["blended"]
    board = sorted(agg.values(), key=lambda a: -a["t7"])[:30]
    for i, a in enumerate(board, 1):
        a["rank"] = i
        a["share7"] = rnd(a["t7"] / total_cur, 4) if total_cur else None
        a["wow"] = rnd(a["t7"] / a["p7"] - 1, 3) if a["p7"] else None
        a["free_share7"] = rnd(a["free7"] / a["t7"], 3) if a["t7"] else None
    out["leaderboard"] = {"window_end": last_day.isoformat(), "total_tokens_7d": total_cur, "rows": board}

    # KPIs
    def seg_share(tot, pred):
        num = den = 0
        for s, t in tot.items():
            if s == "other":
                continue
            md = meta(s)
            if md["open"] is None:
                continue
            den += t
            if pred(md):
                num += t
        return num / den if den else None

    yoy_end = last_day - dt.timedelta(days=364)
    yoy = window_totals(yoy_end, 7) if yoy_end - dt.timedelta(days=6) >= min(daily) else {}
    m4 = window_totals(last_day - dt.timedelta(days=28), 7)
    lw = mix[-1] if mix and mix[-1]["days"] == 7 else (mix[-2] if len(mix) > 1 else None)
    yw = None
    if mix:
        target = week_start(last_day) - dt.timedelta(days=364)
        yw = next((r for r in mix if r["week"] == week_start(target).isoformat()), None)
    out["kpis"] = {
        "window": f"{(last_day - dt.timedelta(days=6)).isoformat()} to {last_day.isoformat()}",
        "tokens_per_day_7d": round(total_cur / 7),
        "tokens_growth_vs_4w": rnd(total_cur / sum(m4.values()) - 1, 3) if m4 else None,
        "tokens_growth_yoy": rnd(total_cur / sum(yoy.values()) - 1, 3) if yoy else None,
        "open_weight_share_7d": rnd(seg_share(cur, lambda m: m["open"]), 3),
        "china_lab_share_7d": rnd(seg_share(cur, lambda m: m["origin"] == "China"), 3),
        "usage_weighted_price_last_week": lw["usage_weighted_blended_usd_per_m"] if lw else None,
        "usage_weighted_price_year_ago": yw["usage_weighted_blended_usd_per_m"] if yw else None,
        "top_model_7d": board[0]["name"] if board else None,
        "top_model_share_7d": board[0]["share7"] if board else None,
    }

# ---------------- price landscape (all listed models) ----------------
out["sources"]["openrouter_models"] = {"as_of": models_date, "citation": f"Source: OpenRouter models API (openrouter.ai/models), snapshot {models_date}."}
land = []
for m in models:
    mid = m.get("id", "")
    if ":" in mid or not m.get("created"):
        continue
    arch = m.get("architecture") or {}
    outs = arch.get("output_modalities") or ["text"]
    if "text" not in outs:
        continue
    pin, pout = model_price(m)
    b = blended(pin, pout)
    if not b or b <= 0:
        continue
    md = meta(m.get("canonical_slug") or mid)
    land.append({"name": md["name"], "lab": md["lab"], "origin": md["origin"], "open": md["open"],
                 "created": md["created"], "blended": rnd(b), "in": rnd(pin), "out": rnd(pout),
                 "intelligence": md["intelligence"]})
land.sort(key=lambda r: r["created"])
q_med = defaultdict(lambda: defaultdict(list))
for r in land:
    d = dt.date.fromisoformat(r["created"])
    q = f"{d.year}-Q{(d.month - 1) // 3 + 1}"
    q_med[q]["all"].append(r["blended"])
    q_med[q]["open" if r["open"] else "closed"].append(r["blended"])
out["price_landscape"] = {
    "models": land,
    "quarterly_median_new_models": [
        {"quarter": q, **{k: rnd(statistics.median(v)) for k, v in g.items()}, "n": len(g["all"])}
        for q, g in sorted(q_med.items())],
    "caveat": "Current OpenRouter list price of models still listed, placed at the date OpenRouter first listed them. Delisted models are missing; later price cuts are reflected at the listing date.",
}

# ---------------- list-price families (AI Price Index) ----------------
FAMILIES = {
    "GPT (main line)": ("openai", r"^(gpt-4|gpt-4-turbo|gpt-4o|gpt-4\.1|gpt-5|gpt-5\.\d)$"),
    "GPT mini": ("openai", r"^(gpt-4o-mini|gpt-5(\.\d)?-mini)$"),
    "Claude Opus": ("anthropic", r"^claude-(3-opus|opus-[\d-]+?)(-\d{8})?$"),
    "Claude Sonnet": ("anthropic", r"^claude-(3-sonnet|3-5-sonnet|3-7-sonnet|sonnet-[\d-]+?)(-\d{8})?$"),
    "Claude Haiku": ("anthropic", r"^claude-(3-haiku|3-5-haiku|haiku-[\d-]+?)(-\d{8})?$"),
    "Gemini Pro": ("google", r"^gemini-[\d.]+-pro(-preview)?$"),
    "Gemini Flash": ("google", r"^gemini-[\d.]+-flash(-preview)?$"),
}
fam_out = {}
if PRICE_INDEX_DIR and os.path.isdir(os.path.join(PRICE_INDEX_DIR, "data", "records")):
    recs_by_provider = {}
    for prov in {p for p, _ in FAMILIES.values()}:
        path = os.path.join(PRICE_INDEX_DIR, "data", "records", f"{prov}.json")
        if os.path.exists(path):
            recs_by_provider[prov] = load_json(path)
    for fam, (prov, rx) in FAMILIES.items():
        recs = [r for r in recs_by_provider.get(prov, []) if re.match(rx, r["model_id"]) and r["variation"] in ("input", "output")]
        if not recs:
            continue
        launch = defaultdict(lambda: "9999")
        for r in recs:
            launch[r["model_id"]] = min(launch[r["model_id"]], r["effective_from"])
        order = sorted(launch, key=lambda k: launch[k])

        def price_at(model, var, day):
            for r in recs:
                if r["model_id"] == model and r["variation"] == var and r["effective_from"] <= day and (not r.get("effective_to") or r["effective_to"] > day):
                    return r["price_usd"]
            return None

        events = sorted({r["effective_from"] for r in recs})
        series = []
        for day in events + [TODAY.isoformat()]:
            current = [mm for mm in order if launch[mm] <= day]
            if not current:
                continue
            cm = current[-1]
            pin, pout = price_at(cm, "input", day), price_at(cm, "output", day)
            if pin is None or pout is None:
                continue
            point = {"date": day, "model": cm, "in": pin, "out": pout, "blended": rnd(blended(pin, pout))}
            if not series or series[-1]["blended"] != point["blended"] or series[-1]["model"] != cm or day == TODAY.isoformat():
                series.append(point)
        fam_out[fam] = {"provider": prov, "series": series}
    out["sources"]["ai_price_index"] = {"citation": "AI Price Index by RoninForge (roninforge.org/data/ai-price-index/), CC BY 4.0. Adapted: blended 3:1 and grouped into model families.",
                                        "caveat": "Dates are the first price observation in the index, which for older models can lag launch."}
else:
    warnings.append("AI Price Index not available at build time")
out["price_families"] = fam_out

# ---------------- Epoch milestones ----------------
ep = []
with open(os.path.join(STATIC, "epoch_fixed_capability_prices.csv"), newline="") as f:
    for r in csv.DictReader(f):
        r["usd_per_mtok_blended_3to1"] = float(r["usd_per_mtok_blended_3to1"])
        ep.append(r)
out["epoch_fixed_capability"] = ep
out["sources"]["epoch"] = {"citation": "Epoch AI, 'LLM inference prices have fallen rapidly but unequally across tasks' (Mar 2025), epoch.ai/data-insights/llm-inference-price-trends. Milestone rows transcribed; data ends Feb 2025."}

# ---------------- Artificial Analysis (effort variants) ----------------
aa_date, aa_pages = latest("aa_models")
aa_rows = []
if aa_pages:
    pages = aa_pages if isinstance(aa_pages, list) else [aa_pages]
    for p in pages:
        for it in (p.get("data") or []):
            name = it.get("name") or it.get("slug") or ""
            ev = it.get("evaluations") or {}
            pr = it.get("pricing") or {}
            pin = fnum(pr.get("price_1m_input_tokens"))
            pout = fnum(pr.get("price_1m_output_tokens"))
            m = re.search(r"\(([^)]*)\)\s*$", name)
            effort = m.group(1) if m else None
            family = re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()
            aa_rows.append({
                "slug": it.get("slug"), "name": name, "family": family, "effort": effort,
                "creator": (it.get("model_creator") or {}).get("name"),
                "intelligence": ev.get("artificial_analysis_intelligence_index"),
                "coding": ev.get("artificial_analysis_coding_index"),
                "in": pin, "out": pout, "blended": rnd(blended(pin, pout)),
                "release_date": it.get("release_date"),
            })
fams = defaultdict(list)
for r in aa_rows:
    if r["effort"]:
        fams[r["family"]].append(r)
effort_families = {k: v for k, v in fams.items() if len(v) >= 2}
out["aa"] = {"as_of": aa_date, "models": aa_rows, "effort_families": effort_families}
out["sources"]["artificial_analysis"] = {"as_of": aa_date, "citation": f"Source: Artificial Analysis (artificialanalysis.ai), free API, snapshot {aa_date}. Internal use only."}

# ---------------- classifications & apps ----------------
cl_date, cl = latest("openrouter_classifications")
out["task_mix"] = (cl or {}).get("data")
apps_date, apps = latest("openrouter_apps")
out["apps"] = {"as_of": (apps or {}).get("meta", {}).get("as_of"), "window": [(apps or {}).get("meta", {}).get("start_date"), (apps or {}).get("meta", {}).get("end_date")], "rows": (apps or {}).get("data", [])}

# ---------------- static geo ----------------
out["state_of_ai_2025"] = load_json(os.path.join(STATIC, "state_of_ai_2025.json"))

last_run = os.path.join(DATA, "last_run.json")
out["fetch_status"] = load_json(last_run) if os.path.exists(last_run) else None
out["build_warnings"] = warnings

os.makedirs(DIST, exist_ok=True)
with open(os.path.join(DIST, "radar.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, separators=(",", ":"), ensure_ascii=False)
print("built dist/radar.json", {k: (len(v) if isinstance(v, (list, dict)) else v) for k, v in out.items() if k != "sources"})
if warnings:
    print("warnings:", warnings)
