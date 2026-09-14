"""Reject non-public destinations before requests and every redirect hop."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

import requests


class UnsafeURL(requests.RequestException):
    """A destination is outside the public HTTP(S) collection boundary."""


def _resolve(host: str, port: int) -> list[str]:
    try:
        return list({entry[4][0] for entry in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
    except socket.gaierror as exc:
        raise requests.ConnectionError("DNS: host name does not resolve (getaddrinfo)") from exc


def validate_public_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").rstrip(".").lower()
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise UnsafeURL("Malformed destination URL") from exc
    if parsed.scheme not in ("http", "https") or not host or parsed.username is not None or parsed.password is not None:
        raise UnsafeURL("Only HTTP(S) URLs without embedded credentials are allowed")
    if port not in (80, 443) or "\\" in url or any(ord(c) < 32 for c in url):
        raise UnsafeURL("Unsupported destination port or URL characters")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise UnsafeURL("Private destinations are not allowed")
    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        addresses = _resolve(host, port)
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise UnsafeURL("Destination must resolve exclusively to public IP addresses")
