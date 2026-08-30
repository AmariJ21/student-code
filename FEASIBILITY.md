# Feasibility Report: Congressional Trading Disclosure Backtest

Research date: 2026-08-30. This report is the required Phase 1 deliverable and is
written *before* the bulk of the implementation. It answers the 10 questions from
the spec honestly, including the ways the ideal experiment is **not** fully
achievable with public/free data. Every limitation documented here is enforced in
code (flags, exclusions, or explicit "not implemented") rather than papered over.

## 1. Official disclosure sources

| Chamber | Source | Format | Notes |
|---|---|---|---|
| Senate | `efdsearch.senate.gov` (eFD system) | Search UI backed by a DataTables JSON endpoint; individual Periodic Transaction Reports (PTRs) are served as PDF (recent filings are digitally generated/text-extractable; some legacy ones are scanned images) | Covers filings since CY2012 (STOCK Act). **The site requires clicking through a legal agreement** before the search endpoint will respond — see §9 (compliance). |
| House | `disclosures-clerk.house.gov` / `fd.house.gov` | Search form returns a list of PTR PDFs by filer/year | Same STOCK Act 45-day deadline. Historically a much larger fraction of House PTRs are scanned image PDFs (no embedded text layer) than Senate ones. |

Both are the ground-truth sources. There is no official bulk API for either
chamber's transaction data.

## 2. House vs. Senate disclosure differences

- Both file the same PTR form (transaction type, asset, date range for the
  transaction date, amount range, owner code) under STOCK Act §407(c)(45-day
  deadline, or 30 days after the filer becomes aware, whichever is first).
- Senate PTRs are viewable as server-rendered HTML tables (confirmed live —
  `efdsearch.senate.gov/search/view/ptr/<uuid>/`) with a real `Transaction Date /
  Owner / Ticker / Asset Name / Asset Type / Type / Amount` table, which is far
  easier to parse reliably than a PDF. House PTRs are PDF-only; more recent
  ones (roughly the current electronic-filing era) carry an embedded, subsetted
  text layer (confirmed structurally on a live sample — embedded CID fonts,
  not a flat scanned image) and are extractable with `pdfplumber`, but older
  paper-era submissions are scanned images and are not reliably extractable
  without OCR, which this project does not implement (see §8).
- **Senate publishes a minute-resolution filing timestamp; House does not** —
  see §3/§4. This is a real, verified chamber asymmetry, not a symmetric gap.
- Community projects historically filled this gap by scraping both sites daily
  and republishing normalized JSON. As of this research date, **House Stock
  Watcher's data bucket is returning HTTP 403 and its GitHub repo has not been
  updated since mid-2025** — it is effectively dead and must not be relied on for
  current data. The Senate Stock Watcher GitHub repo (`timothycarambat/senate-stock-watcher-data`)
  still appears structurally intact but its update cadence could not be verified
  as "live" from this research pass. **Conclusion: treat any third-party
  aggregator as a possibly-stale bootstrap/backfill source, never as the sole or
  authoritative pipeline.** The system's ingestion is built to hit the official
  sites directly and treats community datasets as optional supplemental input,
  tagged with their own provenance.

## 3. Are historical disclosure *timestamps* available?

**Verified directly against the live systems while writing this report (not
assumed) — and the answer differs by chamber, which is itself an important
finding:**

- **Senate**: each individual PTR page on `efdsearch.senate.gov` (e.g.
  `/search/view/ptr/<uuid>/`) renders a line reading `Filed MM/DD/YYYY @ H:MM AM/PM`.
  This was confirmed present, and consistent in format, across every
  electronically-filed PTR checked. It is a genuine minute-resolution
  disclosure timestamp published by the Senate's own system — a real,
  meaningfully better signal than initially expected. Its timezone is not
  explicitly labeled on the page; it is assumed to be US Eastern (where the
  Senate's eFD system and Capitol are located), which is documented as an
  assumption, not verified from the page markup itself.
- **House**: no equivalent time-of-day field exists. The official bulk index
  (`disclosures-clerk.house.gov/public_disc/financial-pdfs/<year>FD.zip`, a
  live, working endpoint) and the individual PTR PDFs it points to
  (`.../ptr-pdfs/<year>/<DocID>.pdf`) both carry only a `FilingDate` — a date,
  no time. This is a genuine, documented **chamber asymmetry**: Senate PTRs
  can carry `SCRAPER_OBSERVED`-grade-or-better timing (in fact closer to
  `EXACT`), House PTRs cannot, and are `DATE_ONLY_ASSUMED` by necessity.

## 4. Can we determine when a filing "actually became publicly accessible"?

- **Senate**: yes, to the minute, via the `Filed @ H:MM` field described above.
  This is treated as `disclosure_confidence = EXACT` (modulo the timezone
  assumption noted above).
- **House**: only to the day. A third-party scraper's own "first observed"
  timestamp is a usable proxy going forward once a live poller is running, but
  is unavailable retroactively and reflects when *our* poller saw it, not the
  true first public moment — tagged `SCRAPER_OBSERVED` when used, distinct from
  the default `DATE_ONLY_ASSUMED`.
- **Design decision**: `disclosure_timestamp` is populated from the real
  Senate filing time when available; otherwise (House, or any Senate filing
  where the field can't be parsed, e.g. legacy paper filings) it is
  synthesized from `disclosure_date` using a conservative, documented
  assumption (next market open, 9:30 AM ET, on/after `disclosure_date`).
  Every transaction carries a `disclosure_confidence` field:
  - `EXACT` — real Senate-published filing timestamp.
  - `SCRAPER_OBSERVED` — our own poller's first-seen timestamp (House, going forward).
  - `DATE_ONLY_ASSUMED` — the default/fallback case, timestamp is inferred, not observed.

This satisfies the spec's requirement to flag low-confidence timestamps rather
than pretend precision that doesn't exist — while also not *understating*
confidence where the Senate genuinely publishes better data than the initial
literature search (community write-ups, third-party API docs) suggested.

## 5. How disclosure time is determined (methodology adopted)

- Senate: `market_entry_timestamp = next_market_open_at_or_after(filed_timestamp_ET) + entry_delay`.
- House (and any unparseable Senate filing): `market_entry_timestamp = next_market_open_at_or_after(disclosure_date, assume 9:30 AM ET) + entry_delay`.

`entry_delay` is a first-class backtest parameter (immediate/5min/15min/30min/1hr/next-open —
see Robustness Testing) because even a real Senate filing timestamp doesn't
prove a trader could transact at that exact instant; the system tests a
*range* of plausible reaction times rather than assuming the best case in
either chamber.

## 6. Transaction date vs. disclosure date separability

Yes — both are explicit, separate fields on every PTR and are preserved as
distinct columns (`transaction_date` vs `disclosure_date`) all the way through
to the trade record. The engine is hard-coded to key off `disclosure_date`
(never `transaction_date`) for entry timing; `transaction_date` is retained only
for reporting the disclosure delay, never for pricing.

## 7. Stock price data availability and resolution

| Resolution | Provider (free) | Real historical coverage |
|---|---|---|
| Daily (adjusted for splits/dividends) | Yahoo Finance, via a direct HTTP client against its public chart JSON endpoint (see implementation note below) | Full listed history for surviving tickers; effectively unlimited for this project's date ranges |
| 5m/15m/30m/60m | Yahoo's chart API | **Only the trailing ~60 days from today** — Yahoo does not serve older intraday bars |
| 1m | Yahoo's chart API | **Only the trailing ~7 days from today** |
| 1m–1s, multi-year history | Polygon.io (paid) | 2 years free tier, 5/10/20 years on paid plans ($29–$199+/mo) |
| Tick data | Not pursued | No free source exists; paid tick data (e.g. exchange direct feeds) is out of scope/cost-prohibitive for this project |

**This is the second major constraint on the ideal experiment.** A multi-year
backtest (e.g. 2019–2025) **cannot** use minute or tick data for the bulk of its
history from free sources — only daily OHLC is available across the full
window. Minute/5-minute data is only obtainable for whatever falls inside the
trailing 60/7-day window from whenever the system is actually run, which is
useful for *validating recent trades* and for a live-forward paper-trading
extension, but not for a historical backtest spanning years.

**Implementation note**: the market-data client hits Yahoo's public chart JSON
endpoint (`query1.finance.yahoo.com/v8/finance/chart/...`) directly via
`requests` rather than depending on the `yfinance` package, whose bundled
browser-impersonation HTTP client (`curl_cffi`) was found during development
to fail outright in this project's own build/verification environment
(TLS/SSL errors) even though the same endpoint is reachable fine with a plain
HTTP client and a normal User-Agent header. This is the same underlying data
source and the same resolution/history limits described above; it's a more
minimal and more portable dependency, not a different data source.

**Design decision**: the data-resolution hierarchy from the spec (tick → 1m →
5m → daily) is implemented as a real fallback chain in `MarketDataProvider`,
and the system will use the best resolution actually available for a given
trade's date range. For anything older than ~60 days at run time, that will be
daily bars, and the report must say so per-trade (`price_resolution` field) and
in aggregate (% of trades priced at daily resolution). Optional integration
with Polygon.io is provided for users willing to pay for deeper intraday
history; it is off by default (no fabricated key, no assumed subscription).

## 8. Historical data limitations (must be visible in every report)

- **Survivorship bias**: Yahoo Finance (and most free providers) primarily
  serve tickers that are still listed or recently delisted. Historical prices
  for older delisted/merged/acquired securities are frequently unavailable.
  The system does **not** silently drop these — a trade whose ticker has no
  price data at the required date is marked `EXCLUDED_NO_PRICE_DATA` and
  counted/reported, never removed silently.
- **Ticker changes / corporate actions**: splits and dividends are handled via
  Yahoo's adjusted close series (documented in code: we backtest on
  split-and-dividend-adjusted prices specifically so a 10:1 split is not
  misread as a 90% loss). Ticker-symbol changes and mergers require a manual
  alias table (`securities.ticker_aliases`); anything not in that table and not
  resolvable will be excluded and reported, not guessed.
- **Sector / market-cap classification is present-day, not historical.**
  Yahoo's `sector`/`marketCap` fields reflect the company *today*. Sector is a
  reasonable static approximation for most companies; market-cap bucketing of a
  trade from 2019 using 2026 shares-outstanding/price is an approximation and
  is labeled as such in every breakdown that uses it.
- **Politician name matching across sources is best-effort, not exact.**
  Senate eFD gives filer names as entered on the form (e.g. "Thomas H
  Tuberville", "William F Hagerty, IV"), which frequently don't
  string-match the common/short names in the unitedstates/congress-legislators
  roster ("Tommy Tuberville", "Bill Hagerty") -- confirmed during live
  testing. Unmatched filers get a minimal `Politician` record created on the
  fly (bioguide_id left null) rather than being silently merged into the
  wrong person or dropped; this means the same real senator can end up as
  two separate `Politician` rows (one roster-matched, one ad hoc) until the
  matching logic is improved with a proper name-normalization/alias step.
- **Ticker-symbol reuse can silently return the wrong company's price
  history.** Confirmed live during development: Yahoo's history for "PARA"
  returned prices in the tens of thousands of dollars per share for January
  2024, because Paramount Global's old ticker was reassigned to an unrelated
  micro-cap after Paramount delisted, and Yahoo's own `longName` for that
  data was the new company, not Paramount. The backtester cross-checks the
  provider's company name against the disclosure's asset name and excludes
  the trade (`EXCLUDED_TICKER_IDENTITY_MISMATCH`) rather than trading on a
  silently wrong price series, but this is a coarse heuristic, not a
  guarantee -- a reused ticker with a similarly-named successor company
  would not be caught.
- **OCR not implemented.** House PTRs that are scanned images (pre-2020-ish,
  and any paper-filed candidate reports) have no extractable text layer. The
  ingestion pipeline detects this (near-empty `pdfplumber` extraction) and
  records the filing as `parse_confidence = "OCR_REQUIRED_NOT_IMPLEMENTED"`
  with the transactions left blank rather than fabricated — it does not
  attempt OCR. A user who needs these specific filings would need to add an
  OCR step themselves.
- **Rate limits / cost**: Yahoo's chart API is free but unofficial and rate-limited by
  Yahoo in practice (bursts get throttled); the provider layer includes local
  caching (Parquet-backed) and backoff to stay usable for a multi-year, many-ticker
  backtest without re-fetching on every run.

## 9. Compliance note (not a technical limitation, but a real constraint)

The Senate eFD system's terms of use explicitly restrict use of Financial
Disclosure Report data for commercial purposes, credit-rating determination, or
solicitation — but permit inspection/analysis, and this project is personal,
non-commercial research (explicitly per the task). The scraper implements the
same "I have read and understand the restrictions" acknowledgment step a human
would click through, gated behind an explicit config flag
(`ingestion.senate_efd.accept_terms: true`) that the user must set themselves —
the system will not silently accept legal terms on the user's behalf. This is
documented in `README.md` and enforced at runtime (ingestion refuses to run
without the flag).

## 10. Can the strategy be backtested without severe look-ahead bias?

Yes, **with the above caveats treated as first-class, visible uncertainty**
rather than hidden assumptions:

- Entry is always keyed off `disclosure_date`/`disclosure_timestamp`, never
  `transaction_date` (hard invariant enforced in the engine — see
  `backtest/engine.py`, which physically cannot see transaction-date-only
  price rows).
- Every trade's `disclosure_confidence` and `price_resolution` are recorded and
  surfaced in every report; headline metrics are always shown alongside the
  breakdown by confidence/resolution so a reader can judge how much of any
  edge rests on lower-confidence assumptions.
- The same-bar problem (daily OHLC can't tell you whether TP or SL happened
  first) is handled by a documented, configurable rule — default is the
  conservative assumption (stop-loss/adverse side wins ties), with a strict
  mode that instead marks such trades `AMBIGUOUS_SAME_BAR` and excludes them
  from headline stats while still reporting their count. The system never
  assumes the favorable outcome by default.

### Necessary modifications to the originally-proposed strategy/spec

1. **"Enter as soon as disclosure becomes public"** is implemented as *a family
   of entry-delay assumptions* (0/5/15/30/60 min, next open) applied on top of
   `disclosure_timestamp`, not a single idealized instant fill — even where
   Senate gives us a real minute-level filing time, nothing proves a trader
   could transact at that exact instant, and House disclosures only have
   date-level confidence in the first place.
2. **Full-history backtests run on daily bars.** Intraday validation is a
   secondary, trailing-window-only analysis, not the backbone of the headline
   result.
3. **Sector/market-cap breakdowns are labeled as present-day approximations.**
4. Trades with no obtainable price data, no resolvable ticker, or same-bar
   ambiguity (in strict mode) are **excluded from performance stats but always
   counted and reported** — never silently dropped.
5. Given (1)-(2), reported significance tests should be read as "is there
   evidence of an edge under realistic disclosure-latency and daily-bar
   assumptions," not "did a bot literally beat the market by front-running
   Congress within minutes" — the data cannot support the stronger claim in
   either direction, and the report says so explicitly in a Limitations
   section on every run.

## What this means for the final classification

Because of (2) and (3) above, even a strong headline backtest result cannot by
itself be read as proof of a minute-level informational edge — it can only
show whether *acting on the disclosure once public, within realistic reaction
times, on realistically-executed daily/available-intraday prices* would have
been profitable after costs. The viability classifier (Phase 7) requires
out-of-sample performance, statistical significance vs. randomized-entry and
buy-and-hold benchmarks, and robustness across slippage/delay assumptions
before calling anything better than "weak edge" — a good number on the full
sample alone is explicitly insufficient (see `backtest/classify.py`).

---

## Addendum: restructuring toward the researched motif

After the baseline system above was built and validated, further research
(academic literature plus current leaderboards and named individuals like
Nancy Pelosi) showed that the mechanical "any disclosed BUY, flip at a fixed
target" baseline is very unlikely to match how the standout individual
performers actually made money. This addendum documents what changed, why,
and what it verified live, in the same spirit as the rest of this document:
nothing here is asserted without having been checked against real data.

### What the research showed (see the conversation's research summary for full citations)

- Post-STOCK-Act academic studies (Belmont/Sacerdote/Sehgal/Van Hoek, NBER 2020;
  Eggers/Hainmueller) find senators' stock **purchases** do not beat matched
  benchmarks on average — the broad "follow any member" signal is weak to
  absent once you're not using their actual (unknowable-to-a-copier)
  transaction-date entry.
- Performance is heavily concentrated in **specific individuals** (in 2025,
  only 100 of 311 disclosed portfolios, ~32%, beat the S&P 500) and in
  **leadership status** (a documented ~47-point annual premium after a
  member ascends to a leadership role).
- The standout individual cases (Pelosi being the most visible) are driven
  mostly by **leveraged long-dated call options** on concentrated positions,
  held for years through a market cycle and exercised into stock — not by a
  quick mechanical stock flip. Verified live: a real Pelosi PTR describes
  buying LEAPS calls on GOOGL/AMZN/NVDA/TEM/VST, and a real Senator's PTR
  shows the same pattern via short-dated calls/puts on individual names and
  ETFs (ARKK, XBI, CLF, GILD, ADBE, ...).

None of this proves the strategy works — it's a prior about *where any edge
plausibly lives*, which is what the restructuring below is built to actually
test rather than assume.

### What changed, and what was verified live while building it

1. **Options are now simulated**, not excluded. `backtest/options.py` prices
   them with Black-Scholes-Merton (validated against textbook values), using
   the underlying's own trailing realized volatility as an IV proxy (no free
   historical IV data exists at these dates/strikes -- a documented
   approximation, not a fabrication) and coarse constants for the risk-free
   rate and dividend yield. **Parsing option details required a real rewrite**,
   not just a new regex: a live-fetched Pelosi PTR showed strike/expiration/
   call-or-put living in a free-text comment line the original parser didn't
   even capture, and showed far more severe column-interleaving than the
   original House parser was built for (the ticker itself, not just an
   asset-type tag, can land after the transaction-type/date/amount block).
   The parser was rewritten around proximity-paired anchors instead of one
   sequential regex. Also found live: the same ticker can carry two different
   asset types in one filing (NVDA as both a stock sale and an option
   purchase in the same Pelosi PTR), and an "Exercised N options..." event
   must resolve to a stock position, not a fresh option purchase at an
   already-expired strike.
2. **A `long_hold` strategy mode** exists alongside the original `short_term_target`
   mode: no fixed take-profit or hold-day ceiling, just a trailing stop from
   the position's post-entry peak, and — for options — realizing intrinsic
   value at an ITM expiration and rolling it into an equivalent-dollar stock
   position that keeps riding the same trailing-stop rule (approximating
   "exercise and hold for years" without literally modeling the exercise
   cash outlay, which is a stated simplification). Verified live: a
   simulated NVDA $80 LEAPS call replicating the real Pelosi Jan-2025 trade
   correctly rode into the real DeepSeek-shock crash for a realistic
   stop-out, and a hypothetical NVDA $40 call from Jan 2024 correctly
   exercised at its March 2024 expiration, rolled into stock, and rode
   NVDA's real 2024 AI rally to a trailing-stop exit during the real August
   2024 pullback.
3. **A causal, point-in-time politician leaderboard** (`backtest/leaderboard.py`)
   is the methodologically honest version of "follow top performers": a
   politician only becomes eligible to be followed once they have enough
   *already-closed* trades in the backtest itself, ranked by trailing
   realized return as of the moment a new candidate is being considered —
   never by performance that hasn't happened yet from that point's
   perspective. A separate, clearly-labeled "named case study" mode exists
   for restricting a run to specific known individuals (e.g. just Pelosi)
   for illustration — this is hindsight selection, not a backtest, and
   `backtest/classify.py` structurally refuses to return anything but
   `CASE STUDY (NOT A GENERAL-EDGE CLAIM)` for such a run regardless of how
   good its numbers look, so this can't be misreported by accident.
4. **Leadership and (present-day) committee data** were added to the
   `Politician` schema, sourced from unitedstates/congress-legislators'
   real `leadership_roles` field (dated, so it's usable causally) and
   `committee-membership-current.yaml` (a present-day snapshot only — no
   free historical committee-roster archive exists, so this is a weaker,
   present-day-only signal, analogous to the sector/market-cap caveat above).

### A real, non-obvious finding from live-testing this end-to-end

Running the restructured system against real 2024-2025 Senate/House data
(1,574 loaded BUY candidates) surfaced something worth stating plainly:
**long-hold positions tie up portfolio capacity for a long time** (average
holding period in that run was ~548 days), so with the default
`max_positions: 10`, **92.5% of candidates were excluded purely on
portfolio-capacity grounds**, not on data or price problems. Only 16 trades
actually got simulated in that run, and while they looked spectacular
(total return well over 100%, profit factor > 3), 16 is far below the
classifier's minimum sample size, so the correct, honest output was
`INSUFFICIENT DATA` — not a viability claim. This is exactly the intended
behavior (a good-looking number on a tiny sample must never pass as
evidence), but it also means: a real long-hold-mode study needs either a
much longer ingestion window (years, to accumulate enough trades) or an
explicitly larger `max_positions`/smaller `position_size_pct`, chosen and
justified up front — not tuned after seeing results, which would be the
exact test-set-leakage the spec prohibits.

### Necessary modification to the classification bar

Because the mechanical baseline and the researched motif are genuinely
different strategies (different holding logic, different instrument mix,
different candidate-selection logic), they are reported as separate,
separately-classified runs — a report never blends "all Congress, quick
flip" trades with "leaderboard-gated, long-hold, options-inclusive" trades
into one headline number. Compare their `report.html` outputs side by side
rather than expecting one run to answer both questions.

---
See `IMPLEMENTATION_PLAN.md` for architecture, schema, and methodology.
