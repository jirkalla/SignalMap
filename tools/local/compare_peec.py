"""Export SignalMap and Peec answers for the same prompts so they can be compared.

Runs on the developer PC (Windows), not on the server. Reads the local dev
database exactly like refresh_dev_db.py does — through `docker compose exec
postgres psql` (docs/TASKS_PEEC_COMPARISON.md design decision 1) — and never
writes to it. The application itself is not touched.

    python tools/local/compare_peec.py                      # Knauf, docs/peec, date range from the exports
    python tools/local/compare_peec.py --plan                # only print what would be loaded and where to
    python tools/local/compare_peec.py --client Knauf --peec-dir docs/peec --overwrite

This is T1 of docs/TASKS_PEEC_COMPARISON.md ("Loading, pairing and source
export"): it loads the Peec JSON exports, matches each one to a SignalMap
prompt by exact prompt TEXT (never by filename), pairs Peec answers with
SignalMap runs by prompt x model x local (Europe/Prague) day, and writes the
`sources/` half of the output folder — the raw CSVs, sources.xlsx and a copy
of the source JSON. Metrics (T2), report.xlsx (T3) and tracked_brands.csv
(needs the brand-matching "ruler" from T2 design decision 6, so it is not
produced here) are separate, not-yet-implemented steps.

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


def load_peec_answers(peec_dir: Path, client_slug: str, prompts: list[dict], tz) -> tuple[list[Path], list[dict]]:
    files = sorted(peec_dir.glob(f"peec_{client_slug}_*.json"))
    if not files:
        sys.exit(f"zadne soubory 'peec_{client_slug}_*.json' v {peec_dir}")

    answers: list[dict] = []
    for path in files:
        records = load_peec_records(path)
        prompt = match_prompt_text(path, records, prompts)
        for rec in records:
            created = parse_peec_created(rec["created"])
            signalmap_model = COMPARED_MODELS.get(rec["model"], "")
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
                rows.append({
                    "source_file": path.name, "peec_answer_id": rec["id"],
                    "peec_prompt_id": rec["promptId"], "model": rec["model"],
                    "source_rank": rank, "domain": domain,
                })
    return rows


def build_signalmap_answers(runs: list[dict], detector_output: dict[str, str], tz) -> list[dict]:
    rows = []
    for run in runs:
        started = parse_db_timestamp(run["started_at_utc"])
        finished = parse_db_timestamp(run["finished_at_utc"])
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
            "detector_output_json": detector_output.get(run["raw_response_id"], ""),
            "rendered_text": run["rendered_text"] or "",
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


def build_pairs(peec_answers: list[dict], signalmap_answers: list[dict]) -> list[dict]:
    """One row per (prompt x compared model x local day) group — design decision 5:
    a day with several Peec repetitions is still one pairing unit, averaged in T2, not
    counted several times. This is the pairing scaffold only; metric columns are T2's.
    """
    groups: dict[tuple[str, str, str], dict] = {}

    for a in peec_answers:
        if a["compared"] != "true":
            continue
        key = (a["signalmap_prompt_id"], a["signalmap_model"], a["local_date"])
        g = groups.setdefault(key, {
            "prompt_id": a["signalmap_prompt_id"], "prompt_set_name": a["prompt_set_name"],
            "signalmap_model": a["signalmap_model"], "peec_model": a["peec_model"],
            "local_date": a["local_date"], "peec_answer_ids": [], "signalmap_run_ids": [],
        })
        g["peec_answer_ids"].append(a["peec_answer_id"])

    for s in signalmap_answers:
        key = (s["prompt_id"], s["signalmap_model"], s["local_date"])
        g = groups.get(key)
        if g is None:
            # A SignalMap run with no matching Peec day for that prompt/model — a coverage
            # gap, not an error; T3's Coverage sheet is where this becomes visible to Philip.
            g = groups.setdefault(key, {
                "prompt_id": s["prompt_id"], "prompt_set_name": "", "signalmap_model": s["signalmap_model"],
                "peec_model": next((k for k, v in COMPARED_MODELS.items() if v == s["signalmap_model"]), ""),
                "local_date": s["local_date"], "peec_answer_ids": [], "signalmap_run_ids": [],
            })
        g["signalmap_run_ids"].append(s["run_id"])

    rows = []
    for g in groups.values():
        rows.append({
            "prompt_id": g["prompt_id"], "prompt_set_name": g["prompt_set_name"],
            "signalmap_model": g["signalmap_model"], "peec_model": g["peec_model"],
            "local_date": g["local_date"],
            "peec_answer_ids": ";".join(g["peec_answer_ids"]),
            "signalmap_run_ids": ";".join(g["signalmap_run_ids"]),
            "peec_answer_count": len(g["peec_answer_ids"]),
            "signalmap_run_count": len(g["signalmap_run_ids"]),
        })
    rows.sort(key=lambda r: (r["prompt_id"], r["signalmap_model"], r["local_date"]))
    return rows


def load_all(client_name: str, peec_dir: Path) -> LoadedData:
    tz = prague_zone()
    client = fetch_client(client_name)
    prompts = fetch_prompts(client["id"])
    model_names = list(COMPARED_MODELS.values())

    files, peec_answers = load_peec_answers(peec_dir, client["slug"], prompts, tz)
    peec_sources = load_peec_sources(peec_dir, client["slug"])

    runs = fetch_runs(client["id"], model_names)
    citations = fetch_citations(client["id"], model_names)
    detector_output = fetch_detector_output(client["id"], model_names)
    signalmap_answers = build_signalmap_answers(runs, detector_output, tz)

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
    "peec_sources.csv": ["source_file", "peec_answer_id", "peec_prompt_id", "model", "source_rank", "domain"],
    "signalmap_answers.csv": [
        "run_id", "prompt_id", "signalmap_model", "started_at_utc", "local_date", "finished_at_utc",
        "latency_ms", "market_code", "persona_label", "raw_response_id", "has_citations",
        "detector_output_json", "rendered_text",
    ],
    "signalmap_citations.csv": ["citation_id", "raw_response_id", "run_id", "source_url", "source_title", "source_domain", "citation_position"],
    "pairs.csv": [
        "prompt_id", "prompt_set_name", "signalmap_model", "peec_model", "local_date",
        "peec_answer_ids", "signalmap_run_ids", "peec_answer_count", "signalmap_run_count",
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
