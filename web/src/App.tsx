import { useCallback, useEffect, useMemo, useState } from "react";
import { AddCompany } from "./AddCompany";
import { loadFeed } from "./api";
import {
  addReportedId,
  dispatchReportJob,
  getReportedIds,
  getToken,
  removeReportedId,
} from "./apiGithub";
import { Countdown } from "./Countdown";
import { Coverage } from "./Coverage";
import {
  applyFilters,
  companyCounts,
  companyOptions,
  remoteOptions,
} from "./filtering";
import { HiddenJobs } from "./HiddenJobs";
import { JobCard } from "./JobCard";
import {
  DEFAULT_FILTERS,
  FRESHNESS_OPTIONS,
  TERM_LABELS,
  TERM_OPTIONS,
  type Feed,
  type Filters,
  type FreshnessHours,
  type HiddenJob,
  type Job,
} from "./types";

type View = "jobs" | "hidden" | "coverage" | "add";

export function App() {
  const [feed, setFeed] = useState<Feed | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [view, setView] = useState<View>("jobs");
  const [reported, setReported] = useState<string[]>(() => getReportedIds());
  const [notice, setNotice] = useState<string | null>(null);

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

  // Jobs reported as dead in this browser disappear at once, before the
  // report workflow commits and the site redeploys without them.
  const jobs = useMemo(
    () => (feed?.jobs ?? []).filter((j) => !reported.includes(j.job_id)),
    [feed, reported],
  );
  const visible = useMemo(() => applyFilters(jobs, filters), [jobs, filters]);
  const counts = useMemo(() => companyCounts(jobs), [jobs]);
  const cantCheck = jobs.filter((j) => j.link_status === "cant_check").length;
  const set = <K extends keyof Filters>(key: K, value: Filters[K]) =>
    setFilters((f) => ({ ...f, [key]: value }));

  async function reportDead(job: Job) {
    const token = getToken();
    if (!token) {
      setNotice(
        'To report a dead link, save your GitHub token once in the "Add a company" tab.',
      );
      return;
    }
    if (!window.confirm(`Hide "${job.title}" at ${job.company} for good? (You can restore it from the Hidden jobs tab.)`)) {
      return;
    }
    setReported(addReportedId(job.job_id));
    try {
      await dispatchReportJob(job, "hide", token);
      setNotice(`Hidden "${job.title}". It's removed for good after the next site update.`);
    } catch (err) {
      setReported(removeReportedId(job.job_id));
      setNotice(err instanceof Error ? err.message : "Could not report the job.");
    }
  }

  async function restore(job: HiddenJob) {
    const token = getToken();
    if (!token) return;
    try {
      await dispatchReportJob(job, "restore", token);
      setReported(removeReportedId(job.job_id));
      setNotice(`Restoring "${job.title}". It comes back after the next site update.`);
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Could not restore the job.");
    }
  }

  const tab = (id: View, label: string) => (
    <button
      type="button"
      className={view === id ? "tab tab-active" : "tab"}
      aria-pressed={view === id}
      onClick={() => setView(id)}
    >
      {label}
    </button>
  );

  return (
    <div className="page">
      <header className="masthead">
        <h1>Jobs I'm eligible for</h1>
        <p className="sub">
          Mechanical-engineering co-op &amp; internship postings matched to your
          profile, filtered to roles you can actually apply for and refreshed
          automatically. No sign-in needed to browse.
        </p>
        {feed && (
          <p className="stamp">
            {jobs.length} eligible {jobs.length === 1 ? "job" : "jobs"} · tracking{" "}
            {feed.tracked_company_count} companies
            {feed.pending_ai_count
              ? ` · ${feed.pending_ai_count} more awaiting AI check`
              : ""}
            {feed.hidden_count != null ? ` · ${feed.hidden_count} hidden` : ""}
          </p>
        )}
        <p className="countdown-row">
          <Countdown generatedAt={feed?.generated_at} onRefresh={refetch} />
        </p>
        <nav className="tabs" aria-label="Views">
          {tab("jobs", "Jobs")}
          {tab("hidden", `Hidden jobs${feed?.hidden_count != null ? ` (${feed.hidden_count})` : ""}`)}
          {tab("coverage", "Coverage")}
          {tab("add", "Add a company")}
        </nav>
      </header>

      {notice && (
        <div className="notice" role="status">
          <span>{notice}</span>
          <button type="button" className="link-button" onClick={() => setNotice(null)}>
            Dismiss
          </button>
        </div>
      )}

      {view === "add" && (
        <AddCompany trackedCompanies={feed?.tracked_companies ?? []} />
      )}

      {view === "hidden" && (
        <HiddenJobs onRestore={restore} canRestore={Boolean(getToken())} />
      )}

      {view === "coverage" && feed && <Coverage items={feed.coverage ?? []} />}

      {view === "jobs" && error && (
        <div className="state state-error" role="alert">
          <p>{error}</p>
          <p className="muted">
            The site updates after each collection run. If this is a brand-new
            deployment, check back after the next run completes.
          </p>
        </div>
      )}

      {view === "jobs" && !error && !feed && (
        <div className="state">Loading your jobs…</div>
      )}

      {view === "jobs" && feed && (
        <>
          {(feed.warnings ?? []).map((w) => (
            <div key={w.kind} className="warning-banner" role="alert">
              <span>⚠ {w.message}</span>{" "}
              <button type="button" className="link-button" onClick={() => setView("coverage")}>
                Open Coverage
              </button>
            </div>
          ))}

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
                  {c} ({counts[c]})
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
            {cantCheck > 0
              ? ` · ${cantCheck} tagged "Can't be checked" (still shown)`
              : ""}
          </p>

          {visible.length === 0 ? (
            <div className="state">
              {jobs.length === 0
                ? "No eligible jobs yet. The next collection run will populate this."
                : "No jobs match these filters. Try clearing the search or lowering the minimum fit."}
            </div>
          ) : (
            <section className="grid">
              {visible.map((job) => (
                <JobCard key={job.job_id} job={job} onReportDead={reportDead} />
              ))}
            </section>
          )}
        </>
      )}

      <footer className="sitefoot">
        <p>
          Every shown job is re-checked on each run: closed postings are removed,
          and postings whose job site blocks our checker are tagged "Can't be
          checked". The Hidden jobs tab lists everything we found but don't show.
        </p>
      </footer>
    </div>
  );
}
