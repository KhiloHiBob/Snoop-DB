"""Tunnel / port preflight. Never guesses ports or credentials."""

from __future__ import annotations

import socket
from dataclasses import dataclass

from .config import EnvConfig


class PreflightError(RuntimeError):
    """The target is not reachable; the message tells the user what to do."""


@dataclass
class PreflightResult:
    ok: bool
    message: str = ""


def check_port(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def preflight(env: EnvConfig, store: str, host: str | None, port: int | None) -> PreflightResult:
    if not host or not port:
        return PreflightResult(
            False,
            f"MISSING: no host/port configured for {store} in environment '{env.name}'. "
            "Add it to config/local.yaml.",
        )
    if check_port(host, int(port)):
        return PreflightResult(True)
    hint = f"MISSING: nothing is listening on {host}:{port} for {store} ({env.name})."
    if env.sdm_resource:
        hint += f" Open the tunnel first, e.g. `sdm connect {env.sdm_resource}`, then retry."
    else:
        hint += " Start the service or open the tunnel, then retry."
    return PreflightResult(False, hint)


def require(env: EnvConfig, store: str, host: str | None, port: int | None) -> None:
    result = preflight(env, store, host, port)
    if not result.ok:
        raise PreflightError(result.message)
