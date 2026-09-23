"""Talks to Apple UAE's store-pickup endpoint.

One request returns every UAE store, so store filtering is a local concern.
Verified live on 2026-09-21; see spec 3.1 for the endpoints that do not work.
"""

import time
from collections.abc import Sequence

import requests

from .models import Availability

PICKUP_URL = "https://www.apple.com/ae/shop/retail/pickup-message"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
MAX_ATTEMPTS = 3
RATE_LIMIT_STATUS = 541

# Backoff between attempts, in seconds.
#
# Widened from (2, 8) after a live incident on 2026-09-23. Polling once a
# minute made Apple return HTTP 541 on about 5% of requests. All three
# attempts then fell inside the same short rate-limit window (roughly 10
# seconds end to end) and the poll was reported as a broken checker, even
# though the very next poll 48 seconds later succeeded.
#
# Retrying quickly into a rate limiter is the one thing guaranteed not to
# help. These values span roughly 65 seconds, comfortably inside the
# 2-minute poll interval, and long enough to outlast the window observed.
BACKOFF_SECONDS = (15, 45)


class AppleError(Exception):
    """Raised when Apple's response cannot be trusted or parsed."""



def _is_rate_limited(error: Exception) -> bool:
    """True for Apple's 541, which it returns when polled too often.

    541 is not a documented status. It was observed live on 2026-09-23 as
    the response to sustained sub-2-minute polling, and a single request
    minutes later succeeded, so it is a throttle rather than an outage.
    """
    response = getattr(error, "response", None)
    return getattr(response, "status_code", None) == RATE_LIMIT_STATUS


def build_params(part_numbers: Sequence[str], location: str) -> dict[str, str]:
    params = {"pl": "true", "mts.0": "regular", "location": location}
    for index, part_number in enumerate(part_numbers):
        params[f"parts.{index}"] = part_number
    return params


def parse_pickup_response(payload: dict) -> list[Availability]:
    status = payload.get("head", {}).get("status")
    if status != "200":
        raise AppleError(f"Apple returned body status {status!r}")

    stores = payload.get("body", {}).get("stores")
    if not stores:
        raise AppleError("response contained no stores")

    observations = []
    for store in stores:
        store_number = store.get("storeNumber")
        for part_number, parts in (store.get("partsAvailability") or {}).items():
            if "pickupDisplay" not in parts:
                # An absent key is a response-shape change, not an
                # unrecognised value: parts.get(..., "") would score it as a
                # hit ("" is not in the denylist), firing an urgent false
                # in-stock alert for every watched pair and locking state to
                # "hit" forever. An unrecognised non-empty VALUE is still
                # legitimately treated as a hit, by design; only the missing
                # key is untrustworthy. See spec 3.4.
                raise AppleError(
                    f"part {part_number!r} at store {store_number!r} has no "
                    f"pickupDisplay key; Apple's response shape may have "
                    f"changed"
                )
            observations.append(
                Availability(
                    part_number=part_number,
                    store_number=store_number,
                    pickup_display=parts["pickupDisplay"],
                    quote=parts.get("pickupSearchQuote", ""),
                )
            )
    return observations


def fetch_availability(
    part_numbers: Sequence[str],
    location: str,
    session: requests.Session | None = None,
) -> list[Availability]:
    """Fetch and parse, retrying transient failures.

    Raises AppleError after MAX_ATTEMPTS so the caller can alert rather than
    treat a broken checker as an absence of stock.
    """
    session = session or requests.Session()
    params = build_params(part_numbers, location)
    last_error: Exception | None = None

    for attempt in range(MAX_ATTEMPTS):
        try:
            response = session.get(
                PICKUP_URL,
                params=params,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=30,
            )
            response.raise_for_status()
            return parse_pickup_response(response.json())
        except (requests.RequestException, ValueError, AppleError) as error:
            last_error = error
            if _is_rate_limited(error):
                # 541 means "you are asking too often". Retrying is the one
                # response guaranteed to make it worse, and it triples the
                # request cost of exactly the polls that are already over
                # budget: at 120s with a 20% failure rate, retries push ~30
                # requests an hour up to ~42, which is enough on its own to
                # keep the limiter engaged. Fail fast and let the next
                # scheduled poll try, by which point the window has moved.
                raise AppleError(f"rate limited (541), not retried: {error}") from None
            if attempt < len(BACKOFF_SECONDS):
                time.sleep(BACKOFF_SECONDS[attempt])

    raise AppleError(f"failed after {MAX_ATTEMPTS} attempts: {last_error}")
