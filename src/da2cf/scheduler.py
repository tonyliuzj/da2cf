from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlmodel import Session, select

from .database import engine
from .models import AppSettings, Domain
from .sync_service import SyncService

logger = logging.getLogger(__name__)


class SchedulerManager:
    def __init__(self, scheduler: AsyncIOScheduler, sync_service: SyncService):
        self.scheduler = scheduler
        self.sync_service = sync_service

    def schedule_all(self) -> None:
        with Session(engine) as session:
            domains = session.exec(select(Domain)).all()
            settings = self.sync_service.get_settings(session)
        for domain in domains:
            self._schedule_domain(domain, settings)

    def _schedule_domain(self, domain: Domain, settings: AppSettings) -> None:
        job_id = f"sync_domain_{domain.id}"
        acme_job_id = f"sync_domain_{domain.id}_acme"
        for jid in (job_id, acme_job_id):
            existing = self.scheduler.get_job(jid)
            if existing:
                self.scheduler.remove_job(jid)
        if not domain.enabled:
            return
        interval_minutes = domain.sync_interval_minutes or settings.default_sync_interval_minutes or 15
        trigger = IntervalTrigger(minutes=interval_minutes, timezone="Europe/London")
        self.scheduler.add_job(
            self._run_for_domain,
            trigger=trigger,
            id=job_id,
            kwargs={"domain_id": domain.id, "acme_only": False},
            max_instances=1,
        )
        logger.info(
            "Scheduled domain %s id=%s every %s minutes",
            domain.name,
            domain.id,
            interval_minutes,
        )

        if settings.acme_sync_interval_minutes:
            acme_trigger = IntervalTrigger(
                minutes=settings.acme_sync_interval_minutes,
                timezone="Europe/London",
            )
            self.scheduler.add_job(
                self._run_for_domain,
                trigger=acme_trigger,
                id=acme_job_id,
                kwargs={"domain_id": domain.id, "acme_only": True},
                max_instances=1,
            )
            logger.info(
                "Scheduled ACME-only sync for domain %s id=%s every %s minutes",
                domain.name,
                domain.id,
                settings.acme_sync_interval_minutes,
            )

    async def _run_for_domain(self, domain_id: int, acme_only: bool = False) -> None:
        from .database import session_scope

        with session_scope() as session:
            domain = session.get(Domain, domain_id)
            if not domain or not domain.enabled:
                return
            if acme_only:
                plan = self.sync_service.compute_plan_for_domain_acme_only(session, domain)
            else:
                plan = self.sync_service.compute_plan_for_domain(session, domain)
            self.sync_service.apply_plan(
                session=session,
                domain=domain,
                plan=plan,
            )

    def next_run_for_domain(self, domain_id: int) -> Optional[datetime]:
        job = self.scheduler.get_job(f"sync_domain_{domain_id}")
        acme_job = self.scheduler.get_job(f"sync_domain_{domain_id}_acme")
        times = []
        if job and job.next_run_time:
            times.append(job.next_run_time)
        if acme_job and acme_job.next_run_time:
            times.append(acme_job.next_run_time)
        return min(times) if times else None

