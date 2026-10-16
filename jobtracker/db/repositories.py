"""Basic data-access helpers over the ORM models."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobtracker.db.models import Company, Job, SourceHealth


class CompanyRepository:
    """Data-access helpers for :class:`Company`."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, company_id: str) -> Company | None:
        return self.session.get(Company, company_id)

    def list(self, *, enabled_only: bool = False, active_only: bool = False) -> list[Company]:
        stmt = select(Company)
        if enabled_only:
            stmt = stmt.where(Company.enabled.is_(True))
        if active_only:
            stmt = stmt.where(Company.active.is_(True))
        return list(self.session.scalars(stmt))

    def add(self, company: Company) -> Company:
        self.session.add(company)
        return company


class JobRepository:
    """Data-access helpers for :class:`Job`."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, job_id: str) -> Job | None:
        return self.session.get(Job, job_id)

    def get_by_source(self, company_id: str, source_job_id: str) -> Job | None:
        stmt = select(Job).where(
            Job.company_id == company_id, Job.source_job_id == source_job_id
        )
        return self.session.scalars(stmt).first()

    def list_for_company(self, company_id: str) -> list[Job]:
        stmt = select(Job).where(Job.company_id == company_id)
        return list(self.session.scalars(stmt))

    def add(self, job: Job) -> Job:
        self.session.add(job)
        return job


class SourceHealthRepository:
    """Data-access helpers for :class:`SourceHealth`."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, company_id: str) -> SourceHealth | None:
        return self.session.get(SourceHealth, company_id)

    def upsert(self, company_id: str) -> SourceHealth:
        health = self.get(company_id)
        if health is None:
            health = SourceHealth(company_id=company_id)
            self.session.add(health)
        return health
