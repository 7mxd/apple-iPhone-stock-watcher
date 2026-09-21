import pytest

from applewatch.catalog import load_catalog
from applewatch.config import ConfigError, load_config
from applewatch.stores import load_stores

CATALOG = load_catalog()
STORES = load_stores()


def write(tmp_path, body: str):
    path = tmp_path / "watches.yml"
    path.write_text(body, encoding="utf-8")
    return path


BASE = """
ntfy_topic_env: NTFY_TOPIC
poll_location: Abu Dhabi
reminder_minutes: 30
watches:
  - name: "Burgundy 512 Pro Max, Abu Dhabi city"
    model: iphone18promax
    capacity: [512gb]
    color: [burgundy]
    cities: [Abu Dhabi]
    priority: urgent
"""


def test_loads_the_default_watch(tmp_path):
    config = load_config(write(tmp_path, BASE), CATALOG, STORES)
    assert config.reminder_minutes == 30
    assert config.poll_location == "Abu Dhabi"
    assert len(config.watches) == 1
    assert config.watches[0].cities == ("Abu Dhabi",)


def test_two_location_selectors_is_an_error(tmp_path):
    body = BASE + "    emirates: [Abu Dhabi]\n"
    with pytest.raises(ConfigError) as exc:
        load_config(write(tmp_path, body), CATALOG, STORES)
    assert "cities" in str(exc.value) and "emirates" in str(exc.value)


def test_misspelled_colour_is_an_error(tmp_path):
    """A typo must fail loudly, not silently match nothing."""
    body = BASE.replace("[burgundy]", "[burgandy]")
    with pytest.raises(ConfigError) as exc:
        load_config(write(tmp_path, body), CATALOG, STORES)
    assert "burgandy" in str(exc.value)


def test_unknown_store_is_an_error(tmp_path):
    body = BASE.replace("cities: [Abu Dhabi]", "stores: [R999]")
    with pytest.raises(ConfigError) as exc:
        load_config(write(tmp_path, body), CATALOG, STORES)
    assert "R999" in str(exc.value)


def test_unknown_priority_is_an_error(tmp_path):
    body = BASE.replace("priority: urgent", "priority: screaming")
    with pytest.raises(ConfigError) as exc:
        load_config(write(tmp_path, body), CATALOG, STORES)
    assert "screaming" in str(exc.value)


def test_unknown_key_is_an_error(tmp_path):
    """`screen` is deliberately not a field. Accepting it silently would let
    a user write a rule they think is narrower than it is."""
    body = BASE + "    screen: 6_9inch\n"
    with pytest.raises(ConfigError) as exc:
        load_config(write(tmp_path, body), CATALOG, STORES)
    assert "screen" in str(exc.value)


def test_reminder_minutes_zero_is_an_error(tmp_path):
    """0 would make the reminder fire on every single run."""
    body = BASE.replace("reminder_minutes: 30", "reminder_minutes: 0")
    with pytest.raises(ConfigError) as exc:
        load_config(write(tmp_path, body), CATALOG, STORES)
    assert "reminder_minutes" in str(exc.value)


def test_reminder_minutes_non_numeric_is_an_error(tmp_path):
    """A non-numeric value used to raise an uncaught ValueError instead."""
    body = BASE.replace("reminder_minutes: 30", "reminder_minutes: soon")
    with pytest.raises(ConfigError) as exc:
        load_config(write(tmp_path, body), CATALOG, STORES)
    assert "reminder_minutes" in str(exc.value)


def test_no_location_selector_is_allowed(tmp_path):
    body = BASE.replace("    cities: [Abu Dhabi]\n", "")
    config = load_config(write(tmp_path, body), CATALOG, STORES)
    assert config.watches[0].cities == ()
