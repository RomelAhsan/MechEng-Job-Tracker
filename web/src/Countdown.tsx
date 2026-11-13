import { useEffect, useState } from "react";

// The collector polls on a fixed cron: every 15 minutes, on the quarter-hour
// (UTC) -> :00, :15, :30, :45. So the next job check is the next quarter-hour
// boundary from now. (The site then redeploys a couple of minutes later, which
// the copy acknowledges.)
const POLL_INTERVAL_MS = 15 * 60 * 1000;

function msToNextPoll(now: number): number {
  return POLL_INTERVAL_MS - (now % POLL_INTERVAL_MS);
}

function format(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

/**
 * Live countdown to the next scheduled job check. When it reaches zero it shows
 * "Checking now…" and calls `onDue` so the page can refetch the feed (the new
 * data lands shortly after the poll + redeploy).
 */
export function Countdown({ onDue }: { onDue?: () => void }) {
  const [remaining, setRemaining] = useState(() => msToNextPoll(Date.now()));

  useEffect(() => {
    const tick = () => {
      const next = msToNextPoll(Date.now());
      setRemaining(next);
      // Near the boundary, nudge a refetch (once per cycle).
      if (next > POLL_INTERVAL_MS - 1500 && onDue) onDue();
    };
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [onDue]);

  const due = remaining <= 1500;
  return (
    <span className="countdown" title="The job boards are checked every 15 minutes">
      {due ? (
        <>⏳ Checking for new jobs now… <span className="cd-note">(new jobs appear within a few minutes)</span></>
      ) : (
        <>⏱️ Next job check in <strong>{format(remaining)}</strong></>
      )}
    </span>
  );
}
