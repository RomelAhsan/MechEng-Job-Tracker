import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Countdown, REFRESH_EVERY_MS, ago } from "./Countdown";

describe("Countdown (last updated)", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("shows how long ago the data was updated", () => {
    vi.setSystemTime(new Date("2026-01-01T12:07:00Z"));
    render(<Countdown generatedAt="2026-01-01T12:00:00Z" />);
    expect(screen.getByText("7 minutes ago")).toBeInTheDocument();
    expect(screen.getByText(/about every 10 minutes/)).toBeInTheDocument();
  });

  it("flags data that is running late", () => {
    vi.setSystemTime(new Date("2026-01-01T13:30:00Z"));
    render(<Countdown generatedAt="2026-01-01T12:00:00Z" />);
    expect(screen.getByText(/running late/)).toBeInTheDocument();
  });

  it("reloads the data every two minutes", () => {
    const onRefresh = vi.fn();
    render(<Countdown generatedAt="2026-01-01T12:00:00Z" onRefresh={onRefresh} />);
    vi.advanceTimersByTime(REFRESH_EVERY_MS * 2);
    expect(onRefresh).toHaveBeenCalledTimes(2);
  });

  it("formats ages", () => {
    const now = new Date("2026-01-01T12:00:00Z").getTime();
    expect(ago("2026-01-01T11:59:50Z", now)).toBe("just now");
    expect(ago("2026-01-01T10:00:00Z", now)).toBe("2 hours ago");
    expect(ago(null, now)).toBe("at an unknown time");
  });
});
