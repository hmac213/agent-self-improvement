import re
from dataclasses import dataclass

_NUM = r"0|[1-9]\d*"
_ID = r"[0-9A-Za-z-]+"
_RE = re.compile(rf"^({_NUM})\.({_NUM})\.({_NUM})(?:-({_ID}(?:\.{_ID})*))?(?:\+({_ID}(?:\.{_ID})*))?$")


@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    prerelease: tuple = ()
    build: tuple = ()

    def __str__(self):
        s = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            s += "-" + ".".join(str(p) for p in self.prerelease)
        if self.build:
            s += "+" + ".".join(self.build)
        return s


def parse(s: str) -> Version:
    m = _RE.match(s) if isinstance(s, str) else None
    if not m:
        raise ValueError(s)
    pre = ()
    if m.group(4):
        ids = []
        for part in m.group(4).split("."):
            if part.isdigit():
                if len(part) > 1 and part[0] == "0":
                    raise ValueError(s)
                ids.append(int(part))
            else:
                ids.append(part)
        pre = tuple(ids)
    build = tuple(m.group(5).split(".")) if m.group(5) else ()
    return Version(int(m.group(1)), int(m.group(2)), int(m.group(3)), pre, build)


def _cmp(x, y):
    return (x > y) - (x < y)


def compare(a: str, b: str) -> int:
    va, vb = parse(a), parse(b)
    c = _cmp((va.major, va.minor, va.patch), (vb.major, vb.minor, vb.patch))
    if c:
        return c
    pa, pb = va.prerelease, vb.prerelease
    if not pa or not pb:
        return _cmp(not pa, not pb)
    for x, y in zip(pa, pb):
        if isinstance(x, int) and isinstance(y, int):
            c = _cmp(x, y)
        elif isinstance(x, int):
            c = -1
        elif isinstance(y, int):
            c = 1
        else:
            c = _cmp(x, y)
        if c:
            return c
    return _cmp(len(pa), len(pb))
