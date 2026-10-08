class Version:
    pass


def parse(s: str) -> Version:
    raise NotImplementedError


def compare(a: str, b: str) -> int:
    raise NotImplementedError
