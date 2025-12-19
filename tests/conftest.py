from __future__ import annotations

import pytest

from da2cf.models import ProxyPolicy
from da2cf.utils.dns import normalize_da_record, normalize_cf_record


@pytest.fixture
def sample_domain() -> str:
    return "example.com"

