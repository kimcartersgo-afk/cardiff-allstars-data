"""
Run this script ONCE on your local PC to log in to FAW Comet.
It opens a browser window - log in normally (including MFA if prompted).
When done, it saves the secret to comet_session.txt on your Desktop
AND prints it to the screen.
"""
import base64, json, os
from playwright.sync_api import sync_playwright


def main():
    print("")
    print("=" * 60)
    print("FAW Comet Session Setup")
    print("=" * 60)
    print("Opening browser... please wait.")
    print("")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()
        page.goto("https://comet.faw.cymru/")

        print("Browser is open.")
        print("1. Log in to FAW Comet")
        print("2. Complete MFA if asked")
        print("3. Wait until you can see your dashboard")
        print("4. Come back here and press ENTER")
        print("")
        input("Press ENTER when logged in >> ")

        print("")
        print("Saving session... please wait.")

        storage = context.storage_state()
        session_b64 = base64.b64encode(json.dumps(storage).encode()).decode()

        # Save to file on Desktop as backup
        desktop = os.path.join(os.path.expanduser("~"), "Desktop", "comet_session.txt")
        with open(desktop, "w") as f:
            f.write(session_b64)

        print("")
        print("=" * 60)
        print("SUCCESS!")
        print("")
        print("Your session has been saved to:")
        print(f"  {desktop}")
        print("")
        print("Also copy the secret below and add it to GitHub:")
        print("  Repo > Settings > Secrets > COMET_SESSION")
        print("=" * 60)
        print("")
        print(session_b64)
        print("")
        print("=" * 60)
        print("File saved. You can close this window.")
        input("Press ENTER to exit >> ")

        browser.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("")
        print("ERROR:", e)
        print("")
        print("Make sure you have run:")
        print("  pip install playwright")
        print("  playwright install chromium")
        input("Press ENTER to exit >> ")
