import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { HiddenJobs } from "./HiddenJobs";
import type { HiddenJob } from "./types";

function hidden(overrides: Partial<HiddenJob>): HiddenJob {
  return {
    job_id: "h",
    company_id: "acme",
    title: "Thermal Intern",
    company: "Acme",
    location: "Toronto, Canada",
    country: "Canada",
    term_season: "spring",
    posted_at: null,
    posted_days_ago: 3,
    application_url: "https://apply.example/h",
    group: "graduation_window",
    reasons: [
      {
        code: "graduation_window",
        label: "Graduation window ends before your graduation year",
        detail: "Requires graduating before your expected graduation year (2031).",
      },
    ],
    evidence: ["Expected graduation between Fall 2027 and Summer 2028"],
    ai_audit: null,
    ...overrides,
  };
}

afterEach(() => vi.unstubAllGlobals());

function mockHidden(jobs: HiddenJob[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ generated_at: "", jobs }) }),
  );
}

describe("HiddenJobs", () => {
  it("lists hidden jobs with the reason and the quoted evidence", async () => {
    mockHidden([hidden({})]);
    render(<HiddenJobs />);
    expect(await screen.findByText("Thermal Intern")).toBeInTheDocument();
    expect(
      screen.getByText(/Graduation window ends before your graduation year/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Fall 2027 and Summer 2028/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "This should be eligible" })).toHaveAttribute(
      "href",
      expect.stringContaining("labels=missed-job"),
    );
  });

  it("filters to jobs the AI thinks are eligible", async () => {
    mockHidden([
      hidden({ job_id: "a", title: "Flagged Intern", ai_audit: { ai_eligible: true, explanation: "Grad date is a preference." } }),
      hidden({ job_id: "b", title: "Other Intern" }),
    ]);
    render(<HiddenJobs />);
    await screen.findByText("Flagged Intern");
    fireEvent.click(screen.getByLabelText(/Only ones the AI thinks/));
    expect(screen.getByText("Flagged Intern")).toBeInTheDocument();
    expect(screen.queryByText("Other Intern")).not.toBeInTheDocument();
    expect(screen.getByText("AI thinks you may be eligible")).toBeInTheDocument();
  });

  it("offers Restore for jobs you reported", async () => {
    mockHidden([hidden({ group: "reported", reasons: [{ code: "reported", label: "You reported the link as dead", detail: "" }] })]);
    const onRestore = vi.fn();
    render(<HiddenJobs onRestore={onRestore} canRestore />);
    fireEvent.click(await screen.findByRole("button", { name: "Restore" }));
    expect(onRestore).toHaveBeenCalledTimes(1);
  });
});
