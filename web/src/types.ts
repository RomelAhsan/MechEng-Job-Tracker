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
  ai_reasoning: AiReasoning | null;
}

// Human-readable AI assessment, present only on cards the AI actually judged.
export interface AiReasoning {
  recommendation?: string;
  relevance_score?: number;
  confidence?: number;
  explanation?: string;
  mechanical_relevance?: string;
  term_assessment?: string;
  duration_assessment?: string;
  location_assessment?: string;
  work_authorization_assessment?: string;
  matched_skills?: string[];
  missing_skills?: string[];
  concerns?: string[];
}

export interface Feed {
  version: number;
  generated_at: string;
  source_last_checked_at: string | null;
  tracked_company_count: number;
  job_count: number;
  pending_ai_count?: number;
  jobs: Job[];
}

export type SortKey = "fit" | "newest";

// Freshness window in HOURS (0 = any time). Drives the "Posted within" dropdown.
export type FreshnessHours = 0 | 12 | 24 | 72 | 168 | 720;

export interface FreshnessOption {
  value: FreshnessHours;
  label: string;
}

export const FRESHNESS_OPTIONS: FreshnessOption[] = [
  { value: 0, label: "Any time" },
  { value: 12, label: "Past 12 hours" },
  { value: 24, label: "Past 24 hours" },
  { value: 72, label: "Past 3 days" },
  { value: 168, label: "Past week" },
  { value: 720, label: "Past 30 days" },
];

// The student's three co-op terms, defined by months. Every employer's naming
// ("Summer 2027", US "Spring 2027", "Fall co-op", month ranges) is mapped onto
// these by the backend, and a posting can cover more than one
// (term_season "winter,spring").
export const TERM_OPTIONS = ["winter", "spring", "fall", "unknown"] as const;

export const TERM_LABELS: Record<string, string> = {
  winter: "Winter (Jan–Apr)",
  spring: "Spring / Summer (May–Aug)",
  fall: "Fall (Sep–Dec)",
  unknown: "Term not stated",
};

/** Split a stored term_season ("winter,spring" / "unknown") into terms. */
export function termsOf(value: string | null | undefined): string[] {
  if (!value || value === "unknown") return [];
  return value.split(",").map((t) => t.trim()).filter(Boolean);
}

export interface Filters {
  search: string; // matches title, company, or location
  company: string; // "" = any
  remote: string; // "" = any
  term: string; // "" = any
  minFit: number; // 0-100
  freshnessHours: FreshnessHours; // 0 = any
  sort: SortKey;
}

export const DEFAULT_FILTERS: Filters = {
  search: "",
  company: "",
  remote: "",
  term: "",
  minFit: 0,
  freshnessHours: 0,
  sort: "fit",
};
