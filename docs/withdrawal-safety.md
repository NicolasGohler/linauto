# Withdrawal safety audit and implementation

## Incident and scope

The October 25 manual withdrawal received HTTP 500 from LinkedIn's pagination
endpoint, fell back to loading about 1,500 invitation cards, and retained the
account browser lock. Dispatch then timed out for more than a day. A restart
cleared that lock. This is evidence of a stalled job, not proof of suspension.

Both the manual API and scheduled sweep call the same action. The previous
implementation also guessed invitation/profile pairs by position, accepted any
HTTP 200 as success, saved progress only at completion, and updated matching URL
substrings across all accounts. These are secondary effects addressed here.

## Plan and invariants

- Retain a maximum of **100 withdrawals per job**. Scheduled jobs use the same
  limit. This is an operational limit, not a guarantee about LinkedIn policy.
- Use at most eleven pagination reads to prepare 100 candidates. No DOM-scroll
  fallback, no retries, no alternate endpoint on failure, and no browser bypass.
- Require unambiguous invitation/profile mapping and explicit success receipts.
  Unsupported response formats stop the job; HTTP 200 alone is insufficient.
- Bound individual browser operations to 30 seconds and the complete job to
  30 minutes. Space withdrawals by 8-12 seconds; 100 is a maximum, not a quota.
- Save the intended mutation before submitting it, then save each confirmed
  result and synchronize that exact profile on that account immediately.
- Interrupted/uncertain mutations require human review, never automatic replay.
  Failed automatic runs stay held until a deliberate manual run resolves them.
- Authentication redirects mark the session expired. HTTP 401/403/429 pauses the
  account for review instead of letting another campaign continue sending.
- Close browser work before releasing ownership. If closure cannot be verified,
  quarantine the account in the pool and alert; never unlock a potentially live
  browser for concurrent use. Recovery then requires operator attention/restart.
- Preserve existing campaign states, limits, cookies, proxies and lead history.
  No manual invitation send or withdrawal is used to validate this change;
  the already-active campaign may continue its normal schedule.

## Verification and deployment

Use mocked browser and database tests for HTTP failures, malformed data,
100-item success, partial results, timeout/cancellation, cleanup failure,
cross-account isolation, task ownership, restart recovery and manual retry gates.
Run the existing pool, connection, dispatch and event-import regression tests.
Back up the database and changed server sources, deploy only matching sources,
restart once, and verify API health and unchanged campaign activation states.

## Deliberate behavior changes

LinkedIn's currently failing pagination path will report an error instead of
scrolling through the backlog. Unsupported response formats will also stop;
this may reduce availability, but avoids guessing who was withdrawn. A 100-item
run is a tested capacity, not a claim of a successful live LinkedIn withdrawal.
Uncertain results are not automatically marked withdrawn in the database.
The per-account durable status is private, stores no cookies or proxy secrets,
and assumes the application's existing single-worker deployment.

## Verified October 27

- 145 focused withdrawal, browser-ownership, dispatch, login and event tests pass.
- Full unit suite: 304 pass and 25 fail. An isolated unmodified HEAD checkout
  reproduced the identical 25 failure identifiers; no new failures were added.
  The baseline pool tests were excluded from that comparison because they use a
  zero monotonic timestamp that hangs on freshly booted hosts; the current pool
  tests use elapsed-time offsets and pass in the focused and full runs.
- Nine source files deployed with SHA-256 verification and rollback backups at
  `/root/linauto/data/backups/withdrawal-safety-20261027T083031Z`.
- Live API healthy; 101-item requests rejected before queuing; 100 remains the
  configured maximum; the scrolling fallback is absent.
- Post-restart table hashes and account/credential fingerprints match the
  quiescent pre-deployment snapshot. The GTM campaign remains active, with its
  prior follow-up setting unchanged. No live withdrawal was used as a test.
- The 100-item path was validated using synthetic response fixtures. Real
  LinkedIn pagination and receipt formats have not been revalidated by this
  deployment; an unsupported format stops safely and may require operator review.
