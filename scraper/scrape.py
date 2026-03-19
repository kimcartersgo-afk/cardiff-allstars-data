"""
Cardiff Allstars FC - FAW Comet League Table Scraper
Uses Playwright to load pages (JS-rendered content) with a saved session.

Requires env var:
  COMET_SESSION  - base64-encoded Playwright session JSON (from setup_session.py)
"""

import base64
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE_DIR = os.path.join(os.path.dirname(__file__), "..")

COMPETITIONS = [
    {
        "name":    "U15 Orange",
        "url":     "https://comet.faw.cymru/resources/jsf/competition/index.xhtml?id=95758708",
        "output":  os.path.join(BASE_DIR, "data", "league-table.json"),
        "division":"Cardiff & District U15 Division C 25/26",
        "team":    "Cardiff Allstars Under 15s Orange",
    },
    {
        "name":    "U15 Black",
        "url":     "https://comet.faw.cymru/resources/jsf/competition/index.xhtml?id=95758731",
        "output":  os.path.join(BASE_DIR, "data", "u15-black.json"),
        "division":"Cardiff & District U15 Division 25/26",
        "team":    "Cardiff Allstars Under 15s Black",
    },
    {
        "name":    "Youth",
        "url":     "https://comet.faw.cymru/resources/jsf/competition/index.xhtml?id=95057526",
        "output":  os.path.join(BASE_DIR, "data", "youth.json"),
        "division":"Cardiff & District Youth Division 25/26",
        "team":    "Cardiff Allstars FC Youth",
    },
    {
        "name":    "First Team",
        "url":     "https://comet.faw.cymru/resources/jsf/competition/index.xhtml?id=95408917",
        "output":  os.path.join(BASE_DIR, "data", "first-team.json"),
        "division":"Cardiff & District Division 25/26",
        "team":    "Cardiff Allstars FC",
    },
    {
        "name":    "Reserves",
        "url":     "https://comet.faw.cymru/resources/jsf/competition/index.xhtml?id=95410077",
        "output":  os.path.join(BASE_DIR, "data", "reserves.json"),
        "division":"Cardiff & District Reserves Division 25/26",
        "team":    "Cardiff Allstars FC Reserves",
    },
]


def parse_table(page) -> list:
    """Extract league table rows from the current page."""
    try:
        page.wait_for_selector("table", timeout=20000)
    except PlaywrightTimeoutError:
        print("  ERROR: Timed out waiting for table.", file=sys.stderr)
        return []

    rows = []
    for row in page.query_selector_all("table tr"):
        cells = row.query_selector_all("td")
        if len(cells) < 9:
            continue
        texts = [c.inner_text().strip() for c in cells]

        # Detect offset (some tables have a leading checkbox/icon column)
        offset = 0
        try:
            int(texts[0])
        except (ValueError, IndexError):
            offset = 1

        try:
            gd_raw = texts[offset + 8].replace("+", "").replace("−", "-").replace("–", "-")
            rows.append({
                "pos":  int(texts[offset]),
                "team": texts[offset + 1],
                "mp":   int(texts[offset + 2]),
                "w":    int(texts[offset + 3]),
                "d":    int(texts[offset + 4]),
                "l":    int(texts[offset + 5]),
                "gf":   int(texts[offset + 6]),
                "ga":   int(texts[offset + 7]),
                "gd":   int(gd_raw),
                "pts":  int(texts[offset + 9]),
            })
        except (ValueError, IndexError) as e:
            print(f"  Skipping row: {texts} — {e}")

    return rows


def main():
    session_b64 = os.environ.get("COMET_SESSION")
    if not session_b64:
        print("ERROR: COMET_SESSION env var not set. Run setup_session.py first.", file=sys.stderr)
        sys.exit(1)

    try:
        session_json = base64.b64decode(session_b64.encode()).decode()
        json.loads(session_json)  # Validate JSON
    except Exception as e:
        print(f"ERROR: Could not decode COMET_SESSION — {e}", file=sys.stderr)
        sys.exit(1)

    # Write session to temp file for Playwright
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tmp:
        tmp.write(session_json)
        session_path = tmp.name

    errors = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            context = browser.new_context(
                storage_state=session_path,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
            )
            page = context.new_page()

            for comp in COMPETITIONS:
                print(f"\n[{comp['name']}] {comp['url']}")
                try:
                    page.goto(comp["url"], wait_until="networkidle", timeout=30000)

                    if "login" in page.url or "auth" in page.url:
                        print("  ERROR: Session expired — re-run setup_session.py.", file=sys.stderr)
                        errors.append(comp["name"])
                        continue

                    table = parse_table(page)
                    if not table:
                        print("  WARNING: No table data found.")
                        errors.append(comp["name"])
                        continue

                    # Try to read real division name from page
                    try:
                        for sel in ["h1", "h2", ".competition-title", ".title"]:
                            el = page.query_selector(sel)
                            if el:
                                txt = el.inner_text().strip()
                                if txt:
                                    comp["division"] = txt
                                    break
                    except Exception:
                        pass

                    output = {
                        "updated":  datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "division": comp["division"],
                        "team":     comp["team"],
                        "source":   comp["url"],
                        "table":    table,
                    }
                    os.makedirs(os.path.dirname(os.path.abspath(comp["output"])), exist_ok=True)
                    with open(comp["output"], "w", encoding="utf-8") as f:
                        json.dump(output, f, indent=2, ensure_ascii=False)
                    print(f"  OK — {len(table)} teams written.")

                except Exception as e:
                    print(f"  ERROR: {e}", file=sys.stderr)
                    errors.append(comp["name"])

            browser.close()
    finally:
        os.unlink(session_path)

    if errors:
        print(f"\nCompleted with errors on: {', '.join(errors)}", file=sys.stderr)
        sys.exit(1)
    print("\nAll competitions updated successfully.")


if __name__ == "__main__":
    main()
