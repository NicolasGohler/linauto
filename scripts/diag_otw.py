"""
Diagnose Open to Work detection on a LinkedIn profile.
  docker exec releasi python3 /app/scripts/diag_otw.py [linkedin_url]
"""
import asyncio
import sys

TARGET = sys.argv[1] if len(sys.argv) > 1 else "https://www.linkedin.com/in/mohamed-aly-kanbar"
ACCOUNT_NAME = "Nicolas Goehler"


async def main():
    from releasi.db.engine import get_session_factory
    from releasi.db.repository import Repository
    from releasi.linkedin.browser import LinkedInBrowser

    session_factory = get_session_factory()
    async with session_factory() as session:
        repo = Repository(session)
        account = await repo.get_account_by_name(ACCOUNT_NAME)
        if not account:
            print(f"Account '{ACCOUNT_NAME}' not found"); return

    browser = LinkedInBrowser()
    await browser.launch(
        account_id=account.id, li_at_cookie=account.li_at_cookie,
        user_agent=account.user_agent, proxy_url=account.proxy_url,
        proxy_country=account.proxy_country, timezone=account.timezone,
    )
    if not await browser.validate_session():
        print("!! Session invalid"); await browser.close(); return
    page = await browser.new_page()
    try:
        print(f"Navigating to {TARGET}...")
        await page.goto(TARGET, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)
        print("URL:", page.url)

        # 1. Current exact-match logic
        r1 = await page.evaluate("""() => {
            var els = document.querySelectorAll('div, span, p');
            var matches = [];
            for (var i = 0; i < els.length; i++) {
                var t = (els[i].innerText || els[i].textContent || '').trim().toLowerCase();
                if (t === 'open to work') matches.push(els[i].tagName + ' | cls=' + els[i].className.substring(0,60));
            }
            return matches;
        }""")
        print("\n[1] Exact 'open to work' div/span/p elements:", r1)

        # 2. Leaf nodes containing substring
        r2 = await page.evaluate("""() => {
            var matches = [];
            var all = document.querySelectorAll('*');
            for (var i = 0; i < all.length; i++) {
                var el = all[i];
                if (el.children.length > 0) continue;
                var t = (el.innerText || el.textContent || '').trim().toLowerCase();
                if (t.indexOf('open to work') !== -1 && t.length < 150) {
                    matches.push(el.tagName + ' [' + el.className.substring(0,60) + '] = "' + t.substring(0,100) + '"');
                }
            }
            return matches.slice(0, 20);
        }""")
        print("[2] Leaf elements containing 'open to work':", r2)

        # 3. img alt
        r3 = await page.evaluate("""() => {
            var matches = [];
            var imgs = document.querySelectorAll('img');
            for (var i = 0; i < imgs.length; i++) {
                var alt = (imgs[i].alt || '').toLowerCase();
                if (alt.indexOf('open to work') !== -1) matches.push(imgs[i].alt.substring(0,100));
            }
            return matches;
        }""")
        print("[3] IMG alt containing 'open to work':", r3)

        # 4. aria-label
        r4 = await page.evaluate("""() => {
            var matches = [];
            var els = document.querySelectorAll('[aria-label]');
            for (var i = 0; i < els.length; i++) {
                var label = (els[i].getAttribute('aria-label') || '').toLowerCase();
                if (label.indexOf('open to work') !== -1) {
                    matches.push(els[i].tagName + ': ' + els[i].getAttribute('aria-label'));
                }
            }
            return matches.slice(0, 10);
        }""")
        print("[4] aria-label containing 'open to work':", r4)

        # 5. Any attribute on any element
        r5 = await page.evaluate("""() => {
            var matches = [];
            var all = document.querySelectorAll('*');
            for (var i = 0; i < all.length; i++) {
                var el = all[i];
                var attrs = el.attributes;
                for (var j = 0; j < attrs.length; j++) {
                    if ((attrs[j].value || '').toLowerCase().indexOf('open to work') !== -1) {
                        matches.push(el.tagName + ' ' + attrs[j].name + '="' + attrs[j].value.substring(0,100) + '"');
                    }
                }
            }
            return matches.slice(0, 20);
        }""")
        print("[5] Any attribute containing 'open to work':", r5)

        await page.screenshot(path="/app/scripts/otw_screenshot.png")
        print("\nScreenshot saved to /app/scripts/otw_screenshot.png")

    finally:
        await browser.close()


asyncio.run(main())
