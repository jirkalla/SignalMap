"""Export SignalMap and Peec answers for the same prompts so they can be compared.

Runs on the developer PC (Windows), not on the server. Reads the local dev
database exactly like refresh_dev_db.py does — through `docker compose exec
postgres psql` (docs/TASKS_PEEC_COMPARISON.md design decision 1) — and never
writes to it. The application itself is not touched.

    python tools/local/compare_peec.py                      # Knauf, docs/peec, date range from the exports
    python tools/local/compare_peec.py --plan                # only print what would be loaded and where to
    python tools/local/compare_peec.py --client Knauf --peec-dir docs/peec --overwrite

This is T1+T2 of docs/TASKS_PEEC_COMPARISON.md: T1 ("Loading, pairing and
source export") loads the Peec JSON exports, matches each one to a SignalMap
prompt by exact prompt TEXT (never by filename), pairs Peec answers with
SignalMap runs by prompt x model x local (Europe/Prague) day, and writes the
`sources/` half of the output folder. T2 ("Metrics and noise") adds the
brand-matching "ruler" (design decision 6), domain normalization (decision
9), per-pair metrics in pairs.csv, brand/domain aggregates, the three noise
references with a bootstrap verdict (decision 7) and the Peec-vs-SignalMap-
vs-ruler Detector check. report.xlsx and README.md (T3) are not produced
here — T2's own aggregate/noise table is only printed to the console.

Requires two exceptions to "standard library only" (design decision 2 allows
openpyxl; tzdata is a second one this script needs for Europe/Prague date
conversion on Windows, which has no IANA timezone database of its own) — both
imported lazily, with a clear pip-install message if missing.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import random
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from _dbtools import DB_USER, PROJECT_DIR, compose

DATABASE = "signalmap"

# Peec model name -> SignalMap ai_models.model_name, for the models present in
# both tools (docs/TASKS_PEEC_COMPARISON.md design decision 4). Anything else
# is listed in model_mapping.csv with a reason instead of being guessed at.
COMPARED_MODELS: dict[str, str] = {
    "gpt-5-6-terra": "gpt-5.6-terra",
    "claude-haiku-4-5": "claude-haiku-4-5-20251001",
}
PEEC_ONLY_MODELS = ("chatgpt-ui", "gemini-ui", "google-ai-mode", "google-ai-overview")
SIGNALMAP_ONLY_MODELS = ("gpt-5.6-luna", "gemini-3.1-flash-lite")

# The brand-matching "ruler" (design decision 6) is built from the DB's own
# client/tracked_entities + aliases, plus this small comparison-only supplement —
# never written back to the DB (that stays the user's own step in the UI). A name
# this short needs case-sensitive, word-boundary matching (SHORT_NAME_MAX_LEN) or
# it risks matching as a common word/acronym fragment elsewhere; everything longer
# is matched case-insensitively so e.g. Peec's "ROCKWOOL" still matches the DB's
# "Rockwool" without needing a dedicated alias for casing alone.
SHORT_NAME_MAX_LEN = 4
RULER_EXTRA_ALIASES: dict[str, list[str]] = {
    "Saint Gobain": ["Saint-Gobain"],
}

# Fixed so the 95% CI in design decision 7's verdict is reproducible; recorded
# here (and to be quoted in T3's README) rather than left to vary per run.
BOOTSTRAP_SEED = 20260926
BOOTSTRAP_ITERATIONS = 2000

# Excel's per-cell character limit; kept well under it so a cell that needs
# truncating still round-trips through Excel's own display width. The longest
# answer seen so far (14,565 chars) is nowhere near this, but design decision
# 11 asks for the safety net anyway: CSV never truncates, sources.xlsx does
# and flags the row with `text_truncated`.
MAX_XLSX_CELL = 32000

OUTPUT_DATE_FMT = "%Y-%m-%d"


class PeecDataError(Exception):
    """A Peec export file doesn't match what T1's design assumes about it."""


# ---------------------------------------------------------------------------
# Timezone / date helpers (Europe/Prague local day, per design decision 5)
# ---------------------------------------------------------------------------


def prague_zone():
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        return ZoneInfo("Europe/Prague")
    except ZoneInfoNotFoundError:
        sys.exit(
            "chybi IANA tz databaze pro 'Europe/Prague' (Windows ji nema vestavenou) - "
            "spust: pip install tzdata"
        )


def local_date_of(dt_utc: datetime, tz) -> date:
    """The Europe/Prague calendar day a UTC timestamp falls on.

    `dt_utc` must be naive-but-UTC (as read back from Postgres via
    `AT TIME ZONE 'UTC'`, or parsed from a Peec 'Z' timestamp) or aware UTC.
    """
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    return dt_utc.astimezone(tz).date()


def parse_peec_created(value: str) -> datetime:
    """'2026-09-22T22:00:00Z' -> aware UTC datetime. Peec only gives a day, not a time."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_db_timestamp(value: str) -> datetime | None:
    """Parse a `... AT TIME ZONE 'UTC'` column as read back from `COPY ... CSV`."""
    if not value:
        return None
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Database access — COPY ... TO STDOUT, not _dbtools.psql's `-tAc` (design
# decision 3): rendered_text and citation text can contain commas, pipes and
# newlines that a delimited single-line format can't round-trip safely.
# ---------------------------------------------------------------------------


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def sql_list(values) -> str:
    return ", ".join(sql_literal(v) for v in values)


def copy_query(sql: str) -> list[dict[str, str]]:
    """Run a read-only SELECT via `COPY (...) TO STDOUT` and parse it as CSV."""
    full_sql = f"COPY ({sql}) TO STDOUT WITH (FORMAT csv, HEADER true)"
    result = compose("exec", "-T", "postgres", "psql", "-U", DB_USER, "-d", DATABASE, "-c", full_sql)
    text = result.stdout.decode("utf-8")
    return list(csv.DictReader(io.StringIO(text)))


def fetch_client(name: str) -> dict[str, str]:
    rows = copy_query(f"SELECT id, name, slug FROM clients WHERE lower(name) = lower({sql_literal(name)})")
    if not rows:
        sys.exit(f"klient '{name}' v DB neexistuje")
    if len(rows) > 1:
        sys.exit(f"klient '{name}' odpovida {len(rows)} radkum v clients - upresni presnejsim nazvem")
    return rows[0]


def fetch_prompts(client_id: str) -> list[dict[str, str]]:
    """All versions of the client's prompts — matching against every version, not just the
    current one, means a Peec export against a stale prompt text is caught, not misreported
    as 'no such prompt'.
    """
    return copy_query(
        "SELECT p.id, p.prompt_set_id, ps.name AS prompt_set_name, p.version, "
        "p.is_current_version, p.is_active, p.text, p.topic, m.code AS market_code "
        "FROM prompts p "
        "JOIN prompt_sets ps ON ps.id = p.prompt_set_id "
        "JOIN markets m ON m.id = p.market_id "
        f"WHERE ps.client_id = {client_id} "
        "ORDER BY p.id, p.version"
    )


def fetch_runs(client_id: str, model_names) -> list[dict[str, str]]:
    return copy_query(
        "SELECT r.id AS run_id, r.prompt_id, am.model_name, "
        "(r.started_at AT TIME ZONE 'UTC') AS started_at_utc, "
        "(r.finished_at AT TIME ZONE 'UTC') AS finished_at_utc, "
        "r.latency_ms, mk.code AS market_code, pe.label AS persona_label, "
        "rr.id AS raw_response_id, rr.rendered_text, rr.has_citations "
        "FROM runs r "
        "JOIN prompts p ON p.id = r.prompt_id "
        "JOIN prompt_sets ps ON ps.id = p.prompt_set_id "
        "JOIN ai_models am ON am.id = r.model_id "
        "JOIN markets mk ON mk.id = r.market_id "
        "JOIN personas pe ON pe.id = r.persona_id "
        "JOIN raw_responses rr ON rr.run_id = r.id "
        f"WHERE ps.client_id = {client_id} AND r.status = 'success' "
        f"AND am.model_name IN ({sql_list(model_names)}) "
        "ORDER BY r.id"
    )


def fetch_citations(client_id: str, model_names) -> list[dict[str, str]]:
    return copy_query(
        "SELECT c.id AS citation_id, c.raw_response_id, r.id AS run_id, "
        "c.source_url, c.source_title, c.source_domain, c.citation_position "
        "FROM citations c "
        "JOIN raw_responses rr ON rr.id = c.raw_response_id "
        "JOIN runs r ON r.id = rr.run_id "
        "JOIN prompts p ON p.id = r.prompt_id "
        "JOIN prompt_sets ps ON ps.id = p.prompt_set_id "
        "JOIN ai_models am ON am.id = r.model_id "
        f"WHERE ps.client_id = {client_id} AND r.status = 'success' "
        f"AND am.model_name IN ({sql_list(model_names)}) "
        "ORDER BY c.id"
    )


def fetch_detector_output(client_id: str, model_names) -> dict[str, str]:
    """raw_response_id -> the Competitive Visibility Detection skill's raw JSON output.

    This is the "own detector" side of design decision 6's Detector check, which T2
    still has to build the brand-matching ruler for — T1 only carries the raw output
    through to signalmap_answers.csv so T2 doesn't need a second DB round trip.
    """
    rows = copy_query(
        "SELECT ar.raw_response_id, ar.output "
        "FROM analysis_results ar "
        "JOIN analysis_skills sk ON sk.id = ar.analysis_skill_id "
        "JOIN raw_responses rr ON rr.id = ar.raw_response_id "
        "JOIN runs r ON r.id = rr.run_id "
        "JOIN prompts p ON p.id = r.prompt_id "
        "JOIN prompt_sets ps ON ps.id = p.prompt_set_id "
        "JOIN ai_models am ON am.id = r.model_id "
        "WHERE sk.key = 'competitive_visibility' "
        f"AND ps.client_id = {client_id} AND r.status = 'success' "
        f"AND am.model_name IN ({sql_list(model_names)})"
    )
    return {row["raw_response_id"]: row["output"] for row in rows}


def fetch_client_aliases(client_id: str) -> list[str]:
    rows = copy_query(f"SELECT alias FROM client_aliases WHERE client_id = {client_id} ORDER BY id")
    return [row["alias"] for row in rows]


def fetch_tracked_entities(client_id: str) -> list[tuple[str, list[str]]]:
    """The client's tracked competitors and their DB aliases (design decision 6's base
    data — currently none of them have an alias row, confirmed against the dev DB).
    """
    entities = copy_query(f"SELECT id, name FROM tracked_entities WHERE client_id = {client_id} ORDER BY id")
    aliases = copy_query(
        "SELECT tea.tracked_entity_id, tea.alias FROM tracked_entity_aliases tea "
        f"JOIN tracked_entities te ON te.id = tea.tracked_entity_id WHERE te.client_id = {client_id} "
        "ORDER BY tea.id"
    )
    aliases_by_entity: dict[str, list[str]] = {}
    for row in aliases:
        aliases_by_entity.setdefault(row["tracked_entity_id"], []).append(row["alias"])
    return [(e["name"], aliases_by_entity.get(e["id"], [])) for e in entities]


# ---------------------------------------------------------------------------
# Peec exports
# ---------------------------------------------------------------------------

REQUIRED_PEEC_FIELDS = ("id", "promptId", "model", "user", "assistant", "created")


def load_peec_records(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as fh:
        try:
            records = json.load(fh)
        except json.JSONDecodeError as exc:
            raise PeecDataError(f"{path.name}: neplatny JSON ({exc})") from None
    if not isinstance(records, list) or not records:
        raise PeecDataError(f"{path.name}: ocekavan neprazdny seznam odpovedi, ne {type(records).__name__}")
    for rec in records:
        missing = [f for f in REQUIRED_PEEC_FIELDS if f not in rec]
        if missing:
            raise PeecDataError(f"{path.name}: zaznamu chybi pole {missing}")
    return records


def match_prompt_text(path: Path, records: list[dict], prompts: list[dict]) -> dict:
    """Prompt identity comes from an exact match of the 'user' text against the DB —
    never from the filename (docs/TASKS_PEEC_COMPARISON.md T1 item 1). A file mixing
    more than one prompt text, or a text nothing in the DB matches, is a hard error
    naming the file, not a silent skip.
    """
    texts = {rec["user"] for rec in records}
    if len(texts) > 1:
        raise PeecDataError(
            f"{path.name}: obsahuje {len(texts)} ruznych textu promptu (ocekavan presne 1 na soubor)"
        )
    (user_text,) = texts
    matches = [p for p in prompts if p["text"] == user_text]
    if not matches:
        preview = user_text[:120].replace("\n", " ")
        raise PeecDataError(f"{path.name}: text promptu neodpovida zadnemu promptu klienta v DB (zacatek: {preview!r})")
    if len(matches) > 1:
        ids = [m["id"] for m in matches]
        raise PeecDataError(f"{path.name}: text promptu odpovida {len(matches)} promptum v DB (id: {ids})")
    return matches[0]


# ---------------------------------------------------------------------------
# The brand ruler (design decision 6) — one shared detector run over both
# tools' raw text, independent of either tool's own built-in detector.
# ---------------------------------------------------------------------------


@dataclass
class BrandRule:
    name: str  # canonical name, exactly as tracked in SignalMap (client name or tracked_entities.name)
    is_client: bool
    match_names: list[str]  # name + DB aliases + RULER_EXTRA_ALIASES, as matched (for tracked_brands.csv)
    pattern: re.Pattern


@dataclass
class BrandHit:
    mentioned: bool
    count: int
    first_pos: int | None


def _brand_pattern(names: list[str], case_sensitive: bool) -> re.Pattern:
    # Longest names first so an alias that contains a shorter one (unlikely here, but
    # a real risk in general) can't shadow it inside the same alternation.
    alternation = "|".join(re.escape(n) for n in sorted(set(names), key=len, reverse=True))
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(rf"\b(?:{alternation})\b", flags)


def build_brand_rules(
    client_name: str, client_aliases: list[str], entities: list[tuple[str, list[str]]]
) -> list[BrandRule]:
    def make_rule(name: str, is_client: bool, db_aliases: list[str]) -> BrandRule:
        match_names = [name, *db_aliases, *RULER_EXTRA_ALIASES.get(name, [])]
        case_sensitive = len(name) <= SHORT_NAME_MAX_LEN
        return BrandRule(
            name=name, is_client=is_client, match_names=match_names,
            pattern=_brand_pattern(match_names, case_sensitive),
        )

    rules = [make_rule(client_name, True, client_aliases)]
    rules += [make_rule(name, False, aliases) for name, aliases in entities]
    return rules


def detect_brands(text: str, rules: list[BrandRule]) -> dict[str, BrandHit]:
    hits = {}
    for rule in rules:
        matches = list(rule.pattern.finditer(text or ""))
        hits[rule.name] = BrandHit(
            mentioned=bool(matches), count=len(matches),
            first_pos=matches[0].start() if matches else None,
        )
    return hits


def brand_metrics(hits: dict[str, BrandHit], client_name: str) -> dict:
    """Client visibility derived from one detect_brands() call: whether the client
    itself was mentioned, its rank among all mentioned brands by first-mention
    position (1 = mentioned first; None if not mentioned at all), and its share of
    voice (its own mention count / all tracked brands' mention count combined, 0.0
    if nothing was mentioned).
    """
    mentioned = {name: hit for name, hit in hits.items() if hit.mentioned}
    total_count = sum(hit.count for hit in hits.values())
    client_hit = hits[client_name]
    rank = None
    if mentioned:
        ranked = sorted(mentioned.items(), key=lambda kv: kv[1].first_pos)
        rank = next((i + 1 for i, (name, _) in enumerate(ranked) if name == client_name), None)
    return {
        "mentioned_brands": set(mentioned),
        "client_mentioned": client_hit.mentioned,
        "client_rank": rank,
        "client_sov": (client_hit.count / total_count) if total_count else 0.0,
    }


def build_tracked_brands(rules: list[BrandRule]) -> list[dict]:
    return [
        {
            "brand": rule.name, "is_client": "true" if rule.is_client else "false",
            "match_names": ";".join(rule.match_names),
            "case_sensitive": "true" if len(rule.name) <= SHORT_NAME_MAX_LEN else "false",
            "extra_aliases": ";".join(RULER_EXTRA_ALIASES.get(rule.name, [])),
        }
        for rule in rules
    ]


# ---------------------------------------------------------------------------
# Domain normalization (design decision 9)
# ---------------------------------------------------------------------------

_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://")
_UTM_PARAM_RE = re.compile(r"[?&]utm_[a-z_]+=[^&]*")
_DOMAIN_SHAPE_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


def normalize_domain(raw: str) -> tuple[str, bool]:
    """Lowercase, strip scheme/www./path/utm_* (decision 9). Returns
    (normalized_domain, flagged) — flagged is True for a shape this design never
    anticipated (e.g. a Gemini vertexaisearch redirect), which the caller must show,
    never silently pass through or drop.
    """
    domain = _SCHEME_RE.sub("", raw.strip().lower())
    domain = _UTM_PARAM_RE.sub("", domain)
    domain = domain.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if domain.startswith("www."):
        domain = domain[4:]
    flagged = "vertexaisearch" in domain or not _DOMAIN_SHAPE_RE.match(domain)
    return domain, flagged


# ---------------------------------------------------------------------------
# Pure statistics helpers (design decisions 7, 8) — kept free of any DB/file
# access so tools/local/test_compare_peec.py (T4) can exercise them directly.
# ---------------------------------------------------------------------------


def jaccard(a: set, b: set) -> float:
    """1.0 when both sides are empty (agreement on 'nothing'), else the usual ratio."""
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def mean(values: list[float]) -> float | None:
    values = list(values)
    return sum(values) / len(values) if values else None


def cohens_kappa(pairs: list[tuple[bool, bool]]) -> float | None:
    """Cohen's kappa for a binary yes/no agreement, e.g. Knauf mentioned or not,
    across every pair given. None if there's nothing to compute it from.
    """
    n = len(pairs)
    if n == 0:
        return None
    agree = sum(1 for a, b in pairs if a == b)
    po = agree / n
    p_a_true = sum(1 for a, _ in pairs if a) / n
    p_b_true = sum(1 for _, b in pairs if b) / n
    pe = p_a_true * p_b_true + (1 - p_a_true) * (1 - p_b_true)
    if pe == 1:
        return 1.0
    return (po - pe) / (1 - pe)


def spearman(pairs: list[tuple[float, float]]) -> float | None:
    """Spearman rank correlation over paired values, ties broken with mid-ranks."""
    if len(pairs) < 2:
        return None

    def ranks(values: list[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        result = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            mid_rank = (i + j) / 2 + 1
            for k in range(i, j + 1):
                result[order[k]] = mid_rank
            i = j + 1
        return result

    xs, ys = zip(*pairs)
    rx, ry = ranks(list(xs)), ranks(list(ys))
    n = len(pairs)
    mean_r = (n + 1) / 2
    numerator = sum((a - mean_r) * (b - mean_r) for a, b in zip(rx, ry))
    denom = (sum((a - mean_r) ** 2 for a in rx) * sum((b - mean_r) ** 2 for b in ry)) ** 0.5
    return numerator / denom if denom else None


def percentile(values: list[float], p: float) -> float:
    """Linear-interpolation percentile (p in [0, 100]); `values` need not be sorted."""
    data = sorted(values)
    if len(data) == 1:
        return data[0]
    rank = (p / 100) * (len(data) - 1)
    lo, hi = int(rank), min(int(rank) + 1, len(data) - 1)
    frac = rank - lo
    return data[lo] + (data[hi] - data[lo]) * frac


def bootstrap_ci(values: list[float], *, seed: int, iterations: int) -> tuple[float, float] | None:
    """95% bootstrap CI of the mean of `values`, resampled with replacement."""
    if not values:
        return None
    rng = random.Random(seed)
    n = len(values)
    means = [mean(rng.choices(values, k=n)) for _ in range(iterations)]
    return percentile(means, 2.5), percentile(means, 97.5)


# ---------------------------------------------------------------------------
# Loading + pairing
# ---------------------------------------------------------------------------


@dataclass
class LoadedData:
    client: dict
    prompts: list[dict]
    peec_files: list[Path]
    peec_answers: list[dict] = field(default_factory=list)
    signalmap_answers: list[dict] = field(default_factory=list)
    signalmap_citations: list[dict] = field(default_factory=list)
    model_mapping: list[dict] = field(default_factory=list)
    prompt_summary: list[dict] = field(default_factory=list)
    pairs: list[dict] = field(default_factory=list)
    peec_sources: list[dict] = field(default_factory=list)
    signalmap_answers_excluded: list[dict] = field(default_factory=list)
    tracked_brands: list[dict] = field(default_factory=list)
    detector_check: list[dict] = field(default_factory=list)
    metric_series: dict = field(default_factory=dict)


def build_model_mapping() -> list[dict]:
    rows = []
    for peec_model, signalmap_model in COMPARED_MODELS.items():
        rows.append({
            "peec_model": peec_model, "signalmap_model": signalmap_model,
            "compared": "true", "reason": "present in both tools",
        })
    for peec_model in PEEC_ONLY_MODELS:
        rows.append({
            "peec_model": peec_model, "signalmap_model": "",
            "compared": "false", "reason": "Peec only - collected from the web UI, not the API",
        })
    for signalmap_model in SIGNALMAP_ONLY_MODELS:
        rows.append({
            "peec_model": "", "signalmap_model": signalmap_model,
            "compared": "false", "reason": "SignalMap only",
        })
    return rows


def load_peec_answers(
    peec_dir: Path, client_slug: str, prompts: list[dict], tz, rules: list[BrandRule]
) -> tuple[list[Path], list[dict]]:
    files = sorted(peec_dir.glob(f"peec_{client_slug}_*.json"))
    if not files:
        sys.exit(f"zadne soubory 'peec_{client_slug}_*.json' v {peec_dir}")

    client_name = next(r.name for r in rules if r.is_client)
    answers: list[dict] = []
    for path in files:
        records = load_peec_records(path)
        prompt = match_prompt_text(path, records, prompts)
        for rec in records:
            created = parse_peec_created(rec["created"])
            signalmap_model = COMPARED_MODELS.get(rec["model"], "")
            hits = detect_brands(rec["assistant"], rules)
            metrics = brand_metrics(hits, client_name)
            domains = {normalize_domain(d)[0] for d in (rec.get("sources") or [])}
            answers.append({
                "source_file": path.name,
                "peec_answer_id": rec["id"],
                "peec_prompt_id": rec["promptId"],
                "signalmap_prompt_id": prompt["id"],
                "prompt_set_name": prompt["prompt_set_name"],
                "peec_model": rec["model"],
                "signalmap_model": signalmap_model,
                "compared": "true" if signalmap_model else "false",
                "created_utc": created.isoformat(),
                "local_date": local_date_of(created, tz).isoformat(),
                "position": rec.get("position") if rec.get("position") is not None else "",
                "citations_count": rec.get("citations", ""),
                "mentions": ";".join(rec.get("mentions") or []),
                "sources_count": len(rec.get("sources") or []),
                "content_in_chat": ";".join(rec.get("content_in_chat") or []),
                "user_text": rec["user"],
                "assistant_text": rec["assistant"],
                # Internal (not CSV columns — ignored by DictWriter's extrasaction="ignore"):
                # ruler-based ground truth for T2's metrics and Detector check.
                "_brand_hits": hits,
                "_client_mentioned": metrics["client_mentioned"],
                "_client_rank": metrics["client_rank"],
                "_client_sov": metrics["client_sov"],
                "_mentioned_brands": metrics["mentioned_brands"],
                "_domain_set": domains,
                "_text_length": len(rec["assistant"]),
            })
    return files, answers


def load_peec_sources(peec_dir: Path, client_slug: str) -> list[dict]:
    """One row per (answer, source domain) — re-reads the same files as load_peec_answers
    so this stays a pure, independently testable transform of the raw JSON.
    """
    rows: list[dict] = []
    for path in sorted(peec_dir.glob(f"peec_{client_slug}_*.json")):
        for rec in load_peec_records(path):
            for rank, domain in enumerate(rec.get("sources") or [], start=1):
                normalized, flagged = normalize_domain(domain)
                rows.append({
                    "source_file": path.name, "peec_answer_id": rec["id"],
                    "peec_prompt_id": rec["promptId"], "model": rec["model"],
                    "source_rank": rank, "domain": domain,
                    "normalized_domain": normalized, "domain_flagged": "true" if flagged else "false",
                })
    return rows


def build_signalmap_answers(
    runs: list[dict], detector_output: dict[str, str], tz, rules: list[BrandRule],
    citation_domains: dict[str, set[str]],
) -> list[dict]:
    client_name = next(r.name for r in rules if r.is_client)
    rows = []
    for run in runs:
        started = parse_db_timestamp(run["started_at_utc"])
        finished = parse_db_timestamp(run["finished_at_utc"])
        text = run["rendered_text"] or ""
        hits = detect_brands(text, rules)
        metrics = brand_metrics(hits, client_name)
        domains = citation_domains.get(run["raw_response_id"], set())
        rows.append({
            "run_id": run["run_id"], "prompt_id": run["prompt_id"],
            "signalmap_model": run["model_name"],
            "started_at_utc": started.isoformat() if started else "",
            "local_date": local_date_of(started, tz).isoformat() if started else "",
            "finished_at_utc": finished.isoformat() if finished else "",
            "latency_ms": run["latency_ms"],
            "market_code": run["market_code"], "persona_label": run["persona_label"],
            "raw_response_id": run["raw_response_id"],
            "has_citations": run["has_citations"],
            "domain_count": len(domains),
            "detector_output_json": detector_output.get(run["raw_response_id"], ""),
            "rendered_text": text,
            # Internal — same ruler-based fields as load_peec_answers, see there.
            "_brand_hits": hits,
            "_client_mentioned": metrics["client_mentioned"],
            "_client_rank": metrics["client_rank"],
            "_client_sov": metrics["client_sov"],
            "_mentioned_brands": metrics["mentioned_brands"],
            "_domain_set": domains,
            "_text_length": len(text),
        })
    return rows


def build_prompt_summary(prompts: list[dict], peec_answers: list[dict]) -> list[dict]:
    current = [p for p in prompts if p["is_current_version"] == "t" and p["is_active"] == "t"]
    matched_files_by_prompt: dict[str, set[str]] = {}
    matched_peec_prompt_id: dict[str, str] = {}
    for a in peec_answers:
        matched_files_by_prompt.setdefault(a["signalmap_prompt_id"], set()).add(a["source_file"])
        matched_peec_prompt_id[a["signalmap_prompt_id"]] = a["peec_prompt_id"]

    rows = []
    for p in current:
        files = sorted(matched_files_by_prompt.get(p["id"], set()))
        rows.append({
            "prompt_id": p["id"], "prompt_set_name": p["prompt_set_name"], "version": p["version"],
            "text": p["text"], "peec_prompt_id": matched_peec_prompt_id.get(p["id"], ""),
            "source_file": ";".join(files),
            "in_peec": "true" if files else "false", "in_signalmap": "true",
        })
    return rows


def _day_groups(answers: list[dict]) -> dict[tuple[str, str, str], list[dict]]:
    """Group answers by (prompt, model, local day) — peec_answers key on
    'signalmap_prompt_id', signalmap_answers on 'prompt_id'; both fall back
    to whichever is present so this works for either list unchanged.
    """
    groups: dict[tuple[str, str, str], list[dict]] = {}
    for a in answers:
        prompt_id = a.get("signalmap_prompt_id") or a["prompt_id"]
        groups.setdefault((prompt_id, a["signalmap_model"], a["local_date"]), []).append(a)
    return groups


def build_pairs(peec_answers: list[dict], signalmap_answers: list[dict]) -> list[dict]:
    """One row per (prompt x compared model x local day) group — design decision 5:
    a day with several Peec repetitions is still one pairing unit. Side-specific
    metrics (knauf_in_*_share, *_rank, *_share_of_voice, *_text_length,
    *_domain_count) are averaged over that side's own repeats for the day; the two
    cross metrics (brand/domain Jaccard) are averaged over every (Peec repeat x
    SignalMap run) combination in the group. Both are "compute per repetition, then
    average the group" (decision 5), just for a one-sided vs. a two-sided metric.
    """
    peec_by_day = _day_groups([a for a in peec_answers if a["compared"] == "true"])
    signalmap_by_day = _day_groups(signalmap_answers)
    keys = set(peec_by_day) | set(signalmap_by_day)

    rows = []
    for prompt_id, model, local_date in keys:
        peec_items = peec_by_day.get((prompt_id, model, local_date), [])
        sm_items = signalmap_by_day.get((prompt_id, model, local_date), [])
        prompt_set_name = peec_items[0]["prompt_set_name"] if peec_items else ""
        peec_model = (
            peec_items[0]["peec_model"] if peec_items
            else next((k for k, v in COMPARED_MODELS.items() if v == model), "")
        )
        row = {
            "prompt_id": prompt_id, "prompt_set_name": prompt_set_name,
            "signalmap_model": model, "peec_model": peec_model, "local_date": local_date,
            "peec_answer_ids": ";".join(a["peec_answer_id"] for a in peec_items),
            "signalmap_run_ids": ";".join(s["run_id"] for s in sm_items),
            "peec_answer_count": len(peec_items), "signalmap_run_count": len(sm_items),
        }
        if peec_items:
            ranks = [a["_client_rank"] for a in peec_items if a["_client_rank"] is not None]
            row["knauf_in_peec_share"] = mean(1.0 if a["_client_mentioned"] else 0.0 for a in peec_items)
            row["knauf_first_mention_rank_peec"] = mean(ranks) if ranks else ""
            row["knauf_share_of_voice_peec"] = mean(a["_client_sov"] for a in peec_items)
            row["peec_text_length"] = mean(a["_text_length"] for a in peec_items)
            row["peec_domain_count"] = mean(len(a["_domain_set"]) for a in peec_items)
        else:
            row.update(dict.fromkeys((
                "knauf_in_peec_share", "knauf_first_mention_rank_peec",
                "knauf_share_of_voice_peec", "peec_text_length", "peec_domain_count",
            ), ""))
        if sm_items:
            ranks = [s["_client_rank"] for s in sm_items if s["_client_rank"] is not None]
            row["knauf_in_signalmap_share"] = mean(1.0 if s["_client_mentioned"] else 0.0 for s in sm_items)
            row["knauf_first_mention_rank_signalmap"] = mean(ranks) if ranks else ""
            row["knauf_share_of_voice_signalmap"] = mean(s["_client_sov"] for s in sm_items)
            row["signalmap_text_length"] = mean(s["_text_length"] for s in sm_items)
            row["signalmap_domain_count"] = mean(len(s["_domain_set"]) for s in sm_items)
        else:
            row.update(dict.fromkeys((
                "knauf_in_signalmap_share", "knauf_first_mention_rank_signalmap",
                "knauf_share_of_voice_signalmap", "signalmap_text_length", "signalmap_domain_count",
            ), ""))
        if peec_items and sm_items:
            row["brand_jaccard"] = mean(
                jaccard(a["_mentioned_brands"], s["_mentioned_brands"]) for a in peec_items for s in sm_items
            )
            row["domain_jaccard"] = mean(
                jaccard(a["_domain_set"], s["_domain_set"]) for a in peec_items for s in sm_items
            )
        else:
            row["brand_jaccard"] = row["domain_jaccard"] = ""
        rows.append(row)

    rows.sort(key=lambda r: (r["prompt_id"], r["signalmap_model"], r["local_date"]))
    return rows


def build_detector_check(peec_answers: list[dict], signalmap_answers: list[dict], rules: list[BrandRule]) -> list[dict]:
    """Each tool's own built-in detector (Peec's `mentions`, SignalMap's Competitive
    Visibility Detection skill) vs. the shared ruler, per brand and model — this is
    where the DB's alias-less "Saint Gobain" (T1's Vychozi stav pilot finding) shows
    up as a run of SignalMap-side disagreements, not just a claim about it.
    """

    def peec_own_mentioned(answer: dict, rule: BrandRule) -> bool:
        # Peec's own vocabulary may use different casing (e.g. "ROCKWOOL" vs our
        # "Rockwool") — that's a casing difference, not a detector disagreement.
        own = {m.lower() for m in answer["mentions"].split(";") if m}
        return any(name.lower() in own for name in rule.match_names)

    def signalmap_own_mentioned(detector_output_json: str, brand_name: str) -> bool:
        if not detector_output_json:
            return False
        try:
            output = json.loads(detector_output_json)
        except json.JSONDecodeError:
            return False
        return any(e.get("name") == brand_name and e.get("mentioned") for e in output.get("entities", []))

    def tally(model_answers: list[dict], rule: BrandRule, ruler_says, own_says, id_of) -> dict:
        agreements = disagreements = 0
        example_id = ""
        for item in model_answers:
            a, b = ruler_says(item, rule), own_says(item, rule)
            if a == b:
                agreements += 1
            else:
                disagreements += 1
                example_id = example_id or id_of(item)
        return {
            "answers_checked": len(model_answers),
            "ruler_mentioned": sum(1 for i in model_answers if ruler_says(i, rule)),
            "own_detector_mentioned": sum(1 for i in model_answers if own_says(i, rule)),
            "agreements": agreements, "disagreements": disagreements, "example_id": example_id,
        }

    compared_peec = [a for a in peec_answers if a["compared"] == "true"]
    rows = []
    for model in COMPARED_MODELS.values():
        peec_model_answers = [a for a in compared_peec if a["signalmap_model"] == model]
        sm_model_answers = [s for s in signalmap_answers if s["signalmap_model"] == model]
        for rule in rules:
            stats = tally(
                peec_model_answers, rule,
                lambda a, r: a["_brand_hits"][r.name].mentioned, peec_own_mentioned,
                lambda a: a["peec_answer_id"],
            )
            rows.append({"tool": "peec", "signalmap_model": model, "brand": rule.name, **stats})

            stats = tally(
                sm_model_answers, rule,
                lambda s, r: s["_brand_hits"][r.name].mentioned,
                lambda s, r: signalmap_own_mentioned(s["detector_output_json"], r.name),
                lambda s: s["run_id"],
            )
            rows.append({"tool": "signalmap", "signalmap_model": model, "brand": rule.name, **stats})
    return rows


# ---------------------------------------------------------------------------
# Noise references + bootstrap verdict (design decision 7), printed as a
# console table — T3's report.xlsx Summary sheet will format this properly.
# ---------------------------------------------------------------------------

METRIC_LABELS = {"knauf_agreement": "Knauf ano/ne", "brand_jaccard": "Znacky", "domain_jaccard": "Domeny"}
COMPARISON_LABELS = {
    "cross": "Peec x SignalMap, stejny den",
    "peec_same_day": "Peec x Peec, stejny den (opakovani)",
    "peec_diff_day": "Peec x Peec, jiny den",
    "signalmap_diff_day": "SignalMap x SignalMap, jiny den",
}
NOISE_KINDS = ("peec_same_day", "peec_diff_day", "signalmap_diff_day")


def _avg_cross(items_a: list[dict], items_b: list[dict], metric_fn) -> float | None:
    return mean(metric_fn(a, b) for a in items_a for b in items_b)


def _avg_intra(items: list[dict], metric_fn) -> float | None:
    return mean(metric_fn(items[i], items[j]) for i in range(len(items)) for j in range(i + 1, len(items)))


def compute_metric_series(peec_answers: list[dict], signalmap_answers: list[dict]) -> dict:
    """{model: {comparison_kind: {metric_name: [values]}}} — the raw per-(prompt[,
    day-pair]) values behind design decision 7's noise references and verdict.
    """
    compared_peec = [a for a in peec_answers if a["compared"] == "true"]
    peec_by_day = _day_groups(compared_peec)
    signalmap_by_day = _day_groups(signalmap_answers)

    metric_fns = {
        "knauf_agreement": lambda a, b: 1.0 if a["_client_mentioned"] == b["_client_mentioned"] else 0.0,
        "brand_jaccard": lambda a, b: jaccard(a["_mentioned_brands"], b["_mentioned_brands"]),
        "domain_jaccard": lambda a, b: jaccard(a["_domain_set"], b["_domain_set"]),
    }
    series = {
        model: {kind: {name: [] for name in metric_fns} for kind in ("cross", *NOISE_KINDS)}
        for model in COMPARED_MODELS.values()
    }

    for key in set(peec_by_day) & set(signalmap_by_day):
        _, model, _ = key
        for name, fn in metric_fns.items():
            value = _avg_cross(peec_by_day[key], signalmap_by_day[key], fn)
            if value is not None:
                series[model]["cross"][name].append(value)

    for key, items in peec_by_day.items():
        if len(items) < 2:
            continue
        _, model, _ = key
        for name, fn in metric_fns.items():
            value = _avg_intra(items, fn)
            if value is not None:
                series[model]["peec_same_day"][name].append(value)

    def different_day_series(by_day: dict, kind: str) -> None:
        by_prompt_model: dict[tuple[str, str], dict[str, list[dict]]] = {}
        for (prompt_id, model, local_date), items in by_day.items():
            by_prompt_model.setdefault((prompt_id, model), {})[local_date] = items
        for (prompt_id, model), days in by_prompt_model.items():
            ordered = sorted(days)
            for i in range(len(ordered)):
                for j in range(i + 1, len(ordered)):
                    for name, fn in metric_fns.items():
                        value = _avg_cross(days[ordered[i]], days[ordered[j]], fn)
                        if value is not None:
                            series[model][kind][name].append(value)

    different_day_series(peec_by_day, "peec_diff_day")
    different_day_series(signalmap_by_day, "signalmap_diff_day")
    return series


def print_metrics_table(series: dict) -> None:
    for model in COMPARED_MODELS.values():
        print(f"\n{model}")
        for metric in ("knauf_agreement", "brand_jaccard", "domain_jaccard"):
            cross_values = series[model]["cross"][metric]
            cross_mean = mean(cross_values)
            print(f"  {METRIC_LABELS[metric]}")
            if cross_mean is None:
                print(f"    {COMPARISON_LABELS['cross']}: n/a (zadna spolecna dvojice)")
                continue
            print(f"    {COMPARISON_LABELS['cross']:38s} {cross_mean:.2f} (n={len(cross_values)})")

            noise_means = {}
            for kind in NOISE_KINDS:
                values = series[model][kind][metric]
                noise_means[kind] = mean(values)
                shown = f"{noise_means[kind]:.2f} (n={len(values)})" if values else "n/a (zadna data)"
                print(f"    {COMPARISON_LABELS[kind]:38s} {shown}")

            available = {k: v for k, v in noise_means.items() if v is not None}
            if not available:
                print("    verdikt: nelze spocitat (chybi vsechny reference sumu)")
                continue
            lowest_kind = min(available, key=available.get)
            ci = bootstrap_ci_diff(
                cross_values, series[model][lowest_kind][metric], seed=BOOTSTRAP_SEED, iterations=BOOTSTRAP_ITERATIONS
            )
            if ci is None:
                print("    verdikt: nelze spocitat bootstrap (malo dat)")
                continue
            diff = cross_mean - available[lowest_kind]
            verdict = "lisi se vic nez sum" if ci[1] < 0 else "v ramci sumu"
            print(
                f"    rozdil (cross - {lowest_kind}, nejnizsi reference) = {diff:.3f}, "
                f"95% CI [{ci[0]:.3f}, {ci[1]:.3f}] (seed={BOOTSTRAP_SEED}) -> {verdict}"
            )


def bootstrap_ci_diff(
    cross_values: list[float], noise_values: list[float], *, seed: int, iterations: int
) -> tuple[float, float] | None:
    """95% CI of (mean(cross) - mean(noise)), each side resampled independently with
    replacement — design decision 7's "rozdil = cross - nizsi z internich hodnot".
    """
    if not cross_values or not noise_values:
        return None
    rng = random.Random(seed)
    diffs = [
        mean(rng.choices(cross_values, k=len(cross_values))) - mean(rng.choices(noise_values, k=len(noise_values)))
        for _ in range(iterations)
    ]
    return percentile(diffs, 2.5), percentile(diffs, 97.5)


def load_all(client_name: str, peec_dir: Path) -> LoadedData:
    tz = prague_zone()
    client = fetch_client(client_name)
    prompts = fetch_prompts(client["id"])
    model_names = list(COMPARED_MODELS.values())

    client_aliases = fetch_client_aliases(client["id"])
    entities = fetch_tracked_entities(client["id"])
    rules = build_brand_rules(client["name"], client_aliases, entities)

    files, peec_answers = load_peec_answers(peec_dir, client["slug"], prompts, tz, rules)
    peec_sources = load_peec_sources(peec_dir, client["slug"])

    runs = fetch_runs(client["id"], model_names)
    citations = fetch_citations(client["id"], model_names)
    detector_output = fetch_detector_output(client["id"], model_names)

    citation_domains: dict[str, set[str]] = {}
    for c in citations:
        normalized, flagged = normalize_domain(c["source_domain"]) if c["source_domain"] else ("", False)
        c["normalized_domain"], c["domain_flagged"] = normalized, "true" if flagged else "false"
        if normalized:
            citation_domains.setdefault(c["raw_response_id"], set()).add(normalized)

    signalmap_answers = build_signalmap_answers(runs, detector_output, tz, rules, citation_domains)

    # Fetch pulls every successful run ever made for the compared models — not just
    # the export's window — so date filtering happens here in Python, not SQL, using
    # the same local_date_of() the pairing itself uses. "Same days" (Vychozi stav) is
    # part of T1's own scope: without this, stray old runs (seen once already: 6 from
    # 2026-09-14, 9 days before this export) would show up as unexplained "gaps"
    # instead of being what they are — out of scope for this comparison, not missing data.
    export_dates = {a["local_date"] for a in peec_answers}
    in_window = [a for a in signalmap_answers if a["local_date"] in export_dates]
    excluded = [a for a in signalmap_answers if a["local_date"] not in export_dates]
    kept_response_ids = {a["raw_response_id"] for a in in_window}
    citations = [c for c in citations if c["raw_response_id"] in kept_response_ids]

    data = LoadedData(client=client, prompts=prompts, peec_files=files)
    data.peec_answers = peec_answers
    data.signalmap_answers = in_window
    data.signalmap_answers_excluded = excluded
    data.signalmap_citations = citations
    data.model_mapping = build_model_mapping()
    data.prompt_summary = build_prompt_summary(prompts, peec_answers)
    data.pairs = build_pairs(peec_answers, in_window)
    data.peec_sources = peec_sources
    data.tracked_brands = build_tracked_brands(rules)
    data.detector_check = build_detector_check(peec_answers, in_window, rules)
    data.metric_series = compute_metric_series(peec_answers, in_window)
    return data


# ---------------------------------------------------------------------------
# --plan
# ---------------------------------------------------------------------------


def count_by(rows: list[dict], *keys) -> dict:
    counts: dict = {}
    for row in rows:
        k = tuple(row[key] for key in keys) if len(keys) > 1 else row[keys[0]]
        counts[k] = counts.get(k, 0) + 1
    return counts


def print_plan(data: LoadedData, peec_sources: list[dict], output_dir: Path) -> None:
    compared_peec = [a for a in data.peec_answers if a["compared"] == "true"]
    current_prompts = [p for p in data.prompts if p["is_current_version"] == "t" and p["is_active"] == "t"]

    print(f"klient            {data.client['name']} (id={data.client['id']}, slug={data.client['slug']})")
    print(f"peec export       {len(data.peec_files)} souboru")
    print(f"prompty (DB)      {len(current_prompts)} aktivnich aktualnich, {len(data.prompt_summary)} sparovano s Peec exportem")
    print(f"modely porovnavane {len(COMPARED_MODELS)} ({', '.join(COMPARED_MODELS.values())})")

    print(f"peec odpovedi     {len(data.peec_answers)} celkem, {len(compared_peec)} porovnavanych modelu")
    for model, n in sorted(count_by(compared_peec, 'signalmap_model').items()):
        print(f"                    {model}: {n}")

    print(f"signalmap runy    {len(data.signalmap_answers)} celkem (v okne exportu {min(data.peec_answers, key=lambda a: a['local_date'])['local_date'] if data.peec_answers else '?'} az {max(data.peec_answers, key=lambda a: a['local_date'])['local_date'] if data.peec_answers else '?'})")
    for model, n in sorted(count_by(data.signalmap_answers, 'signalmap_model').items()):
        print(f"                    {model}: {n}")
    if data.signalmap_answers_excluded:
        print(f"                    + {len(data.signalmap_answers_excluded)} mimo okno exportu vyrazeno (jine datum, viz komentar u filtru)")
        for (model, d), n in sorted(count_by(data.signalmap_answers_excluded, 'signalmap_model', 'local_date').items()):
            print(f"                        {model} {d}: {n}")

    print(f"peec zdroje       {len(peec_sources)} radku (odpoved x domena)")
    print(f"signalmap citace  {len(data.signalmap_citations)} radku")
    print(f"pary (prompt x model x den) {len(data.pairs)}")
    missing = [p for p in data.pairs if p['peec_answer_count'] == 0 or p['signalmap_run_count'] == 0]
    if missing:
        print(f"                    z toho {len(missing)} bez protejsku na druhe strane (viz Coverage v T3)")
    print(f"cil               {output_dir}")


# ---------------------------------------------------------------------------
# Output — sources/*.csv, sources/sources.xlsx, sources/peec_raw/
# ---------------------------------------------------------------------------

CSV_SCHEMAS: dict[str, list[str]] = {
    "prompts.csv": ["prompt_id", "prompt_set_name", "version", "text", "peec_prompt_id", "source_file", "in_peec", "in_signalmap"],
    "model_mapping.csv": ["peec_model", "signalmap_model", "compared", "reason"],
    "peec_answers.csv": [
        "source_file", "peec_answer_id", "peec_prompt_id", "signalmap_prompt_id", "prompt_set_name",
        "peec_model", "signalmap_model", "compared", "created_utc", "local_date", "position",
        "citations_count", "mentions", "sources_count", "content_in_chat", "user_text", "assistant_text",
    ],
    "peec_sources.csv": [
        "source_file", "peec_answer_id", "peec_prompt_id", "model", "source_rank", "domain",
        "normalized_domain", "domain_flagged",
    ],
    "signalmap_answers.csv": [
        "run_id", "prompt_id", "signalmap_model", "started_at_utc", "local_date", "finished_at_utc",
        "latency_ms", "market_code", "persona_label", "raw_response_id", "has_citations", "domain_count",
        "detector_output_json", "rendered_text",
    ],
    "signalmap_citations.csv": [
        "citation_id", "raw_response_id", "run_id", "source_url", "source_title", "source_domain",
        "citation_position", "normalized_domain", "domain_flagged",
    ],
    "pairs.csv": [
        "prompt_id", "prompt_set_name", "signalmap_model", "peec_model", "local_date",
        "peec_answer_ids", "signalmap_run_ids", "peec_answer_count", "signalmap_run_count",
        "knauf_in_peec_share", "knauf_first_mention_rank_peec", "knauf_share_of_voice_peec",
        "peec_text_length", "peec_domain_count",
        "knauf_in_signalmap_share", "knauf_first_mention_rank_signalmap", "knauf_share_of_voice_signalmap",
        "signalmap_text_length", "signalmap_domain_count",
        "brand_jaccard", "domain_jaccard",
    ],
    "tracked_brands.csv": ["brand", "is_client", "match_names", "case_sensitive", "extra_aliases"],
    "detector_check.csv": [
        "tool", "signalmap_model", "brand", "answers_checked", "ruler_mentioned",
        "own_detector_mentioned", "agreements", "disagreements", "example_id",
    ],
}


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    # utf-8-sig: BOM so Excel shows German umlauts correctly (design decision 11).
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def load_openpyxl():
    try:
        import openpyxl
        from openpyxl.styles import Font
    except ImportError:
        sys.exit("openpyxl neni nainstalovany - spust: pip install openpyxl==3.1.5")
    return openpyxl, Font


def write_xlsx(path: Path, sheets: dict[str, tuple[list[str], list[dict]]]) -> None:
    openpyxl, Font = load_openpyxl()
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, (fieldnames, rows) in sheets.items():
        ws = wb.create_sheet(title=name[:31])
        ws.append([*fieldnames, "text_truncated"])
        for cell in ws[1]:
            cell.font = Font(bold=True)
        ws.freeze_panes = "A2"
        for row in rows:
            truncated = False
            values = []
            for key in fieldnames:
                value = row.get(key, "")
                if isinstance(value, str) and len(value) > MAX_XLSX_CELL:
                    value = value[:MAX_XLSX_CELL] + "…"
                    truncated = True
                values.append(value)
            values.append("true" if truncated else "false")
            ws.append(values)
    wb.save(path)


def write_outputs(data: LoadedData, peec_sources: list[dict], output_dir: Path) -> None:
    sources_dir = output_dir / "sources"
    raw_dir = sources_dir / "peec_raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    tables = {
        "prompts.csv": data.prompt_summary,
        "model_mapping.csv": data.model_mapping,
        "peec_answers.csv": data.peec_answers,
        "peec_sources.csv": peec_sources,
        "signalmap_answers.csv": data.signalmap_answers,
        "signalmap_citations.csv": data.signalmap_citations,
        "pairs.csv": data.pairs,
        "tracked_brands.csv": data.tracked_brands,
        "detector_check.csv": data.detector_check,
    }
    for filename, rows in tables.items():
        write_csv(sources_dir / filename, CSV_SCHEMAS[filename], rows)

    write_xlsx(
        sources_dir / "sources.xlsx",
        {name[:-4]: (CSV_SCHEMAS[name], rows) for name, rows in tables.items()},
    )

    for path in data.peec_files:
        shutil.copy2(path, raw_dir / path.name)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--client", default="Knauf", help="client name in the DB (default: Knauf)")
    parser.add_argument("--peec-dir", default="docs/peec", help="folder with peec_<slug>_*.json exports (default: docs/peec)")
    parser.add_argument("--overwrite", action="store_true", help="replace an existing output folder")
    parser.add_argument("--plan", action="store_true", help="only print what would be loaded and where to, change nothing")
    return parser


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(line_buffering=True)
    args = build_parser().parse_args(argv)
    peec_dir = (PROJECT_DIR / args.peec_dir).resolve() if not Path(args.peec_dir).is_absolute() else Path(args.peec_dir)
    if not peec_dir.is_dir():
        sys.exit(f"slozka s peec exporty neexistuje: {peec_dir}")

    try:
        data = load_all(args.client, peec_dir)
    except PeecDataError as exc:
        sys.exit(f"CHYBA: {exc}")

    peec_sources = data.peec_sources

    dates = [a["local_date"] for a in data.peec_answers] or [a["local_date"] for a in data.signalmap_answers if a["local_date"]]
    if not dates:
        sys.exit("CHYBA: zadna data s datem - nelze pojmenovat vystupni slozku")
    start, end = min(dates), max(dates)
    output_dir = peec_dir / f"comparison_{data.client['slug']}_{start}_{end}"

    if args.plan:
        print_plan(data, peec_sources, output_dir)
        return 0

    if output_dir.exists():
        if not args.overwrite:
            sys.exit(f"cilova slozka uz existuje: {output_dir} (pouzij --overwrite pro prepsani)")
        shutil.rmtree(output_dir)

    write_outputs(data, peec_sources, output_dir)
    print(f"hotovo: {output_dir}")
    print(f"  sources/*.csv, sources/sources.xlsx, sources/peec_raw/ ({len(data.peec_files)} souboru)")
    print("\ncross vs sum (design decision 7):")
    print_metrics_table(data.metric_series)
    return 0


if __name__ == "__main__":
    sys.exit(main())
