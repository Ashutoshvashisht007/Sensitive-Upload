from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    """Why a file needs the user's attention.

    Either the marker was found (`where` says where), or the file couldn't be
    inspected (`uninspectable=True`, `reason` says why). A clean file has no
    Finding at all.
    """

    where: str = ""
    excerpt: str = ""
    uninspectable: bool = False
    reason: str = ""

    @classmethod
    def cannot_inspect(cls, reason: str) -> "Finding":
        return cls(uninspectable=True, reason=reason)
