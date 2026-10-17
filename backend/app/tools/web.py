"""Native web tools and schemas."""

from __future__ import annotations

import re


def register(registry, schema):
    def web_fetch(url: str, max_chars: int = 20000) -> str:
        """Fetch an http(s) URL and return its text (HTML stripped)."""
        import html as _html
        import ipaddress
        import socket
        import urllib.parse
        import urllib.request

        url = str(url).strip()
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return "Error: url must be an http(s) URL"
        # SSRF guard: refuse loopback / private / link-local targets.
        try:
            for info in socket.getaddrinfo(parsed.hostname, None):
                ip = ipaddress.ip_address(info[4][0])
                if (ip.is_private or ip.is_loopback or ip.is_link_local
                        or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
                    return f"Error: refusing to fetch non-public address ({ip})"
        except Exception as e:
            return f"Error: cannot resolve host: {e}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "AgentHarness/1.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read(2_000_000)
                ctype = resp.headers.get("Content-Type", "") or ""
        except Exception as e:
            return f"Error: {type(e).__name__}: {e}"
        charset = "utf-8"
        if "charset=" in ctype:
            charset = ctype.split("charset=")[-1].split(";")[0].strip() or "utf-8"
        text = raw.decode(charset, errors="replace")
        if "html" in ctype.lower() or text.lstrip()[:1] == "<":
            text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
            text = re.sub(r"(?s)<[^>]+>", " ", text)
            text = _html.unescape(text)
            text = re.sub(r"[ \t\r\f\v]+", " ", text)
            text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
        try:
            limit = int(max_chars)
        except Exception:
            limit = 20000
        return text[:limit] if text else "(empty response)"

    registry.tools.update({
        'web_fetch': {"schema": schema(
                "web_fetch", "Fetch an http(s) URL and return its readable text (HTML is stripped).",
                {"url": {"type": "string"}, "max_chars": {"type": "integer"}}, ["url"]),
                "handler": web_fetch, "mcp": False},
    })
