import type { Job } from "./types";

function duration(job: Job): string {
  if (job.duration_label) return job.duration_label;
  if (job.duration_months) return `${job.duration_months} months`;
  return "duration not specified";
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
          <dd>{job.term_season}</dd>
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
      </footer>
    </article>
  );
}
