import type { Feed } from "./types";

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
