#!/usr/bin/env python3
"""
Build a static comparison page of Ollama cloud models vs OpenRouter benchmarks.

Usage:
    cp .env.example .env   # then fill in OPENROUTER_API_KEY
    python3 scripts/build.py

OPENROUTER_API_KEY can also just be exported in the shell; a real environment
variable always takes precedence over the .env file.

Writes:
    dist/index.html  - the static page (self-contained, no runtime API calls)
    dist/data.json    - the raw combined dataset, for debugging / re-use

Data sources:
    - https://ollama.com/search?c=cloud            (scraped HTML, no API)
    - https://openrouter.ai/api/v1/models           (needs OPENROUTER_API_KEY)
    - https://openrouter.ai/api/v1/benchmarks       (needs OPENROUTER_API_KEY)
"""
import argparse
import difflib
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
CACHE_DIR = ROOT / ".cache"
OVERRIDES_PATH = ROOT / "overrides.json"
DOTENV_PATH = ROOT / ".env"


def load_dotenv(path=DOTENV_PATH):
    """Minimal .env loader (stdlib only). Real env vars always win over the file."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)

OLLAMA_BASE = "https://ollama.com"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"

USER_AGENT = (
    "Mozilla/5.0 (compatible; ollama-cloud-openrouter-benchmarks/1.0; "
    "+https://github.com)"
)

USAGE_LEVEL_RANK = {"low": 1, "medium": 2, "high": 3, "extra high": 4}

USE_CACHE = True  # toggled by --no-cache


def _cache_file(url):
    digest = hashlib.sha256(url.encode()).hexdigest()[:24]
    return CACHE_DIR / f"{digest}.txt"


def fetch(url, headers=None):
    cache_file = _cache_file(url)
    if USE_CACHE and cache_file.exists():
        return cache_file.read_text()

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8")

    CACHE_DIR.mkdir(exist_ok=True)
    cache_file.write_text(body)
    return body


def fetch_json(url, headers=None):
    return json.loads(fetch(url, headers))


# ---------------------------------------------------------------------------
# Ollama scraping
# ---------------------------------------------------------------------------

FAMILY_BLOCK_RE = re.compile(
    r'<a href="/library/([a-zA-Z0-9._-]+)" class="group w-full">(.*?)</a>',
    re.DOTALL,
)


def scrape_cloud_families():
    html = fetch(f"{OLLAMA_BASE}/search?c=cloud")
    families = []
    for m in FAMILY_BLOCK_RE.finditer(html):
        slug, block = m.group(1), m.group(2)
        desc_m = re.search(r'class="max-w-lg[^"]*">([^<]+)</p>', block)
        tags = re.findall(r">(vision|tools|thinking|audio|cloud)<", block)
        families.append(
            {
                "slug": slug,
                "name": slug,
                "description": (desc_m.group(1).strip() if desc_m else ""),
                "capabilities": sorted(set(t for t in tags if t != "cloud")),
            }
        )
    # de-dupe, first occurrence wins
    seen = set()
    unique = []
    for f in families:
        if f["slug"] in seen:
            continue
        seen.add(f["slug"])
        unique.append(f)
    return unique


CLOUD_TAG_RE = re.compile(r'library/([a-zA-Z0-9._-]+):([a-zA-Z0-9._-]*cloud)"')


def is_snapshot_suffix(suffix):
    # suffix is the part before "-cloud", e.g. "0813", "preview", "120b"
    base = suffix[: -len("-cloud")] if suffix != "cloud" else ""
    return base == "preview" or base.isdigit()


def scrape_family_cloud_tags(family_slug):
    html = fetch(f"{OLLAMA_BASE}/library/{family_slug}/tags")
    tags = set()
    for fam, suffix in CLOUD_TAG_RE.findall(html):
        if fam != family_slug:
            continue
        tags.add(suffix)

    size_tags = [s for s in tags if s != "cloud" and not is_snapshot_suffix(s)]
    if size_tags:
        return sorted(f"{family_slug}:{s}" for s in size_tags)
    if "cloud" in tags:
        return [f"{family_slug}:cloud"]
    return []


# Model pages render one of two stat-box layouts: most show a qualitative "Usage" tier
# (low/medium/high/extra high, as dots), a few newer/priciest ones show a $-per-1M-token
# "Cost" box instead. Context and Size are always present in both layouts. Each field is
# matched independently so a missing/different box for one field doesn't blank the others.
USAGE_LABEL_RE = re.compile(
    r">Usage</div>.*?text-neutral-700[^>]*>(low|medium|high|extra high)</span>",
    re.DOTALL | re.IGNORECASE,
)
CONTEXT_RE = re.compile(r">Context</div>\s*<div[^>]*>\s*<span[^>]*>([^<]+)</span>", re.DOTALL)
SIZE_RE = re.compile(r">Size</div>\s*<div[^>]*>\s*<span[^>]*>([^<]+)</span>", re.DOTALL)


def scrape_variant_stats(tag):
    html = fetch(f"{OLLAMA_BASE}/library/{tag}")
    usage_m = USAGE_LABEL_RE.search(html)
    context_m = CONTEXT_RE.search(html)
    size_m = SIZE_RE.search(html)
    usage_level = usage_m.group(1).lower() if usage_m else None
    return {
        "usage_level": usage_level,
        "usage_rank": USAGE_LEVEL_RANK.get(usage_level),
        "context": context_m.group(1).strip() if context_m else None,
        "size": size_m.group(1).strip() if size_m else None,
    }


def scrape_ollama_cloud_models():
    families = scrape_cloud_families()
    models = []
    for fam in families:
        tags = scrape_family_cloud_tags(fam["slug"])
        if not tags:
            print(f"  ! no cloud tag found for family {fam['slug']}", file=sys.stderr)
            continue
        for tag in tags:
            stats = scrape_variant_stats(tag)
            models.append(
                {
                    "family": fam["slug"],
                    "family_name": fam["name"],
                    "description": fam["description"],
                    "capabilities": fam["capabilities"],
                    "ollama_tag": tag,
                    **stats,
                }
            )
            print(f"  ollama: {tag}  usage={stats['usage_level']}  context={stats['context']}  size={stats['size']}")
    return models


# ---------------------------------------------------------------------------
# OpenRouter fetching
# ---------------------------------------------------------------------------


def fetch_openrouter_models(api_key):
    data = fetch_json(f"{OPENROUTER_BASE}/models", {"Authorization": f"Bearer {api_key}"})
    return data.get("data", [])


def fetch_openrouter_benchmarks(api_key):
    data = fetch_json(
        f"{OPENROUTER_BASE}/benchmarks?source=artificial-analysis",
        {"Authorization": f"Bearer {api_key}"},
    )
    return data.get("data", [])


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def normalize(s):
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.strip()


def best_openrouter_match(ollama_model, benchmark_rows, overrides):
    family = ollama_model["family"]

    if family in overrides:
        override_slug = overrides[family]
        if override_slug is None:
            return None, None, "overridden-no-match"
        for row in benchmark_rows:
            if row.get("model_permaslug") == override_slug:
                return row, 1.0, "override"
        print(f"  ! override for '{family}' points at missing permaslug '{override_slug}'", file=sys.stderr)
        return None, None, "override-missing"

    target = normalize(ollama_model["family_name"])
    size_hint = None
    size_m = re.search(r":([0-9]+(?:\.[0-9]+)?)b", ollama_model["ollama_tag"])
    if size_m:
        size_hint = size_m.group(1) + "b"

    best_row, best_score = None, 0.0
    for row in benchmark_rows:
        candidates = [row.get("display_name", ""), row.get("model_permaslug", "").split("/")[-1]]
        score = max(
            difflib.SequenceMatcher(None, target, normalize(c)).ratio() for c in candidates if c
        )
        if size_hint and size_hint in normalize(row.get("model_permaslug", "")):
            score += 0.1
        if score > best_score:
            best_row, best_score = row, score

    if best_score >= 0.75:
        return best_row, best_score, "fuzzy"
    return None, best_score, "no-match"


# ---------------------------------------------------------------------------
# Combine + render
# ---------------------------------------------------------------------------


def build_dataset(api_key):
    overrides = {}
    if OVERRIDES_PATH.exists():
        overrides = json.loads(OVERRIDES_PATH.read_text())

    print("Scraping Ollama cloud models...", file=sys.stderr)
    ollama_models = scrape_ollama_cloud_models()

    print("Fetching OpenRouter benchmarks...", file=sys.stderr)
    benchmark_rows = fetch_openrouter_benchmarks(api_key)

    print("Matching models...", file=sys.stderr)
    combined = []
    for om in ollama_models:
        match, score, method = best_openrouter_match(om, benchmark_rows, overrides)
        row = dict(om)
        if match:
            row.update(
                {
                    "openrouter_permaslug": match.get("model_permaslug"),
                    "openrouter_display_name": match.get("display_name"),
                    "intelligence_index": match.get("intelligence_index"),
                    "coding_index": match.get("coding_index"),
                    "agentic_index": match.get("agentic_index"),
                    "price_prompt": (match.get("pricing") or {}).get("prompt"),
                    "price_completion": (match.get("pricing") or {}).get("completion"),
                    "match_score": round(score, 2),
                    "match_method": method,
                }
            )
        else:
            row.update(
                {
                    "openrouter_permaslug": None,
                    "openrouter_display_name": None,
                    "intelligence_index": None,
                    "coding_index": None,
                    "agentic_index": None,
                    "price_prompt": None,
                    "price_completion": None,
                    "match_score": round(score, 2) if score else None,
                    "match_method": method,
                }
            )
        if row["usage_rank"]:
            if row["coding_index"] is not None:
                row["coding_per_usage"] = round(row["coding_index"] / row["usage_rank"], 2)
            if row["agentic_index"] is not None:
                row["agentic_per_usage"] = round(row["agentic_index"] / row["usage_rank"], 2)
        print(
            f"  {om['ollama_tag']:32s} -> {row['openrouter_permaslug'] or '(no match)':40s} "
            f"[{method}, score={row['match_score']}]"
        )
        combined.append(row)

    return combined


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ollama Cloud Models vs OpenRouter Benchmarks</title>
<style>
:root {
  --bg: #0b0d12; --panel: #12151c; --border: #232733; --text: #e7e9ee; --muted: #8a90a2;
  --accent: #6aa9ff; --good: #3ecf8e; --warn: #f5b942; --bad: #6b7180;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text); font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
header { padding: 28px 24px 8px; max-width: 1400px; margin: 0 auto; }
h1 { font-size: 20px; margin: 0 0 6px; }
p.sub { color: var(--muted); margin: 0 0 18px; max-width: 760px; }
main { max-width: 1400px; margin: 0 auto; padding: 0 24px 48px; }
.controls { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 14px; align-items: center; }
.controls input[type=text] { background: var(--panel); border: 1px solid var(--border); color: var(--text); padding: 7px 10px; border-radius: 6px; width: 220px; }
.controls select { background: var(--panel); border: 1px solid var(--border); color: var(--text); padding: 7px 10px; border-radius: 6px; }
.controls label { color: var(--muted); font-size: 12px; display: flex; gap: 6px; align-items: center; }
.table-wrap { overflow-x: auto; border: 1px solid var(--border); border-radius: 10px; }
table { border-collapse: collapse; width: 100%; min-width: 1100px; }
th, td { padding: 9px 12px; text-align: left; white-space: nowrap; border-bottom: 1px solid var(--border); }
thead th { background: var(--panel); position: sticky; top: 0; cursor: pointer; user-select: none; color: var(--muted); font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: .03em; }
thead th:hover { color: var(--text); }
thead th.sorted { color: var(--accent); }
tbody tr:hover { background: #161a24; }
.model-name { color: var(--text); font-weight: 600; }
.model-tag { color: var(--muted); font-size: 12px; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 20px; font-size: 11px; font-weight: 600; }
.badge.low { background: rgba(62,207,142,.15); color: var(--good); }
.badge.medium { background: rgba(245,185,66,.15); color: var(--warn); }
.badge.high { background: rgba(245,142,66,.15); color: #f58e42; }
.badge.extra.high, .badge.extra-high { background: rgba(235,90,90,.15); color: #eb5a5a; }
.no-data { color: var(--bad); font-style: italic; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
footer { color: var(--muted); font-size: 12px; padding: 20px 24px; max-width: 1400px; margin: 0 auto; }
a { color: var(--accent); }
</style>
</head>
<body>
<header>
  <h1>Ollama Cloud Models vs OpenRouter Benchmarks</h1>
  <p class="sub">
    Every model available under <a href="https://ollama.com/search?c=cloud" target="_blank">Ollama's cloud tier</a>,
    cross-referenced against <a href="https://openrouter.ai/docs/api-reference/benchmarks" target="_blank">OpenRouter's benchmark scores</a>
    (Artificial Analysis: intelligence / coding / agentic indices). "Usage" is Ollama's own quota-consumption tier
    (low &rarr; extra high) &mdash; on the Pro plan you get 3 concurrent models and 50x Free's usage budget, so the
    <em>index &divide; usage</em> columns approximate "capability per unit of Pro quota burned."
    Generated at build time &mdash; see <code>dist/data.json</code> for raw values and match confidence.
  </p>
</header>
<main>
  <div class="controls">
    <input type="text" id="search" placeholder="Filter by model name...">
    <label>Min usage tier
      <select id="usageFilter">
        <option value="0">any</option>
        <option value="1">low+</option>
        <option value="2">medium+</option>
        <option value="3">high+</option>
        <option value="4">extra high only</option>
      </select>
    </label>
    <label><input type="checkbox" id="hideNoData"> hide models with no benchmark data</label>
  </div>
  <div class="table-wrap">
    <table id="tbl">
      <thead>
        <tr>
          <th data-key="family_name">Model</th>
          <th data-key="ollama_tag">Ollama tag</th>
          <th data-key="usage_rank">Usage tier</th>
          <th data-key="context">Context</th>
          <th data-key="size">Size</th>
          <th data-key="openrouter_display_name">OpenRouter match</th>
          <th class="num" data-key="intelligence_index">Intelligence</th>
          <th class="num" data-key="coding_index">Coding</th>
          <th class="num" data-key="agentic_index">Agentic</th>
          <th class="num" data-key="coding_per_usage">Coding&nbsp;/&nbsp;usage</th>
          <th class="num" data-key="agentic_per_usage">Agentic&nbsp;/&nbsp;usage</th>
          <th class="num" data-key="price_prompt">$/Mtok in</th>
          <th class="num" data-key="price_completion">$/Mtok out</th>
        </tr>
      </thead>
      <tbody></tbody>
    </table>
  </div>
</main>
<footer>
  Data sources: ollama.com/search?c=cloud (scraped), openrouter.ai/api/v1/benchmarks (Artificial Analysis).
  Not affiliated with Ollama or OpenRouter. Static page, no runtime API calls.
</footer>
<script>
const DATA = __DATA_JSON__;

let sortKey = "coding_per_usage", sortDir = -1;

function fmtNum(v) { return (v === null || v === undefined) ? "" : v; }
function fmtPrice(v) { return (v === null || v === undefined) ? "" : "$" + (parseFloat(v) * 1e6).toFixed(2); }
function usageBadge(level) {
  if (!level) return "";
  const cls = level.replace(" ", "-");
  return `<span class="badge ${cls}">${level}</span>`;
}

function render() {
  const q = document.getElementById("search").value.toLowerCase();
  const minUsage = parseInt(document.getElementById("usageFilter").value, 10);
  const hideNoData = document.getElementById("hideNoData").checked;

  let rows = DATA.filter(r => {
    if (q && !(r.family_name.toLowerCase().includes(q) || r.ollama_tag.toLowerCase().includes(q))) return false;
    if (minUsage && (!r.usage_rank || r.usage_rank < minUsage)) return false;
    if (hideNoData && !r.openrouter_permaslug) return false;
    return true;
  });

  rows.sort((a, b) => {
    let av = a[sortKey], bv = b[sortKey];
    if (av === null || av === undefined) av = -Infinity;
    if (bv === null || bv === undefined) bv = -Infinity;
    if (typeof av === "string") return sortDir * av.localeCompare(bv);
    return sortDir * (av - bv);
  });

  const tbody = document.querySelector("#tbl tbody");
  tbody.innerHTML = rows.map(r => `
    <tr>
      <td><div class="model-name">${r.family_name}</div><div class="model-tag">${r.description ? r.description.slice(0, 60) : ""}</div></td>
      <td class="model-tag">${r.ollama_tag}</td>
      <td>${usageBadge(r.usage_level)}</td>
      <td>${r.context || ""}</td>
      <td>${r.size || ""}</td>
      <td>${r.openrouter_display_name ? r.openrouter_display_name : '<span class="no-data">no data</span>'}</td>
      <td class="num">${fmtNum(r.intelligence_index)}</td>
      <td class="num">${fmtNum(r.coding_index)}</td>
      <td class="num">${fmtNum(r.agentic_index)}</td>
      <td class="num">${fmtNum(r.coding_per_usage)}</td>
      <td class="num">${fmtNum(r.agentic_per_usage)}</td>
      <td class="num">${fmtPrice(r.price_prompt)}</td>
      <td class="num">${fmtPrice(r.price_completion)}</td>
    </tr>
  `).join("");
}

document.querySelectorAll("th[data-key]").forEach(th => {
  th.addEventListener("click", () => {
    const key = th.dataset.key;
    if (sortKey === key) sortDir *= -1; else { sortKey = key; sortDir = -1; }
    document.querySelectorAll("th").forEach(h => h.classList.remove("sorted"));
    th.classList.add("sorted");
    render();
  });
});
document.getElementById("search").addEventListener("input", render);
document.getElementById("usageFilter").addEventListener("change", render);
document.getElementById("hideNoData").addEventListener("change", render);

render();
</script>
</body>
</html>
"""


def render_html(dataset):
    return HTML_TEMPLATE.replace("__DATA_JSON__", json.dumps(dataset))


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--render-only",
        action="store_true",
        help="Skip all network/scraping, just re-render dist/index.html from the last dist/data.json",
    )
    p.add_argument(
        "--no-cache",
        action="store_true",
        help="Ignore the .cache/ directory and re-fetch every URL from Ollama/OpenRouter",
    )
    p.add_argument(
        "--clear-cache",
        action="store_true",
        help="Delete .cache/ before running (forces a full re-fetch, same as --no-cache but also frees disk)",
    )
    return p.parse_args()


def main():
    global USE_CACHE
    args = parse_args()
    load_dotenv()

    DIST.mkdir(exist_ok=True)

    if args.render_only:
        data_path = DIST / "data.json"
        if not data_path.exists():
            print(f"ERROR: {data_path} does not exist yet. Run without --render-only first.", file=sys.stderr)
            sys.exit(1)
        dataset = json.loads(data_path.read_text())
        (DIST / "index.html").write_text(render_html(dataset))
        print(f"Re-rendered {DIST / 'index.html'} from cached {data_path} (no network calls).", file=sys.stderr)
        return

    if args.clear_cache and CACHE_DIR.exists():
        for f in CACHE_DIR.glob("*.txt"):
            f.unlink()
    if args.no_cache or args.clear_cache:
        USE_CACHE = False

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key or api_key == "sk-or-...":
        print(
            "ERROR: OPENROUTER_API_KEY is not set. Copy .env.example to .env and fill in a real "
            "key from https://openrouter.ai/settings/keys, or export it in your shell.",
            file=sys.stderr,
        )
        sys.exit(1)

    dataset = build_dataset(api_key)

    (DIST / "data.json").write_text(json.dumps(dataset, indent=2))
    (DIST / "index.html").write_text(render_html(dataset))

    matched = sum(1 for r in dataset if r["openrouter_permaslug"])
    print(f"\nDone. {len(dataset)} models, {matched} matched to OpenRouter benchmarks.", file=sys.stderr)
    print(f"Wrote {DIST / 'index.html'} and {DIST / 'data.json'}", file=sys.stderr)
    if matched < len(dataset):
        print("Review unmatched models above and add corrections to overrides.json if needed.", file=sys.stderr)


if __name__ == "__main__":
    main()
