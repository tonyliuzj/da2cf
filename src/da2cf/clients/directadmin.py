from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs

import requests

from ..models import DNSRecord
from ..utils.dns import normalize_da_record
from ..utils.logging import redact_sensitive

logger = logging.getLogger(__name__)


@dataclass
class DirectAdminClient:
    base_url: str
    username: str
    password: Optional[str] = None
    token: Optional[str] = None
    timeout: int = 15
    # pointer_map maps pointer/alias domains -> their main/base domain
    pointer_map: Dict[str, str] = field(default_factory=dict, init=False, repr=False)

    def _session(self) -> requests.Session:
        session = requests.Session()
        if self.token:
            session.headers.update({"Authorization": f"Bearer {self.token}"})
        return session

    def _parse_domain_list(self, text: str) -> List[str]:
        parsed = parse_qs(text, keep_blank_values=False)
        domains: List[str] = []
        for key in ("list[]", "list", "domains"):
            if key in parsed and parsed[key]:
                for v in parsed[key]:
                    domains.append(str(v).replace("_", "."))
        return domains

    def _parse_pointer_domains(self, text: str) -> List[str]:
        """Parse domain pointers from a DirectAdmin API response.

        Pointers may appear either as values (e.g. list[]=alias_example_com)
        or as keys (e.g. alias_example_com=value), so we consider both.
        """
        parsed = parse_qs(text, keep_blank_values=False)
        domains: set[str] = set()
        for key, values in parsed.items():
            key_s = str(key).replace("_", ".")
            if "." in key_s and " " not in key_s:
                domains.add(key_s)
            for v in values:
                s = str(v).replace("_", ".")
                if "." in s and " " not in s:
                    domains.add(s)
        return sorted(domains)

    def list_domains(self) -> List[str]:
        """Return domains for the current DirectAdmin user plus their pointers."""
        url = f"{self.base_url.rstrip('/')}/CMD_API_SHOW_DOMAINS"
        with self._session() as session:
            resp = session.get(url, auth=self._auth(), timeout=self.timeout)
        resp.raise_for_status()
        base_domains = set(self._parse_domain_list(resp.text))
        logger.info("Fetched DirectAdmin base domains (count=%s)", len(base_domains))

        all_domains: set[str] = set(base_domains)

        # Also fetch domain pointers (aliases) for each domain, best-effort.
        for domain in list(base_domains):
            for ptr in self.list_domain_pointers(domain):
                all_domains.add(ptr)

        logger.info("Fetched DirectAdmin domains and pointers (count=%s)", len(all_domains))
        return sorted(all_domains)

    def list_domain_pointers(self, domain: str) -> List[str]:
        """Return domain pointers (aliases) for a given base domain, if supported."""
        base = self.base_url.rstrip("/")
        endpoints = [
            f"{base}/CMD_API_DOMAIN_POINTER",
            f"{base}/CMD_API_SHOW_DOMAIN_POINTERS",
        ]
        params = {"domain": domain}
        pointers: set[str] = set()
        for url in endpoints:
            try:
                with self._session() as session:
                    resp = session.get(url, params=params, auth=self._auth(), timeout=self.timeout)
                if resp.status_code != 200:
                    continue
                for p in self._parse_pointer_domains(resp.text):
                    pointers.add(p)
            except Exception:  # noqa: BLE001
                continue

        result = sorted(pointers)
        if result:
            for p in result:
                # Store mapping pointer -> base domain
                if p != domain:
                    self.pointer_map[p] = domain
            logger.info(
                "Fetched DirectAdmin domain pointers for %s count=%s",
                domain,
                len(result),
            )
        return result

    def resolve_base_domain(self, domain: str) -> str:
        """Return the base domain for a possible pointer/alias.

        If the domain is a pointer, this returns its mapped base domain.
        Otherwise it returns the domain itself.
        """
        if not self.pointer_map:
            try:
                # Populate pointer_map from DirectAdmin, best-effort.
                self.list_domains()
            except Exception:  # noqa: BLE001
                return domain
        return self.pointer_map.get(domain, domain)

    def get_dns_records(self, domain: str) -> List[DNSRecord]:
        # If this domain is a pointer/alias, use CMD_DNS_CONTROL with ptr + base domain.
        base_url = self.base_url.rstrip("/")
        if not self.pointer_map:
            # Lazily populate pointer map from list_domains, best-effort.
            try:
                self.list_domains()
            except Exception:  # noqa: BLE001
                pass
        pointer_base = self.pointer_map.get(domain)

        if pointer_base and pointer_base != domain:
            url = f"{base_url}/CMD_DNS_CONTROL"
            params = {"domain": pointer_base, "ptr": domain, "json": "yes", "ttl": "yes"}
        else:
            url = f"{base_url}/CMD_API_DNS_CONTROL"
            params = {"domain": domain, "json": "yes"}
        with self._session() as session:
            resp = session.get(url, params=params, auth=self._auth(), timeout=self.timeout)
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:  # type: ignore[attr-defined]
            logger.warning(
                "Failed to fetch DirectAdmin DNS records for %s: %s", domain, exc
            )
            # On DirectAdmin 5xx or other errors, skip this domain's records instead of crashing.
            return []
        try:
            data = resp.json()
            records = self._parse_json_dns(data, domain)
        except ValueError:
            records = self._parse_legacy_dns(resp.text, domain)

        logger.info("Fetched DirectAdmin DNS records for %s count=%s", domain, len(records))
        return records

    def _auth(self) -> Optional[tuple[str, str]]:
        if self.token:
            return None
        if self.username and self.password:
            return (self.username, self.password)
        return None

    def _parse_json_dns(self, data: Any, domain: str) -> List[DNSRecord]:
        records: List[DNSRecord] = []
        if isinstance(data, dict):
            rows = data.get("records") or data.get("list") or data.get("dns")
            if isinstance(rows, list):
                for row in rows:
                    rec = self._record_from_mapping(row, domain)
                    if rec:
                        records.append(rec)
        return records

    def _parse_legacy_dns(self, text: str, domain: str) -> List[DNSRecord]:
        records: List[DNSRecord] = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 3:
                continue
            name, rtype, content = parts[0], parts[1], parts[2]
            ttl = None
            priority = None
            if len(parts) >= 4 and parts[3].isdigit():
                ttl = int(parts[3])
            if len(parts) >= 5 and parts[4].isdigit():
                priority = int(parts[4])
            rec = normalize_da_record(
                {
                    "name": name,
                    "type": rtype,
                    "value": content,
                    "ttl": ttl,
                    "priority": priority,
                },
                domain,
            )
            records.append(rec)
        return records

    def _record_from_mapping(self, row: Dict[str, Any], domain: str) -> Optional[DNSRecord]:
        if "name" not in row or "type" not in row:
            return None
        return normalize_da_record(row, domain)
