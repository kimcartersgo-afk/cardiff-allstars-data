"""
Cardiff Allstars FC - FAW Comet League Table Scraper
Uses saved session cookies (from setup_session.py) via plain HTTP requests.
No headless browser needed in CI.

Requires env var:
  COMET_SESSION  - base64-encoded Playwright session JSON (from setup_session.py)
"""

import base64
import json
import os
import sys
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

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


def make_session(session_data: dict) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.5",
    })
    for cookie in session_data.get("cookies", []):
        s.cookies.set(
            cookie["name"], cookie["value"],
            domain=cookie.get("domain", ""),
            path=cookie.get("path", "/"),
        )
    return s


def parse_table(html: str) -> list:
    soup = BeautifulSoup(html, "lxml")
    rows = []
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = tr.find_all("td")
            if len(cells) < 9:
                continue
            texts = [c.get_text(strip=True) for c in cells]
            offset = 0
            try:
                int(texts[0])
            except ValueError:
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
        session_data = json.loads(session_json)
    except Exception as e:
        print(f"ERROR: Could not decode COMET_SESSION — {e}", file=sys.stderr)
        sys.exit(1)

    sess = make_session(session_data)
    errors = []

    for comp in COMPETITIONS:
        print(f"\n[{comp['name']}] Fetching {comp['url']}")
        try:
            resp = sess.get(comp["url"], timeout=30, allow_redirects=True)

            if "login" in resp.url or "auth" in resp.url:
                print("  ERROR: Session expired. Re-run setup_session.py.", file=sys.stderr)
                errors.append(comp["name"])
                continue

            if resp.status_code != 200:
                print(f"  ERROR: HTTP {resp.status_code}", file=sys.stderr)
                errors.append(comp["name"])
                continue

            table = parse_table(resp.text)
            if not table:
                print(f"  WARNING: No table data found.")
                errors.append(comp["name"])
                continue

            soup = BeautifulSoup(resp.text, "lxml")
            for sel in ["h1", "h2", ".competition-title", ".title"]:
                el = soup.select_one(sel)
                if el and el.get_text(strip=True):
                    comp["division"] = el.get_text(strip=True)
                    break

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

    if errors:
        print(f"\nCompleted with errors on: {', '.join(errors)}", file=sys.stderr)
        sys.exit(1)
    print("\nAll competitions updated successfully.")


if __name__ == "__main__":
    main()
