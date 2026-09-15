"""SPICE ``.mod`` subcircuit parser and flattener (DESIGN.md §2.7.1, §3.7, §4.5).

Produces a flat :class:`Netlist` of R, L, C and K elements between two external pins, ready for
:func:`simple_pi_calculator.core.mna.impedance_two_terminal`.

Error handling: every error is added to the ``IssueCollector`` and then raised as
:class:`~simple_pi_calculator.errors.InputError` carrying that single issue. Warnings are only added
to the collector.

K elements: a multi-inductor coupling ``K1 L1 L2 L3 k`` is emitted as one ``Element(kind="K")`` per
inductor pair (all with the same name and coefficient); ``coupled`` holds the two flattened
inductor names.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Literal

from ..errors import InputError, IssueCollector
from .spice_expr import SpiceExprError, evaluate_expression

__all__ = [
    "Element",
    "SubcktDef",
    "Netlist",
    "parse_spice_file",
    "parse_spice_text",
    "read_spice_source",
    "MAX_DEPTH",
]

MAX_DEPTH = 20

_UNSUPPORTED_DOT = {".include", ".inc", ".lib", ".func"}
_GLOBAL_GND = {"0", "gnd"}


@dataclass
class Element:
    """One flattened element (§5.2)."""

    kind: Literal["R", "L", "C", "K"]
    name: str
    nodes: tuple[str, ...]          # R/L/C: 2 nodes; K: ()
    value: float                    # R ohm, L H, C F, K coefficient
    coupled: tuple[str, ...] = ()   # K: the two coupled inductor names
    line: int = 0


@dataclass
class SubcktDef:
    """A collected ``.SUBCKT`` definition (§5.2)."""

    name: str
    pins: list[str]
    default_params: list[tuple[str, str]]     # unevaluated
    body: list[tuple[int, list[str]]]         # (line_no, tokens); assigns as "name=value" tokens
    line: int = 0


@dataclass
class Netlist:
    """Flattened two-terminal circuit (§5.2)."""

    elements: list[Element]
    pin1: str
    pin2: str
    subckt_name: str
    source_path: str
    params: dict[str, float] = field(default_factory=dict)   # final env of the top subckt


# ------------------------------------------------------------------------------------------------
# Lexical preprocessing
# ------------------------------------------------------------------------------------------------


def read_spice_source(path: str | os.PathLike) -> str:
    """Read a model file as UTF-8 (BOM tolerated); fall back to Latin-1 on decode errors (§4.5)."""
    raw = open(path, "rb").read()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _strip_inline_comment(line: str) -> str:
    """Remove ``;`` comments (outside groups) and ``$`` comments preceded by whitespace/line start."""
    depth = 0
    quote = False
    for i, ch in enumerate(line):
        if quote:
            if ch == "'":
                quote = False
            continue
        if depth:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            continue
        if ch == "'":
            quote = True
        elif ch == "{":
            depth = 1
        elif ch == ";":
            return line[:i]
        elif ch == "$" and (i == 0 or line[i - 1].isspace()):
            return line[:i]
    return line


def _mask_groups(text: str) -> str:
    """Same-length copy of ``text`` with the contents of ``{…}`` and ``'…'`` replaced by ``#``."""
    out = []
    depth = 0
    quote = False
    for ch in text:
        if quote:
            if ch == "'":
                quote = False
                out.append(ch)
            else:
                out.append("#")
        elif depth:
            if ch == "{":
                depth += 1
                out.append("#")
            elif ch == "}":
                depth -= 1
                out.append("}" if depth == 0 else "#")
            else:
                out.append("#")
        else:
            if ch == "'":
                quote = True
            elif ch == "{":
                depth = 1
            out.append(ch)
    return "".join(out)


def _split_head(text: str) -> list[str]:
    """Whitespace tokens, ``(`` ``)`` ``,`` as separators, ``{…}``/``'…'`` groups kept intact."""
    tokens: list[str] = []
    cur: list[str] = []
    depth = 0
    quote = False
    for ch in text:
        if quote:
            cur.append(ch)
            if ch == "'":
                quote = False
            continue
        if depth:
            cur.append(ch)
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            continue
        if ch.isspace() or ch in "(),":
            if cur:
                tokens.append("".join(cur))
                cur = []
            continue
        cur.append(ch)
        if ch == "'":
            quote = True
        elif ch == "{":
            depth = 1
    if cur:
        tokens.append("".join(cur))
    return tokens


_PARAMS_KW_RE = re.compile(r"(?:^|(?<=\s))params\s*:", re.IGNORECASE)
_ASSIGN_RE = re.compile(r"(?:^|(?<=[\s(,]))([a-z_][a-z0-9_]*)\s*=(?!=)", re.IGNORECASE)


class _SyntaxProblem(Exception):
    pass


def _tokenize_logical_line(text: str) -> list[str]:
    """Tokens of one logical line: head tokens, then ``params:`` (if present), then ``name=value``."""
    masked = _mask_groups(text)
    kw = _PARAMS_KW_RE.search(masked)
    am = _ASSIGN_RE.search(masked)
    split = len(text)
    has_kw = False
    if kw and (am is None or kw.start() <= am.start()):
        split = kw.start()
        has_kw = True
        tail_start = kw.end()
    elif am:
        split = am.start()
        tail_start = am.start()
    else:
        tail_start = len(text)
    head = _split_head(text[:split])
    tokens = list(head)
    if has_kw:
        tokens.append("params:")
    tail = text[tail_start:]
    if tail.strip():
        tmask = masked[tail_start:]
        matches = list(_ASSIGN_RE.finditer(tmask))
        if not matches or tail[: matches[0].start()].strip():
            raise _SyntaxProblem(f"expected name=value, got {tail.strip()!r}")
        for idx, mt in enumerate(matches):
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(tail)
            value = tail[mt.end():end].strip()
            if not value:
                raise _SyntaxProblem(f"missing value for parameter {mt.group(1)!r}")
            tokens.append(f"{mt.group(1).lower()}={value}")
    return tokens


def _split_assigns(tokens: list[str]) -> tuple[list[str], list[tuple[str, str]], bool]:
    head: list[str] = []
    assigns: list[tuple[str, str]] = []
    has_kw = False
    for tok in tokens:
        if tok == "params:":
            has_kw = True
        elif _is_assign_token(tok):
            name, value = tok.split("=", 1)
            assigns.append((name, value))
        else:
            head.append(tok)
    return head, assigns, has_kw


_ASSIGN_TOKEN_RE = re.compile(r"^[a-z_][a-z0-9_]*=", re.IGNORECASE)


def _is_assign_token(tok: str) -> bool:
    return bool(_ASSIGN_TOKEN_RE.match(tok))


# ------------------------------------------------------------------------------------------------
# Parser
# ------------------------------------------------------------------------------------------------


class _SpiceReader:
    def __init__(self, text: str, issues: IssueCollector, source: str):
        self.issues = issues
        self.source = source
        self.line_text: dict[int, str] = {}
        self.subckts: dict[str, SubcktDef] = {}
        self.order: list[str] = []
        self.global_params: list[tuple[int, str, str]] = []
        self._warned_gnd: set[str] = set()
        self._lines = self._logical_lines(text)
        self._collect()

    # -- issues ---------------------------------------------------------------------------------
    def error(self, code: str, message: str, line: int | None = None) -> InputError:
        loc = f"line {line}" if line else None
        if line and line in self.line_text:
            message = f"{message} [line {line}: {self.line_text[line].strip()}]"
        issue = self.issues.error(code, message, self.source, loc)
        return InputError([issue])

    def warn(self, code: str, message: str, line: int | None = None) -> None:
        loc = f"line {line}" if line else None
        self.issues.warning(code, message, self.source, loc)

    # -- lexical --------------------------------------------------------------------------------
    def _logical_lines(self, text: str) -> list[tuple[int, str]]:
        logical: list[list] = []
        for no, raw in enumerate(text.splitlines(), start=1):
            stripped = raw.strip()
            if not stripped or stripped.startswith("*"):
                continue
            body = _strip_inline_comment(raw).strip()
            if body.startswith("+"):
                if not logical:
                    self.line_text[no] = raw
                    raise self.error("E_SPICE_SYNTAX", "continuation line without a preceding line", no)
                logical[-1][1] += " " + body[1:].strip()
                self.line_text[logical[-1][0]] += "\n" + raw
                continue
            if not body:
                continue
            logical.append([no, body])
            self.line_text[no] = raw
        return [(no, body) for no, body in logical]

    def tokens(self, no: int, body: str) -> list[str]:
        try:
            return _tokenize_logical_line(body.lower())
        except _SyntaxProblem as exc:
            raise self.error("E_SPICE_SYNTAX", str(exc), no) from None

    # -- first pass -----------------------------------------------------------------------------
    def _collect(self) -> None:
        stack: list[SubcktDef] = []
        for no, body in self._lines:
            toks = self.tokens(no, body)
            if not toks:
                continue
            first = toks[0]
            if first == ".subckt":
                head, assigns, _ = _split_assigns(toks)
                if len(head) < 3:
                    raise self.error("E_SPICE_SYNTAX", ".SUBCKT needs a name and at least one pin", no)
                name = head[1]
                if name in self.subckts:
                    raise self.error("E_SPICE_SYNTAX", f"duplicate .SUBCKT definition {name!r}", no)
                sd = SubcktDef(name=name, pins=head[2:], default_params=assigns, body=[], line=no)
                self.subckts[name] = sd
                self.order.append(name)
                stack.append(sd)
                continue
            if first == ".ends":
                if not stack:
                    raise self.error("E_SPICE_SYNTAX", ".ENDS without matching .SUBCKT", no)
                stack.pop()
                continue
            if stack:
                stack[-1].body.append((no, toks))
                continue
            # outside any subckt
            if first == ".param":
                head, assigns, _ = _split_assigns(toks)
                if len(head) != 1 or not assigns:
                    raise self.error("E_SPICE_SYNTAX", ".PARAM needs name=value assignments", no)
                for name, value in assigns:
                    self.global_params.append((no, name, value))
                continue
            if first == ".end":
                break
            if first in _UNSUPPORTED_DOT:
                raise self.error("E_SPICE_UNSUPPORTED", f"unsupported statement {first.upper()}", no)
            if first.startswith("."):
                self.warn("W_SPICE_IGNORED", f"statement {first.upper()} ignored", no)
                continue
            if first[0] in "rlckx":
                self.warn("W_SPICE_IGNORED", f"element {first.upper()} outside .SUBCKT ignored", no)
                continue
            raise self.error("E_SPICE_UNSUPPORTED", f"unsupported element {first.upper()}", no)
        if stack:
            raise self.error("E_SPICE_SYNTAX", f"missing .ENDS for .SUBCKT {stack[-1].name.upper()}",
                             stack[-1].line)

    # -- evaluation helpers ---------------------------------------------------------------------
    def eval(self, expr: str, env: dict[str, float], no: int) -> float:
        try:
            return evaluate_expression(expr, env)
        except SpiceExprError as exc:
            raise self.error("E_SPICE_EXPR", f"expression error: {exc}", no) from None

    def global_env(self) -> dict[str, float]:
        env: dict[str, float] = {}
        for no, name, value in self.global_params:
            env[name] = self.eval(value, env, no)
        return env

    @staticmethod
    def _instance_target(toks: list[str]) -> str | None:
        head, _, _ = _split_assigns(toks)
        return head[-1] if len(head) >= 2 else None

    def instantiated_names(self) -> dict[str, set[str]]:
        """subckt name → set of subckts instantiating it (excluding itself)."""
        users: dict[str, set[str]] = {}
        for sname in self.order:
            for _, toks in self.subckts[sname].body:
                if toks[0].startswith("x"):
                    target = self._instance_target(toks)
                    if target and target != sname:
                        users.setdefault(target, set()).add(sname)
        return users

    # -- top-level selection ----------------------------------------------------------------------
    def select_top(self, subckt: str | None) -> SubcktDef:
        if not self.subckts:
            raise self.error("E_SPICE_NO_SUBCKT", "no .SUBCKT definition found in file")
        if subckt:
            key = subckt.strip().lower()
            if key not in self.subckts:
                raise self.error("E_SPICE_UNKNOWN_SUBCKT", f"subcircuit {subckt!r} not found in file")
            top = self.subckts[key]
        else:
            users = self.instantiated_names()
            candidates = [n for n in self.order if n not in users]
            if not candidates:
                candidates = list(self.order)
            if len(candidates) == 1:
                top = self.subckts[candidates[0]]
            else:
                two_pin = [n for n in candidates if len(self.subckts[n].pins) == 2]
                if not two_pin:
                    first = self.subckts[candidates[0]]
                    raise self.error(
                        "E_SPICE_PIN_COUNT",
                        f"no top-level subcircuit with exactly 2 pins (candidates: "
                        f"{', '.join(c.upper() for c in candidates)})", first.line)
                top = self.subckts[two_pin[0]]
                self.warn("W_SPICE_MULTI_TOP",
                          f"several top-level subcircuits ({', '.join(c.upper() for c in candidates)}); "
                          f"using {top.name.upper()}", top.line)
        if len(top.pins) != 2:
            raise self.error("E_SPICE_PIN_COUNT",
                             f"subcircuit {top.name.upper()} has {len(top.pins)} pins, exactly 2 required",
                             top.line)
        if top.pins[0] == top.pins[1]:
            raise self.error("E_SPICE_PIN_COUNT",
                             f"subcircuit {top.name.upper()}: pin1 and pin2 are the same node", top.line)
        return top

    # -- flattening -------------------------------------------------------------------------------
    def flatten(self, top: SubcktDef) -> Netlist:
        genv = self.global_env()
        env = dict(genv)
        for name, value in top.default_params:
            env[name] = self.eval(value, env, top.line)
        pin1, pin2 = top.pins
        out: list[Element] = []
        pin_map = {p: p for p in top.pins}
        final_env = self._expand(top, "", pin_map, env, {n for n, _ in top.default_params},
                                 0, [top.name], out, pin2)
        return Netlist(elements=out, pin1=pin1, pin2=pin2, subckt_name=top.name,
                       source_path=self.source, params=final_env)

    def _expand(self, sd: SubcktDef, prefix: str, pin_map: dict[str, str], env: dict[str, float],
                protected: set[str], depth: int, stack: list[str], out: list[Element],
                top_pin2: str) -> dict[str, float]:
        # pass 1: local .PARAM in textual order
        for no, toks in sd.body:
            if toks[0] == ".param":
                head, assigns, _ = _split_assigns(toks)
                if len(head) != 1 or not assigns:
                    raise self.error("E_SPICE_SYNTAX", ".PARAM needs name=value assignments", no)
                for name, value in assigns:
                    if name in protected:
                        raise self.error("E_SPICE_PARAM_REDEF",
                                         f"parameter {name!r} redefined in .SUBCKT {sd.name.upper()} "
                                         "(already given as PARAMS: default/override)", no)
                    env[name] = self.eval(value, env, no)

        def node(n: str, no: int) -> str:
            if n in pin_map:
                return pin_map[n]
            if n in _GLOBAL_GND:
                key = f"{prefix}{n}"
                if key not in self._warned_gnd:
                    self._warned_gnd.add(key)
                    self.warn("W_SPICE_GLOBAL_GND",
                              f"global node {n!r} inside .SUBCKT {sd.name.upper()} merged with pin2 "
                              f"({top_pin2!r})", no)
                return top_pin2
            return prefix + n

        local_elems: list[Element] = []
        local_names: set[str] = set()
        inductors: dict[str, Element] = {}
        k_lines: list[tuple[int, list[str]]] = []

        for no, toks in sd.body:
            first = toks[0]
            if first == ".param":
                continue
            if first.startswith("."):
                if first in _UNSUPPORTED_DOT:
                    raise self.error("E_SPICE_UNSUPPORTED", f"unsupported statement {first.upper()}", no)
                if first == ".end":
                    continue
                self.warn("W_SPICE_IGNORED", f"statement {first.upper()} ignored", no)
                continue
            letter = first[0]
            if letter not in "rlckx":
                raise self.error("E_SPICE_UNSUPPORTED", f"unsupported element {first.upper()}", no)
            if first in local_names:
                raise self.error("E_SPICE_SYNTAX", f"duplicate element name {first.upper()}", no)
            local_names.add(first)
            if letter == "k":
                k_lines.append((no, toks))
                continue
            if letter == "x":
                self._instance(sd, no, toks, prefix, node, env, depth, stack, out, top_pin2)
                continue
            elem = self._rlc(no, toks, prefix, node, env)
            local_elems.append(elem)
            if elem.kind == "L":
                inductors[first] = elem

        out.extend(local_elems)
        for no, toks in k_lines:
            out.extend(self._coupling(no, toks, prefix, inductors, env))
        return env

    def _rlc(self, no, toks, prefix, node, env) -> Element:
        head, assigns, _ = _split_assigns(toks)
        name = head[0]
        kind = name[0].upper()
        if len(head) < 3:
            raise self.error("E_SPICE_SYNTAX", f"element {name.upper()} needs two nodes and a value", no)
        value_expr: str | None = head[3] if len(head) >= 4 else None
        if len(head) > 4:
            self.warn("W_SPICE_OPTION_IGNORED",
                      f"extra tokens {' '.join(head[4:])!r} on {name.upper()} ignored", no)
        multiplier = 1.0
        for key, val in assigns:
            if key == kind.lower() and value_expr is None:
                value_expr = val
            elif key == "m":
                multiplier = self.eval(val, env, no)
                if multiplier <= 0:
                    raise self.error("E_SPICE_VALUE", f"multiplier M must be > 0 on {name.upper()}", no)
            else:
                self.warn("W_SPICE_OPTION_IGNORED", f"option {key.upper()}= on {name.upper()} ignored", no)
        if value_expr is None:
            raise self.error("E_SPICE_SYNTAX", f"element {name.upper()} has no value", no)
        value = self.eval(value_expr, env, no)
        if kind == "R":
            if value < 0:
                self.warn("W_SPICE_NEGATIVE_R", f"negative resistance on {name.upper()}", no)
            value = value / multiplier
        elif kind == "L":
            if value < 0:
                raise self.error("E_SPICE_VALUE", f"negative inductance on {name.upper()}", no)
            value = value / multiplier
        else:
            if value < 0:
                raise self.error("E_SPICE_VALUE", f"negative capacitance on {name.upper()}", no)
            value = value * multiplier
        return Element(kind=kind, name=prefix + name, nodes=(node(head[1], no), node(head[2], no)),
                       value=value, line=no)

    def _coupling(self, no, toks, prefix, inductors, env) -> list[Element]:
        head, assigns, _ = _split_assigns(toks)
        name = head[0]
        value_expr = None
        names = head[1:]
        for key, val in assigns:
            if key == "k" and value_expr is None:
                value_expr = val
            else:
                self.warn("W_SPICE_OPTION_IGNORED", f"option {key.upper()}= on {name.upper()} ignored", no)
        if value_expr is None:
            if not names:
                raise self.error("E_SPICE_SYNTAX", f"coupling {name.upper()} has no coefficient", no)
            value_expr = names[-1]
            names = names[:-1]
        if len(names) < 2:
            raise self.error("E_SPICE_K", f"coupling {name.upper()} needs at least two inductors", no)
        if len(set(names)) != len(names):
            raise self.error("E_SPICE_K", f"coupling {name.upper()} references an inductor twice", no)
        for ln in names:
            if ln not in inductors:
                raise self.error("E_SPICE_K",
                                 f"coupling {name.upper()} references unknown inductor {ln.upper()} "
                                 "(must be an L element in the same subcircuit)", no)
        k = self.eval(value_expr, env, no)
        if abs(k) > 1.0:
            raise self.error("E_SPICE_K", f"coupling coefficient |k| = {abs(k):g} > 1 on {name.upper()}", no)
        if abs(k) == 1.0:
            self.warn("W_K_UNITY", f"coupling coefficient |k| = 1 on {name.upper()}", no)
        elems = []
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                elems.append(Element(kind="K", name=prefix + name, nodes=(), value=k,
                                     coupled=(inductors[names[i]].name, inductors[names[j]].name),
                                     line=no))
        return elems

    def _instance(self, sd, no, toks, prefix, node, env, depth, stack, out, top_pin2) -> None:
        head, assigns, _ = _split_assigns(toks)
        name = head[0]
        if len(head) < 2:
            raise self.error("E_SPICE_SYNTAX", f"instance {name.upper()} needs a subcircuit name", no)
        target = head[-1]
        nodes = head[1:-1]
        child = self.subckts.get(target)
        if child is None:
            raise self.error("E_SPICE_UNKNOWN_SUBCKT", f"unknown subcircuit {target.upper()}", no)
        if target in stack:
            raise self.error("E_SPICE_RECURSION",
                             f"recursive instantiation {' -> '.join(s.upper() for s in stack + [target])}", no)
        if depth + 1 > MAX_DEPTH:
            raise self.error("E_SPICE_RECURSION", f"subcircuit nesting deeper than {MAX_DEPTH}", no)
        if len(nodes) != len(child.pins):
            raise self.error("E_SPICE_PIN_COUNT",
                             f"instance {name.upper()} connects {len(nodes)} nodes but {target.upper()} "
                             f"has {len(child.pins)} pins", no)
        parent_env = env
        child_env = dict(parent_env)
        declared = {n for n, _ in child.default_params}
        for pname, pval in child.default_params:
            # defaults are evaluated in the caller's env (earlier defaults of S are visible)
            child_env[pname] = self.eval(pval, child_env, child.line)
        for pname, pval in assigns:
            if pname not in declared:
                self.warn("W_SPICE_UNKNOWN_PARAM",
                          f"instance {name.upper()} sets parameter {pname.upper()} not declared in "
                          f"{target.upper()} PARAMS:", no)
            child_env[pname] = self.eval(pval, parent_env, no)
        pin_map = {pin: node(n, no) for pin, n in zip(child.pins, nodes)}
        protected = declared | {p for p, _ in assigns}
        self._expand(child, f"{prefix}{name}.", pin_map, child_env, protected, depth + 1,
                     stack + [target], out, top_pin2)


def _floating_check(netlist: Netlist, issues: IssueCollector) -> None:
    """Warn W_MNA_FLOATING for nodes without an R/L/C path to pin2 (§3.7 item 3)."""
    parent: dict[str, str] = {}

    def find(a: str) -> str:
        parent.setdefault(a, a)
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    nodes: set[str] = {netlist.pin1, netlist.pin2}
    for e in netlist.elements:
        if e.kind == "K":
            continue
        nodes.update(e.nodes)
        if e.kind == "C" and e.value == 0.0:
            continue
        ra, rb = find(e.nodes[0]), find(e.nodes[1])
        if ra != rb:
            parent[ra] = rb
    ground = find(netlist.pin2)
    floating = sorted(n for n in nodes if find(n) != ground)
    if floating:
        issues.warning("W_MNA_FLOATING",
                       f"node(s) without a path to pin2 in {netlist.subckt_name.upper()}: "
                       f"{', '.join(floating[:10])}{' …' if len(floating) > 10 else ''} (gmin applied)",
                       netlist.source_path)


def parse_spice_text(text: str, subckt: str | None, issues: IssueCollector,
                     source_path: str = "<text>") -> Netlist:
    """Parse SPICE text and flatten the selected (or auto-detected) two-pin subcircuit."""
    reader = _SpiceReader(text, issues, source_path)
    top = reader.select_top(subckt)
    netlist = reader.flatten(top)
    _floating_check(netlist, issues)
    return netlist


def parse_spice_file(path: str | os.PathLike, subckt: str | None,
                     issues: IssueCollector) -> Netlist:
    """Read (§4.5 step 1) and parse a SPICE model file."""
    text = read_spice_source(path)
    return parse_spice_text(text, subckt, issues, source_path=os.fspath(path))
