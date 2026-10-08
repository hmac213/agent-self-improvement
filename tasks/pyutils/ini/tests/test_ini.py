import pytest

from solution import parse


def test_sections_and_separators():
    text = "[server]\nhost = example.com\nport: 8080\n\n[client]\nName=alice\n"
    assert parse(text) == {"server": {"host": "example.com", "port": "8080"}, "client": {"name": "alice"}}


def test_default_section_and_comments():
    text = "; comment\ntimeout = 5\n# another\n[a]\nx = 1 ; not a comment\n"
    assert parse(text) == {"DEFAULT": {"timeout": "5"}, "a": {"x": "1 ; not a comment"}}


def test_first_separator_wins():
    assert parse("[s]\nurl = http://x:80/a=b\n") == {"s": {"url": "http://x:80/a=b"}}


def test_merge_and_overwrite():
    text = "[a]\nx = 1\n[b]\ny = 2\n[ a ]\nx = 3\nz = 4\n"
    assert parse(text) == {"a": {"x": "3", "z": "4"}, "b": {"y": "2"}}


def test_continuation():
    text = "[a]\nmsg = hello\n   world\n\tagain\n\nother = 1\n"
    assert parse(text)["a"] == {"msg": "hello\nworld\nagain", "other": "1"}


def test_interpolation():
    text = "root = /srv\n[app]\ndir = ${root}/app\nlog = ${dir}/log\n[web]\nstatic = ${app:dir}/static\n"
    d = parse(text)
    assert d["app"]["log"] == "/srv/app/log"
    assert d["web"]["static"] == "/srv/app/static"


def test_interpolation_errors():
    with pytest.raises(ValueError):
        parse("[a]\nx = ${missing}\n")
    with pytest.raises(ValueError):
        parse("[a]\nx = ${y}\ny = ${x}\n")
    with pytest.raises(ValueError):
        parse("[a]\nx = ${b:y}\n")


def test_malformed_lines_report_line_number():
    with pytest.raises(ValueError, match="3"):
        parse("[a]\nx = 1\njust some words\n")
    with pytest.raises(ValueError):
        parse("[a\nx = 1\n")
    with pytest.raises(ValueError):
        parse("[a]\n = 1\n")
