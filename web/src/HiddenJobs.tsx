import { useEffect, useMemo, useState } from "react";
import { loadHidden } from "./api";
import { HIDDEN_GROUP_LABELS, type HiddenJob } from "./types";

const REPO = "RomelAhsan/MechEng-Job-Tracker";

// Pre-filled GitHub issue for a job that should have been shown, so the rule
// that hid it can be fixed (no server needed).
function shouldBeEligibleUrl(job: HiddenJob): string {
  const title = `Missed job: ${job.title} @ ${job.company}`;
  const body = [
    "This posting was hidden but I think I'm eligible.",
    "",
    "**Why (please edit this line):** ",
    "",
    "---",
    "_Auto-filled details:_",
    `- Job: ${job.title}`,
    `- Company: ${job.company}`,
    `- Location: ${job.location} (${job.country ?? "country unknown"})`,
    `- Hidden because: ${job.reasons.map((r) => `${r.label}${r.detail ? ` (${r.detail})` : ""}`).join("; ")}`,
    `- Apply: ${job.application_url ?? "-"}`,
    `- job_id: ${job.job_id}`,
  ].join("\n");
  return (
    `https://github.com/${REPO}/issues/new?labels=missed-job` +
    `&title=${encodeURIComponent(title)}&body=${encodeURIComponent(body)}`
  );
}

function groupLabel(code: string): string {
  return HIDDEN_GROUP_LABELS[code] ?? code;
}

export function HiddenJobs({
  onRestore,
  canRestore,
}: {
  onRestore?: (job: HiddenJob) => void;
  canRestore?: boolean;
}) {
  const [jobs, setJobs] = useState<HiddenJob[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [group, setGroup] = useState("");
  const [search, setSearch] = useState("");
  const [onlyFlagged, setOnlyFlagged] = useState(false);

  useEffect(() => {
    const ctrl = new AbortController();
    loadHidden(ctrl.signal)
      .then((f) => setJobs(f.jobs ?? []))
      .catch((e: unknown) => {
        if ((e as { name?: string }).name !== "AbortError") {
          setError(e instanceof Error ? e.message : "Could not load hidden jobs.");
        }
      });
    return () => ctrl.abort();
  }, []);

  const counts = useMemo(() => {
    const out: Record<string, number> = {};
    for (const j of jobs ?? []) out[j.group] = (out[j.group] ?? 0) + 1;
    return out;
  }, [jobs]);
  const flaggedCount = (jobs ?? []).filter((j) => j.ai_audit?.ai_eligible).length;

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (jobs ?? []).filter(
      (j) =>
        (!group || j.group === group) &&
        (!onlyFlagged || j.ai_audit?.ai_eligible) &&
        (!q ||
          `${j.title} ${j.company} ${j.location}`.toLowerCase().includes(q)),
    );
  }, [jobs, group, search, onlyFlagged]);

  if (error) {
    return (
      <div className="state state-error" role="alert">
        <p>{error}</p>
      </div>
    );
  }
  if (!jobs) return <div className="state">Loading hidden jobs…</div>;

  return (
    <section className="hidden-jobs" aria-label="Hidden jobs">
      <h2>Hidden jobs ({jobs.length})</h2>
      <p className="muted">
        Every co-op, internship, or student posting we found but don't show, and
        exactly why. If one should be on your list, click "This should be
        eligible" so the rule that hid it gets fixed.
        {flaggedCount > 0
          ? ` The AI double-checked some of them: ${flaggedCount} look eligible to it and are worth a look.`
          : ""}
      </p>

      <div className="hidden-controls">
        <input
          type="search"
          className="search"
          placeholder="Search title, company, or location"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          aria-label="Search hidden jobs"
        />
        <select value={group} onChange={(e) => setGroup(e.target.value)} aria-label="Reason">
          <option value="">All reasons</option>
          {Object.entries(counts)
            .sort((a, b) => b[1] - a[1])
            .map(([code, n]) => (
              <option key={code} value={code}>
                {groupLabel(code)} ({n})
              </option>
            ))}
        </select>
        {flaggedCount > 0 && (
          <label className="checkbox">
            <input
              type="checkbox"
              checked={onlyFlagged}
              onChange={(e) => setOnlyFlagged(e.target.checked)}
            />
            Only ones the AI thinks I'm eligible for ({flaggedCount})
          </label>
        )}
      </div>

      <p className="count">
        Showing {visible.length} of {jobs.length}
      </p>
      <ul className="hidden-list">
        {visible.slice(0, 300).map((j) => (
          <li key={j.job_id} className="hidden-item">
            <div className="hidden-head">
              <span className="hidden-title">{j.title}</span>
              <span className="hidden-company">
                {j.company} · {j.location || "location not stated"}
              </span>
            </div>
            <ul className="hidden-reasons">
              {j.reasons.map((r) => (
                <li key={r.code}>
                  <strong>{r.label}</strong>
                  {r.detail ? `: ${r.detail}` : ""}
                </li>
              ))}
            </ul>
            {j.evidence.length > 0 && (
              <p className="hidden-evidence">From the posting: “{j.evidence[0]}”</p>
            )}
            {j.ai_audit?.ai_eligible && (
              <p className="hidden-audit">
                <span className="tag tag-flag">AI thinks you may be eligible</span>{" "}
                {j.ai_audit.explanation}
              </p>
            )}
            <div className="hidden-links">
              {j.application_url && (
                <a href={j.application_url} target="_blank" rel="noreferrer">
                  View posting
                </a>
              )}
              <a href={shouldBeEligibleUrl(j)} target="_blank" rel="noreferrer">
                This should be eligible
              </a>
              {j.group === "reported" && onRestore && (
                <button
                  type="button"
                  className="link-button"
                  disabled={!canRestore}
                  title={canRestore ? undefined : "Save a GitHub token in the Add a company tab first."}
                  onClick={() => onRestore(j)}
                >
                  Restore
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>
      {visible.length > 300 && (
        <p className="muted">Showing the first 300. Narrow it down with search or a reason.</p>
      )}
    </section>
  );
}
