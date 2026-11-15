import { useState } from "react";
import {
  appendAddedCompany,
  clearToken,
  dispatchAddCompany,
  getAddedCompanies,
  getToken,
  setToken,
} from "./apiGithub";
import type { TrackedCompany } from "./types";

const ALREADY_TRACKED_MESSAGE =
  "This company is already being tracked — no need to add it again.";

/** Normalize a free-text value for case-insensitive comparison. */
function normalize(value: string): string {
  return value.trim().toLowerCase();
}

/**
 * Extract a comparable host from a careers URL (host without a leading "www.").
 * Returns "" when the input is not a URL we can parse.
 */
function hostOf(value: string): string {
  const raw = value.trim();
  if (!raw) return "";
  try {
    const url = new URL(/^https?:\/\//i.test(raw) ? raw : `https://${raw}`);
    return url.hostname.replace(/^www\./i, "").toLowerCase();
  } catch {
    return "";
  }
}

/**
 * True when `input` (a company name OR a careers URL) already appears in the
 * tracked list or the just-added list. Matches by case-insensitive name, and
 * for a URL also by host against each known company's name/host tokens.
 */
export function isAlreadyTracked(
  input: string,
  tracked: TrackedCompany[],
  added: string[],
): boolean {
  const value = normalize(input);
  if (!value) return false;

  const names = new Set<string>([
    ...tracked.map((c) => normalize(c.name)),
    ...added.map((a) => normalize(a)),
  ]);
  if (names.has(value)) return true;

  // URL input: compare the typed host against each known name/URL's host.
  const inputHost = hostOf(input);
  if (inputHost) {
    for (const candidate of [...tracked.map((c) => c.name), ...added]) {
      const candidateHost = hostOf(candidate);
      if (candidateHost && candidateHost === inputHost) return true;
    }
  }
  return false;
}

type Status =
  | { kind: "idle" }
  | { kind: "sending" }
  | { kind: "ok"; message: string }
  | { kind: "error"; message: string };

const SUCCESS_MESSAGE =
  "Add requested — new jobs appear within a minute or two after the run.";

export function AddCompany({
  trackedCompanies = [],
}: {
  trackedCompanies?: TrackedCompany[];
}) {
  const [company, setCompany] = useState("");
  const [token, setTokenState] = useState(() => getToken());
  const [tokenInput, setTokenInput] = useState("");
  const [showToken, setShowToken] = useState(() => !getToken());
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [added, setAdded] = useState<string[]>(() => getAddedCompanies());

  const hasToken = token.trim().length > 0;
  const sending = status.kind === "sending";
  const canAdd = hasToken && company.trim().length > 0 && !sending;

  function saveToken() {
    const value = tokenInput.trim();
    if (!value) return;
    setToken(value);
    setTokenState(value);
    setTokenInput("");
    setShowToken(false);
  }

  function forgetToken() {
    clearToken();
    setTokenState("");
    setShowToken(true);
  }

  async function onAdd(e: React.FormEvent) {
    e.preventDefault();
    const name = company.trim();
    if (!name || !hasToken) return;
    // Block an already-tracked company before dispatching the workflow, so we
    // never kick off a run for something we are already pulling jobs from.
    if (isAlreadyTracked(name, trackedCompanies, added)) {
      setStatus({ kind: "error", message: ALREADY_TRACKED_MESSAGE });
      return;
    }
    setStatus({ kind: "sending" });
    try {
      await dispatchAddCompany(name, token);
      setAdded(appendAddedCompany(name));
      setCompany("");
      setStatus({ kind: "ok", message: SUCCESS_MESSAGE });
    } catch (err) {
      setStatus({
        kind: "error",
        message: err instanceof Error ? err.message : "Could not request the add.",
      });
    }
  }

  return (
    <section className="add-company" aria-label="Add a company">
      <h2>Add a company</h2>
      <p className="muted">
        Enter a company name or the link to its careers page. We detect the
        job-board system, start tracking it, and its eligible roles appear here
        after the next run.
      </p>

      <details className="token-box" open={showToken}>
        <summary>
          GitHub access token
          {hasToken ? " — saved" : " — required to add"}
        </summary>
        <p className="muted">
          Adding a company triggers a GitHub Action, which needs a fine-grained
          token with Actions (workflow) write access to this repository. It is
          stored only in this browser and sent only to GitHub. Paste it once.
        </p>
        {hasToken ? (
          <div className="token-saved">
            <span aria-label="token saved">Token saved (hidden).</span>
            <button type="button" className="btn btn-ghost" onClick={forgetToken}>
              Clear token
            </button>
          </div>
        ) : (
          <div className="token-entry">
            <input
              type="password"
              placeholder="github_pat_…"
              value={tokenInput}
              onChange={(e) => setTokenInput(e.target.value)}
              aria-label="GitHub token"
            />
            <button
              type="button"
              className="btn btn-primary"
              onClick={saveToken}
              disabled={!tokenInput.trim()}
            >
              Save token
            </button>
          </div>
        )}
      </details>

      <form className="add-form" onSubmit={onAdd}>
        <input
          type="text"
          className="add-input"
          placeholder="Company name or careers URL"
          value={company}
          onChange={(e) => setCompany(e.target.value)}
          aria-label="Company name or careers URL"
        />
        <button type="submit" className="btn btn-primary" disabled={!canAdd}>
          {sending ? "Adding…" : "Add"}
        </button>
      </form>
      {!hasToken && (
        <p className="muted small">Save a GitHub token above to enable Add.</p>
      )}

      <div className="add-status" role="status" aria-live="polite">
        {status.kind === "ok" && <p className="state-ok">{status.message}</p>}
        {status.kind === "error" && (
          <p className="state-error-text" role="alert">
            {status.message}
          </p>
        )}
      </div>

      {added.length > 0 && (
        <div className="added-list">
          <h3>Companies you've added</h3>
          <ul>
            {added.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
