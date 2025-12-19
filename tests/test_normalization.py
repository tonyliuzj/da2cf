from __future__ import annotations

from da2cf.utils.dns import normalize_da_record, normalize_cf_record, normalize_txt_content


def test_txt_normalization():
    assert normalize_txt_content('"hello  world"') == "hello world"
    assert normalize_txt_content("  hello   world  ") == "hello world"


def test_cname_trailing_dot(sample_domain: str):
    rec = normalize_da_record(
        {"name": "www", "type": "CNAME", "value": "target.example.com."},
        sample_domain,
    )
    assert rec.content == "target.example.com"


def test_root_at_handling(sample_domain: str):
    rec = normalize_da_record(
        {"name": "@", "type": "A", "value": "1.2.3.4"},
        sample_domain,
    )
    assert rec.fqdn == sample_domain

