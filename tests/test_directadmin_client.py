from __future__ import annotations

import responses

from da2cf.clients.directadmin import DirectAdminClient


@responses.activate
def test_list_domains_json():
    client = DirectAdminClient(
        base_url="https://da.example.com:2222",
        username="user",
        password="pass",
    )
    responses.add(
        responses.GET,
        "https://da.example.com:2222/CMD_API_SHOW_DOMAINS",
        json={"list": ["example.com", "example.org"]},
        status=200,
    )
    domains = client.list_domains()
    assert "example.com" in domains
    assert "example.org" in domains


@responses.activate
def test_get_dns_records_plain_text():
    client = DirectAdminClient(
        base_url="https://da.example.com:2222",
        username="user",
        password="pass",
    )
    body = "www A 1.2.3.4 300\n@\tTXT\t\"hello world\" 300\n"
    responses.add(
        responses.GET,
        "https://da.example.com:2222/CMD_API_DNS_CONTROL",
        body=body,
        status=200,
    )
    records = client.get_dns_records("example.com")
    assert any(r.type == "A" for r in records)
    assert any(r.type == "TXT" for r in records)

