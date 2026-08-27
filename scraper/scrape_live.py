"""
Cardiff Allstars FC - Live Match Scraper
Runs continuously (every 10 seconds) for a set duration.
Only pushes to git when match data has actually changed.
Designed to run inside a GitHub Actions job on match days.

Requires env vars:
  COMET_SESSION      - base64 Playwright session
  DURATION_MINUTES   - how long to run (default 90)
"""
import base64, json, os, re, subprocess, sys, tempfile, time
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE_DIR   = os.path.join(os.path.dirname(__file__), "..")
BASE_URL   = "https://comet.faw.cymru"
NEXT_URL   = f"{BASE_URL}/resources/jsf/match/myNextMatches.xhtml?type=Club"
INTERVAL   = 10          # seconds between scrapes
PUSH_EVERY = 6           # push to git every N scrapes even if unchanged (keep alive)

COMPETITIONS = [
    {"name": "Under 9s",   "key": "under-9s",   "team": "Cardiff Allstars Under 9s",       "division": "SWWGL U9 Cardiff Division 26/27",        "url": f"{BASE_URL}/resources/jsf/competition/index.xhtml?id=106365127"},
    {"name": "Under 12s",  "key": "under-12s",  "team": "Cardiff Allstars Under 12s",      "division": "Cardiff & District U12 Division 26/27",  "url": f"{BASE_URL}/resources/jsf/competition/index.xhtml?id=108490756"},
    {"name": "Under 16s",  "key": "under-16s",  "team": "Cardiff Allstars Under 16s",      "division": "Cardiff & District U16 Division 26/27",  "url": f"{BASE_URL}/resources/jsf/competition/index.xhtml?id=107459637"},
    {"name": "First Team", "key": "first-team", "team": "Cardiff Allstars FC",             "division": "Cardiff & District Div 1 26/27",         "url": f"{BASE_URL}/resources/jsf/competition/index.xhtml?id=107459586"},
    {"name": "Reserves",   "key": "reserves",   "team": "Cardiff Allstars FC Reserves",    "division": "Cardiff & District Div 2 26/27",         "url": f"{BASE_URL}/resources/jsf/competition/index.xhtml?id=107459589"},
]

def parse_row(texts):
    """Parse using exact FAW Comet column indices."""
    if len(texts) < 13:
        return None

    raw_id    = texts[0].replace("ID", "").strip()
    match_url = (f"{BASE_URL}/resources/jsf/match/index.xhtml?id={raw_id}"
                 if raw_id.isdigit() else None)

    dt_parts = texts[1].split(" ")
    date_str = dt_parts[0] if dt_parts else ""
    time_str = dt_parts[1] if len(dt_parts) > 1 else ""

    venue       = texts[7] if len(texts) > 7 else ""
    match_str   = texts[9] if len(texts) > 9 else ""
    score_str   = texts[11] if len(texts) > 11 else ""
    status      = texts[12].upper().strip() if len(texts) > 12 else ""

    home = away = our_team = ""
    if " - " in match_str:
        parts    = match_str.split(" - ", 1)
        home     = parts[0].strip()
        away     = parts[1].strip()
        our_team = home if is_allstars(home) else away

    home_score = away_score = None
    has_score  = False
    if score_str and score_str != "-:-":
        sm = re.match(r"^(\d+)\s*[:\-]\s*(\d+)$", score_str)
        if sm:
            home_score = int(sm.group(1))
            away_score = int(sm.group(2))
            has_score  = True

    is_live      = status in ("LIVE", "IN PROGRESS", "PLAYING", "IN_PROGRESS")
    is_postponed = status in ("POSTPONED", "CANCELLED", "ABANDONED")

    if not home:
        return None

    return {
        "date": date_str, "time": time_str,
        "home": home, "away": away, "our_team": our_team,
        "venue": venue,
        "home_score": home_score, "away_score": away_score,
        "has_score": has_score, "is_live": is_live,
        "is_postponed": is_postponed, "status": status,
        "match_url": match_url,
    }


def scrape_live_matches(page):
    """Scrape only live match data from myNextMatches — fast, <5s per call."""
    try:
        page.goto(NEXT_URL, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass

    if "login" in page.url:
        return None   # session expired

    live_matches = []
    next_matches = []

    for sel in [".ui-datatable-data tr", "table tbody tr"]:
        try:
            page.wait_for_selector(sel, timeout=8000)
            rows = page.query_selector_all(sel)
            for row in rows:
                if not is_allstars(row.inner_text()):
                    continue
                m = parse_row(row.query_selector_all("td"))
                if m:
                    if m["is_live"]:
                        live_matches.append(m)
                    elif not m["has_score"]:
                        next_matches.append(m)
            break
        except PlaywrightTimeoutError:
            continue

    return {"live": live_matches, "upcoming": next_matches}


def scrape_match_details(page, match_url, is_home):
    """Visit a match detail page and return (goals, minute)."""
    goals = []
    minute = None
    if not match_url:
        return goals, minute
    try:
        page.goto(match_url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_load_state("networkidle", timeout=6000)
        
        # 1. Scrape Goals
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
                    g_min = int(m.group(1))
                    player = m.group(2).strip(" -•|:")
                    if g_min <= 120 and len(player) > 2:
                        goals.append({"player": player, "minute": g_min})
            if goals:
                break
                
        # 2. Attempt to scrape live minute clock
        for sel in [".match-minute", ".live-time", ".match-time", ".clock", ".status-live"]:
            el = page.query_selector(sel)
            if el:
                clock_text = el.inner_text().strip()
                m = re.search(r"(\d{1,3})['\u2019]?", clock_text)
                if m:
                    minute = int(m.group(1))
                    break

    except Exception as e:
        print(f"    Match details error: {e}", file=sys.stderr)
    return goals, minute


def clean(m):
    if m is None:
        return None
    return {k: v for k, v in m.items() if k not in ("is_live", "has_score", "match_url")}


def git_push(message):
    subprocess.run(["git", "config", "user.name",  "github-actions[bot]"], cwd=BASE_DIR)
    subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], cwd=BASE_DIR)
    subprocess.run(["git", "add", "data/"], cwd=BASE_DIR)
    diff = subprocess.run(["git", "diff", "--staged", "--quiet"], cwd=BASE_DIR)
    if diff.returncode != 0:
        subprocess.run(["git", "commit", "-m", message], cwd=BASE_DIR)
        subprocess.run(["git", "pull", "--rebase", "-X", "theirs", "origin", "main"], cwd=BASE_DIR)
        subprocess.run(["git", "push"], cwd=BASE_DIR)
        return True
    return False


def load_existing(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def main():
    session_b64 = os.environ.get("COMET_SESSION", "")
    duration_minutes = int(os.environ.get("DURATION_MINUTES", "90"))

    try:
        session_json = base64.b64decode(session_b64.encode()).decode()
        json.loads(session_json)
    except Exception as e:
        print(f"ERROR: Bad COMET_SESSION: {e}", file=sys.stderr)
        sys.exit(1)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tmp:
        tmp.write(session_json)
        session_path = tmp.name

    end_time   = time.time() + (duration_minutes * 60)
    iteration  = 0
    last_data  = {}   # key → JSON string for change detection

    print(f"🔴 Live match mode — running for {duration_minutes} minutes, "
          f"scraping every {INTERVAL}s")

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

            # Load last results once at the start (slow, from competition pages)
            print("\n[Init] Loading last results...")
            last_results = {}
            for comp in COMPETITIONS:
                existing = load_existing(
                    os.path.join(BASE_DIR, "data", f"matches-{comp['key']}.json")
                )
                last_results[comp["key"]] = existing.get("last_result")

            os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)
            now_str = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            while time.time() < end_time:
                iteration += 1
                tick = datetime.now(timezone.utc).strftime("%H:%M:%S")
                print(f"\n[{tick}] Iteration {iteration}")

                result = scrape_live_matches(page)

                if result is None:
                    print("  Session expired — stopping.", file=sys.stderr)
                    break

                live_all     = result["live"]
                upcoming_all = result["upcoming"]

                changed = False
                for comp in COMPETITIONS:
                    team_live     = [m for m in live_all     if is_allstars(m["home"]) or is_allstars(m["away"])]
                    team_upcoming = [m for m in upcoming_all if is_allstars(m["home"]) or is_allstars(m["away"])]

                    # Fetch goals for live match if there's one
                    live_with_goals = None
                    if team_live:
                        lm = team_live[0]
                        is_home = is_allstars(lm["home"])
                        goals, current_minute = scrape_match_details(page, lm.get("match_url"), is_home)
                        live_with_goals = {**clean(lm), "goals": goals, "minute": current_minute, "status": "live"}
                        print(f"  🔴 LIVE [{comp['name']}]: "
                              f"{lm['home']} {lm['home_score']}–{lm['away_score']} {lm['away']} "
                              f"| {len(goals)} goals")

                    output = {
                        "updated":    now_str(),
                        "team":       comp["team"],
                        "division":   comp["division"],
                        "live":       live_with_goals,
                        "next_match": clean(team_upcoming[0]) if team_upcoming else None,
                        "last_result": last_results.get(comp["key"]),
                    }

                    path    = os.path.join(BASE_DIR, "data", f"matches-{comp['key']}.json")
                    new_str = json.dumps(output, sort_keys=True)

                    if new_str != last_data.get(comp["key"], ""):
                        last_data[comp["key"]] = new_str
                        with open(path, "w", encoding="utf-8") as f:
                            json.dump(output, f, indent=2, ensure_ascii=False)
                        changed = True

                # Push if data changed OR every PUSH_EVERY iterations as keepalive
                if changed or iteration % PUSH_EVERY == 0:
                    pushed = git_push(
                        f"live: {now_str()} — iter {iteration}"
                        + (" [live]" if live_all else "")
                    )
                    print(f"  {'✅ Pushed' if pushed else '⏭  No changes to push'}")

                # Sleep until next interval
                elapsed = time.time() % INTERVAL
                sleep_for = INTERVAL - elapsed
                print(f"  Sleeping {sleep_for:.1f}s...")
                time.sleep(sleep_for)

            print(f"\n🏁 Live match mode finished after {iteration} iterations.")
            browser.close()
    finally:
        os.unlink(session_path)


if __name__ == "__main__":
    main()
