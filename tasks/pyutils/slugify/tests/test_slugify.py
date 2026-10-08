from solution import slugify


def test_basic():
    assert slugify("Hello World") == "hello-world"


def test_accents():
    assert slugify("Crème Brûlée") == "creme-brulee"


def test_punctuation_runs():
    assert slugify("  Hello,   World!!  How's it going?  ") == "hello-world-how-s-it-going"


def test_drop_non_latin():
    assert slugify("Hello 世界 World") == "hello-world"


def test_empty():
    assert slugify("!!! ???") == ""
    assert slugify("") == ""


def test_digits():
    assert slugify("Python 3.12 Release") == "python-3-12-release"


def test_max_length_word_boundary():
    assert slugify("the quick brown fox", max_length=12) == "the-quick"


def test_max_length_exact_boundary():
    assert slugify("the quick brown fox", max_length=9) == "the-quick"


def test_max_length_hard_cut():
    assert slugify("supercalifragilistic word", max_length=5) == "super"


def test_max_length_no_trailing_dash():
    assert slugify("ab cd", max_length=3) == "ab"
