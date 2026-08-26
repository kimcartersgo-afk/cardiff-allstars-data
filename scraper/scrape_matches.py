"""
Cardiff Allstars FC - FAW Comet Match Day Scraper
Scrapes upcoming fixtures, last result and goalscorers for all 5 teams.
Runs every 5 minutes via GitHub Actions.

Requires env var:
  COMET_SESSION  - base64-encoded Playwright session JSON (from setup_session.py)
"""
import base64, json, os, re, sys, tempfile
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE_DIR = os.path.join(os.path.dirname(__file__), "..")

COMPETITIONS = [
    {
        "name":    "Under 9s",
        "url":     "https://comet.faw.cymru/resources/jsf/competition/index.xhtml?id=106365127",
        "output":  os.path.join(BASE_DIR, "data", "matches-under-9s.json"),
        "team":    "Cardiff Allstars Under 9s",
        "division":"SWWGL U9 Cardiff Division 26/27",
    },
    {
        "name":    "Under 12s",
        "url":     "https://comet.faw.cymru/resources/jsf/competition/index.xhtml?id=108490756",
        "output":  os.path.join(BASE_DIR, "data", "matches-under-12s.json"),
        "team":    "Cardiff Allstars Under 12s",
        "division":"Cardiff & District U12 Division 26/27",
    },
    {
        "name":    "Under 16s",
        "url":     "https://comet.faw.cymru/resources/jsf/competition/index.xhtml?id=107459637",
        "output":  os.path.join(BASE_DIR, "data", "matches-under-16s.json"),
        "team":    "Cardiff Allstars Under 16s",
        "division":"Cardiff & District U16 Division 26/27",
    },
    {
        "name":    "First Team",
        "url":     "https://comet.faw.cymru/resources/jsf/competition/index.xhtml?id=107459586",
        "output":  os.path.join(BASE_DIR, "data", "matches-first-team.json"),
        "team":    "Cardiff Allstars FC",
        "division":"Cardiff & District Div 1 26/27",
    },
    {
        "name":    "Reserves",
        "url":     "https://comet.faw.cymru/resources/jsf/competition/index.xhtml?id=107459589",
        "output":  os.path.join(BASE_DIR, "data", "matches-reserves.json"),
        "team":    "Cardiff Allstars FC Reserves",
        "division":"Cardiff & District Div 2 26/27",
    },
]


def normalise_team(name):
    """Lowercase and strip for fuzzy matching."""
    return name.lower().strip()


def is_our_team(cell_text, our_team):
    """Check if a cell text matches our team name."""
    return "allstars" in normalise_team(cell_text)


def scrape_goals(page, match_url):
    """Visit a match detail page and scrape goalscorer events."""
    goals = []
    try:
        page.goto(match_url, wait_until="domcontentloaded", timeout=20000)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass

        # Goal events: look for rows containing goal icons or "Goal" text
        # FAW Comet uses event rows with icons like pi-circle or goal images
        for sel in [
            ".match-event", ".event-row", "tr.goal", ".goal-event",
            "[class*='goal']", ".ui-datatable-data tr"
        ]:
            rows = page.query_selector_all(sel)
            if rows:
                for row in rows:
                    text = row.inner_text().strip()
                    # Look for minute pattern like "23'" or "23" near a name
                    minute_match = re.search(r"(\d{1,3})['\u2019\+]?", text)
                    if minute_match:
                        minute = int(minute_match.group(1))
                        # Remove minute from text to get name
                        name = re.sub(r"\d{1,3}['\u2019\+]?\d*", "", text).strip()
                        name = re.sub(r"\s+", " ", name).strip(" -•|")
                        if name and len(name) > 2:
                            goals.append({"player": name, "minute": minute})
                break
    except Exception as e:
        print(f"    Warning: Could not scrape goals from {match_url}: {e}", file=sys.stderr)
    return goals


def scrape_matches(page, comp):
    """
    Scrape match data from a competition page.
    The Matches tab is the default active tab — no click needed.
    Returns dict with live, next_match, last_result keys.
    """
    our_team = comp["team"]
    print(f"  Loading: {comp['url']}")

    page.goto(comp["url"], wait_until="domcontentloaded", timeout=30000)
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass

    if "login" in page.url or "sso" in page.url:
        print("  ERROR: Session expired.", file=sys.stderr)
        return None

    # Save debug HTML if needed
    def save_debug():
        html = page.content()
        path = os.path.join(BASE_DIR, "data", "debug-matches.html")
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        print("  Debug HTML saved to data/debug-matches.html", file=sys.stderr)

    # Wait for match rows — try multiple selectors
    match_rows = []
    MATCH_SELECTORS = [
        ".a-competition-matches tbody tr",
        ".match-list tr",
        ".ui-datatable-data tr",
        "table tbody tr",
    ]
    for sel in MATCH_SELECTORS:
        try:
            page.wait_for_selector(sel, timeout=15000)
            rows = page.query_selector_all(sel)
            if rows:
                match_rows = rows
                print(f"  Found {len(rows)} rows with selector: {sel}")
                break
        except PlaywrightTimeoutError:
            continue

    if not match_rows:
        print(f"  WARNING: No match rows found for {comp['name']}", file=sys.stderr)
        save_debug()
        return None

    live_match = None
    next_match = None
    last_result = None
    all_results = []

    for row in match_rows:
        cells = row.query_selector_all("td")
        if len(cells) < 3:
            continue

        texts = [c.inner_text().strip() for c in cells]
        full_text = " ".join(texts).lower()

        # Skip header rows
        if not any(char.isdigit() or "allstars" in full_text for char in full_text):
            continue

        # Check if this row involves our team
        involves_us = any("allstars" in t.lower() for t in texts)
        if not involves_us:
            continue

        # Try to find score pattern like "2:1", "2 : 1", "2-1"
        score_pattern = re.search(r"(\d+)\s*[:\-]\s*(\d+)", " ".join(texts))
        is_live = "live" in full_text or "playing" in full_text

        # Try to find match date
        date_str = ""
        for t in texts:
            date_match = re.search(r"(\d{1,2})[./\-](\d{1,2})[./\-](\d{2,4})", t)
            if date_match:
                date_str = t.strip()
                break

        # Try to find home/away teams (usually 2nd and 3rd or 3rd and 4th columns)
        home_team = ""
        away_team = ""
        for i, t in enumerate(texts):
            if len(t) > 3 and not re.match(r"^\d", t) and ":" not in t:
                if not home_team:
                    home_team = t
                elif not away_team:
                    away_team = t
                    break

        # Find match detail link
        match_url = None
        for cell in cells:
            link = cell.query_selector("a[href*='match']")
            if link:
                href = link.get_attribute("href")
                if href:
                    if href.startswith("/"):
                        match_url = "https://comet.faw.cymru" + href
                    elif href.startswith("http"):
                        match_url = href
                break

        # Determine home/away score
        if score_pattern:
            home_score = int(score_pattern.group(1))
            away_score = int(score_pattern.group(2))
            result = {
                "date":       date_str,
                "home":       home_team,
                "away":       away_team,
                "home_score": home_score,
                "away_score": away_score,
                "goals":      [],
                "match_url":  match_url,
            }
            if is_live:
                live_match = result.copy()
                live_match["status"] = "live"
            else:
                all_results.append(result)
        else:
            # Upcoming fixture
            if next_match is None:
                # Try to find time
                time_str = ""
                for t in texts:
                    if re.match(r"^\d{1,2}:\d{2}$", t.strip()):
                        time_str = t.strip()
                        break
                next_match = {
                    "date":      date_str,
                    "time":      time_str,
                    "home":      home_team,
                    "away":      away_team,
                    "match_url": match_url,
                }

    # Get the most recent completed result
    if all_results:
        last_result = all_results[-1]  # Last in list = most recent
        # Fetch goalscorers if we have a match URL
        if last_result.get("match_url"):
            print(f"  Fetching goalscorers from match page...")
            last_result["goals"] = scrape_goals(page, last_result["match_url"])
            # Navigate back
            page.goto(comp["url"], wait_until="domcontentloaded", timeout=20000)

    # Clean up match_url from output (internal use only)
    if last_result:
        last_result.pop("match_url", None)
    if next_match:
        next_match.pop("match_url", None)
    if live_match:
        live_match.pop("match_url", None)

    return {
        "live":        live_match,
        "next_match":  next_match,
        "last_result": last_result,
    }


def main():
    session_b64 = os.environ.get("COMET_SESSION")
    if not session_b64:
        print("ERROR: COMET_SESSION not set.", file=sys.stderr)
        sys.exit(1)

    try:
        session_json = base64.b64decode(session_b64.encode()).decode()
        json.loads(session_json)
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
                print(f"\n[{comp['name']}]")
                try:
                    result = scrape_matches(page, comp)
                    if result is None:
                        errors.append(comp["name"])
                        continue

                    output = {
                        "updated":  datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "team":     comp["team"],
                        "division": comp["division"],
                        **result,
                    }
                    os.makedirs(os.path.dirname(os.path.abspath(comp["output"])), exist_ok=True)
                    with open(comp["output"], "w", encoding="utf-8") as f:
                        json.dump(output, f, indent=2, ensure_ascii=False)
                    print(f"  OK - written to {os.path.basename(comp['output'])}")

                except Exception as e:
                    print(f"  ERROR: {e}", file=sys.stderr)
                    errors.append(comp["name"])

            browser.close()
    finally:
        os.unlink(session_path)

    if errors:
        print(f"\nErrors on: {', '.join(errors)}", file=sys.stderr)
        sys.exit(1)
    print("\nAll match data updated successfully.")


if __name__ == "__main__":
    main()
