#!/usr/bin/env python3
"""
Voyager API-based invitation withdrawal — diagnostic / test version.

IMPORTANT: Run inside the container so the account's proxy and browser
profile are used correctly:
  docker exec releasi python3 /app/scripts/diag_voyager_withdraw.py
  docker exec releasi python3 /app/scripts/diag_voyager_withdraw.py --no-dry-run

Default behaviour: dry-run (prints what would be withdrawn, touches nothing).
Pass --no-dry-run to actually call the withdraw API.
Count is hard-capped at 3 during this test phase.

Why this approach:
  The DOM-scroll approach loads all ~1900 invitation cards into the DOM at
  once, triggering OOM kills on the 3 GB host. The Voyager API paginates
  server-side — we never render more than 20 cards in DOM at once.
"""
from __future__ import annotations
import asyncio
import argparse
import json
from urllib.parse import urlparse, parse_qs, urlencode, quote

HARD_CAP = 3  # Never withdraw more than this during testing


async def main(account_name: str, count: int, dry_run: bool) -> None:
    from releasi.db.engine import get_session_factory
    from releasi.db.repository import Repository
    from releasi.linkedin.browser import LinkedInBrowser

    session_factory = get_session_factory()
    async with session_factory() as session:
        repo = Repository(session)
        account = await repo.get_account_by_name(account_name)
        if not account:
            print(f"[ERROR] Account '{account_name}' not found")
            return

    print(f"Account : {account.name}  (status={account.status})")
    print(f"Count   : {count}  (hard-capped at {HARD_CAP})")
    print(f"Dry-run : {dry_run}")

    intercepted: list[dict] = []

    browser = LinkedInBrowser()
    await browser.launch(
        account_id=account.id,
        li_at_cookie=account.li_at_cookie,
        user_agent=account.user_agent,
        proxy_url=account.proxy_url,
        proxy_country=account.proxy_country,
        timezone=account.timezone,
        cookies_json=account.cookies_json if hasattr(account, "cookies_json") else None,
    )

    try:
        if not await browser.validate_session():
            print("[ERROR] Session invalid — re-login required")
            return

        page = await browser.new_page()

        def on_request(req):
            # Capture ALL XHR/fetch requests to linkedin.com so we can discover
            # the actual API endpoint LinkedIn uses (it changes periodically).
            url = req.url
            if "linkedin.com" not in url:
                return
            if any(x in url for x in ("/api/", "/voyager/", "/graphql")):
                h = dict(req.headers)
                intercepted.append({
                    "method": req.method,
                    "url": url,
                    "csrf": h.get("csrf-token", ""),
                })

        page.on("request", on_request)

        try:
            # ── Phase 1: Navigate to invitation manager ──────────────────────
            print("\n── Phase 1: Navigate ──────────────────────────────────────")
            await page.goto(
                "https://www.linkedin.com/mynetwork/invitation-manager/sent/",
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            await asyncio.sleep(4)  # Initial render
            # Scroll the invitation list container (may be a child div with overflow:auto,
            # not the body) to trigger lazy-loading of more cards.
            n_before = await page.locator('a[aria-label^="Withdraw invitation"]').count()
            print(f"Withdraw anchors before scroll: {n_before}")
            scroll_result = await page.evaluate("""() => {
                // Find the deepest scrollable ancestor of an invite card
                const anchor = document.querySelector('a[aria-label^="Withdraw invitation"]');
                if (!anchor) return 'no_anchor';
                let el = anchor;
                while (el) {
                    const st = window.getComputedStyle(el);
                    if ((st.overflow === 'auto' || st.overflow === 'scroll' ||
                         st.overflowY === 'auto' || st.overflowY === 'scroll') &&
                        el.scrollHeight > el.clientHeight) {
                        el.scrollTop = el.scrollHeight;
                        return 'scrolled_container:' + el.tagName + '.' + el.className.slice(0, 60);
                    }
                    el = el.parentElement;
                }
                // Fallback: scroll the whole page
                window.scrollTo(0, document.body.scrollHeight);
                return 'scrolled_body';
            }""")
            print(f"Scroll result: {scroll_result}")
            await asyncio.sleep(5)  # Wait for lazy XHR to fire
            n_after = await page.locator('a[aria-label^="Withdraw invitation"]').count()
            print(f"Withdraw anchors after scroll: {n_after}")

            if any(p in page.url for p in ("login", "checkpoint", "uas/login")):
                print(f"[ERROR] Redirected to {page.url} — session expired")
                return
            print(f"Page URL : {page.url}")
            print(f"API requests intercepted: {len(intercepted)}")
            for r in intercepted:
                print(f"  {r['method']}  {r['url'][:150]}")

            # ── Extract CSRF token ──────────────────────────────────────────
            csrf = ""
            for r in intercepted:
                if r["csrf"]:
                    csrf = r["csrf"]
                    print(f"CSRF from intercepted request header: {csrf[:30]}…")
                    break
            if not csrf:
                cookies = await page.context.cookies()
                for ck in cookies:
                    if ck["name"] == "JSESSIONID":
                        csrf = ck["value"].strip('"')
                        print(f"CSRF from JSESSIONID cookie: {csrf[:30]}…")
                        break
            if not csrf:
                print("[ERROR] Could not extract CSRF token — cannot call Voyager API")
                return

            # ── Find the invitation-specific API request ─────────────────────
            # Keywords that strongly suggest an invitation-list endpoint
            INVITE_KEYWORDS = (
                "invitation", "sentInvitation", "myNetwork", "invitationView",
            )
            api_base = "/voyager/api/relationships/invitationViews"
            extra_params: dict = {}
            invite_requests = [
                r for r in intercepted
                if any(kw.lower() in r["url"].lower() for kw in INVITE_KEYWORDS)
            ]
            print(f"\nInvitation-related requests: {len(invite_requests)}")
            for r in invite_requests:
                print(f"  {r['method']}  {r['url'][:150]}")

            if invite_requests:
                best = invite_requests[0]
                parsed = urlparse(best["url"])
                if parsed.path:
                    api_base = parsed.path
                    qs = parse_qs(parsed.query)
                    for k, v in qs.items():
                        if k not in ("start", "count"):
                            extra_params[k] = v[0]
                    if best["csrf"]:
                        csrf = best["csrf"]
                    print(f"Using observed endpoint: {api_base}")
                    print(f"Extra params: {extra_params}")
            else:
                print(f"No invitation endpoint observed — using default: {api_base}")
                print("(LinkedIn may use GraphQL; check all requests above)")

            headers_js = {
                "csrf-token": csrf,
                "x-restli-protocol-version": "2.0.0",
                "accept": "application/vnd.linkedin.normalized+json+2.1",
            }

            # ── Phase 2: Check DOM for pending count + invitation data ──────
            print("\n── Phase 2: DOM inspection ─────────────────────────────────")
            dom_info = await page.evaluate("""() => {
                // Extract "People (N)" count pill
                let total = 0;
                const all = document.querySelectorAll('a, li, span, button, [role="tab"]');
                for (const el of all) {
                    const m = (el.textContent || '').match(/^\\s*People\\s*\\((\\d[\\d,]*)\\)\\s*$/i);
                    if (m) { total = parseInt(m[1].replace(/,/g,''), 10); break; }
                }
                // Count invite cards/anchors
                const withdrawAnchors = document.querySelectorAll('a[aria-label^="Withdraw invitation"]').length;
                // Get any visible profile names from invite cards
                const names = [];
                document.querySelectorAll('a[aria-label^="Withdraw invitation"]').forEach(a => {
                    const card = a.closest('li') || a.parentElement;
                    if (card) {
                        const link = card.querySelector('a[href*="/in/"]');
                        if (link) names.push(link.href);
                    }
                });
                return { total, withdrawAnchors, sampleUrls: names.slice(0, 5) };
            }""")
            print(f"People pill count (DOM): {dom_info.get('total')}")
            print(f"Withdraw anchors in DOM: {dom_info.get('withdrawAnchors')}")
            print(f"Sample URLs: {dom_info.get('sampleUrls')}")

            dom_total = dom_info.get("total", 0)

            # ── Phase 3: Try multiple Voyager REST endpoint variants ─────────
            print("\n── Phase 3: Probe Voyager API endpoint variants ────────────")
            CANDIDATES = [
                "/voyager/api/relationships/invitationViews?q=sentInvitation&start=0&count=1",
                "/voyager/api/relationships/invitationViews?start=0&count=1&invitationType=CONNECTION",
                "/voyager/api/relationships/invitationViews?start=0&count=1",
                "/voyager/api/relationships/invitations?q=sentByMember&start=0&count=1",
                "/voyager/api/relationships/invitations?q=sent&start=0&count=1",
            ]
            working_endpoint = None
            total = dom_total  # fallback to DOM-read value

            for candidate in CANDIDATES:
                result = await page.evaluate(
                    """async ([url, hdrs]) => {
                        try {
                            const r = await fetch(url, { headers: hdrs, credentials: "include" });
                            return { status: r.status, body: await r.text() };
                        } catch (e) { return { error: e.message }; }
                    }""",
                    [candidate, headers_js],
                )
                status = result.get("status")
                err = result.get("error")
                body = result.get("body", "")
                preview = body[:200].replace("\n", " ")
                print(f"  {candidate[:80]}")
                print(f"    → HTTP {status}  {preview}")
                if not err and status == 200:
                    try:
                        d = json.loads(body)
                        paging_total = (d.get("paging") or d.get("data", {}).get("paging") or {}).get("total")
                        if paging_total is not None:
                            total = paging_total
                            working_endpoint = candidate.split("?")[0]
                            extra_params = dict(x.split("=") for x in candidate.split("?")[1].split("&")
                                                if "=" in x and x.split("=")[0] not in ("start", "count"))
                            print(f"    ✓ Working endpoint! total={total}")
                            break
                    except Exception:
                        pass

            if not working_endpoint:
                print("\n── Phase 3b: SDUI API — pagination + minimal withdrawal ────")
                # LinkedIn uses SDUI (Server-Driven UI), not Voyager REST.
                # Pagination: POST /flagship-web/rsc-action/actions/pagination
                # Withdrawal: POST /flagship-web/rsc-action/actions/server-request
                # Hypothesis: withdrawal works with just invitationId (no profile data needed).

                PAGINATION_URL = "/flagship-web/rsc-action/actions/pagination?sduiid=com.linkedin.sdui.pagers.mynetwork.invitationsList"
                WITHDRAW_URL = "/flagship-web/rsc-action/actions/server-request?sduiid=com.linkedin.sdui.requests.mynetwork.addaWithdrawInvitation"
                SDUI_HEADERS = {
                    "csrf-token": csrf,
                    "content-type": "application/json",
                    "accept": "*/*",
                }

                # ── Step 1: Fetch oldest invitations via pagination API ──────
                # Use a lower startIndex for the probe (high startIndex may cause 500
                # if LinkedIn's server-side pager doesn't have that page loaded yet).
                # Once we confirm the endpoint works, we'll use a high startIndex.
                probe_start_idx = 20  # Start with a known-safe offset for probe
                oldest_start_idx = max(0, dom_total - 10)  # For real oldest fetch

                def make_pagination_payload(start_idx):
                    """Build the full SDUI pagination request body (matches real browser traffic)."""
                    args = {
                        "$type": "proto.sdui.actions.requests.RequestedArguments",
                        "payload": {
                            "startIndex": start_idx,
                            "invitationTypeEnum": ["GenericInvitationType_CONNECTION"],
                            "invitationClassificationTypes": [],
                            "filterCriteriaEnum": "FilterCriteria_UNKNOWN",
                            "invitationDirectionEnum": "PendingInvitationDirection_SENT"
                        },
                        "requestedStateKeys": [],
                        "requestMetadata": {"$type": "proto.sdui.common.RequestMetadata"},
                        "states": [],
                        "screenId": "com.linkedin.sdui.flagshipnav.mynetwork.invitations.InvitationSentWithType"
                    }
                    return {
                        "pagerId": "com.linkedin.sdui.pagers.mynetwork.invitationsList",
                        "clientArguments": args,
                        "paginationRequest": {
                            "$type": "proto.sdui.actions.requests.PaginationRequest",
                            "pagerId": "com.linkedin.sdui.pagers.mynetwork.invitationsList",
                            "requestedArguments": {
                                "$type": "proto.sdui.actions.requests.RequestedArguments",
                                "payload": args["payload"],
                                "requestedStateKeys": [],
                                "requestMetadata": {"$type": "proto.sdui.common.RequestMetadata"},
                            },
                            "trigger": {
                                "$case": "itemDistanceTrigger",
                                "itemDistanceTrigger": {
                                    "$type": "proto.sdui.actions.requests.ItemDistanceTrigger",
                                    "preloadDistance": 3,
                                    "preloadLength": 250
                                }
                            },
                            "retryCount": 2
                        }
                    }

                import re as _re

                print(f"\nProbing SDUI pagination API (startIndex={probe_start_idx})…")
                pag_result = await page.evaluate(
                    """async ([url, hdrs, body]) => {
                        try {
                            const r = await fetch(url, {
                                method: "POST", headers: hdrs,
                                credentials: "include", body: JSON.stringify(body),
                            });
                            return { status: r.status, body: await r.text() };
                        } catch (e) { return { error: e.message }; }
                    }""",
                    [PAGINATION_URL, SDUI_HEADERS, make_pagination_payload(probe_start_idx)],
                )
                print(f"Probe pagination status: {pag_result.get('status')}")
                if pag_result.get("error"):
                    print(f"Error: {pag_result['error']}")
                    return
                probe_body = pag_result.get("body", "")
                print(f"Probe body length: {len(probe_body)}")
                probe_ids = _re.findall(r'InvitationUrn\(invitationId=(\d+)\)', probe_body)
                print(f"Probe invitationIds (first 5): {probe_ids[:5]}")

                if pag_result.get("status") != 200:
                    print(f"Probe failed — body: {probe_body[:300]}")
                    return

                # Now fetch the OLDEST batch
                print(f"\nFetching oldest invitations (startIndex={oldest_start_idx})…")
                pag_result = await page.evaluate(
                    """async ([url, hdrs, body]) => {
                        try {
                            const r = await fetch(url, {
                                method: "POST", headers: hdrs,
                                credentials: "include", body: JSON.stringify(body),
                            });
                            return { status: r.status, body: await r.text() };
                        } catch (e) { return { error: e.message }; }
                    }""",
                    [PAGINATION_URL, SDUI_HEADERS, make_pagination_payload(oldest_start_idx)],
                )
                print(f"Oldest pagination status: {pag_result.get('status')}")
                if pag_result.get("error"):
                    print(f"Error: {pag_result['error']}")
                    return
                pag_body = pag_result.get("body", "")
                print(f"Oldest body length: {len(pag_body)}")

                # Extract invitationIds from SDUI RSC payload
                inv_ids = _re.findall(r'InvitationUrn\(invitationId=(\d+)\)', pag_body)
                # Deduplicate while preserving order (each ID appears ~2x in modelStates)
                seen_ids: set = set()
                unique_ids = []
                for iid in inv_ids:
                    if iid not in seen_ids:
                        seen_ids.add(iid)
                        unique_ids.append(iid)
                print(f"Extracted {len(unique_ids)} unique invitationIds: {unique_ids[:5]}")

                if not unique_ids:
                    print("No invitationIds found — check pagination body below:")
                    print(pag_body[:1000])
                    return

                # Apply the count cap before dry-run check so the log is accurate
                unique_ids = unique_ids[:count]

                if dry_run:
                    print(f"\n[DRY-RUN] Would test withdrawal for {len(unique_ids)} invitationId(s).")
                    print("Pass --no-dry-run to test actual API calls.")
                    return

                # ── Step 2: Test minimal withdrawal (just invitationId) ──────
                print(f"\n── Testing minimal SDUI withdrawal for {len(unique_ids)} old invitations ──")
                print("Using MINIMAL payload — just invitationId, no profile data.")

                for inv_id in unique_ids:
                    withdraw_payload = {
                        "requestId": "com.linkedin.sdui.requests.mynetwork.addaWithdrawInvitation",
                        "serverRequest": {
                            "requestId": "com.linkedin.sdui.requests.mynetwork.addaWithdrawInvitation",
                            "requestedArguments": {
                                "$type": "proto.sdui.actions.requests.RequestedArguments",
                                "payload": {
                                    "inviterActionType": "InviterActionType_WITHDRAW",
                                    "invitationType": "GenericInvitationType_CONNECTION",
                                    "invitationUrn": {"invitationId": inv_id}
                                },
                                "requestedStateKeys": [],
                                "requestMetadata": {"$type": "proto.sdui.common.RequestMetadata"},
                                "states": [],
                                "screenId": "com.linkedin.sdui.flagshipnav.mynetwork.invitations.WithdrawConfirmationDialog"
                            }
                        },
                        "states": [],
                        "requestedArguments": {
                            "$type": "proto.sdui.actions.requests.RequestedArguments",
                            "payload": {
                                "inviterActionType": "InviterActionType_WITHDRAW",
                                "invitationType": "GenericInvitationType_CONNECTION",
                                "invitationUrn": {"invitationId": inv_id}
                            },
                            "requestedStateKeys": [],
                            "requestMetadata": {"$type": "proto.sdui.common.RequestMetadata"},
                            "states": [],
                            "screenId": "com.linkedin.sdui.flagshipnav.mynetwork.invitations.WithdrawConfirmationDialog"
                        }
                    }

                    wd_result = await page.evaluate(
                        """async ([url, hdrs, body]) => {
                            try {
                                const r = await fetch(url, {
                                    method: "POST", headers: hdrs,
                                    credentials: "include", body: JSON.stringify(body),
                                });
                                return { status: r.status, body: await r.text() };
                            } catch (e) { return { error: e.message }; }
                        }""",
                        [WITHDRAW_URL, SDUI_HEADERS, withdraw_payload],
                    )
                    status = wd_result.get("status")
                    body_preview = (wd_result.get("body") or "")[:300]
                    err = wd_result.get("error")
                    if err:
                        print(f"  invitationId={inv_id}: ERROR {err}")
                    elif status == 200:
                        # Check if response indicates success or error
                        if "errors" in body_preview.lower() or "Sorry" in body_preview:
                            print(f"  invitationId={inv_id}: HTTP 200 but server error? {body_preview[:100]}")
                        else:
                            print(f"  invitationId={inv_id}: SUCCESS (HTTP {status})")
                    else:
                        print(f"  invitationId={inv_id}: FAIL HTTP {status} — {body_preview[:100]}")
                    await asyncio.sleep(2.0)

                print("\n[DONE] Minimal SDUI withdrawal test complete.")
                return

                print("\n── Phase 3c: Extract URNs from DOM (UNREACHABLE, KEPT FOR REFERENCE) ──")
                # Since all REST endpoints are dead and data is pre-loaded into JS state
                # (no API call on scroll), try to extract invitation URNs directly from the
                # DOM. LinkedIn often stores the URN in data-* attributes or aria-labels.
                urn_data = await page.evaluate("""() => {
                    const results = [];
                    // Strategy 1: aria-label on the Withdraw anchor
                    // Format: 'Withdraw invitation sent to Full Name'
                    // The card's profile link has the /in/ slug; if there's a data-urn,
                    // that gives us the invite ID directly.
                    const anchors = document.querySelectorAll('a[aria-label^="Withdraw invitation"]');
                    for (const anchor of anchors) {
                        const label = anchor.getAttribute('aria-label') || '';
                        const name = label.replace('Withdraw invitation sent to ', '').trim();
                        // Walk up to find data-* attributes with URN
                        let urn = '';
                        let el = anchor;
                        for (let i = 0; i < 8 && el; i++) {
                            // Check all data-* attributes
                            for (const attr of el.attributes) {
                                const v = attr.value;
                                if (v.includes('urn:li:invitation:') || v.includes('urn%3Ali%3Ainvitation')) {
                                    urn = decodeURIComponent(v);
                                    break;
                                }
                            }
                            if (urn) break;
                            el = el.parentElement;
                        }
                        // Also grab profile URL
                        let profileUrl = '';
                        let c = anchor;
                        for (let i = 0; i < 8 && c; i++) {
                            const link = c.querySelector('a[href*="/in/"]');
                            if (link) { profileUrl = link.href; break; }
                            c = c.parentElement;
                        }
                        results.push({ name, urn, profileUrl });
                        if (results.length >= 5) break;  // Just need a sample
                    }
                    return results;
                }""")
                print("Sample URN extraction from DOM:")
                for item in urn_data:
                    print(f"  name={item.get('name')!r}  urn={item.get('urn')!r}  url={item.get('profileUrl', '')[:60]!r}")

                # Try to intercept the actual withdraw API by clicking withdraw on
                # the first card and capturing the resulting network request.
                # We will NOT confirm the withdraw dialog.
                print("\n── Phase 3c: Intercept real withdraw API call ───────────────")
                print("Clicking withdraw on first (newest) card to capture the API endpoint…")
                print("NOTE: We will NOT confirm — the actual invite is NOT withdrawn.")

                # We already know the SDUI endpoint from prior runs. Now capture
                # full request bodies + response for pagination and withdraw.
                SDUI_PAGINATION = "flagship-web/rsc-action/actions/pagination"
                SDUI_WITHDRAW = "flagship-web/rsc-action/actions/server-request"
                SDUI_NAV = "flagship-web/rsc-action/actions/navigation"

                captured_full: list[dict] = []
                captured_responses: list[dict] = []

                def on_sdui_request(req):
                    url = req.url
                    if not any(k in url for k in (SDUI_PAGINATION, SDUI_WITHDRAW, SDUI_NAV)):
                        return
                    try:
                        body = req.post_data or ""
                    except Exception:
                        try:
                            body = req.post_data_buffer.decode("utf-8", errors="replace")
                        except Exception:
                            body = "<binary>"
                    captured_full.append({
                        "method": req.method,
                        "url": url,
                        "csrf": req.headers.get("csrf-token", ""),
                        "body": str(body),
                    })

                async def on_sdui_response(resp):
                    url = resp.url
                    if not any(k in url for k in (SDUI_PAGINATION, SDUI_WITHDRAW)):
                        return
                    try:
                        body = await resp.text()
                    except Exception:
                        body = "<error reading body>"
                    captured_responses.append({
                        "status": resp.status,
                        "url": url,
                        "body": body,
                    })

                page.on("request", on_sdui_request)
                page.on("response", on_sdui_response)

                # Click the first Withdraw anchor to open confirmation overlay
                first_anchor = page.locator('a[aria-label^="Withdraw invitation"]').first
                await first_anchor.scroll_into_view_if_needed(timeout=5000)
                await asyncio.sleep(0.8)
                await first_anchor.click(timeout=5000)
                await asyncio.sleep(2)

                # Click the CONFIRM button to trigger the actual withdraw API call.
                # NOTE: This WILL withdraw one invitation (newest). Acceptable for test.
                confirm_result = await page.evaluate("""() => {
                    const btn = Array.from(document.querySelectorAll('button'))
                        .find(b => b.innerText.trim() === 'Withdraw' && b.offsetParent !== null);
                    if (btn) { btn.click(); return true; }
                    return false;
                }""")
                print(f"Confirm clicked: {confirm_result}")
                await asyncio.sleep(3)

                print(f"\n── Captured SDUI requests ({len(captured_full)}) ──────────────")
                for r in captured_full:
                    label = "PAGINATION" if SDUI_PAGINATION in r["url"] else (
                            "WITHDRAW" if SDUI_WITHDRAW in r["url"] else "NAV")
                    print(f"\n[{label}]  {r['url'][:140]}")
                    print(f"BODY: {r['body']}")

                print(f"\n── Captured SDUI responses ({len(captured_responses)}) ────────")
                for r in captured_responses:
                    label = "PAGINATION" if SDUI_PAGINATION in r["url"] else "WITHDRAW"
                    print(f"\n[{label}] HTTP {r['status']}  {r['url'][:100]}")
                    print(f"RESPONSE (first 2000): {r['body'][:2000]}")

                print("\n[SUCCESS] SDUI endpoint analysis complete.")
                print("[STOP] Research phase complete.")
                return

            api_base = working_endpoint
            print(f"\nUsing endpoint: {api_base}")
            print(f"Total invitations: {total}")
            if total == 0:
                print("No pending invitations — nothing to do")
                return

            # ── Phase 4: Fetch the oldest page ─────────────────────────────
            print("\n── Phase 4: Fetch oldest invitations ──────────────────────")
            fetch_n = min(count, 20)  # Voyager page size cap
            start_offset = max(0, total - fetch_n)
            oldest_params = {"start": str(start_offset), "count": str(fetch_n), **extra_params}
            oldest_url = api_base + "?" + urlencode(oldest_params)
            print(f"Request: GET {oldest_url}")

            oldest_result = await page.evaluate(
                """async ([url, hdrs]) => {
                    try {
                        const r = await fetch(url, { headers: hdrs, credentials: "include" });
                        return { status: r.status, body: await r.text() };
                    } catch (e) { return { error: e.message }; }
                }""",
                [oldest_url, headers_js],
            )
            print(f"Status: {oldest_result.get('status')}")
            if oldest_result.get("error"):
                print(f"[ERROR] {oldest_result['error']}")
                return

            raw_body = oldest_result.get("body", "")
            print(f"Body length: {len(raw_body)} chars")
            print(f"Body preview (first 800):\n{raw_body[:800]}\n")

            try:
                oldest_data = json.loads(raw_body)
            except json.JSONDecodeError as exc:
                print(f"[ERROR] JSON parse failed: {exc}")
                return

            print(f"Top-level keys: {list(oldest_data.keys())}")
            elements = oldest_data.get("elements", [])
            included = oldest_data.get("included", [])
            print(f"elements: {len(elements)}   included: {len(included)}")

            # ── Extract invitation URNs ─────────────────────────────────────
            invitations: list[dict] = []
            for elem in elements:
                # Voyager schema varies by endpoint — try multiple paths
                entity_urn = (
                    elem.get("entityUrn")
                    or (elem.get("invitation") or {}).get("entityUrn")
                    or ""
                )
                inv_id = (
                    (elem.get("invitation") or {}).get("invitationId")
                    or ""
                )
                name = (
                    (elem.get("title") or {}).get("text")
                    or elem.get("name")
                    or ""
                )
                subtitle = (
                    (elem.get("subtitle") or {}).get("text")
                    or ""
                )
                invitations.append({
                    "entityUrn": entity_urn,
                    "invitationId": str(inv_id),
                    "name": name,
                    "subtitle": subtitle,
                    "raw_keys": list(elem.keys()),
                })

            if not invitations:
                print("\n[WARN] Could not extract invitation records.")
                print("  Full first element (if any):")
                if elements:
                    print(json.dumps(elements[0], indent=2)[:1000])
                else:
                    print("  No elements in response — check body above.")
                return

            print(f"\n Identified {len(invitations)} invitation(s) to target:")
            for inv in invitations:
                print(f"  URN: {inv['entityUrn'] or '(missing)'!r:50s}  name: {inv['name']!r}")
                if not inv["entityUrn"]:
                    print(f"       raw_keys: {inv['raw_keys']}")

            if dry_run:
                print(f"\n[DRY-RUN] Would withdraw {len(invitations)} invitation(s).")
                print("Pass --no-dry-run to execute the withdrawals.")
                return

            # ── Phase 5: Withdraw ───────────────────────────────────────────
            print(f"\n── Phase 5: WITHDRAWING {len(invitations)} invitation(s) ─────────")
            succeeded = []
            for inv in invitations:
                urn = inv["entityUrn"]
                if not urn:
                    print(f"  SKIP {inv['name']!r} — no entityUrn")
                    continue

                encoded_urn = quote(urn, safe="")
                withdraw_url = f"/voyager/api/relationships/invitations/{encoded_urn}?action=withdraw"
                print(f"  POST {withdraw_url[:90]}  ({inv['name']!r})")

                result = await page.evaluate(
                    """async ([url, hdrs]) => {
                        const h = { ...hdrs, "content-type": "application/json" };
                        try {
                            const r = await fetch(url, {
                                method: "POST", headers: h,
                                credentials: "include", body: "{}",
                            });
                            return { status: r.status, body: await r.text() };
                        } catch (e) { return { error: e.message }; }
                    }""",
                    [withdraw_url, headers_js],
                )

                status = result.get("status")
                err = result.get("error")
                body_snip = (result.get("body") or "")[:200]

                if err:
                    print(f"    ERROR: {err}")
                elif status in (200, 201, 204):
                    print(f"    OK (HTTP {status})")
                    succeeded.append(inv["name"])
                else:
                    print(f"    FAIL HTTP {status} — {body_snip}")

                # Human-paced delay between calls
                await asyncio.sleep(2.5)

            print(f"\nWithdrawals succeeded: {len(succeeded)}/{len(invitations)}")
            for name in succeeded:
                print(f"  ✓ {name}")

        finally:
            await page.close()

    finally:
        await browser.close()
        print("\nDone.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Voyager API withdrawal test")
    parser.add_argument("--account", default="Joseph Appolos")
    parser.add_argument(
        "--count",
        type=int,
        default=3,
        help=f"Invitations to target (capped at {HARD_CAP} during testing)",
    )
    parser.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="Actually call the withdraw API (default: dry-run only)",
    )
    parser.set_defaults(dry_run=True)
    args = parser.parse_args()

    safe_count = min(args.count, HARD_CAP)
    asyncio.run(main(args.account, safe_count, args.dry_run))
