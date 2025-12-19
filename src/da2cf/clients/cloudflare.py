from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..models import DNSRecord
from ..utils.dns import normalize_cf_record
from ..utils.logging import redact_sensitive

logger = logging.getLogger(__name__)


def _retrying_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST", "PUT", "DELETE"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


@dataclass
class CloudflareClient:
    email: str
    api_key: str
    base_url: str = "https://api.cloudflare.com/client/v4"
    timeout: int = 15

    def _session(self) -> requests.Session:
        session = _retrying_session()
        session.headers.update(
            {
                "X-Auth-Email": self.email,
                "X-Auth-Key": self.api_key,
                "Content-Type": "application/json",
            }
        )
        return session

    def find_zone_by_name(self, name: str) -> Optional[str]:
        url = f"{self.base_url}/zones"
        params = {"name": name, "status": "active"}
        with self._session() as session:
            resp = session.get(url, params=params, timeout=self.timeout)
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:  # type: ignore[attr-defined]
            # Treat 4xx/5xx here as "zone not found" so we can continue syncing other domains.
            logger.warning("Cloudflare zone lookup failed for %s: %s", name, exc)
            return None
        data = resp.json()
        result = data.get("result", [])
        if result:
            zone_id = result[0]["id"]
            logger.info("Found Cloudflare zone %s id=%s", name, zone_id)
            return zone_id
        logger.warning("Cloudflare zone not found for %s", name)
        return None

    def list_dns_records(self, zone_id: str) -> List[DNSRecord]:
        page = 1
        per_page = 100
        records: List[DNSRecord] = []
        while True:
            url = f"{self.base_url}/zones/{zone_id}/dns_records"
            params = {"page": page, "per_page": per_page}
            with self._session() as session:
                resp = session.get(url, params=params, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            result = data.get("result", [])
            for row in result:
                records.append(normalize_cf_record(row))
            if len(result) < per_page:
                break
            page += 1
        logger.info("Fetched Cloudflare DNS records for zone=%s count=%s", zone_id, len(records))
        return records

    def create_dns_record(self, zone_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}/zones/{zone_id}/dns_records"
        with self._session() as session:
            resp = session.post(url, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def update_dns_record(self, zone_id: str, record_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}/zones/{zone_id}/dns_records/{record_id}"
        with self._session() as session:
            resp = session.put(url, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def delete_dns_record(self, zone_id: str, record_id: str) -> Dict[str, Any]:
        url = f"{self.base_url}/zones/{zone_id}/dns_records/{record_id}"
        with self._session() as session:
            resp = session.delete(url, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()
