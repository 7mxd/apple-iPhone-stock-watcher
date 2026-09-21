"""Renders and sends ntfy notifications.

The topic name is the only secret, so it is read from the environment and
never logged.
"""

import requests

from .models import EXPECTED_POSITIVE_STATE, Event

NTFY_BASE = "https://ntfy.sh"
TAGS = {"in_stock": "rotating_light", "reminder": "hourglass", "gone": "wave"}


class NotifyError(Exception):
    """Raised when an ntfy POST fails, in place of the raw requests error.

    requests embeds the full request URL -- https://ntfy.sh/<topic> -- in
    both raise_for_status() messages and connection-error text, so letting
    one of those propagate would print the project's only secret straight
    to the caller's logs. The message here is deliberately limited to the
    failing exception's type name; callers must not try to recover more
    detail from it.
    """


def render(event: Event) -> tuple[str, str, dict[str, str]]:
    phone = event.sku.label
    store = event.store.name

    if event.kind == "in_stock":
        title = f"IN STOCK: {phone} at {store}"
        body = event.quote or "Available for pickup"
        if event.pickup_display != EXPECTED_POSITIVE_STATE:
            body += f"\n(unexpected pickup state: {event.pickup_display})"
    elif event.kind == "reminder":
        title = f"Still in stock: {phone} at {store}"
        body = f"{event.quote}\nUp for {event.minutes} min"
    else:
        title = f"Gone: {phone} at {store}"
        body = f"Window lasted {event.minutes} min"

    body += f"\nWatch: {', '.join(event.watches)}"

    headers = {
        "Title": title,
        "Priority": str(event.priority),
        "Tags": TAGS.get(event.kind, "bell"),
        "Click": event.sku.buy_url,
    }
    return title, body, headers


def send(topic: str, event: Event, session: requests.Session | None = None) -> None:
    session = session or requests.Session()
    _, body, headers = render(event)
    try:
        response = session.post(
            f"{NTFY_BASE}/{topic}",
            data=body.encode("utf-8"),
            headers=headers,
            timeout=15,
        )
        response.raise_for_status()
    except Exception as error:
        # Fixed at the source so every call site is safe by construction,
        # including ones that forget to wrap this call themselves: `from
        # None` is mandatory, otherwise Python chains the original,
        # URL-bearing exception into the traceback anyway.
        raise NotifyError(f"ntfy POST failed: {type(error).__name__}") from None


def send_text(
    topic: str,
    title: str,
    body: str,
    priority: int,
    session: requests.Session | None = None,
) -> None:
    """Send an alert with no SKU or store behind it, such as a health warning."""
    session = session or requests.Session()
    try:
        response = session.post(
            f"{NTFY_BASE}/{topic}",
            data=body.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": str(priority),
                "Tags": "warning",
            },
            timeout=15,
        )
        response.raise_for_status()
    except Exception as error:
        # See send(): fixed at the source, from None is mandatory.
        raise NotifyError(f"ntfy POST failed: {type(error).__name__}") from None
