"""
Cardiff Allstars FC - FAW Comet Match Day Scraper (v4)
Now uses exact column indices from the FAW Comet match table structure:
  [0]  Match ID (e.g. ID108687658)
  [1]  Date + Time (e.g. 05.09.2026 10:00)
  [5]  Competition name
  [7]  Venue
  [9]  Teams (e.g. Cardiff Allstars FC - Tongwynlais AFC Reserves)
  [11] Score (e.g. -:- or 2:1)
  [12] Status (SCHEDULED / POSTPONED / LIVE / PLAYED)

Also clicks "My previous matches" on the same page to get last results.

Requires env var:
  COMET_SESSION  - base64-encoded Playwright session JSON
"""
import base64, json, os, re, sys, tempfile
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE_DIR    = os.path.join(os.path.dirname(__file__), "..")
BASE_URL    = "https://comet.faw.cymru"
MATCHES_URL = f"{BASE_URL}/resources/jsf/match/myNextMatches.xhtml?type=Club"

TEAMS = [
    {
        "key":      "under-9s",
        "name":     "Cardiff Allstars Under 9s",
        "division": "SWWGL U9 Cardiff Division 26/27",
        "output":   os.path.join(BASE_DIR, "data", "matches-under-9s.json"),
        "match_kw": "U9",
    },
    {
        "key":      "under-12s",
        "name":     "Cardiff Allstars Under 12s",
        "division": "Cardiff & District U12 Division 26/27",
        "output":   os.path.join(BASE_DIR, "data", "matches-under-12s.json"),
        "match_kw": "U12",
    },
    {
        "key":      "under-16s",
        "name":     "Cardiff Allstars Under 16s",
        "division": "Cardiff & District U16 Division 26/27",
        "output":   os.path.join(BASE_DIR, "data", "matches-under-16s.json"),
        "match_kw": "U16",
    },
    {
        "key":      "first-team",
        "name":     "Cardiff Allstars FC",
        "division": "Cardiff & District Div 1 26/27",
        "output":   os.path.join(BASE_DIR, "data", "matches-first-team.json"),
        "match_kw": "FIRST",   # special — see is_team_match()
    },
    {
        "key":      "reserves",
        "name":     "Cardiff Allstars FC Reserves",
        "division": "Cardiff & District Div 2 26/27",
        "output":   os.path.join(BASE_DIR, "data", "matches-reserves.json"),
        "match_kw": "Reserves",
    },
]


def is_allstars(text):
    return "allstars" in text.lower()


def is_team_match(match, team):
    """Check if a parsed match belongs to a specific team."""
    kw  = team["match_kw"]
    our = match.get("our_team", "")
    if kw == "FIRST":
        # First team: our side is exactly "Cardiff Allstars FC" (no suffix)
        return our == "Cardiff Allstars FC"
    return kw.lower() in our.lower()


def parse_row(texts):
    """
    Parse a match row using known column indices.
    texts = list of cell inner_text values (at least 13 items).
    """
    if len(texts) < 13:
        return None

    # Col 0: ID → match URL
    raw_id    = texts[0].replace("ID", "").strip()
    match_url = (f"{BASE_URL}/resources/jsf/match/index.xhtml?id={raw_id}"
                 if raw_id.isdigit() else None)

    # Col 1: "DD.MM.YYYY HH:MM"
    dt_parts = texts[1].split(" ")
    date_str = dt_parts[0] if dt_parts else ""
    time_str = dt_parts[1] if len(dt_parts) > 1 else ""

    # Col 5: competition name
    competition = texts[5]

    # Col 7: venue
    venue = texts[7]

    # Col 9: "Home Team - Away Team"
    match_str = texts[9]
    home = away = our_team = ""
    if " - " in match_str:
        parts    = match_str.split(" - ", 1)
        home     = parts[0].strip()
        away     = parts[1].strip()
        our_team = home if is_allstars(home) else away

    # Col 11: score "-:-" or "2:1"
    score_str  = texts[11]
    home_score = away_score = None
    has_score  = False
    if score_str and score_str != "-:-":
        sm = re.match(r"^(\d+)\s*[:\-]\s*(\d+)$", score_str)
        if sm:
            home_score = int(sm.group(1))
            away_score = int(sm.group(2))
            has_score  = True

    # Col 12: status
    status      = texts[12].upper().strip()
    is_live      = status in ("LIVE", "IN PROGRESS", "PLAYING", "IN_PROGRESS")
    is_postponed = status in ("POSTPONED", "CANCELLED", "ABANDONED")

    if not home:
        return None

    return {
        "date":        date_str,
        "time":        time_str,
        "home":        home,
        "away":        away,
        "our_team":    our_team,
        "venue":       venue,
        "competition": competition,
        "home_score":  home_score,
        "away_score":  away_score,
        "has_score":   has_score,
        "is_live":     is_live,
        "is_postponed":is_postponed,
        "status":      status,
        "match_url":   match_url,
    }


def scrape_matches_page(page, click_previous=False):
    """
    Scrape the myNextMatches page. If click_previous=True, click
    the 'My previous matches' menu item first to switch views.
    Returns list of parsed match dicts.
    """
    if not click_previous:
        page.goto(MATCHES_URL, wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
    else:
        # Click the "My previous matches" menu item (PrimeFaces AJAX nav)
        try:
            prev_btn = page.query_selector("#menuform\\:myPreviousMatchesClubId a")
            if prev_btn:
                prev_btn.click()
                page.wait_for_load_state("networkidle", timeout=10000)
            else:
                print("  WARNING: Could not find 'My previous matches' button", file=sys.stderr)
                return []
        except Exception as e:
            print(f"  WARNING: Could not click previous matches: {e}", file=sys.stderr)
            return []

    if "login" in page.url or "sso" in page.url:
        print("  ERROR: Session expired.", file=sys.stderr)
        return []

    # Wait for datatable rows
    rows = []
    for sel in [".ui-datatable-data tr", "table tbody tr"]:
        try:
            page.wait_for_selector(sel, timeout=10000)
            found = page.query_selector_all(sel)
            if len(found) > 1:
                rows = found
                break
        except PlaywrightTimeoutError:
            continue

    if not rows:
        print("  WARNING: No rows found", file=sys.stderr)
        return []

    matches = []
    for row in rows:
        if not is_allstars(row.inner_text()):
            continue
        texts = [c.inner_text().strip() for c in row.query_selector_all("td")]
        m = parse_row(texts)
        if m:
            matches.append(m)

    return matches


def scrape_goals(page, match_url, is_home):
    """Visit a match detail page and return goalscorer list for our team only."""
    goals = []
    if not match_url:
        return goals
    try:
        page.goto(match_url, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_load_state("networkidle", timeout=8000)
        for sel in [".match-event tr", ".event-row", ".ui-datatable-data tr", "table tr"]:
            rows = page.query_selector_all(sel)
            for row in rows:
                cells = row.query_selector_all("td")
                if len(cells) < 2:
                    continue
                # Home events are in the first column, Away events in the last column
                text = cells[0].inner_text() if is_home else cells[len(cells)-1].inner_text()
                text = text.strip()
                m = re.search(r"(\d{1,3})['\u2019+]?\s+(.{3,40})", text)
                if m:
                    minute = int(m.group(1))
                    player = re.sub(r"\s+", " ", m.group(2)).strip(" -•|:")
                    if minute <= 120 and len(player) > 2:
                        goals.append({"player": player, "minute": minute})
            if goals:
                break
    except Exception as e:
        print(f"    Goals error: {e}", file=sys.stderr)
    return goals


def clean(m):
    """Strip internal fields before writing to JSON."""
    if m is None:
        return None
    keep = {"date","time","home","away","venue","competition",
            "home_score","away_score","status","goals"}
    return {k: v for k, v in m.items() if k in keep}


def main():
    session_b64 = os.environ.get("COMET_SESSION", "")
    try:
        session_json = base64.b64decode(session_b64.encode()).decode()
        json.loads(session_json)
    except Exception as e:
        print(f"ERROR: Bad COMET_SESSION: {e}", file=sys.stderr)
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
            now  = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            # ── Step 1: Upcoming + Live matches ─────────────────────────────
            print("\n[Step 1] Scraping upcoming / live matches...")
            upcoming_raw = scrape_matches_page(page, click_previous=False)
            live_matches  = [m for m in upcoming_raw if m["is_live"]]
            upcoming      = [m for m in upcoming_raw if not m["is_live"]
                             and not m["has_score"] and not m["is_postponed"]]

            print(f"  Totals: {len(live_matches)} live, {len(upcoming)} upcoming")

            # ── Step 2: Previous results ─────────────────────────────────────
            print("\n[Step 2] Scraping previous results...")
            previous_raw = scrape_matches_page(page, click_previous=True)
            results = [m for m in previous_raw if m["has_score"] and not m["is_live"]]
            print(f"  Found {len(results)} previous results")

            # ── Step 3: Write per-team JSON ──────────────────────────────────
            print("\n[Step 3] Writing team files...")
            os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)

            for team in TEAMS:
                # Filter upcoming and live to this team
                team_live     = [m for m in live_matches if is_team_match(m, team)]
                team_upcoming = [m for m in upcoming     if is_team_match(m, team)]
                team_results  = [m for m in results      if is_team_match(m, team)]

                # Next match = first upcoming for this team
                next_match = team_upcoming[0] if team_upcoming else None

                # Last result = most recent completed match
                last_result = None
                if team_results:
                    lr = team_results[-1]
                    # Fetch goalscorers
                    if lr.get("match_url"):
                        is_home = is_allstars(lr["home"])
                        lr["goals"] = scrape_goals(page, lr["match_url"], is_home)
                        # Return to matches page for next iteration
                        page.goto(MATCHES_URL, wait_until="domcontentloaded", timeout=20000)
                    else:
                        lr["goals"] = []
                    last_result = lr

                output = {
                    "updated":     now,
                    "team":        team["name"],
                    "division":    team["division"],
                    "live":        clean(team_live[0]) if team_live else None,
                    "next_match":  clean(next_match),
                    "last_result": clean(last_result),
                }

                with open(team["output"], "w", encoding="utf-8") as f:
                    json.dump(output, f, indent=2, ensure_ascii=False)

                nxt = output["next_match"]
                lst = output["last_result"]
                print(f"  [{team['name']}]")
                print(f"    Next:   {nxt['home']} vs {nxt['away']} on {nxt['date']} @ {nxt['time']} ({nxt.get('venue','')})"
                      if nxt else f"    Next:   none")
                print(f"    Last:   {lst['home']} {lst['home_score']}–{lst['away_score']} {lst['away']} ({len(lst.get('goals',[]))} goals)"
                      if lst else f"    Last:   none (no results yet this season)")
                print(f"    Written → {os.path.basename(team['output'])}")

            browser.close()
    finally:
        os.unlink(session_path)

    print("\nAll match data updated successfully.")


if __name__ == "__main__":
    main()
