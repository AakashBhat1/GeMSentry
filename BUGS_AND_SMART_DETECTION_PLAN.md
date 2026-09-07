# Outstanding bugs and smarter tender detection

Date: 2026-09-07

Status: **all five confirmed bugs and the eligibility-label issue are fixed** (2026-09-07), with regression coverage in `tests/test_bug_regressions.py`. Each section below keeps the original finding and records the fix underneath. The "Proposed smarter detection" and "Other useful upgrades" sections remain proposals, except items 2, 3 and 5 (extraction states, unit-aware amounts, conflict detection for turnover), which the turnover fix required and therefore delivered.

Earlier completed fixes are separate: GeM pagination, broader capacity-based searches, decimal bid-value parsing, and refresh of rediscovered listing dates.

## Confirmed bugs

### 1. Manual shortlist decisions can be overwritten — high priority

- **Trigger:** A scrape loads the workspace; a user then shortlists or rejects a tender before that scrape saves.
- **Cause:** The scrape saves its old in-memory snapshot through `save_metadata()` and `db.replace_all()`, replacing the newer database record.
- **Reproduced:** A manually pinned `Shortlisted` tender reverted to `Pending Review`, losing its manual status marker.
- **Locations:** `gemsentry/storage.py:179`, `gemsentry/db.py:156`, `gemsentry/pipeline.py:535`, `gemsentry/web/tenders.py` status-update endpoint.
- **Proposed fix:** Preserve the latest manual status inside the database write transaction. Ensure the JSON/CSV exports reflect the merged records. Apply the same protection to rescores and repair operations.
- **Verification:** Simulate a manual decision between loading and saving; confirm both the database and exports retain it. Test rollback on a failed save.
- **Fixed:** `gemsentry/db.py` gained a `status_source` column (added and backfilled in place for older workspaces) and `apply_manual_pins`. `replace_all` now takes `BEGIN IMMEDIATE`, re-reads the manual pins under that write lock, merges them onto the incoming snapshot, and returns the records as stored; `save_metadata` writes the exports from that merged list and returns it, so scrape/rescore/repair all publish the same data the store holds.

### 2. Turnover requirements can be missed and produce false eligibility — high priority

- **Trigger:** An RFP says `Minimum Average Annual Turnover of the bidder (For 3 Years) 75 Lakh (s)`.
- **Cause:** The parser takes the first number, `3`, discards it as too small, and never parses `75 Lakh`. The eligibility logic can treat an unparsed requirement as no requirement.
- **Reproduced:** The extracted turnover was `None`; with no turnover waiver and a company turnover of ₹10 lakh, the result was `eligible` instead of identifying the ₹75 lakh requirement.
- **Locations:** `gemsentry/parsing/signals.py:165`, `gemsentry/scoring/eligibility.py:84`.
- **Proposed fix:** Parse the value after label qualifiers, recognize INR/lakh/crore units, and distinguish an explicitly waived requirement from an absent or unreadable field. Uncertain eligibility should require review.
- **Verification:** Cover year qualifiers, decimal amounts, lakh/crore units, explicit waivers, missing values, contradictory values, and adjacent fields containing unrelated numbers.
- **Fixed:** new `gemsentry/parsing/amounts.py` strips `(For 3 Years)`-style qualifiers, understands lakh/lac/crore/million and Indian comma grouping, stops the window at the next field label, and classifies every reading as `parsed` / `not_required` / `not_stated` / `unreadable` / `conflicting`. `extract_bid_signals` stores the state and the source text alongside the amount; `compute_eligibility` returns `unknown` (flag `turnover_req_unreadable` or `turnover_req_conflicting`) for anything it could not resolve, and `compute_recommendation` downgrades such a bid from Pursue to Review. Card-only `unknown` is deliberately untouched — no PDF is a different fact from an unreadable one.

### 3. Cached tenders can retain outdated expiry scores — high priority

- **Trigger:** A previously downloaded and analyzed tender expires, then another scrape runs without rediscovering that tender with changed listing facts.
- **Cause:** The download planner skips processed records before evaluating their current date window.
- **Reproduced:** A tender ending in 2020 retained `is_expired: false` and `recommendation: Pursue`; neither analysis nor download was queued.
- **Location:** `gemsentry/pipeline.py:150`.
- **Proposed fix:** Refresh date-dependent scoring for cached records on each run, without re-downloading unchanged PDFs. Preserve manual workflow statuses while updating automated recommendations.
- **Verification:** Cover expired bids, bids entering the minimum-days window, extended deadlines, and manual status pins.
- **Fixed:** `plan_downloads` now calls the new `refresh_cached_verdict` before taking its processed-record shortcut. That re-derives the date-dependent layer from the signals already stored on the analysis — no PDF read, no re-download — and `apply_verdict` keeps a manual pin. The plan summary line reports how many cached verdicts were refreshed.

### 4. Failed scrapes can display SUCCESS — medium priority

- **Trigger:** A background scrape raises an exception.
- **Cause:** The backend returns the job to `idle` without a distinct failure outcome. The dashboard appends a success message whenever a non-running job has logs.
- **Reproduced:** A simulated portal failure produced `status: idle` with no outcome/error field. The dashboard's idle branch unconditionally displays `[SUCCESS]`.
- **Locations:** `gemsentry/web/context.py:280`, `static/app.js:1276`, `gemsentry/web/tenders.py` status endpoint.
- **Proposed fix:** Track running, succeeded, partially completed, and failed outcomes separately from whether a job is active. Expose errors and incomplete-search warnings to the UI. Cover bulk scrape, single-bid acquisition, and rescore jobs.
- **Verification:** Simulate success, exceptions, empty successful searches, and partial retrieval failures; verify each displays the correct outcome.
- **Fixed:** `scrape_status` now separates `status` (is a job running) from `job`, `outcome` (`succeeded` / `partial` / `failed`), `error` and `warnings`, managed by `begin_job` / `finish_job` in `gemsentry/web/context.py`. Bulk scrape, single-bid acquisition and rescore all report through them; an external portal that fails makes the run `partial` rather than sinking the whole scrape. `/api/status` exposes the new fields and `static/app.js` renders them via `jobOutcomeMessage`, which also distinguishes an empty-but-successful search.

### 5. Database save failures are swallowed — high priority

- **Trigger:** SQLite rejects a save, for example because the database is locked.
- **Cause:** `save_metadata()` logs the database error but continues writing exports and returns normally.
- **Reproduced:** A simulated SQLite failure returned `None` without raising and still called the export writer.
- **Location:** `gemsentry/storage.py:199`.
- **Impact:** The database and exported files can disagree, while the caller believes the save succeeded.
- **Proposed fix:** Propagate failed database commits and do not publish exports for uncommitted data. Report export failures separately when the database commit succeeded.
- **Verification:** Confirm a failed commit leaves existing exports untouched and marks the job failed; test transaction rollback and export-only failure handling.
- **Fixed:** `save_metadata` raises the new `storage.StorageError` when the commit fails and does not reach the export writer, so the previous good exports survive and the background job reports `failed`. `write_exports` now returns a success flag and logs an export failure separately, since the store of record is already committed by that point.

## Additional issue noticed during follow-up inspection

The expanded eligibility panel labels every verdict other than `turnover_gap` as **Eligible**, including `unknown`. This is confirmed by the rendering branch in `static/app.js` near the `eligibilityVerdictLabel` assignment. Give unknown eligibility its own review label and neutral/warning styling. This needs a UI regression check.

- **Fixed:** a single `eligibilityPresentation()` helper now drives the panel: only an explicit `eligible` renders as a pass, and anything else reads "Needs Review — Eligibility Unconfirmed" in the warning colour. The tender card gained a matching `? Eligibility Unconfirmed` badge (`.tag-eligibility-unknown` in `static/app.css`) — showing no badge at all read as "no concerns", which was the same false reassurance. Still worth a manual pass over the dashboard.

## Proposed smarter detection

These are planned improvements, not implemented features:

1. **Evidence-backed extraction:** Store the source text for estimated value, minimum turnover, and experience requirements. Show it in tender details; add page references only when extraction actually tracks pages.
2. **Explicit extraction states:** Distinguish parsed, not stated, unreadable, conflicting, and explicitly not required. Do not interpret a failed parse as proof of eligibility.
3. **Unit-aware amounts:** Recognize rupees, lakh/lac, crore, Indian comma grouping, and decimal values. Handle label qualifiers such as `(For 3 Years)` without treating them as amounts.
4. **Conflict detection:** Flag inconsistent requirements within a document or differences between listing metadata and the PDF. Require review rather than silently choosing an unsupported value.
5. **Explainable confidence:** Base confidence on source evidence and successful checks. Keep EMD-derived bid-value estimates visibly separate from stated bid values.
6. **Fresh decisions from cached evidence:** Recompute date-sensitive scores and eligibility when relevant profile inputs change. Reparse PDFs when extraction rules change, using the existing text cache.
7. **Review safeguards:** Ensure uncertain eligibility and material extraction conflicts cannot appear as confidently eligible in badges or detailed explanations. Review any recommendation-rule changes against existing relaxation-policy tests.

## Other useful upgrades

- Checkpoint searches so interrupted runs can resume and retry only failed queries/pages.
- Show separate counts for listings scanned, unique tenders found, date/relevance exclusions, PDFs downloaded, analysis failures, and saved records.
- Record why a tender was filtered out, so users can investigate a missing result.

## Suggested implementation order

1. Protect manual decisions and make database failures visible.
2. Fix turnover parsing and unknown-eligibility handling.
3. Refresh cached expiry decisions.
4. Add accurate job outcomes and UI messages.
5. Add evidence/conflict displays, then resumable searches.

Use isolated regression tests first. Back up stored records before any subsequent production re-analysis. The audit reproductions used in-memory databases and mocks; they did not alter the user's tender records.
