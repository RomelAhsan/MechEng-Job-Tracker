"""Tests for the job-feedback classifier (turns 'wrong match' reports into
concrete filter-fix actions)."""

from __future__ import annotations

from jobtracker.services import feedback_service


def _issue(number: int, title: str, reason: str, *, company: str, job_id: str) -> dict:
    body = (
        f"This posting should NOT have been shown.\n\n"
        f"**Why it's wrong:** {reason}\n\n"
        f"- Company: {company}\n- job_id: {job_id}\n"
    )
    return {"number": number, "title": title, "body": body}


def test_classifies_us_citizenship_report() -> None:
    item = feedback_service.classify_issue(
        _issue(1, "Wrong match: Propulsion Engineer @ SpaceX",
               "This requires US citizenship / ITAR, I can't apply.",
               company="SpaceX", job_id="abc")
    )
    assert "us_citizenship" in item.signals
    assert item.company == "SpaceX"
    assert item.job_id == "abc"
    assert any("itar" in a.lower() for a in item.suggested_actions)


def test_classifies_seniority_and_degree() -> None:
    item = feedback_service.classify_issue(
        _issue(2, "Wrong match: Sr. Engineer @ Acme",
               "Senior role needing 5 years of experience and a Master's degree.",
               company="Acme", job_id="d2")
    )
    assert "seniority" in item.signals
    assert "degree" in item.signals


def test_report_aggregates_signals_and_companies() -> None:
    issues = [
        _issue(1, "Wrong: A @ SpaceX", "US citizenship required", company="SpaceX", job_id="j1"),
        _issue(2, "Wrong: B @ SpaceX", "ITAR clearance needed", company="SpaceX", job_id="j2"),
        _issue(3, "Wrong: C @ Acme", "full-time permanent role, not a co-op",
               company="Acme", job_id="j3"),
    ]
    report = feedback_service.build_report(issues)
    assert len(report.items) == 3
    assert report.signal_counts.get("us_citizenship") == 2
    assert report.company_counts.get("SpaceX") == 2
    assert report.signal_counts.get("full_time") == 1


def test_unclassifiable_report_has_no_signals() -> None:
    item = feedback_service.classify_issue(
        {"number": 9, "title": "Wrong match: X @ Y", "body": "Just feels off."}
    )
    assert item.signals == []
    assert item.suggested_actions == []
