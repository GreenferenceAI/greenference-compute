"""Optional auth for compute agent endpoints."""

from __future__ import annotations

from fastapi import HTTPException, status


def _validate(header: str | None, bearer: str | None, expected: str | None) -> None:
    if not expected:
        return
    if header and header == expected:
        return
    if bearer and bearer == expected:
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="missing or invalid auth",
    )


def validate_agent_auth(header: str | None, bearer: str | None, expected: str | None) -> None:
    _validate(header, bearer, expected)


def validate_compute_auth(header: str | None, bearer: str | None, expected: str | None) -> None:
    _validate(header, bearer, expected)
