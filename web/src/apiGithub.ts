// Client-side dispatcher for the "Add a company" workflow.
//
// The static site has no backend, so adding a company means triggering the
// add-company.yml GitHub Actions workflow via the REST workflow-dispatch
// endpoint. The fine-grained token the request is authorized with is pasted by
// the user once and stored ONLY in browser localStorage; it is sent exclusively
// in the GitHub Authorization header and is never written to any committed file.

const REPO = "RomelAhsan/MechEng-Job-Tracker";
const DISPATCH_URL = `https://api.github.com/repos/${REPO}/actions/workflows/add-company.yml/dispatches`;

export const TOKEN_KEY = "gh_token";
export const ADDED_COMPANIES_KEY = "added_companies";

/** Read the stored fine-grained GitHub token (localStorage only). */
export function getToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

/** Persist the token to localStorage (never committed, never sent elsewhere). */
export function setToken(token: string): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* localStorage unavailable (private mode): fail silently. */
  }
}

/** Remove the stored token. */
export function clearToken(): void {
  setToken("");
}

/** The list of companies the user has requested, newest first. */
export function getAddedCompanies(): string[] {
  try {
    const raw = localStorage.getItem(ADDED_COMPANIES_KEY);
    const parsed = raw ? (JSON.parse(raw) as unknown) : [];
    return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

/** Append a company to the added-list (dedup, newest first). */
export function appendAddedCompany(company: string): string[] {
  const current = getAddedCompanies().filter((c) => c !== company);
  const next = [company, ...current];
  try {
    localStorage.setItem(ADDED_COMPANIES_KEY, JSON.stringify(next));
  } catch {
    /* ignore */
  }
  return next;
}

/** Map a non-2xx dispatch status to a human-readable error message. */
export function errorForStatus(status: number): string {
  switch (status) {
    case 401:
      return "Invalid or expired token. Paste a fresh fine-grained token with Actions write access.";
    case 403:
      return "Rate limited, or the token lacks workflow (Actions) permission on this repository.";
    case 404:
    case 422:
      return "Workflow not found or the input was rejected. Make sure add-company.yml exists on the main branch.";
    default:
      return `Could not request the add (HTTP ${status}). Please try again.`;
  }
}

/**
 * Dispatch the add-company workflow for `company` (a name or careers URL).
 * Resolves on success (204), rejects with a mapped Error otherwise.
 */
export async function dispatchAddCompany(
  company: string,
  token: string,
): Promise<void> {
  const res = await fetch(DISPATCH_URL, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: "main", inputs: { company } }),
  });
  // A successful workflow dispatch returns 204 No Content.
  if (res.status === 204 || res.ok) return;
  throw new Error(errorForStatus(res.status));
}
