from applewatch.catalog import load_catalog, select_skus

TARGET = "MJXC4AH/A"


def test_catalog_is_a_complete_cross_product():
    """Guards the committed data file, not just the generator."""
    catalog = load_catalog()
    assert len(catalog) == 32
    assert len({(s.screen, s.capacity, s.color) for s in catalog.values()}) == 32


def test_target_sku_is_burgundy_512_pro_max():
    sku = load_catalog()[TARGET]
    assert (sku.model, sku.capacity, sku.color) == (
        "iphone18promax",
        "512gb",
        "burgundy",
    )


def test_one_tb_burgundy_pro_max_is_present():
    """MJXG4AH/A was silently dropped by the first extraction attempt."""
    assert load_catalog()["MJXG4AH/A"].capacity == "1tb"


def test_empty_filters_select_everything():
    catalog = load_catalog()
    assert len(select_skus(catalog, None, (), ())) == 32


def test_full_filter_selects_exactly_one():
    catalog = load_catalog()
    selected = select_skus(catalog, "iphone18promax", ("512gb",), ("burgundy",))
    assert [s.part_number for s in selected] == [TARGET]


def test_list_values_within_a_field_are_ored():
    catalog = load_catalog()
    selected = select_skus(
        catalog, "iphone18promax", ("512gb",), ("burgundy", "black")
    )
    assert len(selected) == 2


def test_model_narrows_to_one_family():
    catalog = load_catalog()
    assert len(select_skus(catalog, "iphone18promax", (), ())) == 16
