from __future__ import annotations

from typing import Any, Dict


SENSITIVE_KEYS = {"password", "token", "secret", "key", "authorization", "cookie"}


def redact_sensitive(data: Dict[str, Any]) -> Dict[str, Any]:
    redacted: Dict[str, Any] = {}
    for k, v in data.items():
        lower = k.lower()
        if any(s in lower for s in SENSITIVE_KEYS):
            redacted[k] = "***redacted***"
        else:
            redacted[k] = v
    return redacted

