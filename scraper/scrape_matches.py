"""
Cardiff Allstars FC - FAW Comet Match Day Scraper (v2)
Uses the Club match feed pages instead of individual competition pages.
Much faster - only 2 page visits instead of 5.

URLs used:
  /match/myNextMatches.xhtml?type=Club  - all upcoming matches
  /match/myLastMatches.xhtml?type=Club  - all recent results (if exists)

Requires env var:
  COMET_SESSION  - base64-encoded Playwright session JSON (from setup_session.py)
"""
import base64, json, os, re, sys, tempfile
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
BASE_URL = "https://comet.faw.cymru"

NEXT_MATCHES_URL = f"{BASE_URL}/resources/jsf/match/myNextMatches.xhtml?type=Club"
LAST_MATCHES_URL = f"{BASE_URL}/resources/jsf/match/myLastMatches.xhtml?type=Club"

# Our teams — used to match scraped team names
TEAMS = [
    {
        "key":      "under-9s",
        "name":     "Cardiff Allstars Under 9s",
        "keywords": ["allstars", "cardiff allstars"],
        "output":   os.path.join(BASE_DIR, "data", "matches-under-9s.json"),
        "division": "SWWGL U9 Cardiff Division 26/27",
    },
    {
        "key":      "under-12s",
        "name":     "Cardiff Allstars Under 12s",
        "keywords": ["allstars", "cardiff allstars"],
        "output":   os.path.join(BASE_DIR, "data", "matches-under-12s.json"),
        "division": "Cardiff & District U12 Division 26/27",
    },
    {
        "key":      "under-16s",
        "name":     "Cardiff Allstars Under 16s",
        "keywords": ["allstars", "cardiff allstars"],
        "output":   os.path.join(BASE_DIR, "data", "matches-under-16s.json"),
        "division": "Cardiff & District U16 Division 26/27",
    },
    {
        "key":      "first-team",
        "name":     "Cardiff Allstars FC",
        "keywords": ["allstars", "cardiff allstars"],
        "output":   os.path.join(BASE_DIR, "data", "matches-first-team.json"),
        "division": "Cardiff & District Div 1 26/27",
    },
    {
        "key":      "reserves",
        "name":     "Cardiff Allstars FC Reserves",
        "keywords": ["allstars", "cardiff allstars"],
        "output":   os.path.join(BASE_DIR, "data", "matches-reserves.json"),
        "division": "Cardiff & District Div 2 26/27",
    },
]


def is_allstars(text):
    return "allstars" in text.lower()


def save_debug(page, filename="debug-matches.html"):
    html = page.content()
    path = os.path.join(BASE_DIR, "data", filename)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Debug saved to data/{filename}", file=sys.stderr)


def parse_match_row(cells):
    """
    Parse a match row from the FAW Comet match list.
    Returns a dict or None if the row can't be parsed.
    Tries to extract: date, time, home, away, score, match_url, is_live
    """
    texts = [c.inner_text().strip() for c in cells]
    if len(texts) < 3:
        return None

    full = " ".join(texts)

    # Skip rows with no team names
    if not any(len(t) > 5 for t in texts):
        return None

    # Detect live match
    is_live = any(t.lower() in ("live", "playing", "in progress") for t in texts)

    # Score pattern: "2:1" or "2 - 1" or "2:1 AET"
    score_match = re.search(r"(\d+)\s*[:\-]\s*(\d+)", full)

    # Date pattern: dd/mm/yyyy or dd.mm.yyyy
    date_str = ""
    time_str = ""
    for t in texts:
        dm = re.search(r"(\d{1,2}[./]\d{1,2}[./]\d{2,4})", t)
        if dm and not date_str:
            date_str = dm.group(1)
        tm = re.match(r"^(\d{1,2}:\d{2})$", t.strip())
        if tm and not time_str:
            time_str = tm.group(1)

    # Team names: cells that look like team names (not numbers, dates, scores)
    team_names = []
    for t in texts:
        if (
            len(t) > 4
            and not re.match(r"^\d{1,2}[./:\-]\d", t)
            and not re.match(r"^\d+$", t)
            and t.lower() not in ("live", "playing", "home", "away", "vs", "v")
        ):
            team_names.append(t)

    home = team_names[0] if len(team_names) > 0 else ""
    away = team_names[1] if len(team_names) > 1 else ""

    return {
        "date":       date_str,
        "time":       time_str,
        "home":       home,
        "away":       away,
        "home_score": int(score_match.group(1)) if score_match else None,
        "away_score": int(score_match.group(2)) if score_match else None,
        "is_live":    is_live,
        "has_score":  score_match is not None,
    }


def scrape_page(page, url, label):
    """
    Load a page and return all match rows involving Cardiff Allstars.
    Returns list of match dicts.
    """
    print(f"  Loading {label}: {url}")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
    except Exception as e:
        print(f"  ERROR loading {url}: {e}", file=sys.stderr)
        return []

    if "login" in page.url or "sso" in page.url:
        print("  ERROR: Session expired — re-run setup_session.py", file=sys.stderr)
        return []

    # Try multiple selectors for match rows
    SELECTORS = [
        ".ui-datatable-data tr",
        "table tbody tr",
        ".match-list tr",
        "tr",
    ]

    rows = []
    for sel in SELECTORS:
        try:
            page.wait_for_selector(sel, timeout=10000)
            all_rows = page.query_selector_all(sel)
            if len(all_rows) > 1:
                rows = all_rows
                print(f"  Found {len(rows)} rows with: {sel}")
                break
        except PlaywrightTimeoutError:
            continue

    if not rows:
        print(f"  WARNING: No rows found on {label} page", file=sys.stderr)
        save_debug(page, f"debug-{label.lower().replace(' ', '-')}.html")
        return []

    matches = []
    for row in rows:
        cells = row.query_selector_all("td")
        if len(cells) < 3:
            continue

        # Only rows involving Cardiff Allstars
        row_text = row.inner_text()
        if not is_allstars(row_text):
            continue

        match = parse_match_row(cells)
        if not match:
            continue

        # Get link to match detail page
        link = row.query_selector("a[href*='match']")
        if link:
            href = link.get_attribute("href") or ""
            match["match_url"] = (BASE_URL + href) if href.startswith("/") else href

        matches.append(match)

    print(f"  Found {len(matches)} Cardiff Allstars matches on {label} page")
    return matches


def scrape_goals(page, match_url):
    """Visit a match detail page and scrape goalscorer events."""
    goals = []
    if not match_url:
        return goals
    try:
        print(f"    Fetching goals from: {match_url}")
        page.goto(match_url, wait_until="domcontentloaded", timeout=20000)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass

        # Try various selectors for match events
        for sel in [
            ".match-event tr", ".event-row", "[class*='goal']",
            ".ui-datatable-data tr", "table tr"
        ]:
            rows = page.query_selector_all(sel)
            for row in rows:
                text = row.inner_text().strip()
                # Look for minute pattern + player name
                m = re.search(r"(\d{1,3})['\u2019+]?\s*(.{3,40})", text)
                if m:
                    minute = int(m.group(1))
                    player = m.group(2).strip(" -•|:")
                    if minute <= 120 and len(player) > 2:
                        goals.append({"player": player, "minute": minute})
            if goals:
                break

    except Exception as e:
        print(f"    Warning: Could not get goals: {e}", file=sys.stderr)
    return goals


def main():
    session_b64 = os.environ.get("COMET_SESSION")
    if not session_b64:
        print("ERROR: COMET_SESSION not set.", file=sys.stderr)
        sys.exit(1)

    try:
        session_json = base64.b64decode(session_b64.encode()).decode()
        json.loads(session_json)
    except Exception as e:
        print(f"ERROR: Bad COMET_SESSION - {e}", file=sys.stderr)
        sys.exit(1)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tmp:
        tmp.write(session_json)
        session_path = tmp.name

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

            # Scrape upcoming matches (one page for all teams)
            upcoming = scrape_page(page, NEXT_MATCHES_URL, "Next Matches")

            # Scrape last results (try the last matches URL)
            last = scrape_page(page, LAST_MATCHES_URL, "Last Matches")

            # Also check for live matches on the next matches page
            # (live matches typically appear at the top of the upcoming list)
            live_matches = [m for m in upcoming if m.get("is_live")]
            upcoming_only = [m for m in upcoming if not m.get("is_live") and not m.get("has_score")]
            results = [m for m in upcoming if m.get("has_score") and not m.get("is_live")]
            results += [m for m in last if m.get("has_score") and not m.get("is_live")]

            # Fetch goals for the most recent result
            last_result_with_goals = None
            if results:
                r = results[-1]
                match_url = r.get("match_url")
                goals = scrape_goals(page, match_url) if match_url else []
                last_result_with_goals = {**r, "goals": goals}

            # Since all teams share "Allstars" branding, we write a single
            # combined JSON file plus one per team where we can distinguish
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            # Write a combined all-teams file
            combined = {
                "updated":  now,
                "live":     [clean(m) for m in live_matches],
                "upcoming": [clean(m) for m in upcoming_only],
                "results":  [clean(m) for m in results],
                "last_result_with_goals": clean(last_result_with_goals) if last_result_with_goals else None,
            }

            os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)
            combined_path = os.path.join(BASE_DIR, "data", "matches-all.json")
            with open(combined_path, "w", encoding="utf-8") as f:
                json.dump(combined, f, indent=2, ensure_ascii=False)
            print(f"\nWritten combined data: {len(live_matches)} live, "
                  f"{len(upcoming_only)} upcoming, {len(results)} results")

            # Also write individual team files based on best-guess matching
            # (upcoming list has all teams — we pick next match per team by searching team name)
            for team in TEAMS:
                team_upcoming = [m for m in upcoming_only if
                                 is_allstars(m.get("home","")) or is_allstars(m.get("away",""))]
                team_results  = [m for m in results if
                                 is_allstars(m.get("home","")) or is_allstars(m.get("away",""))]
                team_live     = [m for m in live_matches if
                                 is_allstars(m.get("home","")) or is_allstars(m.get("away",""))]

                team_out = {
                    "updated":    now,
                    "team":       team["name"],
                    "division":   team["division"],
                    "live":       clean(team_live[0]) if team_live else None,
                    "next_match": clean(team_upcoming[0]) if team_upcoming else None,
                    "last_result": {
                        **clean(team_results[-1]),
                        "goals": last_result_with_goals.get("goals", []) if last_result_with_goals else []
                    } if team_results else None,
                }
                with open(team["output"], "w", encoding="utf-8") as f:
                    json.dump(team_out, f, indent=2, ensure_ascii=False)
                print(f"  Written: {os.path.basename(team['output'])}")

            browser.close()
    finally:
        os.unlink(session_path)

    print("\nMatch data updated successfully.")


def clean(m):
    """Remove internal fields before saving."""
    if m is None:
        return None
    return {k: v for k, v in m.items() if k not in ("is_live", "has_score", "match_url")}


if __name__ == "__main__":
    main()
