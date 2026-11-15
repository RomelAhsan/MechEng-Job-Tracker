import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AddCompany } from "./AddCompany";
import { ADDED_COMPANIES_KEY, TOKEN_KEY } from "./apiGithub";

function mockFetch(status: number) {
  const fn = vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => ({}),
  } as Response);
  vi.stubGlobal("fetch", fn);
  return fn;
}

function saveTokenViaUi(token = "github_pat_test") {
  fireEvent.change(screen.getByLabelText("GitHub token"), {
    target: { value: token },
  });
  fireEvent.click(screen.getByRole("button", { name: /save token/i }));
}

beforeEach(() => localStorage.clear());
afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("AddCompany", () => {
  it("disables Add until a token is stored", () => {
    render(<AddCompany />);
    const input = screen.getByLabelText(/company name or careers url/i);
    fireEvent.change(input, { target: { value: "RTX" } });
    // No token yet: Add stays disabled even with a company typed.
    expect(screen.getByRole("button", { name: /^add$/i })).toBeDisabled();

    saveTokenViaUi();
    fireEvent.change(input, { target: { value: "RTX" } });
    expect(screen.getByRole("button", { name: /^add$/i })).toBeEnabled();
  });

  it("dispatches the workflow and shows success, appending to the added list", async () => {
    const fetchFn = mockFetch(204);
    render(<AddCompany />);
    saveTokenViaUi("github_pat_abc");
    fireEvent.change(screen.getByLabelText(/company name or careers url/i), {
      target: { value: "https://careers.rtx.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^add$/i }));

    expect(await screen.findByText(/Add requested/i)).toBeInTheDocument();

    // POST to the dispatch endpoint with the bearer token and the right body.
    const [url, init] = fetchFn.mock.calls[0];
    expect(String(url)).toContain(
      "/actions/workflows/add-company.yml/dispatches",
    );
    expect((init as RequestInit).method).toBe("POST");
    const headers = (init as RequestInit).headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer github_pat_abc");
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({
      ref: "main",
      inputs: { company: "https://careers.rtx.com" },
    });

    // Appended to the persisted added-companies list.
    expect(
      screen.getByRole("list").textContent,
    ).toContain("https://careers.rtx.com");
    expect(
      JSON.parse(localStorage.getItem(ADDED_COMPANIES_KEY) ?? "[]"),
    ).toContain("https://careers.rtx.com");
  });

  it("shows the invalid-token error on 401 and does not append", async () => {
    mockFetch(401);
    render(<AddCompany />);
    saveTokenViaUi();
    fireEvent.change(screen.getByLabelText(/company name or careers url/i), {
      target: { value: "RTX" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^add$/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/Invalid or expired token/i);
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
    expect(localStorage.getItem(ADDED_COMPANIES_KEY)).toBeNull();
  });

  it("shows the rate-limit/scope error on 403", async () => {
    mockFetch(403);
    render(<AddCompany />);
    saveTokenViaUi();
    fireEvent.change(screen.getByLabelText(/company name or careers url/i), {
      target: { value: "RTX" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^add$/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /Rate limited|workflow \(Actions\) permission/i,
    );
  });

  it("blocks adding a company already in the tracked list (no dispatch)", async () => {
    const fetchFn = mockFetch(204);
    render(
      <AddCompany
        trackedCompanies={[{ name: "RTX", ats: "phenom", job_count: 3 }]}
      />,
    );
    saveTokenViaUi();
    // Case-insensitive name match against the tracked list.
    fireEvent.change(screen.getByLabelText(/company name or careers url/i), {
      target: { value: "rtx" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^add$/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /already being tracked/i,
    );
    // The workflow is NOT dispatched.
    expect(fetchFn).not.toHaveBeenCalled();
  });

  it("blocks adding a careers URL whose host matches a tracked company", async () => {
    const fetchFn = mockFetch(204);
    render(
      <AddCompany
        trackedCompanies={[
          { name: "https://careers.rtx.com", ats: "phenom", job_count: 1 },
        ]}
      />,
    );
    saveTokenViaUi();
    fireEvent.change(screen.getByLabelText(/company name or careers url/i), {
      target: { value: "https://www.careers.rtx.com/search" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^add$/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /already being tracked/i,
    );
    expect(fetchFn).not.toHaveBeenCalled();
  });

  it("restores a saved token from localStorage on mount", async () => {
    localStorage.setItem(TOKEN_KEY, "github_pat_saved");
    render(<AddCompany />);
    await waitFor(() =>
      expect(screen.getByText(/Token saved/i)).toBeInTheDocument(),
    );
    fireEvent.change(screen.getByLabelText(/company name or careers url/i), {
      target: { value: "RTX" },
    });
    expect(screen.getByRole("button", { name: /^add$/i })).toBeEnabled();
  });
});
