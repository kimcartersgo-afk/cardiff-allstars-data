"""
Cardiff Allstars FC - FAW Comet Match Day Scraper (v3)
- Uses myNextMatches.xhtml?type=Club for upcoming fixtures and live scores
- Falls back to competition pages for last results + goalscorers
- Fixes time vs score detection (HH:MM is never a score)

Requires env var:
  COMET_SESSION  - base64-encoded Playwright session JSON (from setup_session.py)
"""
import base64, json, os, re, sys, tempfile
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
BASE_URL = "https://comet.faw.cymru"
NEXT_MATCHES_URL = f"{BASE_URL}/resources/jsf/match/myNextMatches.xhtml?type=Club"

COMPETITIONS = [
    {
        "name":     "Under 9s",
        "url":      f"{BASE_URL}/resources/jsf/competition/index.xhtml?id=106365127",
        "output":   os.path.join(BASE_DIR, "data", "matches-under-9s.json"),
        "team":     "Cardiff Allstars Under 9s",
        "division": "SWWGL U9 Cardiff Division 26/27",
    },
    {
        "name":     "Under 12s",
        "url":      f"{BASE_URL}/resources/jsf/competition/index.xhtml?id=108490756",
        "output":   os.path.join(BASE_DIR, "data", "matches-under-12s.json"),
        "team":     "Cardiff Allstars Under 12s",
        "division": "Cardiff & District U12 Division 26/27",
    },
    {
        "name":     "Under 16s",
        "url":      f"{BASE_URL}/resources/jsf/competition/index.xhtml?id=107459637",
        "output":   os.path.join(BASE_DIR, "data", "matches-under-16s.json"),
        "team":     "Cardiff Allstars Under 16s",
        "division": "Cardiff & District U16 Division 26/27",
    },
    {
        "name":     "First Team",
        "url":      f"{BASE_URL}/resources/jsf/competition/index.xhtml?id=107459586",
        "output":   os.path.join(BASE_DIR, "data", "matches-first-team.json"),
        "team":     "Cardiff Allstars FC",
        "division": "Cardiff & District Div 1 26/27",
    },
    {
        "name":     "Reserves",
        "url":      f"{BASE_URL}/resources/jsf/competition/index.xhtml?id=107459589",
        "output":   os.path.join(BASE_DIR, "data", "matches-reserves.json"),
        "team":     "Cardiff Allstars FC Reserves",
        "division": "Cardiff & District Div 2 26/27",
    },
]

IS_TIME = re.compile(r'^\d{1,2}:\d{2}$')          # e.g. 10:30 or 09:00
IS_SCORE = re.compile(r'^(\d{1,3})\s*[:\-]\s*(\d{1,3})$')  # e.g. 2:1 or 0-0
IS_DATE = re.compile(r'\d{1,2}[./]\d{1,2}[./]\d{2,4}')


def is_allstars(text):
    return "allstars" in text.lower()


def save_debug(page, filename):
    html = page.content()
    path = os.path.join(BASE_DIR, "data", filename)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Debug saved → data/{filename}", file=sys.stderr)


def parse_match_row(cells):
    """
    Parse a single match table row into a structured dict.
    Key fix: cells that are exactly HH:MM are kick-off times, NOT scores.
    """
    texts = [c.inner_text().strip() for c in cells]
    if len(texts) < 3:
        return None

    # Classify each cell
    time_str  = ""
    date_str  = ""
    score     = None
    team_names = []

    for t in texts:
        if IS_TIME.match(t):
            time_str = t          # kick-off time — not a score
            continue

        dm = IS_DATE.search(t)
        if dm and not date_str:
            date_str = dm.group(0)
            continue

        sm = IS_SCORE.match(t)
        if sm and score is None:
            h, a = int(sm.group(1)), int(sm.group(2))
            # Extra guard: real scores are unlikely to have minute values (xx:00, xx:30)
            # A time like "1:00" or "2:30" could slip through — reject if it looks like H:MM
            if not (a in (0, 15, 30, 45) and h < 24):
                score = (h, a)
            continue

        # Likely a team name if long enough and not a number/status
        if (len(t) > 3
                and not re.match(r'^\d+$', t)
                and t.lower() not in ("home", "away", "vs", "v", "live", "ft", "aet")):
            team_names.append(t)

    if not team_names:
        return None

    home = team_names[0] if len(team_names) > 0 else ""
    away = team_names[1] if len(team_names) > 1 else ""

    # Determine status
    full = " ".join(texts).lower()
    is_live = any(w in full for w in ("live", "playing", "in progress"))

    # Find match detail link
    match_url = None
    for cell in cells:
        link = cell.query_selector("a[href*='match']")
        if link:
            href = link.get_attribute("href") or ""
            match_url = (BASE_URL + href) if href.startswith("/") else href
            break

    return {
        "date":       date_str,
        "time":       time_str,
        "home":       home,
        "away":       away,
        "home_score": score[0] if score else None,
        "away_score": score[1] if score else None,
        "has_score":  score is not None,
        "is_live":    is_live,
        "match_url":  match_url,
    }


def scrape_next_matches(page):
    """Load myNextMatches.xhtml and return all Cardiff Allstars rows."""
    print(f"  Loading next matches: {NEXT_MATCHES_URL}")
    page.goto(NEXT_MATCHES_URL, wait_until="domcontentloaded", timeout=30000)
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass

    if "login" in page.url or "sso" in page.url:
        print("  ERROR: Session expired.", file=sys.stderr)
        return []

    rows = []
    for sel in [".ui-datatable-data tr", "table tbody tr", "tr"]:
        try:
            page.wait_for_selector(sel, timeout=10000)
            found = page.query_selector_all(sel)
            if len(found) > 1:
                rows = found
                print(f"  Found {len(rows)} rows ({sel})")
                break
        except PlaywrightTimeoutError:
            continue

    if not rows:
        save_debug(page, "debug-next-matches.html")
        return []

    # Save debug HTML for inspection
    save_debug(page, "debug-next-matches.html")

    matches = []
    for row in rows:
        if not is_allstars(row.inner_text()):
            continue
        cells = row.query_selector_all("td")
        cell_texts = [c.inner_text().strip() for c in cells]
        print(f"  ROW CELLS: {cell_texts}")
        m = parse_match_row(cells)
        if m:
            print(f"    → home='{m['home']}' away='{m['away']}' date='{m['date']}' time='{m['time']}' score={m['home_score']}:{m['away_score']}")
            matches.append(m)
        else:
            print(f"    → skipped (parse returned None)")

    live     = [m for m in matches if m["is_live"]]
    upcoming = [m for m in matches if not m["is_live"] and not m["has_score"]]
    results  = [m for m in matches if not m["is_live"] and m["has_score"]]

    print(f"  Cardiff Allstars: {len(live)} live, {len(upcoming)} upcoming, {len(results)} completed")
    return matches


def scrape_last_result(page, comp):
    """
    Visit the competition Matches tab and get the most recently completed
    Cardiff Allstars match plus goalscorers.
    """
    print(f"  Getting last result for {comp['name']}...")
    page.goto(comp["url"], wait_until="domcontentloaded", timeout=30000)
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass

    # Matches tab is the DEFAULT active tab — just wait for rows
    last = None
    for sel in [".ui-datatable-data tr", "table tbody tr", "tr"]:
        try:
            page.wait_for_selector(sel, timeout=10000)
            rows = page.query_selector_all(sel)
            results = []
            for row in rows:
                if not is_allstars(row.inner_text()):
                    continue
                cells = row.query_selector_all("td")
                m = parse_match_row(cells)
                if m and m["has_score"] and not m["is_live"]:
                    results.append(m)
            if results:
                last = results[-1]   # most recent completed match
                break
        except PlaywrightTimeoutError:
            continue

    if not last:
        return None

    # Fetch goalscorers from match detail page
    goals = []
    if last.get("match_url"):
        goals = scrape_goals(page, last["match_url"])
        # Navigate back
        page.goto(comp["url"], wait_until="domcontentloaded", timeout=20000)

    last["goals"] = goals
    return last


def scrape_goals(page, match_url):
    """Visit a match detail page and scrape goalscorer events."""
    goals = []
    try:
        print(f"    Fetching goals: {match_url}")
        page.goto(match_url, wait_until="domcontentloaded", timeout=20000)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass

        for sel in [".match-event tr", ".event-row", "[class*='goal']",
                    ".ui-datatable-data tr", "table tr"]:
            rows = page.query_selector_all(sel)
            for row in rows:
                text = row.inner_text().strip()
                m = re.search(r"(\d{1,3})['\u2019+]?\s+(.{3,40})", text)
                if m:
                    minute = int(m.group(1))
                    player = re.sub(r'\s+', ' ', m.group(2)).strip(" -•|:")
                    if minute <= 120 and len(player) > 2:
                        goals.append({"player": player, "minute": minute})
            if goals:
                break
    except Exception as e:
        print(f"    Warning: Could not get goals: {e}", file=sys.stderr)
    return goals


def clean(m):
    if m is None:
        return None
    return {k: v for k, v in m.items() if k not in ("is_live", "has_score", "match_url")}


def main():
    session_b64 = os.environ.get("COMET_SESSION")
    if not session_b64:
        print("ERROR: COMET_SESSION not set.", file=sys.stderr)
        sys.exit(1)

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
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            # ── Step 1: scrape the club-wide next matches page ──────────────
            print("\n[Step 1] Club next matches page")
            all_matches = scrape_next_matches(page)
            live_all     = [m for m in all_matches if m["is_live"]]
            upcoming_all = [m for m in all_matches if not m["is_live"] and not m["has_score"]]

            # ── Step 2: get last result per team from competition pages ──────
            print("\n[Step 2] Last results from competition pages")
            os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)

            for comp in COMPETITIONS:
                print(f"\n[{comp['name']}]")

                # Filter next/live by team
                team_live     = [m for m in live_all
                                 if is_allstars(m["home"]) or is_allstars(m["away"])]
                team_upcoming = [m for m in upcoming_all
                                 if is_allstars(m["home"]) or is_allstars(m["away"])]

                # Last result from competition page
                last = scrape_last_result(page, comp)

                output = {
                    "updated":    now,
                    "team":       comp["team"],
                    "division":   comp["division"],
                    "live":       clean(team_live[0])     if team_live     else None,
                    "next_match": clean(team_upcoming[0]) if team_upcoming else None,
                    "last_result": {
                        **clean(last),
                        "goals": last.get("goals", []),
                    } if last else None,
                }

                with open(comp["output"], "w", encoding="utf-8") as f:
                    json.dump(output, f, indent=2, ensure_ascii=False)

                nxt = output["next_match"]
                lst = output["last_result"]
                print(f"  Next:   {nxt['home']} vs {nxt['away']} @ {nxt.get('time','?')} on {nxt.get('date','?')}"
                      if nxt else "  Next:   none found")
                print(f"  Last:   {lst['home']} {lst['home_score']}–{lst['away_score']} {lst['away']} "
                      f"({len(lst.get('goals',[]))} goals)"
                      if lst else "  Last:   none found")
                print(f"  Written → {os.path.basename(comp['output'])}")

            browser.close()
    finally:
        os.unlink(session_path)

    print("\nAll match data updated successfully.")


if __name__ == "__main__":
    main()
