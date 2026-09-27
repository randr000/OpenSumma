from decimal import Decimal

import pytest

from opensumma.money import ensure_money, round_money


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("10"), Decimal("10.00")),
        (Decimal("0.005"), Decimal("0.01")),
        (Decimal("0.004"), Decimal("0.00")),
        (Decimal("2.675"), Decimal("2.68")),  # the float 2.675 would round to 2.67
        (Decimal("-0.005"), Decimal("-0.01")),
        ("33.335", Decimal("33.34")),
        (7, Decimal("7.00")),
    ],
)
def test_round_money_rounds_half_up_to_cents(
    value: Decimal | int | str, expected: Decimal
) -> None:
    result = round_money(value)
    assert result == expected
    assert result.as_tuple().exponent == -2


def test_round_money_rejects_floats() -> None:
    with pytest.raises(TypeError):
        round_money(0.1)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", ["NaN", "Infinity"])
def test_round_money_rejects_non_finite_amounts(value: str) -> None:
    with pytest.raises(ValueError):
        round_money(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("10"), Decimal("10.00")),
        (Decimal("10.5"), Decimal("10.50")),
        (Decimal("-0.01"), Decimal("-0.01")),
        (Decimal("0.00"), Decimal("0.00")),
    ],
)
def test_ensure_money_accepts_exact_cents_without_changing_them(
    value: Decimal, expected: Decimal
) -> None:
    result = ensure_money(value)
    assert result == expected
    assert result.as_tuple().exponent == -2


@pytest.mark.parametrize(
    "value", [Decimal("0.005"), Decimal("NaN"), Decimal("Infinity"), Decimal("1E+30")]
)
def test_ensure_money_never_rounds_and_rejects_the_unrecordable(value: Decimal) -> None:
    with pytest.raises(ValueError):
        ensure_money(value)


@pytest.mark.parametrize("value", [0.1, 10, "10.00", None])
def test_ensure_money_accepts_only_decimals(value: object) -> None:
    with pytest.raises(TypeError):
        ensure_money(value)
