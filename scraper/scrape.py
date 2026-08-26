"""
Cardiff Allstars FC - FAW Comet League Table Scraper
Uses Playwright to load pages (JS-rendered content) with a saved session.

Requires env var:
  COMET_SESSION  - base64-encoded Playwright session JSON (from setup_session.py)
"""
import base64, json, os, sys, tempfile
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
    """
    The competition table has class 'a-competition-table'.
    Row cells: [0] checkbox [1] pos [2] club [3] MP [4] W [5] D [6] L
               [7] GF [8] GA [9] GD [10] Pts [11] actions
    """
    print(f"  URL  : {page.url}")
    print(f"  Title: {page.title()}")

    SELECTOR = ".a-competition-table tbody tr"
    try:
        page.wait_for_selector(SELECTOR, timeout=30000)
    except PlaywrightTimeoutError:
        full_html = page.content()
        debug_path = os.path.join(BASE_DIR, "data", "debug.html")
        os.makedirs(os.path.dirname(os.path.abspath(debug_path)), exist_ok=True)
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(full_html)
        print("  ERROR: Table not found. Debug saved to data/debug.html", file=sys.stderr)
        return []

    rows = []
    for row in page.query_selector_all(SELECTOR):
        cells = row.query_selector_all("td")
        if len(cells) < 11:
            continue
        texts = [c.inner_text().strip() for c in cells]
        try:
            gd_raw = texts[9].replace("+", "").replace("\u2212", "-").replace("\u2013", "-")
            rows.append({
                "pos":  int(texts[1]),
                "team": texts[2],
                "mp":   int(texts[3]),
                "w":    int(texts[4]),
                "d":    int(texts[5]),
                "l":    int(texts[6]),
                "gf":   int(texts[7]),
                "ga":   int(texts[8]),
                "gd":   int(gd_raw),
                "pts":  int(texts[10]),
            })
        except (ValueError, IndexError) as e:
            print(f"  Skipping row: {texts} - {e}")
    return rows


def main():
    session_b64 = os.environ.get("COMET_SESSION")
    if not session_b64:
        print("ERROR: COMET_SESSION not set. Run setup_session.py first.", file=sys.stderr)
        sys.exit(1)

    try:
        session_json = base64.b64decode(session_b64.encode()).decode()
        json.loads(session_json)  # validate
    except Exception as e:
        print(f"ERROR: Could not decode COMET_SESSION - {e}", file=sys.stderr)
        sys.exit(1)

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
                    page.goto(comp["url"], wait_until="domcontentloaded", timeout=30000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        pass

                    if "login" in page.url or "auth" in page.url:
                        print("  ERROR: Session expired - re-run setup_session.py.", file=sys.stderr)
                        errors.append(comp["name"])
                        continue

                    # Click the Table tab - lazy-loaded via AJAX
                    try:
                        page.locator('a[href$=":tableTab"]').click(timeout=10000)
                        print("  Clicked Table tab, waiting for AJAX...")
                        page.wait_for_timeout(2000)
                    except Exception as e:
                        print(f"  WARNING: Could not click Table tab: {e}", file=sys.stderr)

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
                    print(f"  OK - {len(table)} teams written.")

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
