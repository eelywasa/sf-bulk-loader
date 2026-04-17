"""EmailMessage dataclass and EmailCategory enum.

`EmailMessage` represents a single outbound email. All fields are immutable;
validation runs in `__post_init__` and raises `ValueError` on violation.
"""

from __future__ import annotations

import email.utils
import enum
from dataclasses import dataclass, field


class EmailCategory(str, enum.Enum):
    """Category tag applied to every outbound email.

    Drives metric labels and audit separation — auth mail is tracked
    independently from notification mail and system mail.
    """

    NOTIFICATION = "notification"
    AUTH = "auth"
    SYSTEM = "system"


@dataclass(frozen=True)
class EmailMessage:
    """Immutable envelope for a single outbound email.

    Exactly one recipient per message. `cc`/`bcc` are not supported;
    multi-recipient delivery is a `send_many` concern if it ever arises.
    """

    to: str
    subject: str
    text_body: str
    html_body: str | None = None
    reply_to: str | None = None
    # Arbitrary additional SMTP/SES headers (e.g. X-Priority).  Keys must not
    # collide with headers managed by the backend (From, To, Subject, etc.).
    headers: dict[str, str] | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        # Validate `to` — must be non-empty and contain exactly one '@' with a
        # non-empty domain.  We use email.utils.parseaddr so "Display Name
        # <user@domain>" is also accepted.
        if not self.to:
            raise ValueError("EmailMessage.to must not be empty.")
        _, addr_spec = email.utils.parseaddr(self.to)
        at_count = addr_spec.count("@")
        domain = addr_spec.split("@", 1)[1] if at_count == 1 else ""
        if at_count != 1 or not domain:
            raise ValueError(
                f"EmailMessage.to {self.to!r} is not a valid RFC-5321 address. "
                "Expected 'user@domain' or 'Display Name <user@domain>'."
            )

        if not self.subject:
            raise ValueError("EmailMessage.subject must not be empty.")

        if not self.text_body:
            raise ValueError("EmailMessage.text_body must not be empty.")
