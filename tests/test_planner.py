from __future__ import annotations

from da2cf.models import DNSRecord, ProxyPolicy
from da2cf.sync_service import SyncService
from da2cf.utils.dns import record_key


class DummyDA:
    def get_dns_records(self, domain: str):
        return []


class DummyCF:
    base_url = ""


def test_proxy_policy_enforced_in_plan():
    da = DummyDA()
    cf = DummyCF()
    service = SyncService(da, cf)  # type: ignore[arg-type]
    proxy_policy = ProxyPolicy(
        proxy_a=True,
        proxy_aaaa=False,
        proxy_cname=True,
        proxy_host=True,
        proxy_sub=True,
    )
    desired = [
        DNSRecord(
            type="A",
            name="www",
            fqdn="www.example.com",
            content="1.2.3.4",
        ),
        DNSRecord(
            type="AAAA",
            name="www",
            fqdn="www.example.com",
            content="::1",
        ),
        DNSRecord(
            type="CNAME",
            name="blog",
            fqdn="blog.example.com",
            content="target.example.com",
        ),
    ]
    current = []
    plan = service._compute_plan(
        "example.com",
        desired,
        current,
        proxy_policy,
        managed_types=["A", "AAAA", "CNAME"],
        exclude_patterns=[],
    )
    assert len(plan.creates) == 3
    a = next(op for op in plan.creates if op.record.type == "A")
    aaaa = next(op for op in plan.creates if op.record.type == "AAAA")
    cname = next(op for op in plan.creates if op.record.type == "CNAME")
    assert a.record.proxied is True
    assert aaaa.record.proxied is False
    assert cname.record.proxied is True
