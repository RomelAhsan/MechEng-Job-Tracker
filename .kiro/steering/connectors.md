# Connector coverage and sourcing policy

Repo-scoped guidance for expanding employer coverage in `config/companies.yaml`.

## Core sourcing rules (from JobAggregateSPEC.md)

- Public endpoints only. Never bypass logins, CAPTCHAs, auth, IP blocks, or
  access controls. A `401`/`403` means the board stays an honest `ats: html`
  stub; never fabricate tokens/tenants to get around it.
- Prefer official/public ATS APIs over HTML scraping.
- A board counts as usable only if its public endpoint returns a NON-ZERO list
  of real postings for the CORRECT employer. Record the exact endpoint +
  observed posting count in the entry's `notes`. (SmartRecruiters returns
  `200` + `totalFound=0` for almost any string, so a `200` with 0 postings is
  NOT usable.)
- Unconfirmed employers stay honest `ats: html` stubs with a `notes` line
  explaining why (JS-loaded Workday tenant, SuccessFactors / BambooHR /
  Oracle HCM / own portal, or auth-walled).

## Third-party data tools (e.g. Apify) — allowed, but free tier only

- The user is fine using a reputable, widely used third-party service (such as
  Apify Actors for Workday) to FETCH board data, because the service does the
  fetching, not the user's own machine/IP. This does not violate the no-scrape
  rule, whose purpose was to avoid the user being IP-banned.
- HARD CONSTRAINT: **the user will not pay for anything.** Any such tool must
  stay strictly within a free tier (Apify's free plan renews $5 credit/month,
  no credit card). Never trigger a paid Actor run. Before any run that could
  incur cost, stop and confirm it is free; if it is not, do not run it.
- Apify needs the user's own account/token (browser OAuth or `APIFY_TOKEN`
  env var / secret). Never paste tokens into chat, source, or command output.
- An Apify-backed Workday connector is a deferred follow-up, not part of the
  current coverage pass. The native Workday CXS connector
  (`jobtracker/connectors/workday.py`) already ingests real jobs from public
  boards; its only limitation is throughput on very large boards (it fetches
  each posting's detail page sequentially).
