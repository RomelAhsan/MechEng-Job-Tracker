import { useCallback, useEffect, useMemo, useState } from "react";
import { loadFeed } from "./api";
import { Countdown } from "./Countdown";
import { applyFilters, companyOptions, remoteOptions } from "./filtering";
import { JobCard } from "./JobCard";
import {
  DEFAULT_FILTERS,
  FRESHNESS_OPTIONS,
  TERM_OPTIONS,
  type Feed,
  type Filters,
  type FreshnessHours,
} from "./types";

const TERM_LABELS: Record<string, string> = {
  spring: "Spring (May–Aug)",
  summer: "Summer",
  winter: "Winter (Jan–Apr)",
};

function formatStamp(iso: string | null): string {
  if (!iso) return "unknown";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "unknown" : d.toLocaleString();
}

export function App() {
  const [feed, setFeed] = useState<Feed | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);

  const refetch = useCallback(() => {
    loadFeed()
      .then((f) => {
        setFeed(f);
        setError(null);
      })
      .catch(() => {
        /* keep showing the current data if a refetch blips */
      });
  }, []);

  useEffect(() => {
    const ctrl = new AbortController();
    loadFeed(ctrl.signal)
      .then(setFeed)
      .catch((e: unknown) => {
        if ((e as { name?: string }).name !== "AbortError") {
          setError(e instanceof Error ? e.message : "Could not load jobs.");
        }
      });
    return () => ctrl.abort();
  }, []);

  const jobs = feed?.jobs ?? [];
  const visible = useMemo(() => applyFilters(jobs, filters), [jobs, filters]);
  const set = <K extends keyof Filters>(key: K, value: Filters[K]) =>
    setFilters((f) => ({ ...f, [key]: value }));

  return (
    <div className="page">
      <header className="masthead">
        <h1>Jobs I'm eligible for</h1>
        <p className="sub">
          Mechanical-engineering co-op &amp; internship postings matched to your
          profile — filtered to roles you can actually apply for and refreshed
          automatically. No sign-in needed to browse.
        </p>
        {feed && (
          <p className="stamp">
            {feed.job_count} eligible {feed.job_count === 1 ? "job" : "jobs"} ·
            updated {formatStamp(feed.generated_at)} · tracking{" "}
            {feed.tracked_company_count} companies
          </p>
        )}
        <p className="countdown-row">
          <Countdown onDue={refetch} />
        </p>
      </header>

      {error && (
        <div className="state state-error" role="alert">
          <p>{error}</p>
          <p className="muted">
            The site updates after each collection run. If this is a brand-new
            deployment, check back after the next run completes.
          </p>
        </div>
      )}

      {!error && !feed && <div className="state">Loading your jobs…</div>}

      {feed && (
        <>
          <section className="controls" aria-label="Filters">
            <input
              className="search"
              type="search"
              placeholder="Search title, company, or location"
              value={filters.search}
              onChange={(e) => set("search", e.target.value)}
            />
            <select
              value={filters.company}
              onChange={(e) => set("company", e.target.value)}
              aria-label="Company"
            >
              <option value="">All companies</option>
              {companyOptions(jobs).map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
            <select
              value={filters.term}
              onChange={(e) => set("term", e.target.value)}
              aria-label="Term"
            >
              <option value="">Any term</option>
              {TERM_OPTIONS.map((t) => (
                <option key={t} value={t}>
                  {TERM_LABELS[t] ?? t}
                </option>
              ))}
            </select>
            <select
              value={filters.remote}
              onChange={(e) => set("remote", e.target.value)}
              aria-label="Setting"
            >
              <option value="">Any setting</option>
              {remoteOptions(jobs).map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
            <label className="minfit">
              Min fit {filters.minFit}
              <input
                type="range"
                min={0}
                max={100}
                value={filters.minFit}
                onChange={(e) => set("minFit", Number(e.target.value))}
              />
            </label>
            <select
              value={filters.freshnessHours}
              onChange={(e) =>
                set("freshnessHours", Number(e.target.value) as FreshnessHours)
              }
              aria-label="Posted within"
            >
              {FRESHNESS_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            <select
              value={filters.sort}
              onChange={(e) => set("sort", e.target.value as Filters["sort"])}
              aria-label="Sort"
            >
              <option value="fit">Sort: best fit</option>
              <option value="newest">Sort: newest</option>
            </select>
          </section>

          <p className="count">
            Showing {visible.length} of {jobs.length}
          </p>

          {visible.length === 0 ? (
            <div className="state">
              {jobs.length === 0
                ? "No eligible jobs yet — the next collection run will populate this."
                : "No jobs match these filters. Try clearing the search or lowering the minimum fit."}
            </div>
          ) : (
            <section className="grid">
              {visible.map((job) => (
                <JobCard key={job.job_id} job={job} />
              ))}
            </section>
          )}
        </>
      )}

      <footer className="sitefoot">
        <p>
          Read-only view. Saving jobs, email alerts, and AI-on-demand live in the
          full app. Data regenerates after each automated collection run.
        </p>
      </footer>
    </div>
  );
}
