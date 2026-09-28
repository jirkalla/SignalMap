"""One-off measurement for docs/TASKS_CITATION_VERIFICATION.md T4 point 6: does the honest

`SignalMapVerifier` User-Agent (app/services/source_capture.py's USER_AGENT) get blocked
noticeably more often than a normal browser UA, on real citation source URLs?

Makes REAL outbound HTTP requests to real, external citation domains (up to 2x --limit) — never
run automatically, in CI, or as part of the test suite. Run it once, manually, when you've
decided this is an acceptable moment to generate that traffic:

    docker compose exec app python -m scripts.measure_capture_ua --limit 100

Per design decision 7: if the browser-UA success rate beats the honest-UA rate by more than 10
percentage points, stop and let the user decide (do not silently switch to a browser UA) — this
script only measures and reports; it changes nothing.

This is a crude reachability check (HTTP status < 400), not a full capture_url run — it does not
run bot-challenge detection, robots.txt, or extraction, since the question here is specifically
"does the UA string itself change whether the site responds at all", not the rest of the
pipeline (already covered by tests/test_source_capture.py against mocked responses).
"""

import argparse

import httpx
from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import Citation
from app.services.source_capture import USER_AGENT as HONEST_UA

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

TIMEOUT_SECONDS = 15.0


def _reachable(url: str, user_agent: str) -> bool:
    try:
        with httpx.Client(headers={"User-Agent": user_agent}, timeout=TIMEOUT_SECONDS, follow_redirects=True) as client:
            response = client.get(url)
            return response.status_code < 400
    except httpx.RequestError:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=100, help="How many distinct citation URLs to sample.")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        urls = db.scalars(
            select(Citation.source_url)
            .where(Citation.source_url.isnot(None))
            .distinct()
            .order_by(func.random())
            .limit(args.limit)
        ).all()
    finally:
        db.close()

    if not urls:
        print("No citation source URLs found in this database — nothing to measure.")
        return

    honest_ok = 0
    browser_ok = 0
    disagreements: list[tuple[str, bool, bool]] = []

    for i, url in enumerate(urls, start=1):
        honest = _reachable(url, HONEST_UA)
        browser = _reachable(url, BROWSER_UA)
        honest_ok += honest
        browser_ok += browser
        if honest != browser:
            disagreements.append((url, honest, browser))
        print(f"[{i}/{len(urls)}] honest={honest} browser={browser}  {url}")

    total = len(urls)
    honest_rate = 100 * honest_ok / total
    browser_rate = 100 * browser_ok / total
    gap = browser_rate - honest_rate

    print()
    print(f"Tested {total} distinct citation URLs")
    print(f"Honest UA  ({HONEST_UA}): {honest_ok}/{total} reachable ({honest_rate:.1f}%)")
    print(f"Browser UA ({BROWSER_UA}): {browser_ok}/{total} reachable ({browser_rate:.1f}%)")
    print(f"Gap (browser - honest): {gap:.1f} percentage points")

    if disagreements:
        print(f"\n{len(disagreements)} URL(s) where the two UAs disagreed:")
        for url, honest, browser in disagreements:
            print(f"  honest={honest} browser={browser}  {url}")

    if gap > 10:
        print(
            "\nGap exceeds 10 percentage points (design decision 7) — do not switch to a "
            "browser UA on your own; report this back and let the user decide."
        )


if __name__ == "__main__":
    main()
