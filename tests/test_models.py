from applewatch.models import Availability, Sku

TARGET = Sku(
    part_number="MJXC4AH/A",
    model="iphone18promax",
    capacity="512gb",
    color="burgundy",
    screen="6_9inch",
)


def test_sku_label_is_human_readable():
    assert TARGET.label == "iPhone 18 Pro Max 512GB Burgundy"


def test_sku_buy_url_matches_apples_real_url():
    assert TARGET.buy_url == (
        "https://www.apple.com/ae/shop/buy-iphone/iphone-18-pro"
        "/6.9-inch-display-512gb-burgundy"
    )


def test_unavailable_is_not_a_hit():
    obs = Availability("MJXC4AH/A", "R706", "unavailable", "Currently unavailable")
    assert obs.is_hit is False


def test_ineligible_is_not_a_hit():
    obs = Availability("MJX94AH/A", "R597", "ineligible", "Currently unavailable")
    assert obs.is_hit is False


def test_unrecognised_state_is_a_hit():
    """The positive value was never observed live, so anything not on the
    denylist must count as stock. See spec 3.4."""
    obs = Availability("MJXC4AH/A", "R595", "wibble", "Available Wed 23 Sep")
    assert obs.is_hit is True
    assert obs.is_unexpected is True


def test_available_is_a_hit_and_not_unexpected():
    obs = Availability("MJXC4AH/A", "R595", "available", "Available Wed 23 Sep")
    assert obs.is_hit is True
    assert obs.is_unexpected is False
