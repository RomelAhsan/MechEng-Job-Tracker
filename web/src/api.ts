import type { Feed, HiddenFeed } from "./types";

// Fetch the static feed. The path is relative to the Vite `base`
// (/MechEng-Job-Tracker/), so on GitHub Pages this resolves to
// https://RomelAhsan.github.io/MechEng-Job-Tracker/data/jobs.json and locally
// to /MechEng-Job-Tracker/data/jobs.json under `vite preview`.
export async function loadFeed(signal?: AbortSignal): Promise<Feed> {
  const url = `${import.meta.env.BASE_URL}data/jobs.json`;
  const res = await fetch(url, { signal, cache: "no-cache" });
  if (!res.ok) {
    throw new Error(`Could not load jobs (HTTP ${res.status}).`);
  }
  return (await res.json()) as Feed;
}

// The hidden-jobs list is a separate (larger) file, loaded only when the
// "Hidden jobs" tab is opened.
export async function loadHidden(signal?: AbortSignal): Promise<HiddenFeed> {
  const url = `${import.meta.env.BASE_URL}data/hidden.json`;
  const res = await fetch(url, { signal, cache: "no-cache" });
  if (!res.ok) {
    throw new Error(`Could not load hidden jobs (HTTP ${res.status}).`);
  }
  return (await res.json()) as HiddenFeed;
}
