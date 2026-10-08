# semver

Implement Semantic Versioning 2.0.0 parsing and precedence in `solution.py`:

- `parse(s: str) -> Version` where `Version` has attributes `major`, `minor`, `patch` (ints), `prerelease` (tuple of identifiers, each an `int` if numeric else `str`; empty tuple if none) and `build` (tuple of `str`; empty if none). Raise `ValueError` for invalid versions, including leading zeros in numeric parts (`"01.2.3"`, `"1.2.3-01"`), missing parts (`"1.2"`), empty identifiers (`"1.2.3-"`, `"1.2.3-a..b"`), and characters outside `[0-9A-Za-z-]` in identifiers.
- `compare(a: str, b: str) -> int` returning -1, 0 or 1 by SemVer precedence. Build metadata is ignored; a pre-release is lower than the release; pre-release identifiers compare numerically if both numeric, lexically (ASCII) if both alphanumeric, numeric < alphanumeric, and a shorter set of identifiers is lower if all preceding ones are equal.
- `str(parse(s)) == s` for valid `s`.
