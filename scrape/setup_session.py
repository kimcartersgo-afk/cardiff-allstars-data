"""
Cardiff Allstars FC - One-Time Login Setup
Run this ONCE on your PC to save your FAW Comet session (including MFA).
The saved session is then used by the automated scraper without needing to log in again.

Usage:
  pip install playwright
  python -m playwright install chromium
  python setup_session.py
"""

import asyncio
import base64
import json
import os
from playwright.async_api import async_playwright

OUTPUT_FILE = os.path.join(os.path.dirname(__file__), "comet_session.json")
LOGIN_URL = "https://comet.faw.cymru"


async def main():
    print("=" * 60)
    print("Cardiff Allstars FC - FAW Comet Session Setup")
    print("=" * 60)
    print()
    print("A browser window will open. Please:")
    print("  1. Log in to FAW Comet as normal")
    print("  2. Complete the MS Authenticator prompt")
    print("  3. Wait until you can see a league table page")
    print("  4. Come back here and press ENTER")
    print()
    input("Press ENTER to open the browser...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(LOGIN_URL)

        print()
        print("Browser is open. Log in and complete MFA...")
        print("When you can see the Comet dashboard/table, come back here.")
        print()
        input("Press ENTER once you are logged in...")

        storage = await context.storage_state()
        await browser.close()

    with open(OUTPUT_FILE, "w") as f:
        json.dump(storage, f)

    b64 = base64.b64encode(json.dumps(storage).encode()).decode()

    print()
    print("=" * 60)
    print("Session saved!")
    print("=" * 60)
    print()
    print("Copy the code below and add it as a GitHub Secret")
    print("  Name:  COMET_SESSION")
    print("  Value: (the long code below)")
    print()
    print(b64)
    print()
    print("Done!")


if __name__ == "__main__":
    asyncio.run(main())
