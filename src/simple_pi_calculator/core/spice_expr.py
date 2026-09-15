"""SPICE number and expression parser (DESIGN.md §4.5).

* :func:`parse_spice_number` — number with optional scale suffix and ignored trailing unit letters.
* :func:`evaluate_expression` — own recursive-descent evaluator for ``.PARAM`` / ``{…}`` / ``'…'``
  expressions. Python ``eval`` is never used; only the grammar of §4.5 is accepted.

Grammar (§4.5)::

    expr   = term { ("+" | "-") term } ;
    term   = unary { ("*" | "/") unary } ;
    unary  = [ "+" | "-" ] power ;
    power  = atom [ ("**" | "^") unary ] ;           (right associative)
    atom   = number | NAME | NAME "(" expr { "," expr } ")" | "(" expr ")" ;
"""

from __future__ import annotations

import math
import re
from typing import Callable, Mapping

__all__ = [
    "SpiceExprError",
    "parse_spice_number",
    "evaluate_expression",
    "FUNCTIONS",
    "CONSTANTS",
]


class SpiceExprError(ValueError):
    """Invalid expression, unknown name/function, division by zero, or non-finite result."""


# Mantissa + optional exponent; the exponent is parsed before the suffix (`1e3k` → 1e6).
_NUMBER_RE = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?", re.IGNORECASE)
_UNSIGNED_NUMBER_RE = re.compile(r"(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?", re.IGNORECASE)
_LETTERS_RE = re.compile(r"[a-zµμ]*", re.IGNORECASE)

_SINGLE_SUFFIX = {
    "t": 1e12,
    "g": 1e9,
    "k": 1e3,
    "m": 1e-3,
    "u": 1e-6,
    "µ": 1e-6,  # U+00B5 micro sign
    "μ": 1e-6,  # U+03BC Greek small mu
    "n": 1e-9,
    "p": 1e-12,
    "f": 1e-15,
}


def _suffix_multiplier(letters: str) -> float:
    """Multiplier of a letter run following a number (longest suffix first; rest ignored)."""
    s = letters.lower()
    if s.startswith("meg"):
        return 1e6
    if s.startswith("mil"):
        return 25.4e-6
    if s:
        return _SINGLE_SUFFIX.get(s[0], 1.0)
    return 1.0


def parse_spice_number(text: str) -> float:
    """Parse a SPICE number such as ``10uF``, ``1MEG``, ``3mil``, ``2.2nH``, ``1e-9``.

    Raises ``ValueError`` when the text does not start with a number or has non-letter trailing text.
    """
    s = text.strip()
    m = _NUMBER_RE.match(s)
    if not m:
        raise ValueError(f"not a SPICE number: {text!r}")
    rest = s[m.end():]
    lm = _LETTERS_RE.fullmatch(rest)
    if lm is None:
        raise ValueError(f"not a SPICE number: {text!r}")
    value = float(m.group(0)) * _suffix_multiplier(rest)
    if not math.isfinite(value):
        raise ValueError(f"SPICE number out of range: {text!r}")
    return value


# ------------------------------------------------------------------------------------------------
# Expressions
# ------------------------------------------------------------------------------------------------


def _fn_sqrt(x: float) -> float:
    if x < 0:
        raise SpiceExprError(f"sqrt of negative number {x!r}")
    return math.sqrt(x)


def _fn_log(x: float) -> float:
    if x <= 0:
        raise SpiceExprError(f"log of non-positive number {x!r}")
    return math.log(x)


def _fn_log10(x: float) -> float:
    if x <= 0:
        raise SpiceExprError(f"log10 of non-positive number {x!r}")
    return math.log10(x)


def _pow(x: float, y: float) -> float:
    if x == 0.0 and y < 0:
        raise SpiceExprError("division by zero (0 raised to a negative power)")
    try:
        return math.pow(x, y)
    except (ValueError, OverflowError) as exc:
        raise SpiceExprError(f"invalid power {x!r}^{y!r}") from exc


def _fn_exp(x: float) -> float:
    try:
        return math.exp(x)
    except OverflowError as exc:
        raise SpiceExprError(f"exp overflow for {x!r}") from exc


FUNCTIONS: dict[str, tuple[int, Callable[..., float]]] = {
    "sqrt": (1, _fn_sqrt),
    "abs": (1, abs),
    "exp": (1, _fn_exp),
    "log": (1, _fn_log),
    "log10": (1, _fn_log10),
    "pow": (2, _pow),
    "min": (2, min),
    "max": (2, max),
}

CONSTANTS: dict[str, float] = {"pi": math.pi}

_TOKEN_NAME_RE = re.compile(r"[a-z_][a-z0-9_]*", re.IGNORECASE)


def _tokenize(expr: str) -> list[tuple[str, object]]:
    tokens: list[tuple[str, object]] = []
    i = 0
    n = len(expr)
    while i < n:
        ch = expr[i]
        if ch.isspace():
            i += 1
            continue
        if ch.isdigit() or (ch == "." and i + 1 < n and expr[i + 1].isdigit()):
            m = _UNSIGNED_NUMBER_RE.match(expr, i)
            assert m is not None
            j = m.end()
            lm = _LETTERS_RE.match(expr, j)
            k = lm.end() if lm else j
            value = float(m.group(0)) * _suffix_multiplier(expr[j:k])
            tokens.append(("num", value))
            i = k
            continue
        m = _TOKEN_NAME_RE.match(expr, i)
        if m:
            tokens.append(("name", m.group(0).lower()))
            i = m.end()
            continue
        if expr.startswith("**", i):
            tokens.append(("op", "^"))
            i += 2
            continue
        if ch in "+-*/^(),":
            tokens.append(("op", ch))
            i += 1
            continue
        raise SpiceExprError(f"unexpected character {ch!r} in expression {expr!r}")
    tokens.append(("end", None))
    return tokens


class _Parser:
    def __init__(self, expr: str, env: Mapping[str, float]):
        self.expr = expr
        self.env = env
        self.tokens = _tokenize(expr)
        self.pos = 0

    def peek(self) -> tuple[str, object]:
        return self.tokens[self.pos]

    def take(self) -> tuple[str, object]:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def expect_op(self, op: str) -> None:
        kind, val = self.take()
        if kind != "op" or val != op:
            raise SpiceExprError(f"expected {op!r} in expression {self.expr!r}")

    def parse(self) -> float:
        value = self.expr_()
        if self.peek()[0] != "end":
            raise SpiceExprError(f"unexpected token {self.peek()[1]!r} in expression {self.expr!r}")
        return value

    def expr_(self) -> float:
        value = self.term()
        while True:
            kind, val = self.peek()
            if kind == "op" and val in ("+", "-"):
                self.take()
                rhs = self.term()
                value = value + rhs if val == "+" else value - rhs
            else:
                return value

    def term(self) -> float:
        value = self.unary()
        while True:
            kind, val = self.peek()
            if kind == "op" and val in ("*", "/"):
                self.take()
                rhs = self.unary()
                if val == "*":
                    value = value * rhs
                else:
                    if rhs == 0.0:
                        raise SpiceExprError(f"division by zero in expression {self.expr!r}")
                    value = value / rhs
            else:
                return value

    def unary(self) -> float:
        kind, val = self.peek()
        if kind == "op" and val in ("+", "-"):
            self.take()
            operand = self.power()
            return -operand if val == "-" else operand
        return self.power()

    def power(self) -> float:
        base = self.atom()
        kind, val = self.peek()
        if kind == "op" and val == "^":
            self.take()
            exponent = self.unary()
            return _pow(base, exponent)
        return base

    def atom(self) -> float:
        kind, val = self.take()
        if kind == "num":
            return float(val)  # type: ignore[arg-type]
        if kind == "op" and val == "(":
            value = self.expr_()
            self.expect_op(")")
            return value
        if kind == "name":
            name = str(val)
            nk, nv = self.peek()
            if nk == "op" and nv == "(":
                self.take()
                if name not in FUNCTIONS:
                    raise SpiceExprError(f"unknown function {name!r} in expression {self.expr!r}")
                nargs, fn = FUNCTIONS[name]
                args = [self.expr_()]
                while self.peek() == ("op", ","):
                    self.take()
                    args.append(self.expr_())
                self.expect_op(")")
                if len(args) != nargs:
                    raise SpiceExprError(
                        f"function {name}() takes {nargs} argument(s), got {len(args)} "
                        f"in expression {self.expr!r}"
                    )
                return float(fn(*args))
            if name in self.env:
                return float(self.env[name])
            if name in CONSTANTS:
                return CONSTANTS[name]
            raise SpiceExprError(f"unknown name {name!r} in expression {self.expr!r}")
        if kind == "end":
            raise SpiceExprError(f"unexpected end of expression {self.expr!r}")
        raise SpiceExprError(f"unexpected token {val!r} in expression {self.expr!r}")


def evaluate_expression(expr: str, env: Mapping[str, float]) -> float:
    """Evaluate an expression; names are case-insensitive (``env`` keys must be lower-case).

    Surrounding ``{}`` or ``''`` delimiters are stripped. Raises :class:`SpiceExprError`.
    """
    text = expr.strip()
    if len(text) >= 2 and ((text[0] == "{" and text[-1] == "}") or (text[0] == "'" and text[-1] == "'")):
        text = text[1:-1].strip()
    if not text:
        raise SpiceExprError("empty expression")
    try:
        value = _Parser(text, env).parse()
    except SpiceExprError:
        raise
    except (OverflowError, ValueError, RecursionError) as exc:
        raise SpiceExprError(f"cannot evaluate expression {expr!r}: {exc}") from exc
    if not math.isfinite(value):
        raise SpiceExprError(f"non-finite result of expression {expr!r}")
    return value
