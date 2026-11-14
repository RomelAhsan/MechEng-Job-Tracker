import { TERM_LABELS, termsOf, type AiReasoning, type Job } from "./types";

const REPO = "RomelAhsan/MechEng-Job-Tracker";

// Build a pre-filled GitHub issue so one click captures structured feedback
// (the static site has no server to POST to). The maintainer / AI uses these
// 'job-feedback' issues to tighten the filters so similar postings stop
// slipping through -- closing the loop the user asked for.
function reportWrongUrl(job: Job): string {
  const title = `Wrong match: ${job.title} @ ${job.company}`;
  const body = [
    "This posting should NOT have been shown as eligible.",
    "",
    "**Why it's wrong (please edit this line):** ",
    "",
    "---",
    "_Auto-filled job details:_",
    `- Job: ${job.title}`,
    `- Company: ${job.company}`,
    `- Location: ${job.location} (${job.country ?? "country unknown"})`,
    `- Employment type: ${job.employment_type}`,
    `- Term: ${job.term_season} · Duration: ${job.duration_label || "unspecified"}`,
    `- Apply: ${job.application_url ?? "—"}`,
    `- Source: ${job.source_url ?? "—"}`,
    `- job_id: ${job.job_id}`,
  ].join("\n");
  return (
    `https://github.com/${REPO}/issues/new` +
    `?labels=job-feedback&title=${encodeURIComponent(title)}` +
    `&body=${encodeURIComponent(body)}`
  );
}

function duration(job: Job): string {
  if (job.duration_label) return job.duration_label;
  if (job.duration_months) return `${job.duration_months} months`;
  return "duration not specified";
}

function AiReasoningPanel({ r }: { r: AiReasoning }) {
  const rows: Array<[string, string | undefined]> = [
    ["Mechanical fit", r.mechanical_relevance],
    ["Term", r.term_assessment],
    ["Duration", r.duration_assessment],
    ["Location", r.location_assessment],
    ["Work authorization", r.work_authorization_assessment],
  ];
  return (
    <details className="ai-reasoning">
      <summary>
        🤖 AI eligibility reasoning
        {r.recommendation ? ` — ${r.recommendation}` : ""}
        {r.confidence != null
          ? ` (confidence ${Math.round(r.confidence * 100)}%)`
          : ""}
      </summary>
      <div className="ai-body">
        {r.explanation && <p className="ai-explain">{r.explanation}</p>}
        <dl className="ai-grid">
          {rows
            .filter(([, v]) => v)
            .map(([k, v]) => (
              <div key={k}>
                <dt>{k}</dt>
                <dd>{v}</dd>
              </div>
            ))}
        </dl>
        {r.matched_skills && r.matched_skills.length > 0 && (
          <p className="ai-skills">
            <strong>Matched skills:</strong> {r.matched_skills.join(", ")}
          </p>
        )}
        {r.missing_skills && r.missing_skills.length > 0 && (
          <p className="ai-skills">
            <strong>Missing skills:</strong> {r.missing_skills.join(", ")}
          </p>
        )}
        {r.concerns && r.concerns.length > 0 && (
          <p className="ai-concerns">
            <strong>Concerns:</strong> {r.concerns.join("; ")}
          </p>
        )}
      </div>
    </details>
  );
}

export function JobCard({ job }: { job: Job }) {
  return (
    <article className="card">
      <header className="card-head">
        <div>
          <h2 className="card-title">
            {job.title}
            {job.is_new && <span className="badge badge-new">NEW</span>}
          </h2>
          <p className="card-company">{job.company}</p>
        </div>
        {job.fit_score != null && (
          <div className="fit" title="Fit score (0-100)">
            <span className="fit-num">{job.fit_score}</span>
            <span className="fit-label">fit</span>
          </div>
        )}
      </header>

      <dl className="meta">
        <div>
          <dt>Location</dt>
          <dd>{job.location || "—"}</dd>
        </div>
        <div>
          <dt>Term</dt>
          <dd>
            {termsOf(job.term_season).map((t) => TERM_LABELS[t] ?? t).join(" or ") ||
              "not stated"}
          </dd>
        </div>
        <div>
          <dt>Duration</dt>
          <dd>{duration(job)}</dd>
        </div>
        <div>
          <dt>Setting</dt>
          <dd>{job.remote_status}</dd>
        </div>
      </dl>

      {job.concerns.length > 0 && (
        <p className="concerns">⚠ {job.concerns.join("; ")}</p>
      )}

      <footer className="card-foot">
        <div className="links">
          {job.application_url && (
            <a
              className="btn btn-primary"
              href={job.application_url}
              target="_blank"
              rel="noreferrer"
            >
              Apply
            </a>
          )}
          {job.source_url && (
            <a
              className="btn btn-ghost"
              href={job.source_url}
              target="_blank"
              rel="noreferrer"
            >
              View posting
            </a>
          )}
        </div>
        <p className="freshness">
          {job.first_seen_ago ? `Found ${job.first_seen_ago}` : ""}
          {job.source_last_checked_ago
            ? ` · board checked ${job.source_last_checked_ago}`
            : ""}
        </p>
        {job.ai_reasoning && <AiReasoningPanel r={job.ai_reasoning} />}
        <a
          className="report-wrong"
          href={reportWrongUrl(job)}
          target="_blank"
          rel="noreferrer"
          title="Tell the maintainer this job shouldn't be here and why; it improves the filters."
        >
          ⚑ Report wrong match
        </a>
      </footer>
    </article>
  );
}
