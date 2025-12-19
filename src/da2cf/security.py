from __future__ import annotations

import os
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .config import env_config


security = HTTPBasic()


def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    username = env_config.app_admin_user or ""
    password = env_config.app_admin_password or ""
    if not username or not password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin credentials not configured",
        )
    if credentials.username != username or credentials.password != password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def get_current_user(request: Request) -> Optional[str]:
    return request.session.get("user")


def require_user(request: Request) -> str:
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_302_FOUND, headers={"Location": "/login"})
    return user


def get_session_secret() -> str:
    secret = env_config.app_secret_key
    if not secret:
        secret = os.urandom(32).hex()
    return secret

