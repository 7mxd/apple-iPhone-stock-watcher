from applewatch.catalog import load_catalog
from applewatch.models import Event
from applewatch.notify import render
from applewatch.stores import load_stores

CATALOG = load_catalog()
STORES = load_stores()
SKU = CATALOG["MJXC4AH/A"]
YAS = STORES["R595"]


def event(kind, priority, display="available", quote="Available Wed 23 Sep", minutes=0):
    return Event(
        kind=kind,
        sku=SKU,
        store=YAS,
        quote=quote,
        pickup_display=display,
        priority=priority,
        watches=("default",),
        minutes=minutes,
    )


def test_in_stock_title_names_the_phone_and_the_store():
    title, body, headers = render(event("in_stock", 5))
    assert "iPhone 18 Pro Max 512GB Burgundy" in title
    assert "Yas Mall" in title
    assert "Available Wed 23 Sep" in body
    assert headers["Priority"] == "5"


def test_in_stock_links_straight_to_the_buy_page():
    _, _, headers = render(event("in_stock", 5))
    assert headers["Click"] == SKU.buy_url


def test_unexpected_state_is_flagged_in_the_body():
    _, body, _ = render(event("in_stock", 5, display="wibble"))
    assert "wibble" in body


def test_expected_state_is_not_flagged():
    _, body, _ = render(event("in_stock", 5, display="available"))
    assert "wibble" not in body and "unexpected" not in body.lower()


def test_reminder_reports_how_long_it_has_been_up():
    title, body, headers = render(event("reminder", 3, minutes=47))
    assert "47" in title or "47" in body
    assert headers["Priority"] == "3"


def test_gone_reports_the_window_length():
    title, body, headers = render(event("gone", 2, display="unavailable",
                                        quote="Currently unavailable", minutes=52))
    assert "52" in title or "52" in body
    assert headers["Priority"] == "2"


def test_send_text_posts_a_bare_message(monkeypatch):
    """Health alerts have no SKU or store behind them."""
    captured = {}

    class FakeSession:
        def post(self, url, data, headers, timeout):
            captured["url"] = url
            captured["data"] = data
            captured["headers"] = headers
            return type("R", (), {"raise_for_status": lambda self: None})()

    from applewatch.notify import send_text

    send_text("topic-abc", "Checker broken", "simulated outage", 2,
              session=FakeSession())
    assert captured["url"].endswith("/topic-abc")
    assert captured["headers"]["Priority"] == "2"
    assert b"simulated outage" in captured["data"]
