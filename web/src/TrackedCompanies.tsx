import type { TrackedCompany } from "./types";

// Read-only panel listing the employers we actually pull jobs from, straight
// from the static feed (feed.tracked_companies). No backend, no interactivity.
export function TrackedCompanies({
  companies,
}: {
  companies: TrackedCompany[];
}) {
  return (
    <section className="tracked-companies" aria-label="Tracked companies">
      <h2>Companies we're tracking</h2>
      {companies.length === 0 ? (
        <p className="muted">
          No companies are being tracked yet. Add one from the “Add a company”
          tab.
        </p>
      ) : (
        <ul className="tracked-list">
          {companies.map((c) => (
            <li key={c.name} className="tracked-item">
              <span className="tracked-name">{c.name}</span>
              <span className="tracked-ats">{c.ats}</span>
              <span className="tracked-count">
                {c.job_count} {c.job_count === 1 ? "job" : "jobs"}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
