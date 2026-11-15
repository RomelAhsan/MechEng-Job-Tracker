import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TrackedCompanies } from "./TrackedCompanies";

describe("TrackedCompanies", () => {
  it("renders each tracked company's name, ATS, and job count", () => {
    render(
      <TrackedCompanies
        companies={[
          { name: "Acme Robotics", ats: "greenhouse", job_count: 2 },
          { name: "Zeta Dynamics", ats: "lever", job_count: 1 },
        ]}
      />,
    );
    expect(screen.getByText("Acme Robotics")).toBeInTheDocument();
    expect(screen.getByText("Zeta Dynamics")).toBeInTheDocument();
    expect(screen.getByText("greenhouse")).toBeInTheDocument();
    expect(screen.getByText("lever")).toBeInTheDocument();
    expect(screen.getByText("2 jobs")).toBeInTheDocument();
    expect(screen.getByText("1 job")).toBeInTheDocument();
  });

  it("shows an empty state when no companies are tracked", () => {
    render(<TrackedCompanies companies={[]} />);
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
    expect(
      screen.getByText(/No companies are being tracked yet/i),
    ).toBeInTheDocument();
  });
});
