from __future__ import annotations

import fnmatch
from typing import Dict, Iterable, List, Optional, Tuple

from ..models import DNSRecord


def normalize_name(name: str, domain: str) -> Tuple[str, str]:
    domain = domain.rstrip(".").lower()
    raw = name.strip().rstrip(".")
    if raw in ("", "@", domain):
        return "@", domain
    if raw.lower().endswith("." + domain):
        rel = raw[: -(len(domain) + 1)]
        return rel, f"{rel}.{domain}"
    if "." not in raw:
        return raw, f"{raw}.{domain}"
    return raw, raw


def normalize_txt_content(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith('"') and stripped.endswith('"'):
        stripped = stripped[1:-1]
    return " ".join(stripped.split())


def normalize_da_record(row: Dict, domain: str) -> DNSRecord:
    rtype = str(row.get("type") or row.get("record_type") or "").upper()
    name = str(row.get("name") or row.get("host") or "@")
    value = str(row.get("value") or row.get("data") or row.get("content") or "")
    ttl = row.get("ttl")
    priority = row.get("priority")
    if isinstance(ttl, str) and ttl.isdigit():
        ttl = int(ttl)
    if isinstance(priority, str) and priority.isdigit():
        priority = int(priority)

    rel, fqdn = normalize_name(name, domain)
    if rtype == "TXT":
        value = normalize_txt_content(value)
    if rtype == "CNAME":
        value = value.rstrip(".")

    return DNSRecord(
        type=rtype,
        name=rel,
        fqdn=fqdn,
        content=value,
        ttl=ttl,
        priority=priority,
    )


def normalize_cf_record(row: Dict) -> DNSRecord:
    rtype = str(row.get("type", "")).upper()
    name = str(row.get("name", ""))
    content = str(row.get("content", ""))
    ttl = row.get("ttl")
    priority = row.get("priority")
    proxied = row.get("proxied")
    comment = row.get("comment")
    record_id = row.get("id")

    fqdn = name.rstrip(".").lower()
    parts = fqdn.split(".")
    rel = fqdn

    # Special handling: treat *.de5.net like a "TLD domain" (e.g. about.de5.net),
    # so about.de5.net is the zone root, and www.about.de5.net has name "www".
    if fqdn.endswith(".de5.net") and len(parts) >= 3:
        # Base zone is the last 3 labels: <label>.de5.net
        if len(parts) == 3:
            rel = "@"
        else:
            rel = ".".join(parts[:-3])
    else:
        if len(parts) > 2:
            rel = ".".join(parts[:-2])
        elif len(parts) == 2:
            rel = "@"

    if rtype == "TXT":
        content = normalize_txt_content(content)
    if rtype == "CNAME":
        content = content.rstrip(".")

    return DNSRecord(
        type=rtype,
        name=rel,
        fqdn=fqdn,
        content=content,
        ttl=ttl,
        priority=priority,
        proxied=proxied,
        comment=comment,
        cf_id=record_id,
    )


def record_key(record: DNSRecord) -> Tuple[str, str, int]:
    t = record.type.upper()
    priority = record.priority or 0
    if t not in {"MX", "SRV"}:
        priority = 0
    return t, record.fqdn.lower(), priority


def should_exclude(record: DNSRecord, patterns: Iterable[str]) -> bool:
    for pattern in patterns:
        if fnmatch.fnmatch(record.name, pattern) or fnmatch.fnmatch(
            record.fqdn, pattern
        ):
            return True
    return False
