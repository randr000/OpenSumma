from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Any

import pytest

from opensumma.objects.data import MAX_INTEGER, MIN_INTEGER, ensure_business_data


def test_json_values_are_accepted_and_copied() -> None:
    data: dict[str, Any] = {
        "invoice_number": "INV-1001",
        "amount": "1200.00",
        "quantity": 3,
        "paid": False,
        "note": None,
        "lines": [{"description": "Hosting", "amount": "1200.00"}],
        "tags": ["aws", "monthly"],
    }
    copied = ensure_business_data(data)

    assert copied == data
    assert copied is not data
    assert copied["lines"] is not data["lines"]
    assert copied["lines"][0] is not data["lines"][0]


def test_later_changes_to_the_callers_data_do_not_reach_the_copy() -> None:
    data = {"lines": [{"amount": "10.00"}]}
    copied = ensure_business_data(data)
    data["lines"][0]["amount"] = "99.00"

    assert copied == {"lines": [{"amount": "10.00"}]}


def test_any_mapping_is_accepted_and_becomes_a_dict() -> None:
    copied = ensure_business_data(MappingProxyType({"a": MappingProxyType({"b": 1})}))
    assert copied == {"a": {"b": 1}}
    assert type(copied) is dict and type(copied["a"]) is dict


@pytest.mark.parametrize(
    "value", [0.1, 1.0, float("nan"), float("inf"), Decimal("1200.00")]
)
def test_floats_and_decimals_are_refused_with_advice(value: object) -> None:
    with pytest.raises(TypeError, match="write amounts as strings"):
        ensure_business_data({"amount": value})


def test_the_path_to_a_bad_value_is_reported() -> None:
    with pytest.raises(TypeError, match=r"data\.lines\[1\]\.amount is a float"):
        ensure_business_data({"lines": [{"amount": "1.00"}, {"amount": 2.5}]})


@pytest.mark.parametrize(
    "value",
    [(1, 2), {1, 2}, date(2026, 3, 15), b"bytes", object()],
    ids=["tuple", "set", "date", "bytes", "object"],
)
def test_values_json_would_not_return_exactly_are_refused(value: object) -> None:
    with pytest.raises(TypeError, match="JSON cannot store exactly"):
        ensure_business_data({"value": value})


def test_keys_must_be_strings() -> None:
    with pytest.raises(TypeError, match="JSON keys are strings"):
        ensure_business_data({"nested": {1: "one"}})


@pytest.mark.parametrize("value", [[], "text", None, 3])
def test_business_data_is_a_json_object(value: object) -> None:
    with pytest.raises(TypeError, match="must be a JSON object"):
        ensure_business_data(value)


def test_integers_must_fit_in_64_bits() -> None:
    assert ensure_business_data({"n": MAX_INTEGER, "m": MIN_INTEGER}) == {
        "n": MAX_INTEGER,
        "m": MIN_INTEGER,
    }
    with pytest.raises(ValueError, match="does not fit in 64 bits"):
        ensure_business_data({"n": MAX_INTEGER + 1})
    with pytest.raises(ValueError, match="does not fit in 64 bits"):
        ensure_business_data({"n": MIN_INTEGER - 1})


def test_booleans_stay_booleans() -> None:
    copied = ensure_business_data({"flag": True})
    assert copied["flag"] is True
