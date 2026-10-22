"""Douyin: public video and note details without login.

The share-page API ``www.iesdouyin.com/web/api/v2/aweme/slidesinfo/`` returns the full
``aweme_details`` record (title, author, duration, play addresses, images, Live Photos) for
public items, with no cookie and no a_bogus signature. Videos answer without
``request_source``; image notes need ``request_source=200``, so both are tried.

This is the technique used by maintained parsers such as wujunwei928/parse-video-py (MIT),
which moved to it after the share page's SSR ``videoInfoRes`` stopped carrying data.
Verified 2026-10-22: two public videos returned details and their play URL served video/mp4.
The old ``/web/api/v2/aweme/iteminfo/`` endpoint is retired and must not come back.
"""

from __future__ import annotations

SLIDESINFO = "https://www.iesdouyin.com/web/api/v2/aweme/slidesinfo/?aweme_ids=%5B{aweme_id}%5D"

# A mobile Safari user agent: the share-page API serves mobile clients.
MOBILE_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


def slidesinfo_urls(aweme_id: str) -> list[str]:
    """Video first, then the note variant."""
    base = SLIDESINFO.format(aweme_id=aweme_id)
    return [base, f"{base}&request_source=200"]


def item_from_slidesinfo(payload: object) -> dict:
    """The aweme record from a slidesinfo response, or {} when it carries none."""
    if not isinstance(payload, dict):
        return {}
    details = payload.get("aweme_details")
    if isinstance(details, list) and details and isinstance(details[0], dict):
        return details[0]
    return {}


def no_watermark(url: str) -> str:
    """Play addresses that still say ``playwm`` point at the watermarked stream."""
    return url.replace("/playwm/", "/play/")
