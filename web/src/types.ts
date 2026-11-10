// Shape of the static feed written by `python -m jobtracker export-web`.
// Keep in sync with jobtracker/services/web_export_service.py.

export interface Job {
  job_id: string;
  title: string;
  company: string;
  location: string;
  country: string | null;
  remote_status: string;
  employment_type: string;
  term_season: string;
  duration_months: number | null;
  duration_label: string;
  fit_score: number | null;
  eligibility_reasons: string[];
  visa_sponsorship: string;
  citizenship_requirement: string;
  concerns: string[];
  skills: string[];
  application_url: string | null;
  source_url: string | null;
  posted_at: string | null;
  first_seen_at: string | null;
  first_seen_ago: string | null;
  source_last_checked_at: string | null;
  source_last_checked_ago: string | null;
  is_new: boolean;
}

export interface Feed {
  version: number;
  generated_at: string;
  source_last_checked_at: string | null;
  tracked_company_count: number;
  job_count: number;
  jobs: Job[];
}

export type SortKey = "fit" | "newest";

export interface Filters {
  search: string; // matches title, company, or location
  company: string; // "" = any
  remote: string; // "" = any
  term: string; // "" = any
  minFit: number; // 0-100
  onlyNew: boolean;
  sort: SortKey;
}

export const DEFAULT_FILTERS: Filters = {
  search: "",
  company: "",
  remote: "",
  term: "",
  minFit: 0,
  onlyNew: false,
  sort: "fit",
};
