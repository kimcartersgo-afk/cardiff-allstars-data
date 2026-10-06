import os
from playwright.sync_api import sync_playwright
import re

def is_allstars(name: str) -> bool:
    return "allstars" in name.lower() or "all stars" in name.lower()

def parse_row(texts):
    if len(texts) < 13: return None
    dt_parts = texts[1].split(" ")
    date_str = dt_parts[0] if dt_parts else ""
    time_str = dt_parts[1] if len(dt_parts) > 1 else ""
    competition = texts[5]
    venue = texts[7]
    match_str = texts[9]
    home = away = our_team = ""
    if " - " in match_str:
        parts = match_str.split(" - ", 1)
        home = parts[0].strip()
        away = parts[1].strip()
        our_team = home if is_allstars(home) else away
    score_str = texts[11]
    home_score = away_score = None
    has_score = False
    if score_str and score_str != "-:-":
        sm = re.match(r"^(\d+)\s*[:\-]\s*(\d+)$", score_str)
        if sm:
            home_score = int(sm.group(1))
            away_score = int(sm.group(2))
            has_score = True
    status = texts[12].upper().strip()
    is_live = status in ("LIVE", "IN PROGRESS", "PLAYING", "IN_PROGRESS")
    if not home: return None
    return {"date": date_str, "time": time_str, "home": home, "away": away, "has_score": has_score, "is_live": is_live, "score": score_str, "status": status}

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    html = open("data/debug-previous-matches.html", encoding="utf-8").read()
    page.set_content(html)
    rows = page.query_selector_all(".ui-datatable-data tr")
    results = []
    for row in rows:
        if not is_allstars(row.evaluate("(el) => el.textContent")):
            continue
        cells = row.query_selector_all("td")
        texts = [c.evaluate("(el) => { const clone = el.cloneNode(true); const title = clone.querySelector('.ui-column-title'); if(title) title.remove(); return clone.textContent.trim(); }") for c in cells]
        m = parse_row(texts)
        if m and m["has_score"] and not m["is_live"]:
            results.append(m)
    browser.close()

print(f"Found {len(results)} previous results")
for target_name in ["Cardiff Allstars FC", "Cardiff Allstars FC Reserves"]:
    print(f"\nChecking target: {target_name}")
    for m in results:
        if m["home"] == target_name or m["away"] == target_name:
            print(f"  LAST RESULT -> {m['home']} {m['score']} {m['away']} (Status: {m['status']})")
            break
