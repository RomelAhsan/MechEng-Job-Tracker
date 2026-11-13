// Pure, framework-free filtering + sorting over the already-eligible job set.
//
// The feed already contains ONLY jobs the user is eligible for (the backend's
// Stage-1 hard filter INTERSECT the Stage-2 AI gate). Nothing here re-decides
// eligibility; this is purely the in-browser narrowing (search/company/remote/
// term/min-fit/only-new) and sort that make the page feel instant. Kept pure
// so it is trivially unit-testable.

import type { Filters, Job, SortKey } from "./types";

function matches(job: Job, f: Filters): boolean {
  if (f.search.trim()) {
    const q = f.search.trim().toLowerCase();
    const hay = `${job.title} ${job.company} ${job.location}`.toLowerCase();
    if (!hay.includes(q)) return false;
  }
  if (f.company && job.company !== f.company) return false;
  if (f.remote && job.remote_status !== f.remote) return false;
  if (f.term && job.term_season !== f.term) return false;
  if ((job.fit_score ?? 0) < f.minFit) return false;
  if (f.freshnessHours > 0 && !withinHours(job, f.freshnessHours)) return false;
  return true;
}

/** True if the job was first seen within the last `hours`. */
function withinHours(job: Job, hours: number): boolean {
  const seen = job.first_seen_at ?? job.posted_at;
  if (!seen) return false;
  const ts = Date.parse(seen);
  if (Number.isNaN(ts)) return false;
  return Date.now() - ts <= hours * 3600_000;
}

function compare(a: Job, b: Job, sort: SortKey): number {
  if (sort === "fit") {
    const byFit = (b.fit_score ?? -1) - (a.fit_score ?? -1);
    if (byFit !== 0) return byFit;
  }
  // Newest first by first_seen_at, then posted_at; used both as the primary
  // "newest" sort and as the tiebreaker for equal fit scores.
  const at = (j: Job) =>
    Date.parse(j.first_seen_at ?? j.posted_at ?? "") || 0;
  return at(b) - at(a);
}

export function applyFilters(jobs: Job[], f: Filters): Job[] {
  return jobs.filter((j) => matches(j, f)).sort((a, b) => compare(a, b, f.sort));
}

/** Distinct, sorted company names present in the feed (for the dropdown). */
export function companyOptions(jobs: Job[]): string[] {
  return Array.from(new Set(jobs.map((j) => j.company).filter(Boolean))).sort();
}

// Term options come from the fixed SPEC set (spring/summer/winter), NOT from
// whatever happens to be in the feed, so the user always sees all three.
// (Previously only terms present in the current data showed, so some were
// missing.) Imported where the dropdown is rendered.

/** Distinct, sorted remote statuses present in the feed. */
export function remoteOptions(jobs: Job[]): string[] {
  return Array.from(new Set(jobs.map((j) => j.remote_status).filter(Boolean))).sort();
}
