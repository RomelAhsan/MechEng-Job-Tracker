import type { CoverageItem } from "./types";

// Coverage tab: every company we track, whether its job board was checked
// successfully, why not if it wasn't, and how many of its jobs are shown vs
// hidden. Lets you verify nothing is silently missing.

const STATUS_LABEL: Record<CoverageItem["status"], string> = {
  ok: "Checked",
  failing: "Can't be checked",
  never_checked: "Not checked yet",
  not_supported: "No supported job board",
};

function capitalize(text: string): string {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
}

function formatDate(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d.toLocaleDateString();
}

function Row({ c }: { c: CoverageItem }) {
  const since = formatDate(c.failing_since);
  return (
    <li className={`cov-item cov-${c.status}`}>
      <div className="cov-main">
        <span className="cov-name">{c.name}</span>
        <span className={`tag cov-tag-${c.status}`}>{STATUS_LABEL[c.status]}</span>
        <span className="cov-ats">{c.ats}</span>
      </div>
      <div className="cov-detail">
        {c.status === "ok" && (
          <>
            Last checked {c.last_success_ago ?? "recently"}
            {c.via === "apify" ? " (through Apify)" : ""}
          </>
        )}
        {c.status === "failing" && (
          <>
            {capitalize(c.error ?? "The job board could not be read")}
            {since ? ` (since ${since})` : ""}.{" "}
            {c.last_success_ago
              ? `Last successful check ${c.last_success_ago}.`
              : "Never checked successfully."}
          </>
        )}
        {c.status === "never_checked" && <>Waiting for its first check.</>}
        {c.status === "not_supported" && (
          <>
            We can't read this company's job board yet. Add its careers page URL
            in the "Add a company" tab if it uses a supported system.
          </>
        )}
      </div>
      {c.status !== "not_supported" && (
        <div className="cov-counts">
          {c.postings} postings tracked · {c.shown} shown · {c.hidden} hidden
          {c.careers_url && (
            <>
              {" · "}
              <a href={c.careers_url} target="_blank" rel="noreferrer">
                careers page
              </a>
            </>
          )}
        </div>
      )}
    </li>
  );
}

export function Coverage({ items }: { items: CoverageItem[] }) {
  const tracked = items.filter((c) => c.status !== "not_supported");
  const failing = items.filter((c) => c.status === "failing");
  const unsupported = items.filter((c) => c.status === "not_supported");
  return (
    <section className="coverage" aria-label="Coverage">
      <h2>Companies we're tracking</h2>
      <p className="muted">
        {tracked.length} companies are tracked: {tracked.length - failing.length} checked
        successfully, {failing.length} can't be checked right now.
        {unsupported.length > 0
          ? ` ${unsupported.length} more have no supported job board yet.`
          : ""}{" "}
        "Hidden" counts student roles we found but don't show; the Hidden jobs tab
        says why for each one.
      </p>
      {tracked.length === 0 ? (
        <p className="muted">No companies are being tracked yet.</p>
      ) : (
        <ul className="cov-list">
          {tracked.map((c) => (
            <Row key={c.id} c={c} />
          ))}
        </ul>
      )}
      {unsupported.length > 0 && (
        <details className="cov-unsupported">
          <summary>Companies without a supported job board ({unsupported.length})</summary>
          <ul className="cov-list">
            {unsupported.map((c) => (
              <Row key={c.id} c={c} />
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
