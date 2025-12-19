from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel
from sqlmodel import Column, Field, JSON, SQLModel


class Domain(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    enabled: bool = Field(default=True)

    cf_zone_id: Optional[str] = Field(default=None)
    cf_zone_exists: bool = Field(default=False)

    sync_interval_minutes: Optional[int] = Field(default=None)

    last_sync_started_at: Optional[datetime] = Field(default=None)
    last_sync_finished_at: Optional[datetime] = Field(default=None)
    last_sync_status: Optional[str] = Field(default=None)
    last_sync_summary: Optional[str] = Field(default=None)

    last_plan_preview_at: Optional[datetime] = Field(default=None)
    last_plan_preview_summary: Optional[str] = Field(default=None)
    last_plan_preview_details: Optional[dict] = Field(
        sa_column=Column(JSON), default=None
    )


class AppSettings(SQLModel, table=True):
    id: Optional[int] = Field(default=1, primary_key=True)
    default_sync_interval_minutes: int = Field(default=15)
    acme_sync_interval_minutes: Optional[int] = Field(default=None)
    proxy_a: bool = Field(default=True)
    proxy_aaaa: bool = Field(default=True)
    proxy_cname: bool = Field(default=True)
    proxy_host: bool = Field(default=True)
    proxy_sub: bool = Field(default=True)
    exclude_names_csv: Optional[str] = Field(default=None)


class SyncRun(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    domain_id: Optional[int] = Field(default=None, foreign_key="domain.id")
    domain_name: Optional[str] = Field(default=None, index=True)
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = Field(default=None)
    status: str = Field(default="pending")

    created_count: int = Field(default=0)
    updated_count: int = Field(default=0)
    deleted_count: int = Field(default=0)
    skipped_count: int = Field(default=0)
    noop_count: int = Field(default=0)

    error_summary: Optional[str] = Field(default=None)
    plan_summary: Optional[str] = Field(default=None)
    plan_details: Optional[dict] = Field(sa_column=Column(JSON), default=None)


class DNSRecord(BaseModel):
    type: str
    name: str
    fqdn: str
    content: str
    ttl: Optional[int] = None
    priority: Optional[int] = None
    proxied: Optional[bool] = None
    comment: Optional[str] = None
    cf_id: Optional[str] = None


class ProxyPolicy(BaseModel):
    proxy_a: bool = True
    proxy_aaaa: bool = True
    proxy_cname: bool = True
    proxy_host: bool = True
    proxy_sub: bool = True

    def desired_proxied(self, record: DNSRecord) -> Optional[bool]:
        t = record.type.upper()
        if t not in {"A", "AAAA", "CNAME"}:
            return None

        type_flag = False
        if t == "A":
            type_flag = self.proxy_a
        elif t == "AAAA":
            type_flag = self.proxy_aaaa
        elif t == "CNAME":
            type_flag = self.proxy_cname

        if not type_flag:
            return False

        name = (record.name or "").lower()
        is_host = name in {"@", "www", ""}  # root (@) and www treated as host

        if is_host:
            return self.proxy_host
        return self.proxy_sub


class PlanOperation(BaseModel):
    action: str  # create, update, delete, noop, skip
    record: DNSRecord
    current: Optional[DNSRecord] = None
    changes: dict[str, tuple[Optional[str], Optional[str]]] | None = None


class SyncPlan(BaseModel):
    domain: str
    creates: List[PlanOperation]
    updates: List[PlanOperation]
    deletes: List[PlanOperation]
    noops: List[PlanOperation]
    skips: List[PlanOperation]

    def summary(self) -> str:
        return (
            f"creates={len(self.creates)}, "
            f"updates={len(self.updates)}, "
            f"deletes={len(self.deletes)}, "
            f"noops={len(self.noops)}, "
            f"skips={len(self.skips)}"
        )
