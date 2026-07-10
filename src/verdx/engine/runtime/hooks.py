"""Event hooks — fire-and-forget webhook dispatch.

Hooks are configured in <state_dir>/hooks.yml. On registered events,
the system POSTs a JSON payload to the configured URL. Failures are
logged but never fail the validation run.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
import urllib.request
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import yaml

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

VALID_EVENTS = frozenset({"run-start", "validator-complete", "run-complete"})
_DISPATCH_TIMEOUT = 5


@dataclass(frozen=True, slots=True)
class HookConfig:
    url: str
    events: frozenset[str] = field(default_factory=lambda: frozenset(VALID_EVENTS))
    headers: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.url:
            raise ValueError("url must be non-empty")
        _validate_hook_url(self.url)
        unknown = self.events - VALID_EVENTS
        if unknown:
            raise ValueError(f"unknown events: {sorted(unknown)}")


@dataclass
class HookRegistry:
    """Manages hook configs persisted in <state_dir>/hooks.yml."""

    _state_dir: Path
    _hooks: list[HookConfig] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._path = self._state_dir / "hooks.yml"
        if self._path.exists():
            raw = yaml.safe_load(self._path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and "hooks" in raw:
                for entry in raw["hooks"]:
                    self._hooks.append(_parse_hook(entry))

    @property
    def hooks(self) -> list[HookConfig]:
        return list(self._hooks)

    def add(self, hook: HookConfig) -> None:
        self._hooks.append(hook)
        self._save()

    def remove(self, url: str) -> bool:
        before = len(self._hooks)
        self._hooks = [h for h in self._hooks if h.url != url]
        if len(self._hooks) < before:
            self._save()
            return True
        return False

    def _save(self) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "hooks": [
                {
                    "url": h.url,
                    "events": sorted(h.events),
                    **({"headers": dict(h.headers)} if h.headers else {}),
                }
                for h in self._hooks
            ]
        }
        self._path.write_text(
            yaml.dump(data, default_flow_style=False),
            encoding="utf-8",
        )


def _validate_hook_url(url: str) -> None:
    """Reject non-HTTPS URLs and URLs targeting private/loopback addresses."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"hook URL must use HTTPS, got {parsed.scheme!r}")
    hostname = parsed.hostname or ""
    _check_hostname_not_private(hostname)


def _check_hostname_not_private(hostname: str) -> None:
    """Resolve hostname and reject if any resolved IP is private/loopback/link-local."""
    try:
        addr = ipaddress.ip_address(hostname)
        if addr.is_private or addr.is_loopback or addr.is_link_local:
            raise ValueError(f"hook URL must not target private address: {hostname}")
        return
    except ValueError as exc:
        if "hook URL" in str(exc):
            raise
    # hostname is a DNS name; resolve and check all returned IPs
    try:
        addrinfos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return  # unresolvable at config time is fine; dispatch will fail naturally
    for _family, _type, _proto, _canonname, sockaddr in addrinfos:
        ip_str = sockaddr[0]
        try:
            addr = ipaddress.ip_address(ip_str)
            if addr.is_private or addr.is_loopback or addr.is_link_local:
                raise ValueError(
                    f"hook URL must not target private address: {hostname} resolves to {ip_str}"
                )
        except ValueError as exc:
            if "hook URL" in str(exc):
                raise


def _parse_hook(entry: dict[str, Any]) -> HookConfig:
    url = entry.get("url", "")
    events = frozenset(entry.get("events", VALID_EVENTS))
    headers = dict(entry.get("headers", {}))
    return HookConfig(url=url, events=events, headers=headers)


def dispatch_hook(
    registry: HookRegistry,
    event: str,
    payload: dict[str, Any],
) -> None:
    """POST payload to all hooks registered for this event."""
    body = json.dumps(payload).encode("utf-8")
    for hook in registry.hooks:
        if event not in hook.events:
            continue
        try:
            # Re-validate resolved IPs at dispatch time to prevent DNS rebinding
            parsed = urlparse(hook.url)
            _check_hostname_not_private(parsed.hostname or "")
            req = urllib.request.Request(
                hook.url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    **hook.headers,
                },
                method="POST",
            )
            urllib.request.urlopen(req, timeout=_DISPATCH_TIMEOUT)
        except Exception:
            logger.debug("hook dispatch failed: %s %s", event, hook.url, exc_info=True)
