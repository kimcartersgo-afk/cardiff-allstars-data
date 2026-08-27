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

IS_TIME  = re.compile(r'^\d{1,2}:\d{2}$')
IS_SCORE = re.compile(r'^(\d{1,3})\s*[:\-]\s*(\d{1,3})$')
IS_DATE  = re.compile(r'\d{1,2}[./]\d{1,2}[./]\d{2,4}')


def is_allstars(text):
    return "allstars" in text.lower()


def parse_row(cells):
    texts = [c.inner_text().strip() for c in cells]
    if len(texts) < 3:
        return None

    time_str = date_str = ""
    score = None
    teams = []

    for t in texts:
        if IS_TIME.match(t):
            time_str = t
            continue
        dm = IS_DATE.search(t)
        if dm and not date_str:
            date_str = dm.group(0)
            continue
        sm = IS_SCORE.match(t)
        if sm and score is None:
            h, a = int(sm.group(1)), int(sm.group(2))
            if not (a in (0, 15, 30, 45) and h < 24):
                score = (h, a)
            continue
        if (len(t) > 3 and not re.match(r'^\d+$', t)
                and t.lower() not in ("home","away","vs","v","live","ft","aet")):
            teams.append(t)

    if not teams:
        return None

    full = " ".join(texts).lower()
    is_live = any(w in full for w in ("live", "playing", "in progress"))
    link = None
    for cell in cells:
        a = cell.query_selector("a[href*='match']")
        if a:
            href = a.get_attribute("href") or ""
            link = (BASE_URL + href) if href.startswith("/") else href
            break

    return {
        "date": date_str, "time": time_str,
        "home": teams[0] if teams else "",
        "away": teams[1] if len(teams) > 1 else "",
        "home_score": score[0] if score else None,
        "away_score": score[1] if score else None,
        "has_score": score is not None, "is_live": is_live,
        "match_url": link,
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


def scrape_goals(page, match_url):
    goals = []
    if not match_url:
        return goals
    try:
        page.goto(match_url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_load_state("networkidle", timeout=6000)
        for sel in [".match-event tr", ".event-row", ".ui-datatable-data tr", "table tr"]:
            rows = page.query_selector_all(sel)
            for row in rows:
                text = row.inner_text().strip()
                m = re.search(r"(\d{1,3})['\u2019+]?\s+(.{3,40})", text)
                if m:
                    minute, player = int(m.group(1)), m.group(2).strip(" -•|:")
                    if minute <= 120 and len(player) > 2:
                        goals.append({"player": player, "minute": minute})
            if goals:
                break
    except Exception as e:
        print(f"    Goals error: {e}", file=sys.stderr)
    return goals


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
        subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=BASE_DIR)
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
                        goals = scrape_goals(page, lm.get("match_url"))
                        live_with_goals = {**clean(lm), "goals": goals, "status": "live"}
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
