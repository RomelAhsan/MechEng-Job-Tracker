import { useEffect, useState } from "react";

// How fresh the page is. The collector runs about every 10 minutes (each run
// starts the next one), and the site redeploys a couple of minutes after each
// run, so instead of a countdown to a fixed clock time we show when the data
// was last updated and quietly reload it every 2 minutes.
export const REFRESH_EVERY_MS = 2 * 60 * 1000;

export function ago(iso: string | null | undefined, now: number): string {
  if (!iso) return "at an unknown time";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "at an unknown time";
  const minutes = Math.max(0, Math.round((now - t) / 60000));
  if (minutes < 1) return "just now";
  if (minutes === 1) return "1 minute ago";
  if (minutes < 60) return `${minutes} minutes ago`;
  const hours = Math.round(minutes / 60);
  return hours === 1 ? "1 hour ago" : `${hours} hours ago`;
}

/**
 * "Updated N minutes ago". Calls `onRefresh` every two minutes so new jobs
 * appear without reloading the page. Warns when the data is over 45 minutes
 * old (the collector is behind).
 */
export function Countdown({
  generatedAt,
  onRefresh,
}: {
  generatedAt?: string | null;
  onRefresh?: () => void;
}) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const tick = setInterval(() => setNow(Date.now()), 30 * 1000);
    const refresh = onRefresh ? setInterval(onRefresh, REFRESH_EVERY_MS) : undefined;
    return () => {
      clearInterval(tick);
      if (refresh) clearInterval(refresh);
    };
  }, [onRefresh]);

  const t = generatedAt ? new Date(generatedAt).getTime() : NaN;
  const stale = !Number.isNaN(t) && now - t > 45 * 60 * 1000;
  return (
    <span
      className={stale ? "countdown countdown-stale" : "countdown"}
      title="Job boards are checked about every 10 minutes; this page reloads the data every 2 minutes."
    >
      ⏱️ Updated <strong>{ago(generatedAt, now)}</strong>
      <span className="cd-note"> · checks run about every 10 minutes</span>
      {stale ? <span className="cd-note"> · the last check is running late</span> : null}
    </span>
  );
}
