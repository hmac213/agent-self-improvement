import pytest

from solution import from_roman, to_roman


@pytest.mark.parametrize("n,s", [(1, "I"), (4, "IV"), (9, "IX"), (14, "XIV"), (40, "XL"), (90, "XC"),
                                 (400, "CD"), (1994, "MCMXCIV"), (2024, "MMXXIV"), (3999, "MMMCMXCIX")])
def test_to_roman(n, s):
    assert to_roman(n) == s


@pytest.mark.parametrize("bad", [0, 4000, -1, 2.5, True, "10"])
def test_to_roman_invalid(bad):
    with pytest.raises(ValueError):
        to_roman(bad)


def test_round_trip():
    for n in range(1, 4000):
        assert from_roman(to_roman(n)) == n


@pytest.mark.parametrize("bad", ["", "IIII", "IC", "VX", "mcm", "MMMM", "IIV", "XM", "ABC", "VV"])
def test_from_roman_invalid(bad):
    with pytest.raises(ValueError):
        from_roman(bad)
