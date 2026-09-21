"""Domain types shared across the package.

Every type here is frozen and free of I/O so the matching and diffing logic
can be tested without touching the network or the filesystem.
"""

from dataclasses import dataclass

# Values of `pickupDisplay` that mean "you cannot collect this today".
# Deliberately a denylist: the positive value has never been observed live,
# so anything outside this set is treated as stock. See spec 3.4.
NEGATIVE_PICKUP_STATES = frozenset({"unavailable", "ineligible"})

# The one positive value we believe exists. Used only to decide whether to
# annotate a message as unexpected, never to decide whether it is a hit.
EXPECTED_POSITIVE_STATE = "available"

PRIORITY_TIERS = {"urgent": 5, "high": 4, "default": 3, "low": 2}

_MODEL_LABELS = {
    "iphone18pro": "iPhone 18 Pro",
    "iphone18promax": "iPhone 18 Pro Max",
}


@dataclass(frozen=True)
class Store:
    number: str   # "R706", stable across renames
    name: str     # "Al Maryah Island"
    city: str     # "Abu Dhabi", must match Apple's string exactly
    emirate: str  # "Abu Dhabi", maintained by us; Apple sends an empty state


@dataclass(frozen=True)
class Sku:
    part_number: str  # "MJXC4AH/A"
    model: str        # "iphone18promax"
    capacity: str     # "512gb"
    color: str        # "burgundy"
    screen: str       # "6_9inch", provenance only, not filterable

    @property
    def label(self) -> str:
        model = _MODEL_LABELS.get(self.model, self.model)
        return f"{model} {self.capacity.upper()} {self.color.title()}"

    @property
    def buy_url(self) -> str:
        # "6_9inch" -> "6.9-inch"
        inches = self.screen.replace("inch", "").replace("_", ".")
        slug = f"{inches}-inch-display-{self.capacity}-{self.color}"
        return (
            "https://www.apple.com/ae/shop/buy-iphone/iphone-18-pro/" + slug
        )


@dataclass(frozen=True)
class Availability:
    """One (sku, store) observation from Apple."""

    part_number: str
    store_number: str
    pickup_display: str
    quote: str  # pickupSearchQuote, passed through verbatim

    @property
    def is_hit(self) -> bool:
        return self.pickup_display not in NEGATIVE_PICKUP_STATES

    @property
    def is_unexpected(self) -> bool:
        return self.is_hit and self.pickup_display != EXPECTED_POSITIVE_STATE


@dataclass(frozen=True)
class Watch:
    name: str
    priority: str = "urgent"
    model: str | None = None
    capacity: tuple[str, ...] = ()
    color: tuple[str, ...] = ()
    stores: tuple[str, ...] = ()
    cities: tuple[str, ...] = ()
    emirates: tuple[str, ...] = ()


@dataclass(frozen=True)
class Event:
    kind: str  # "in_stock" | "reminder" | "gone"
    sku: Sku
    store: Store
    quote: str
    pickup_display: str
    priority: int
    watches: tuple[str, ...]
    minutes: int = 0
