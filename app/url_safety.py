from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeExternalUrlError(ValueError):
    """Raised when a user-controlled URL targets a local or reserved network."""


_SYNTHETIC_DNS_RANGE = ipaddress.ip_network("198.18.0.0/15")


def assert_public_http_url(raw_url: str) -> str:
    """Validate a URL before the server makes a request to it."""
    value = str(raw_url or "").strip()
    parsed = urlparse(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise UnsafeExternalUrlError("只支持公开的 HTTP 或 HTTPS 网页地址。")
    if parsed.username or parsed.password:
        raise UnsafeExternalUrlError("带有登录信息的地址不能直接处理。")

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".localhost"):
        raise UnsafeExternalUrlError("不能处理本机地址，请提供公开网页链接。")

    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        if not literal.is_global:
            raise UnsafeExternalUrlError("不能处理本机或内网地址，请提供公开网页链接。")
        return value

    try:
        resolved = socket.getaddrinfo(
            hostname,
            parsed.port or (443 if parsed.scheme.lower() == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except (OSError, ValueError):
        return value

    for item in resolved:
        address = item[4][0] if item and len(item) > 4 and item[4] else ""
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        # Some managed Windows/network environments map public DNS answers to
        # RFC 2544's 198.18/15 benchmarking range.  It is not routable to a
        # user-selected private service, so keep public platform links usable
        # there while still rejecting loopback, RFC1918, link-local, and other
        # reserved targets.
        if not ip.is_global and ip not in _SYNTHETIC_DNS_RANGE:
            raise UnsafeExternalUrlError("不能处理本机或内网地址，请提供公开网页链接。")
    return value
