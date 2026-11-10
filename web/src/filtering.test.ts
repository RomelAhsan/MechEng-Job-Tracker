import { describe, expect, it } from "vitest";
import { applyFilters, companyOptions } from "./filtering";
import { DEFAULT_FILTERS, type Job } from "./types";

function job(partial: Partial<Job> & { job_id: string }): Job {
  return {
    title: "Mechanical Engineering Intern",
    company: "Acme",
    location: "Toronto, Ontario, Canada",
    country: "Canada",
    remote_status: "onsite",
    employment_type: "internship",
    term_season: "spring",
    duration_months: 4,
    duration_label: "4-month",
    fit_score: 50,
    eligibility_reasons: [],
    visa_sponsorship: "unknown",
    citizenship_requirement: "",
    concerns: [],
    skills: [],
    application_url: "https://a",
    source_url: "https://s",
    posted_at: null,
    first_seen_at: null,
    first_seen_ago: null,
    source_last_checked_at: null,
    source_last_checked_ago: null,
    is_new: false,
    ...partial,
  };
}

describe("applyFilters", () => {
  const jobs = [
    job({ job_id: "a", company: "Figure", fit_score: 90, is_new: true }),
    job({ job_id: "b", company: "Nuro", fit_score: 40, location: "Austin, TX" }),
    job({ job_id: "c", company: "Figure", fit_score: 70, term_season: "fall" }),
  ];

  it("searches title, company, and location case-insensitively", () => {
    expect(applyFilters(jobs, { ...DEFAULT_FILTERS, search: "austin" }).map((j) => j.job_id)).toEqual([
      "b",
    ]);
    expect(applyFilters(jobs, { ...DEFAULT_FILTERS, search: "FIGURE" }).map((j) => j.job_id).sort()).toEqual([
      "a",
      "c",
    ]);
  });

  it("filters by company, term, and minimum fit", () => {
    expect(applyFilters(jobs, { ...DEFAULT_FILTERS, company: "Nuro" }).map((j) => j.job_id)).toEqual(["b"]);
    expect(applyFilters(jobs, { ...DEFAULT_FILTERS, term: "fall" }).map((j) => j.job_id)).toEqual(["c"]);
    expect(applyFilters(jobs, { ...DEFAULT_FILTERS, minFit: 60 }).map((j) => j.job_id).sort()).toEqual([
      "a",
      "c",
    ]);
  });

  it("filters to new-only", () => {
    expect(applyFilters(jobs, { ...DEFAULT_FILTERS, onlyNew: true }).map((j) => j.job_id)).toEqual(["a"]);
  });

  it("sorts by fit score descending by default", () => {
    expect(applyFilters(jobs, DEFAULT_FILTERS).map((j) => j.job_id)).toEqual(["a", "c", "b"]);
  });

  it("does not mutate the input array", () => {
    const original = jobs.map((j) => j.job_id);
    applyFilters(jobs, { ...DEFAULT_FILTERS, sort: "fit" });
    expect(jobs.map((j) => j.job_id)).toEqual(original);
  });
});

describe("companyOptions", () => {
  it("returns distinct, sorted company names", () => {
    const jobs = [job({ job_id: "1", company: "Zoox" }), job({ job_id: "2", company: "Figure" }), job({ job_id: "3", company: "Figure" })];
    expect(companyOptions(jobs)).toEqual(["Figure", "Zoox"]);
  });
});
