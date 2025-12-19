from __future__ import annotations

import responses

from da2cf.clients.cloudflare import CloudflareClient


@responses.activate
def test_find_zone_by_name():
    client = CloudflareClient(email="user@example.com", api_key="key")
    responses.add(
        responses.GET,
        "https://api.cloudflare.com/client/v4/zones",
        json={"result": [{"id": "zone123"}]},
        status=200,
    )
    zone_id = client.find_zone_by_name("example.com")
    assert zone_id == "zone123"


@responses.activate
def test_list_dns_records():
    client = CloudflareClient(email="user@example.com", api_key="key")
    responses.add(
        responses.GET,
        "https://api.cloudflare.com/client/v4/zones/zone123/dns_records",
        json={
            "result": [
                {
                    "id": "rec1",
                    "type": "A",
                    "name": "example.com",
                    "content": "1.2.3.4",
                    "ttl": 300,
                    "proxied": True,
                }
            ]
        },
        status=200,
    )
    records = client.list_dns_records("zone123")
    assert len(records) == 1
    assert records[0].type == "A"

