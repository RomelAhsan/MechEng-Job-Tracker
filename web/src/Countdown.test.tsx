import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Countdown } from "./Countdown";

describe("Countdown", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("shows time remaining to the next quarter-hour poll", () => {
    // 12:07:00 UTC -> next poll at 12:15 -> 8:00 remaining.
    vi.setSystemTime(new Date("2026-01-01T12:07:00Z"));
    render(<Countdown />);
    expect(screen.getByText(/Next job check in/i)).toBeInTheDocument();
    expect(screen.getByText("8:00")).toBeInTheDocument();
  });

  it("shows a 'checking now' state at the boundary", () => {
    vi.setSystemTime(new Date("2026-01-01T12:14:59.600Z"));
    render(<Countdown />);
    // Advance one tick past the boundary.
    vi.advanceTimersByTime(1000);
    expect(screen.getByText(/Checking for new jobs now/i)).toBeInTheDocument();
  });
});
