import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { JobCard } from "./JobCard";
import type { Job } from "./types";

function job(overrides: Partial<Job> = {}): Job {
  return {
    job_id: "a",
    title: "Mechanical Design Co-op",
    company: "Figure",
    location: "Toronto, Ontario, Canada",
    country: "Canada",
    remote_status: "onsite",
    employment_type: "co-op",
    term_season: "spring",
    duration_months: 4,
    duration_label: "4-month",
    fit_score: 88,
    eligibility_reasons: [],
    visa_sponsorship: "unknown",
    citizenship_requirement: "",
    concerns: [],
    skills: ["CAD"],
    application_url: "https://apply.example/a",
    source_url: "https://src.example/a",
    posted_at: null,
    posted_days_ago: null,
    first_seen_at: null,
    first_seen_ago: null,
    source_last_checked_at: null,
    source_last_checked_ago: null,
    is_new: false,
    ai_reasoning: null,
    ...overrides,
  };
}

describe("JobCard posted-ago label", () => {
  it("renders an exact 'Posted N days ago' for a known date", () => {
    render(<JobCard job={job({ posted_days_ago: 10 })} />);
    expect(screen.getByText("Posted 10 days ago")).toBeInTheDocument();
  });

  it("renders 'Posted today' and singular 'Posted 1 day ago'", () => {
    const { rerender } = render(<JobCard job={job({ posted_days_ago: 0 })} />);
    expect(screen.getByText("Posted today")).toBeInTheDocument();
    rerender(<JobCard job={job({ posted_days_ago: 1 })} />);
    expect(screen.getByText("Posted 1 day ago")).toBeInTheDocument();
  });

  it("renders 'Posted 30+ days ago' for a lower-bound age (Workday 30+)", () => {
    render(
      <JobCard
        job={job({ posted_days_ago: 30, posted_days_ago_is_lower_bound: true })}
      />,
    );
    expect(screen.getByText("Posted 30+ days ago")).toBeInTheDocument();
    // Must NOT show the precise but wrong "Posted 30 days ago".
    expect(screen.queryByText("Posted 30 days ago")).not.toBeInTheDocument();
  });

  it("shows no posted label when the age is unknown", () => {
    render(<JobCard job={job({ posted_days_ago: null })} />);
    expect(screen.queryByText(/Posted/)).not.toBeInTheDocument();
  });
});

describe("JobCard date source and link checks", () => {
  it("says 'First seen' when the employer gives no posting date", () => {
    render(
      <JobCard job={job({ posted_days_ago: 3, posted_date_source: "first_seen" })} />,
    );
    expect(screen.getByText("First seen 3 days ago")).toBeInTheDocument();
    expect(screen.getByText(/employer gives no posting date/)).toBeInTheDocument();
  });

  it("labels the employer's own posting date", () => {
    render(<JobCard job={job({ posted_days_ago: 9, posted_date_source: "employer" })} />);
    expect(screen.getByText("Posted 9 days ago")).toBeInTheDocument();
    expect(screen.getByText(/date from the employer's job board/)).toBeInTheDocument();
  });

  it("tags a posting whose job site refuses our checker, with the reason", () => {
    render(
      <JobCard
        job={job({
          link_status: "cant_check",
          cant_check_reason: "the job site is refusing our checker (too many requests)",
          link_checked_ago: "7 days ago",
        })}
      />,
    );
    expect(screen.getByText("Can't be checked")).toBeInTheDocument();
    expect(screen.getByText(/The job site is refusing our checker/)).toBeInTheDocument();
    expect(screen.getByText(/Last confirmed open 7 days ago/)).toBeInTheDocument();
  });

  it("shows when a posting was last confirmed open", () => {
    render(<JobCard job={job({ link_status: "open", link_checked_ago: "8 minutes ago" })} />);
    expect(screen.getByText(/Still open \(checked 8 minutes ago\)/)).toBeInTheDocument();
  });

  it("calls onReportDead from the Report dead link button", () => {
    const onReportDead = vi.fn();
    render(<JobCard job={job()} onReportDead={onReportDead} />);
    fireEvent.click(screen.getByRole("button", { name: /Report dead link/ }));
    expect(onReportDead).toHaveBeenCalledWith(expect.objectContaining({ job_id: "a" }));
  });
});
