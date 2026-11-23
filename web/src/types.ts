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
  // Whole days since the best-available posting date (true posting date first,
  // then first-seen). null when the age cannot be determined.
  posted_days_ago: number | null;
  // True when the age is only a lower bound (Workday "Posted 30+ Days Ago"):
  // posted_days_ago is then "at least" that many days, so the card renders
  // "Posted 30+ days ago" rather than a precise but wrong exact count.
  posted_days_ago_is_lower_bound?: boolean;
  // "employer" = the job board's own posting date; "first_seen" = the
  // employer gave no date, so the age is from when we first saw the job.
  posted_date_source?: "employer" | "first_seen" | null;
  first_seen_at: string | null;
  first_seen_ago: string | null;
  source_last_checked_at: string | null;
  source_last_checked_ago: string | null;
  is_new: boolean;
  ai_reasoning: AiReasoning | null;
  // Link checker: "open" (confirmed listed), "cant_check" (the job site
  // refused our checker; the job is kept and tagged), or null (not checked).
  link_status?: "open" | "cant_check" | null;
  link_checked_at?: string | null;
  link_checked_ago?: string | null;
  cant_check_reason?: string | null;
}

// One company's check status, for the Coverage tab.
export interface CoverageItem {
  id: string;
  name: string;
  ats: string;
  status: "ok" | "failing" | "never_checked" | "not_supported";
  last_success_at: string | null;
  last_success_ago: string | null;
  failing_since: string | null;
  error: string | null;
  via: string | null;
  postings: number;
  shown: number;
  hidden: number;
  hidden_reasons: Record<string, number>;
  careers_url: string | null;
}

export interface FeedWarning {
  kind: string;
  count: number;
  message: string;
}

export interface HiddenReason {
  code: string;
  label: string;
  detail: string;
}

// A student role we fetched but do not show (hidden.json), with the reasons.
export interface HiddenJob {
  job_id: string;
  company_id: string;
  title: string;
  company: string;
  location: string;
  country: string | null;
  term_season: string;
  posted_at: string | null;
  posted_days_ago: number | null;
  application_url: string | null;
  group: string;
  reasons: HiddenReason[];
  evidence: string[];
  ai_audit: { ai_eligible: boolean; explanation: string } | null;
}

export interface HiddenFeed {
  generated_at: string;
  jobs: HiddenJob[];
}

// Plain-English names for the hidden-job groups (mirrors RULE_LABELS in
// web_export_service.py).
export const HIDDEN_GROUP_LABELS: Record<string, string> = {
  reported: "You reported the link as dead",
  foreign_country: "Outside Canada and the US",
  itar_company: "US defense/aerospace employer",
  itar: "Requires US citizenship / ITAR / clearance",
  citizenship: "Citizenship or sponsorship requirement",
  employment_type: "Not a co-op or internship",
  professional_role: "Professional (non-student) title",
  graduation_window: "Graduation window ends too early",
  degree: "Requires a completed or graduate degree",
  experience: "Requires work experience",
  term: "Term does not match",
  duration: "Duration does not match",
  not_relevant: "Not a mechanical-engineering role",
  ai_rejected: "The AI check found a problem",
  ai_pending: "Waiting for the AI check",
  stale: "Older than 14 days",
  removed: "Posting removed",
};

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

// One actively-tracked employer we really pull jobs from (connector-backed,
// active, enabled). Mirrors web_export_service._tracked_companies.
export interface TrackedCompany {
  name: string;
  ats: string;
  // Careers host (e.g. "careers.l3harris.com"), derived from the company's
  // careers_url, so the Add tab can block a pasted careers URL for an
  // already-tracked company. null when no careers_url is known.
  host?: string | null;
  job_count: number;
}

export interface Feed {
  version: number;
  generated_at: string;
  source_last_checked_at: string | null;
  tracked_company_count: number;
  // Older feeds may omit this; code treats a missing value as [].
  tracked_companies?: TrackedCompany[];
  job_count: number;
  pending_ai_count?: number;
  // Newer feeds only; code treats missing values as empty.
  coverage?: CoverageItem[];
  warnings?: FeedWarning[];
  hidden_count?: number;
  hidden_counts?: Record<string, number>;
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
