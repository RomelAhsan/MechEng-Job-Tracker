import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { Feed } from "./types";

function feed(overrides: Partial<Feed> = {}): Feed {
  return {
    version: 1,
    generated_at: "2026-01-01T12:00:00+00:00",
    source_last_checked_at: "2026-01-01T11:50:00+00:00",
    tracked_company_count: 2,
    tracked_companies: [
      { name: "Acme Robotics", ats: "greenhouse", job_count: 2 },
      { name: "Figure", ats: "lever", job_count: 1 },
    ],
    job_count: 1,
    jobs: [
      {
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
        posted_days_ago: 2,
        first_seen_at: "2026-01-01T10:00:00+00:00",
        first_seen_ago: "2 hours ago",
        source_last_checked_at: "2026-01-01T11:50:00+00:00",
        source_last_checked_ago: "10 minutes ago",
        is_new: true,
        ai_reasoning: {
          recommendation: "apply",
          confidence: 0.9,
          explanation: "Strong mechanical co-op fit for a first-year.",
          work_authorization_assessment: "Canada-located; eligible.",
        },
      },
    ],
    ...overrides,
  };
}

function mockFetch(value: unknown, ok = true, status = 200) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok,
      status,
      json: async () => value,
    } as Response),
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("App", () => {
  it("renders eligible jobs from the feed", async () => {
    mockFetch(feed());
    render(<App />);
    expect(await screen.findByText("Mechanical Design Co-op")).toBeInTheDocument();
    // "Figure" also appears as a <option> in the company filter, so scope to
    // the rendered job card.
    expect(screen.getByRole("heading", { name: /Mechanical Design Co-op/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Apply" })).toHaveAttribute(
      "href",
      "https://apply.example/a",
    );
  });

  it("shows the AI eligibility reasoning control on an AI-assessed card", async () => {
    mockFetch(feed());
    render(<App />);
    expect(
      await screen.findByText(/AI eligibility reasoning/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Strong mechanical co-op fit for a first-year/i),
    ).toBeInTheDocument();
  });

  it("lists tracked companies and their check status in the Coverage tab", async () => {
    mockFetch(
      feed({
        coverage: [
          {
            id: "acme", name: "Acme Robotics", ats: "greenhouse", status: "ok",
            last_success_at: null, last_success_ago: "5 minutes ago", failing_since: null,
            error: null, via: "direct", postings: 2, shown: 1, hidden: 1,
            hidden_reasons: {}, careers_url: null,
          },
        ],
      }),
    );
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "Coverage" }));
    expect(
      screen.getByRole("heading", { name: /Companies we're tracking/i }),
    ).toBeInTheDocument();
    expect(screen.getByText("Acme Robotics")).toBeInTheDocument();
    expect(screen.getByText(/2 postings tracked · 1 shown · 1 hidden/)).toBeInTheDocument();
  });

  it("shows a warning banner when boards could not be checked", async () => {
    mockFetch(
      feed({
        warnings: [
          { kind: "boards_failing", count: 19, message: "19 companies could not be checked on the last run" },
        ],
      }),
    );
    render(<App />);
    expect(await screen.findByText(/19 companies could not be checked/)).toBeInTheDocument();
  });

  it("shows an empty-state when there are no eligible jobs", async () => {
    mockFetch(feed({ job_count: 0, jobs: [] }));
    render(<App />);
    expect(
      await screen.findByText(/No eligible jobs yet/i),
    ).toBeInTheDocument();
  });

  it("shows an error state when the feed cannot be loaded", async () => {
    mockFetch(null, false, 404);
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/HTTP 404/);
  });
});
