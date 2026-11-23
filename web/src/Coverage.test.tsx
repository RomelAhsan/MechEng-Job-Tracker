import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Coverage } from "./Coverage";
import type { CoverageItem } from "./types";

function item(overrides: Partial<CoverageItem>): CoverageItem {
  return {
    id: "x",
    name: "X",
    ats: "greenhouse",
    status: "ok",
    last_success_at: null,
    last_success_ago: "5 minutes ago",
    failing_since: null,
    error: null,
    via: "direct",
    postings: 10,
    shown: 2,
    hidden: 3,
    hidden_reasons: {},
    careers_url: null,
    ...overrides,
  };
}

describe("Coverage", () => {
  it("shows checked, failing, and unsupported companies with reasons", () => {
    render(
      <Coverage
        items={[
          item({ id: "a", name: "Acme Robotics" }),
          item({
            id: "b",
            name: "Airbus Canada",
            ats: "workday",
            status: "failing",
            error: "the job site is refusing our checker (too many requests)",
            failing_since: "2026-11-16T18:00:00Z",
            last_success_ago: "7 days ago",
          }),
          item({ id: "c", name: "Stantec", ats: "html", status: "not_supported" }),
        ]}
      />,
    );
    expect(screen.getByText("Acme Robotics")).toBeInTheDocument();
    expect(screen.getByText(/Last checked 5 minutes ago/)).toBeInTheDocument();
    expect(
      screen.getByText(/The job site is refusing our checker \(too many requests\)/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Last successful check 7 days ago/)).toBeInTheDocument();
    expect(screen.getByText(/2 companies are tracked: 1 checked successfully, 1 can't be checked/)).toBeInTheDocument();
    expect(screen.getByText(/Companies without a supported job board \(1\)/)).toBeInTheDocument();
  });
});
