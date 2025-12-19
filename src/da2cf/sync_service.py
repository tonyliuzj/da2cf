from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from threading import Lock
from typing import Dict, Iterable, List, Optional, Tuple

from sqlmodel import Session, select

from .clients.cloudflare import CloudflareClient
from .clients.directadmin import DirectAdminClient
from .config import env_config
from .models import (
    AppSettings,
    DNSRecord,
    Domain,
    ProxyPolicy,
    SyncPlan,
    SyncRun,
    PlanOperation,
)
from .utils.dns import record_key, should_exclude

logger = logging.getLogger(__name__)


class SyncService:
    def __init__(self, da_client: DirectAdminClient, cf_client: CloudflareClient):
        self.da = da_client
        self.cf = cf_client
        self.domain_locks: Dict[str, Lock] = {}

    def _get_lock(self, domain: str) -> Lock:
        if domain not in self.domain_locks:
            self.domain_locks[domain] = Lock()
        return self.domain_locks[domain]

    def get_settings(self, session: Session) -> AppSettings:
        settings = session.get(AppSettings, 1)
        if not settings:
            settings = AppSettings(
                id=1,
                default_sync_interval_minutes=env_config.default_sync_interval_minutes,
                acme_sync_interval_minutes=env_config.acme_sync_interval_minutes,
                proxy_a=env_config.proxy_a,
                proxy_aaaa=env_config.proxy_aaaa,
                proxy_cname=env_config.proxy_cname,
                proxy_host=env_config.proxy_host,
                proxy_sub=env_config.proxy_sub,
                exclude_names_csv=",".join(env_config.exclude_names),
            )
            session.add(settings)
            session.commit()
        return settings

    def compute_plan_for_domain_acme_only(self, session: Session, domain: Domain) -> SyncPlan:
        settings = self.get_settings(session)
        proxy_policy = ProxyPolicy(
            proxy_a=settings.proxy_a,
            proxy_aaaa=settings.proxy_aaaa,
            proxy_cname=settings.proxy_cname,
            proxy_host=settings.proxy_host,
            proxy_sub=settings.proxy_sub,
        )
        exclude_patterns = (
            settings.exclude_names_csv.split(",") if settings.exclude_names_csv else []
        )

        da_records_all = self.da.get_dns_records(domain.name)
        if not domain.cf_zone_id:
            zone_id = self.cf.find_zone_by_name(domain.name)
            domain.cf_zone_id = zone_id
            domain.cf_zone_exists = bool(zone_id)
            session.add(domain)
            session.commit()
        if not domain.cf_zone_id:
            return SyncPlan(
                domain=domain.name,
                creates=[],
                updates=[],
                deletes=[],
                noops=[],
                skips=[],
            )

        cf_records_all = self.cf.list_dns_records(domain.cf_zone_id)

        def is_acme(record: DNSRecord) -> bool:
            return record.name.startswith("_acme-challenge")

        da_records = [r for r in da_records_all if is_acme(r)]
        cf_records = [r for r in cf_records_all if is_acme(r)]

        plan = self._compute_plan(
            domain.name,
            da_records,
            cf_records,
            proxy_policy,
            env_config.managed_record_types,
            exclude_patterns,
        )
        return plan

    def compute_plan_for_domain(self, session: Session, domain: Domain) -> SyncPlan:
        settings = self.get_settings(session)
        proxy_policy = ProxyPolicy(
            proxy_a=settings.proxy_a,
            proxy_aaaa=settings.proxy_aaaa,
            proxy_cname=settings.proxy_cname,
            proxy_host=settings.proxy_host,
            proxy_sub=settings.proxy_sub,
        )
        exclude_patterns = (
            settings.exclude_names_csv.split(",") if settings.exclude_names_csv else []
        )

        da_records = self.da.get_dns_records(domain.name)
        # Cloudflare zones are created per FQDN (e.g. connect.de5.net),
        # so use the domain name directly for zone lookup.
        if not domain.cf_zone_id:
            zone_id = self.cf.find_zone_by_name(domain.name)
            domain.cf_zone_id = zone_id
            domain.cf_zone_exists = bool(zone_id)
            session.add(domain)
            session.commit()
        if not domain.cf_zone_id:
            return SyncPlan(
                domain=domain.name,
                creates=[],
                updates=[],
                deletes=[],
                noops=[],
                skips=[],
            )

        cf_records = self.cf.list_dns_records(domain.cf_zone_id)
        plan = self._compute_plan(
            domain.name,
            da_records,
            cf_records,
            proxy_policy,
            env_config.managed_record_types,
            exclude_patterns,
        )
        return plan

    def _compute_plan(
        self,
        domain: str,
        desired: Iterable[DNSRecord],
        current: Iterable[DNSRecord],
        proxy_policy: ProxyPolicy,
        managed_types: Iterable[str],
        exclude_patterns: Iterable[str],
    ) -> SyncPlan:
        managed_types = {t.upper() for t in managed_types}
        desired_map: Dict[Tuple[str, str, int], DNSRecord] = {}
        current_map: Dict[Tuple[str, str, int], DNSRecord] = {}

        desired_list: List[DNSRecord] = []
        for rec in desired:
            if rec.type.upper() in {"NS", "SOA"}:
                continue
            if not (
                rec.name.startswith("_acme-challenge")
                or rec.fqdn.startswith("_acme-challenge.")
            ):
                if should_exclude(rec, exclude_patterns):
                    continue
            desired_proxied = proxy_policy.desired_proxied(rec)
            if desired_proxied is not None:
                rec.proxied = desired_proxied
            desired_list.append(rec)
            desired_map[record_key(rec)] = rec

        current_list: List[DNSRecord] = []
        for rec in current:
            if rec.type.upper() in {"NS", "SOA"}:
                continue
            if not (
                rec.name.startswith("_acme-challenge")
                or rec.fqdn.startswith("_acme-challenge.")
            ):
                if should_exclude(rec, exclude_patterns):
                    continue
            current_list.append(rec)
            current_map[record_key(rec)] = rec

        creates: List[PlanOperation] = []
        updates: List[PlanOperation] = []
        deletes: List[PlanOperation] = []
        noops: List[PlanOperation] = []
        skips: List[PlanOperation] = []

        all_keys = set(desired_map.keys()) | set(current_map.keys())
        for key in sorted(all_keys):
            desired_rec = desired_map.get(key)
            current_rec = current_map.get(key)
            if desired_rec and not current_rec:
                creates.append(PlanOperation(action="create", record=desired_rec, current=None))
                continue
            if current_rec and not desired_rec:
                if current_rec.type.upper() in managed_types:
                    deletes.append(
                        PlanOperation(action="delete", record=current_rec, current=current_rec)
                    )
                else:
                    noops.append(
                        PlanOperation(action="noop", record=current_rec, current=current_rec)
                    )
                continue
            if not desired_rec or not current_rec:
                continue

            changes: Dict[str, Tuple[Optional[str], Optional[str]]] = {}

            if self._normalized_content(desired_rec) != self._normalized_content(current_rec):
                changes["content"] = (current_rec.content, desired_rec.content)

            if (desired_rec.ttl or 0) != (current_rec.ttl or 0):
                changes["ttl"] = (str(current_rec.ttl), str(desired_rec.ttl))

            if (desired_rec.priority or 0) != (current_rec.priority or 0):
                changes["priority"] = (str(current_rec.priority), str(desired_rec.priority))

            desired_proxied = proxy_policy.desired_proxied(desired_rec)
            if desired_proxied is not None:
                if (current_rec.proxied or False) != desired_proxied:
                    changes["proxied"] = (
                        str(current_rec.proxied),
                        str(desired_proxied),
                    )
                    desired_rec.proxied = desired_proxied

            if changes:
                updates.append(
                    PlanOperation(
                        action="update",
                        record=desired_rec,
                        current=current_rec,
                        changes=changes,
                    )
                )
            else:
                noops.append(
                    PlanOperation(
                        action="noop",
                        record=desired_rec,
                        current=current_rec,
                        changes=None,
                    )
                )

        return SyncPlan(
            domain=domain,
            creates=creates,
            updates=updates,
            deletes=deletes,
            noops=noops,
            skips=skips,
        )

    def _normalized_content(self, record: DNSRecord) -> str:
        if record.type.upper() == "TXT":
            return record.content
        if record.type.upper() == "CNAME":
            return record.content.rstrip(".")
        return record.content

    def apply_plan(self, session: Session, domain: Domain, plan: SyncPlan) -> SyncRun:
        lock = self._get_lock(domain.name)
        settings = self.get_settings(session)
        run = SyncRun(
            domain_id=domain.id,
            domain_name=domain.name,
            started_at=datetime.utcnow(),
            status="running",
            plan_summary=plan.summary(),
            plan_details=plan.dict(),
        )
        session.add(run)
        session.commit()

        with lock:
            try:
                if not domain.cf_zone_id:
                    zone_id = self.cf.find_zone_by_name(domain.name)
                    domain.cf_zone_id = zone_id
                    domain.cf_zone_exists = bool(zone_id)
                    session.add(domain)
                    session.commit()
                if not domain.cf_zone_id:
                    run.status = "failed"
                    run.error_summary = "Cloudflare zone not found"
                    run.finished_at = datetime.utcnow()
                    session.add(run)
                    session.commit()
                    return run

                results = self._apply_operations(domain.cf_zone_id, plan, settings)
                run.created_count = results["created"]
                run.updated_count = results["updated"]
                run.deleted_count = results["deleted"]
                run.skipped_count = results["skipped"]
                run.noop_count = results["noop"]
                run.status = "success" if not results["errors"] else "partial"
                run.error_summary = "; ".join(results["errors"]) if results["errors"] else None
                run.finished_at = datetime.utcnow()
                domain.last_sync_started_at = run.started_at
                domain.last_sync_finished_at = run.finished_at
                domain.last_sync_status = run.status
                domain.last_sync_summary = run.plan_summary
                session.add(domain)
                session.add(run)
                session.commit()
                return run
            except Exception as exc:  # noqa: BLE001
                logger.exception("Error applying sync plan for %s", domain.name)
                run.status = "failed"
                run.error_summary = str(exc)
                run.finished_at = datetime.utcnow()
                session.add(run)
                session.commit()
                return run

    def _apply_operations(
        self, zone_id: str, plan: SyncPlan, settings: AppSettings
    ) -> Dict[str, any]:
        counts = {"created": 0, "updated": 0, "deleted": 0, "skipped": 0, "noop": 0, "errors": []}

        def worker(op: PlanOperation) -> Tuple[str, Optional[str]]:
            try:
                if op.action == "create":
                    payload = self._to_cf_payload(op.record)
                    self.cf.create_dns_record(zone_id, payload)
                    return "created", None
                if op.action == "update":
                    if not op.current or not op.current.cf_id:
                        return "skipped", "missing cf_id for update"
                    payload = self._to_cf_payload(op.record)
                    self.cf.update_dns_record(zone_id, op.current.cf_id, payload)
                    return "updated", None
                if op.action == "delete":
                    if not op.current or not op.current.cf_id:
                        return "skipped", "missing cf_id for delete"
                    try:
                        self.cf.delete_dns_record(zone_id, op.current.cf_id)
                        return "deleted", None
                    except Exception as exc:  # noqa: BLE001
                        return "skipped", f"delete failed: {exc}"
                if op.action == "noop":
                    return "noop", None
            except Exception as exc:  # noqa: BLE001
                return "error", str(exc)
            return "noop", None

        with ThreadPoolExecutor(max_workers=env_config.sync_concurrency) as executor:
            futures = [
                executor.submit(worker, op)
                for op in (plan.creates + plan.updates + plan.deletes + plan.noops)
            ]
            for future in as_completed(futures):
                result, error = future.result()
                if result in counts:
                    counts[result] += 1
                if result == "error" and error:
                    counts["errors"].append(error)

        return counts

    def _to_cf_payload(self, record: DNSRecord) -> Dict[str, any]:
        content = record.content
        if record.type.upper() == "TXT":
            if not (content.startswith('"') and content.endswith('"')):
                content = f'"{content}"'
        payload: Dict[str, any] = {
            "type": record.type,
            "name": record.fqdn,
            "content": content,
        }
        if record.ttl:
            payload["ttl"] = record.ttl
        if record.type.upper() in {"MX", "SRV"} and record.priority is not None:
            payload["priority"] = record.priority
        if record.type.upper() in {"A", "AAAA", "CNAME"} and record.proxied is not None:
            payload["proxied"] = record.proxied
        if record.comment:
            payload["comment"] = record.comment
        return payload
