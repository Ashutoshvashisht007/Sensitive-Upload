"""Dialog backend interface and the wording shown to the user."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..sites import site_label as site_name

MARKER = "WALLED_SENSITIVE"


@dataclass(frozen=True)
class DialogText:
    title: str  # bold first line
    body: str  # details


class DialogBackend(Protocol):
    name: str

    def confirm(self, text: DialogText) -> bool:
        """Show Cancel (default) and Continue. Return True only for Continue."""
        ...


def prompt_text(site: str, excerpt: str) -> DialogText:
    return DialogText(
        title="Sensitive content detected",
        body=(
            f"Your prompt to {site_name(site)} contains {MARKER}.\n\n"
            f"“{excerpt}”\n\n"
            "Continue sends it unchanged. Cancel stops it here: nothing is sent, and you can edit and try again."
        ),
    )


@dataclass(frozen=True)
class FlaggedFile:
    name: str
    where: str = ""
    uninspectable: bool = False
    reason: str = ""


def files_text(site: str, flagged: list[FlaggedFile], clean_count: int) -> DialogText:
    site_label = site_name(site)
    any_marker = any(not f.uninspectable for f in flagged)
    title = "Sensitive content detected" if any_marker else "File couldn't be checked"

    if len(flagged) == 1:
        f = flagged[0]
        if f.uninspectable:
            lead = (f"“{f.name}” couldn't be inspected ({f.reason}) and is about to be "
                    f"uploaded to {site_label}.")
        else:
            lead = (f"“{f.name}” ({f.where}) contains {MARKER} and is about to be "
                    f"uploaded to {site_label}.")
        lines = [lead]
        cont = "Continue uploads it unchanged."
        cancel = "Cancel stops this upload."
    else:
        lines = [f"These files are about to be uploaded to {site_label}:"]
        for f in flagged:
            if f.uninspectable:
                lines.append(f"• “{f.name}” couldn't be inspected ({f.reason})")
            else:
                lines.append(f"• “{f.name}” ({f.where}) contains {MARKER}")
        cont = "Continue uploads them unchanged."
        cancel = "Cancel stops this upload."

    if clean_count:
        noun = "file" if clean_count == 1 else "files"
        verb = "uploads" if clean_count == 1 else "upload"
        them = "this file" if len(flagged) == 1 else "these files"
        cancel = f"Cancel removes {them}; the other {clean_count} {noun} {verb} normally."

    return DialogText(title=title, body="\n".join(lines) + f"\n\n{cont} {cancel}")
