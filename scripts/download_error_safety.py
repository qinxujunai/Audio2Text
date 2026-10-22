"""Redact configured proxy credentials at downloader process boundaries."""
import re
from urllib.parse import unquote


def safe_download_error(error, proxy: str = "") -> str:
    message = str(error)
    if proxy:
        message = message.replace(proxy, "[proxy]")
        # Also handle malformed one-slash addresses rejected by yt-dlp.
        match = re.search(r":/{1,2}([^/\s]*?)@", proxy)
        if match:
            for value in match.group(1).split(":", 1):
                for secret in {value, unquote(value)}:
                    if secret:
                        message = message.replace(secret, "[redacted]")
    return re.sub(r"(?<=/)[^/\s:@]+:[^/@\s]+@", "[redacted]@", message)[:1200]


class SilentDownloadLogger:
    def debug(self, *_args, **_kwargs): pass
    def warning(self, *_args, **_kwargs): pass
    def error(self, *_args, **_kwargs): pass
