from applewatch.stores import load_stores


def test_loads_all_five_uae_stores():
    assert len(load_stores()) == 5


def test_al_jimi_is_al_ain_city_but_abu_dhabi_emirate():
    """The trap this project exists to avoid. Apple returns city 'Al Ain',
    so a city filter for Abu Dhabi must not pick it up, but an emirate
    filter must. See spec 3.2."""
    al_jimi = load_stores()["R785"]
    assert al_jimi.city == "Al Ain"
    assert al_jimi.emirate == "Abu Dhabi"


def test_abu_dhabi_city_has_exactly_two_stores():
    stores = load_stores().values()
    assert {s.number for s in stores if s.city == "Abu Dhabi"} == {"R706", "R595"}


def test_abu_dhabi_emirate_has_exactly_three_stores():
    stores = load_stores().values()
    assert {s.number for s in stores if s.emirate == "Abu Dhabi"} == {
        "R706",
        "R595",
        "R785",
    }
