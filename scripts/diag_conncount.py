"""
Diagnose connection-count misdetection (mutual vs actual).
  docker exec releasi python3 /app/scripts/diag_conncount.py
"""
import asyncio
import json

import sys
TARGET = sys.argv[1] if len(sys.argv) > 1 else "https://www.linkedin.com/in/rennaba"
ACCOUNT_NAME = "Nicolas Goehler"


async def main():
    from releasi.db.engine import get_session_factory
    from releasi.db.repository import Repository
    from releasi.linkedin.browser import LinkedInBrowser
    from releasi.linkedin.profile_filter import ProfileFilter

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
        await page.goto(TARGET, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(3)
        print("URL:", page.url)

        # Dump every element whose exact text is "connections"/"connection",
        # and the parent-chain texts the production JS walks (up to 3 levels).
        dump = await page.evaluate(r"""
            () => {
                const out = [];
                const labels = document.querySelectorAll('p, span');
                for (const el of labels) {
                    const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                    if (t !== 'connections' && t !== 'connection') continue;
                    const chain = [];
                    let cur = el.parentElement;
                    for (let i = 0; i < 3 && cur; i++) {
                        const full = (cur.innerText || cur.textContent || '').trim();
                        const m = full.match(/^([\d,]+)\+?\s*connections?$/i);
                        chain.push({level: i, text: full.slice(0,120), matched: m ? m[1] : null});
                        cur = cur.parentElement;
                    }
                    out.push({label: el.tagName, chain});
                }
                return out;
            }
        """)
        print("=== elements with exact text 'connections' ===")
        print(json.dumps(dump, indent=2))

        # Dump what each CSS fallback selector matches
        from releasi.linkedin import selectors
        print("\n=== CSS fallback selector matches ===")
        for sel in selectors.PROFILE_CONNECTION_COUNT:
            try:
                locs = await page.locator(sel).all()
                print(f"\nselector: {sel}  (n={len(locs)})")
                for i, l in enumerate(locs[:5]):
                    txt = (await l.text_content() or "").strip()
                    print(f"  [{i}] {txt[:120]!r}")
            except Exception as e:
                print(f"  err: {e}")

        # Find any text containing 'connections' anywhere (mutual vs actual)
        anytext = await page.evaluate(r"""
            () => {
                const out = [];
                const els = document.querySelectorAll('span, a, p, li');
                for (const el of els) {
                    const t = (el.innerText || el.textContent || '').trim();
                    if (/\b(connections?|followers?)\b/i.test(t) && t.length < 80) out.push(t);
                }
                return [...new Set(out)];
            }
        """)
        print("\n=== all short texts containing 'connection(s)' ===")
        for t in anytext:
            print(" ", repr(t))

        count = await ProfileFilter()._get_connection_count(page)
        print("\n_get_connection_count() returned:", count)
    finally:
        await browser.close()
        print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
