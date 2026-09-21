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
BACKOFF_SECONDS = (2, 8)


class AppleError(Exception):
    """Raised when Apple's response cannot be trusted or parsed."""


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
            observations.append(
                Availability(
                    part_number=part_number,
                    store_number=store_number,
                    pickup_display=parts.get("pickupDisplay", ""),
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
            if attempt < len(BACKOFF_SECONDS):
                time.sleep(BACKOFF_SECONDS[attempt])

    raise AppleError(f"failed after {MAX_ATTEMPTS} attempts: {last_error}")
