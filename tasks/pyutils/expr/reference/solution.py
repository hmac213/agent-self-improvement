import math
import re

_TOKEN = re.compile(r"\s*(?:(\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)|([A-Za-z_]\w*)|(\*\*|[-+*/%(),]))")
_FUNCS = {"abs": (abs, 1, 1), "sqrt": (math.sqrt, 1, 1), "min": (min, 1, None), "max": (max, 1, None)}


def _tokenize(s):
    pos, out = 0, []
    s = s.rstrip()
    while pos < len(s):
        m = _TOKEN.match(s, pos)
        if not m:
            raise ValueError(f"bad character at {pos}")
        num, name, op = m.groups()
        out.append(("num", float(num)) if num else ("name", name) if name else ("op", op))
        pos = m.end()
    return out


class _Parser:
    def __init__(self, tokens, variables):
        self.t, self.i, self.vars = tokens, 0, variables

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def take(self, op=None):
        tok = self.peek()
        if tok[0] is None or (op is not None and tok != ("op", op)):
            raise ValueError(f"expected {op or 'token'}")
        self.i += 1
        return tok

    def expr(self):
        v = self.term()
        while self.peek() in (("op", "+"), ("op", "-")):
            op = self.take()[1]
            r = self.term()
            v = v + r if op == "+" else v - r
        return v

    def term(self):
        v = self.unary()
        while self.peek() in (("op", "*"), ("op", "/"), ("op", "%")):
            op = self.take()[1]
            r = self.unary()
            if op == "*":
                v *= r
            elif r == 0:
                raise ValueError("division by zero")
            else:
                v = v / r if op == "/" else v % r
        return v

    def unary(self):
        if self.peek() in (("op", "+"), ("op", "-")):
            op = self.take()[1]
            v = self.unary()
            return -v if op == "-" else v
        return self.power()

    def power(self):
        base = self.atom()
        if self.peek() == ("op", "**"):
            self.take()
            return base ** self.unary()
        return base

    def atom(self):
        kind, val = self.take()
        if kind == "num":
            return val
        if kind == "op" and val == "(":
            v = self.expr()
            self.take(")")
            return v
        if kind == "name":
            if self.peek() == ("op", "("):
                if val not in _FUNCS:
                    raise ValueError(f"unknown function {val}")
                fn, lo, hi = _FUNCS[val]
                self.take("(")
                args = []
                if self.peek() != ("op", ")"):
                    args.append(self.expr())
                    while self.peek() == ("op", ","):
                        self.take()
                        args.append(self.expr())
                self.take(")")
                if len(args) < lo or (hi is not None and len(args) > hi):
                    raise ValueError(f"wrong number of arguments to {val}")
                return float(fn(*args) if hi == 1 else fn(args))
            if val not in self.vars:
                raise ValueError(f"unknown name {val}")
            return float(self.vars[val])
        raise ValueError(f"unexpected {val}")


def evaluate(expression, variables=None):
    p = _Parser(_tokenize(expression), variables or {})
    v = p.expr()
    if p.i != len(p.t):
        raise ValueError("trailing input")
    return float(v)
