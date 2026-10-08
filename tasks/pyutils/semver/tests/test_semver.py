import pytest

from solution import compare, parse


def test_parse_fields():
    v = parse("1.2.3-alpha.1+build.5")
    assert (v.major, v.minor, v.patch) == (1, 2, 3)
    assert tuple(v.prerelease) == ("alpha", 1)
    assert tuple(v.build) == ("build", "5")


def test_parse_plain():
    v = parse("10.20.30")
    assert (v.major, v.minor, v.patch) == (10, 20, 30)
    assert tuple(v.prerelease) == () and tuple(v.build) == ()


@pytest.mark.parametrize("s", ["1.2.3", "1.0.0-alpha", "1.0.0-0.3.7", "1.0.0-x.7.z.92", "1.0.0+20130313144700",
                               "1.0.0-beta+exp.sha.5114f85", "1.0.0-x-y-z.--", "0.0.0"])
def test_round_trip(s):
    assert str(parse(s)) == s


@pytest.mark.parametrize("s", ["01.2.3", "1.02.3", "1.2", "1.2.3-", "1.2.3-a..b", "1.2.3-01", "1.2.3+", "v1.2.3",
                               "1.2.3-ä", "1.2.3.4", ""])
def test_invalid(s):
    with pytest.raises(ValueError):
        parse(s)


def test_precedence_chain():
    chain = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta", "1.0.0-beta.2",
             "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0", "1.0.1", "1.1.0", "2.0.0"]
    for a, b in zip(chain, chain[1:]):
        assert compare(a, b) == -1, (a, b)
        assert compare(b, a) == 1, (b, a)


def test_build_ignored():
    assert compare("1.0.0+a", "1.0.0+b") == 0
    assert compare("1.0.0-rc.1+x", "1.0.0-rc.1") == 0


def test_numeric_not_lexical():
    assert compare("1.10.0", "1.9.0") == 1
    assert compare("1.0.0-2", "1.0.0-10") == -1
