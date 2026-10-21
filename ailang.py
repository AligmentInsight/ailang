#!/usr/bin/env python3
# ═══════════════════════════════════════════════════════════════════
#  AI-lang v0.7.0 "Shell" — single-file distribution
#  A programming language where every value is an AI agent.
#  Requires: Python 3.10+ and `pip install lark`. Nothing else.
#  Works offline (mock mode). Live models: add --live and API keys.
#  v0.5.1: grammar terminals one-per-line (fixes the line-79 crash),
#  plus 9 more audit fixes. See release notes.
#  v0.6.0: open kind registry — new agent species join by field values
#  (kind: NAME), never by grammar changes. GRAMMAR is a versioned
#  contract: the fingerprint is locked in CI and in selftest.
#  v0.7.0: examples/, escalation gate inside ??, stats, --stream,
#  check --offline. The grammar contract held: the fingerprint is
#  unchanged.
#  v0.7.1: typed agents can audit, streaming respects `out`, race ties
#  break in declaration order, conservative --offline, dry-run in stats.
#  v0.7.2: strict verdict parsing, usage accounting for streams, --offline
#  follows `use`, stats --json, AILANG_JOURNAL for run/demo.
#  v0.7.3: judge: field for independent audits (no grammar change),
#  stats over several journals, ask --stream, clean errors from run.
#  v0.8.0: GRAMMAR CHANGE (explicitly sanctioned): match is an expression,
#  kind names may be hyphenated, `||` yields a ForkValue with parts.
#  v0.9.0: gates read nested fields (order.total), `use` resolves relative
#  to the importing file and detects cycles, check --offline --strict.
#  v0.10.0: deterministic branching `if <cond> { … } else { … }` with
#  boolean literals — no model calls, the value is the block's last line.
#  v0.11.0: `ailang fmt` — canonical printer with --check/--write; agent
#  field values keep the Ref/str distinction so ids stay unquoted.
#  v0.11.1: English-only sources (no Cyrillic, no BOM), fmt prints
#  `print if …` / `print match …` blocks.
#  v0.12.0: `ailang shell` — the bespoke terminal: own line editor with
#  history and completion, :commands, typed value rendering.
#  v0.13.0: persistent shell history, emacs-style keys, fork parts
#  addressable by branch name (`crowd.one.v`) in gates and `if`.
#  v0.13.1: runtime errors name the source line; UTF-8 BOM files load.
#  v0.14.0: compiler - static analysis with Rust-style diagnostics
#  (AIL001..), `build` emits a runnable image stamped with the grammar.
#  v0.15.0: `assert` statements, `ailang test` for .ai suites, and build
#  links `use "*.ai"` libraries so the image is self-contained.
#  v0.16.0: incremental build (source hash), `inspect`, `run --offline`,
#  `fmt` over directories, repository hygiene guard.
#  v0.17.0: `doctor` and `grammar` commands, runtime error codes (AIL1xx).
#  v0.18.0: check results are cached (invalidated by grammar), plugin
#  bundles in plugins/, and --json for check/test/doctor.
# ═══════════════════════════════════════════════════════════════════
from __future__ import annotations

import argparse
import asyncio
import base64
import concurrent.futures
import hashlib
import importlib
import json
import logging
import operator
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import reduce
from pathlib import Path

from lark import Lark, Transformer, UnexpectedInput

__version__ = "0.38.0"
log = logging.getLogger("ailang")


# ═══════════════════════ 1. CONFIG ═════════════════════════════════

@dataclass
class AIlangConfig:
    dry_run: bool = True          # mock mode by default — no keys, no spend
    live: bool = False
    meta_engine: str = "gpt-4o-mini"
    memory_window: int = 8
    max_tokens: int = 1024
    request_timeout: int = 60
    budget_usd: float | None = None
    cache: bool = False           # env AILANG_CACHE=1
    stream: bool = False          # --stream: print tokens as they arrive


# ═══════════════════ 2. GRAMMAR (Lark) ═════════════════════════════

GRAMMAR = r'''
start: (_NL | statement)*

?statement: assignment
          | print_stmt
          | assert_stmt
          | until_stmt
          | use_stmt
          | schema_decl
          | policy_stmt
          | gate_decl
          | tool_decl
          | flow_decl
          | patch_stmt
          | expr

assignment: NAME "=" expr
print_stmt: _PRINT expr
assert_stmt: _ASSERT cond
use_stmt: _USE STRING

schema_decl: _SCHEMA NAME "{" (schema_field | _NL | ",")* "}"
schema_field: NAME ":" type_expr QUESTION? invariant?
invariant: _WHERE cond
gate_decl: _GATE NAME "=" gate_expr
policy_stmt: _POLICY "{" (policy_field | _NL | ",")* "}"
policy_field: NAME ":" agent_field_value

?type_expr: list_type
          | NAME
list_type: "[" type_expr "]"

tool_decl: _TOOL NAME "=" _PY STRING params_clause? desc_clause?
params_clause: _PARAMS params_obj
desc_clause: _DESC STRING
params_obj: "{" (param_def | _NL | ",")* "}"
param_def: NAME ":" type_expr

flow_decl: _FLOW NAME "(" NAME ")" block
patch_stmt: _PATCH NAME "{" (patch_field | _NL | ",")* "}"
patch_field: gate_ref ":" literal

until_stmt: _UNTIL postfix max_clause? block
max_clause: _MAX INT
block: "{" (_NL | statement)* "}"

match_expr: _MATCH call "{" (_NL | match_case)* "}"
if_expr: _IF cond block (_ELSE block)?
?cond: cond_or
cond_or: cond_and (_OR cond_and)*
cond_and: cond_unit (_AND cond_unit)*
?cond_unit: comparison | "(" cond_or ")"
match_case: case_label ">>" unit_chain
case_label: STRING | DEFAULT
unit_chain: postfix (">>" postfix)*

?expr: flow
flow: fallback (">>" fallback)*
fallback: parallel ("??" parallel)*
parallel: sequence ("||" sequence)*
sequence: merge ("&" merge)*
merge: postfix ("+" postfix)*
postfix: atom audit?
audit: QUESTION gate_expr

?gate_expr: gate_or
gate_or: gate_and (_OR gate_and)*
gate_and: gate_unit (_AND gate_unit)*
?gate_unit: STRING | comparison | NAME | "(" gate_or ")"
comparison: gate_ref COMP_OP literal
          | measure COMP_OP literal
measure: MEASURE "(" gate_ref ")"
?gate_ref: attr | NAME
?literal: NUMBER | INT | STRING | NAME | TRUE | FALSE | signed_number
signed_number: MINUS (NUMBER | INT)

?atom: NAME
     | attr
     | STRING
     | ML_STRING
     | agent_decl
     | call
     | race_expr
     | match_expr
     | if_expr
     | "(" expr ")"

attr: NAME ("." NAME)+
race_expr: _RACE "(" parallel ")"
call: NAME "(" expr ")"
agent_decl: _AGENT "{" (agent_field | _NL | ",")* "}"
agent_field: NAME ":" agent_field_value
?agent_field_value: STRING | ML_STRING | NUMBER | INT | NAME | name_list
                    | hyphen_name
hyphen_name: NAME (MINUS NAME)+
name_list: "[" (NAME ("," NAME)*)? "]"

ML_STRING: /"""[\s\S]*?"""/
STRING: /"(\\.|[^"\\\n])*"/
NAME: /[^\W\d]\w*/
NUMBER: /\d+\.\d+/
INT: /\d+/
DEFAULT.5: "_"
QUESTION: "?"
COMP_OP: /<=|>=|==|!=|<|>/
TRUE.5: /true(?!\w)/
FALSE.5: /false(?!\w)/
MINUS: "-"

_PRINT.5: /print(?!\w)/
_ASSERT.5: /assert(?!\w)/
_UNTIL.5: /until(?!\w)/
_MATCH.5: /match(?!\w)/
_MAX.5: /max(?!\w)/
_USE.5: /use(?!\w)/
_SCHEMA.5: /schema(?!\w)/
_TOOL.5: /tool(?!\w)/
_PY.5: /py(?!\w)/
_PARAMS.5: /params(?!\w)/
_DESC.5: /desc(?!\w)/
_RACE.5: /race(?!\w)/
_AND.5: /and(?!\w)/
_OR.5: /or(?!\w)/
_IF.5: /if(?!\w)/
_ELSE.5: /else(?!\w)/
_FLOW.5: /flow(?!\w)/
_PATCH.5: /patch(?!\w)/
_GATE.5: /gate(?!\w)/
_POLICY.5: /policy(?!\w)/
_WHERE.5: /where(?!\w)/
MEASURE.5: /len(?!\w)|sum(?!\w)|min(?!\w)|max(?!\w)|avg(?!\w)/
_AGENT.5: /Agent(?!\w)/

_NL.5: /\r?\n+/
%ignore /[ \t]+/
%ignore /#[^\n]*/
'''


# ═══════════════════ 3. AST + PARSER ════════════════════════════════

@dataclass
class Name:        id: str
@dataclass
class Str:         value: str


class Ref(str):
    """A bare identifier used as a value (agent/patch field), not a string.

    Kept as a str subclass so every runtime path that treats field values
    as plain strings keeps working, while the formatter can tell
    `out: Pitch` from `role: "Pitch"`.
    """

@dataclass
class Attr:        base: str; field_name: str
@dataclass
class AgentDecl:   fields: dict
@dataclass
class Call:        name: str; arg: object
@dataclass
class Merge:       parts: list
@dataclass
class Sequence:    parts: list
@dataclass
class Parallel:    parts: list
@dataclass
class Fallback:    parts: list
@dataclass
class Race:        inner: object
@dataclass
class Gate:        unit: object; criterion: object
@dataclass
class Comparison:  left: object; op: str; right: object
@dataclass
class Measure:     kind: str; target: str
@dataclass
class GateAnd:     parts: list
@dataclass
class GateOr:      parts: list
@dataclass
class Flow:        source: object; targets: list
@dataclass
class Assignment:  name: str; expr: object
@dataclass
class Print:       expr: object
@dataclass
class Assert:      cond: object
@dataclass
class Until:       gate: Gate; max_iter: int; body: list
@dataclass
class Case:        label: str | None; chain: list
@dataclass
class Match:       router: Call; cases: list
@dataclass
class If:          cond: object; then: list; other: list | None
@dataclass
class UseStmt:     path: str
@dataclass
class SchemaDecl:  name: str; fields: list; invariants: dict
@dataclass
class ToolDecl:    name: str; target: str; params: list; desc: str
@dataclass
class FlowDecl:    name: str; param: str; body: list
@dataclass
class Patch:       target: str; fields: list
@dataclass
class GateDecl:    name: str; criterion: object
@dataclass
class Policy:      fields: list


class AILangSyntaxError(SyntaxError):
    pass


def _unquote(tok) -> str:
    try:
        return json.loads(str(tok))
    except ValueError:
        return str(tok)[1:-1]


_SCALAR_WORDS = {"str", "int", "float", "bool", "any"}


def _norm_type(t):
    """Token | Name | ('list', inner) → normalized tuple."""
    if isinstance(t, Name):
        if t.id in _SCALAR_WORDS:
            return ("scalar", t.id)
        return ("schema", t.id)
    if isinstance(t, tuple):
        return t
    return ("scalar", str(t))


class _ToAST(Transformer):
    def start(self, i): return list(i)
    def assignment(self, i): return Assignment(i[0].id, i[1])
    def print_stmt(self, i): return Print(i[0])
    def assert_stmt(self, i): return Assert(i[0])
    def use_stmt(self, i): return UseStmt(i[0].value)

    def schema_decl(self, i):
        fields, invariants = [], {}
        for item in i[1:]:
            if not isinstance(item, tuple):
                continue
            name, kind, optional, cond = item
            fields.append((name, kind, optional))
            if cond is not None:
                invariants.setdefault(name, []).append(cond)
        return SchemaDecl(i[0].id, fields, invariants)
    def schema_field(self, i):
        rest = i[2:]
        optional = any(getattr(x, "type", "") == "QUESTION" for x in rest)
        cond = next((x for x in rest
                     if not getattr(x, "type", None)), None)
        return (i[0].id, _norm_type(i[1]), optional, cond)
    def invariant(self, i): return i[0]
    def list_type(self, i): return ("list", _norm_type(i[0]))

    def tool_decl(self, i):
        target = i[1].value
        params = next((x for x in i[2:] if isinstance(x, list)), [])
        desc = i[-1].value if (len(i) > 2 and isinstance(i[-1], Str)
                               and i[-1] is not i[1]) else ""
        return ToolDecl(i[0].id, target, params, desc)
    def params_clause(self, i): return i[0]
    def desc_clause(self, i): return i[0]
    def params_obj(self, i): return [p for p in i if isinstance(p, tuple)]
    def param_def(self, i): return (i[0].id, _norm_type(i[1]))

    def flow_decl(self, i): return FlowDecl(i[0].id, i[1].id, i[2])
    def patch_stmt(self, i):
        return Patch(i[0].id, [p for p in i[1:] if isinstance(p, tuple)])
    def patch_field(self, i):
        left = i[0]
        name = (f"{left.base}.{left.field_name}"
                if isinstance(left, Attr) else left.id)
        v = i[1]
        if isinstance(v, Name): v = Ref(v.id)
        elif isinstance(v, Str): v = v.value
        return (name, v)

    def until_stmt(self, i):
        max_iter = next((x for x in i if isinstance(x, int)), 3)
        return Until(i[0], max_iter, i[-1])
    def max_clause(self, i): return i[0]
    def block(self, i): return list(i)

    def match_expr(self, i):
        return Match(i[0], [c for c in i[1:] if isinstance(c, Case)])

    def match_stmt(self, i):      # kept: old name, same node
        return self.match_expr(i)

    def if_expr(self, i):
        cond = i[0]
        then_block = i[1]
        other = i[2] if len(i) > 2 else None
        return If(cond, then_block, other)

    def cond_or(self, i): return GateOr(i) if len(i) > 1 else i[0]
    def cond_and(self, i): return GateAnd(i) if len(i) > 1 else i[0]
    def match_case(self, i):
        return Case(i[0].value if isinstance(i[0], Str) else None, i[1])
    def case_label(self, i):
        return i[0] if i else None
    def unit_chain(self, i): return list(i)

    def flow(self, i):     return Flow(i[0], i[1:]) if len(i) > 1 else i[0]
    def fallback(self, i): return Fallback(i) if len(i) > 1 else i[0]
    def parallel(self, i): return Parallel(i) if len(i) > 1 else i[0]
    def sequence(self, i): return Sequence(i) if len(i) > 1 else i[0]
    def merge(self, i):    return Merge(i) if len(i) > 1 else i[0]

    def postfix(self, i):
        if len(i) == 2:
            i[1].unit = i[0]
            return i[1]
        return i[0]
    def audit(self, i): return Gate(unit=None, criterion=i[-1])
    def gate_decl(self, i): return GateDecl(i[0].id, i[1])
    def policy_stmt(self, i):
        return Policy([f for f in i if isinstance(f, tuple)])
    def policy_field(self, i):
        v = i[1]
        if isinstance(v, Name):
            return (i[0].id, Ref(v.id))
        if isinstance(v, Str):
            return (i[0].id, v.value)
        return (i[0].id, v)

    def gate_and(self, i): return GateAnd(i) if len(i) > 1 else i[0]
    def gate_or(self, i):  return GateOr(i) if len(i) > 1 else i[0]
    def signed_number(self, i):
        value = i[-1]
        return -value if isinstance(value, (int, float)) else -float(value)
    def measure(self, i):
        kind = str(i[0]).lower()
        target = i[-1]
        target = (f"{target.base}.{target.field_name}"
                  if isinstance(target, Attr) else target.id)
        return Measure(kind, target)

    def comparison(self, i):
        left = i[0]
        if isinstance(left, Measure):
            pass
        elif isinstance(left, Attr):
            left = f"{left.base}.{left.field_name}"
        else:
            left = left.id
        right = i[2]
        if isinstance(right, Str):
            right = right.value          # Name stays Name → runtime resolves
        return Comparison(left, str(i[1]), right)

    def attr(self, i):
        return Attr(i[0].id, ".".join(t.id for t in i[1:]))
    def race_expr(self, i): return Race(i[0])
    def call(self, i): return Call(i[0].id, i[1])
    def agent_decl(self, i): return AgentDecl(dict(i))
    def agent_field(self, i):
        v = i[1]
        if isinstance(v, Name):  return (i[0].id, Ref(v.id))
        if isinstance(v, Str):   return (i[0].id, v.value)
        if isinstance(v, str):   return (i[0].id, v)
        if isinstance(v, list):  return (i[0].id, [n.id for n in v])
        return (i[0].id, v)
    def hyphen_name(self, i):
        return "".join(x.id if isinstance(x, Name) else str(x) for x in i)
    def name_list(self, i): return list(i)

    def NAME(self, t): return Name(str(t))
    def TRUE(self, t): return True
    def FALSE(self, t): return False
    def STRING(self, t): return Str(_unquote(t))
    def ML_STRING(self, t): return Str(str(t)[3:-3])
    def NUMBER(self, t): return float(t)
    def INT(self, t): return int(t)


_parser = Lark(GRAMMAR, parser="lalr", transformer=_ToAST())
# A second, transformer-free parser used only to learn where statements start.
_parser_pos = Lark(GRAMMAR, parser="lalr", propagate_positions=True)


def _read_source(path) -> str:
    """Read a program as UTF-8, tolerating a BOM from Windows editors."""
    return Path(path).read_text(encoding="utf-8-sig")


def _load_program(path):
    """Read a program or report a friendly error; None means 'give up'."""
    try:
        return _read_source(path)
    except OSError as e:
        print(f"cannot read {path!r}: {e}", file=sys.stderr)
        return None


def parse(source: str) -> list:
    try:
        return _parser.parse(source)
    except UnexpectedInput as e:
        ctx = e.get_context(source).splitlines()[0].strip()
        raise AILangSyntaxError(f"Line {e.line}: cannot parse '{ctx}'") from e


def parse_located(source: str) -> list:
    """Parse and remember the source line each top-level statement starts on."""
    stmts = parse(source)
    try:
        tree = _parser_pos.parse(source)
    except UnexpectedInput:
        return stmts                      # parse() would already have raised
    lines = []
    for child in tree.children:
        meta = getattr(child, "meta", None)
        lines.append(getattr(meta, "line", None))
    for st, line in zip(stmts, lines):
        if line:
            st._line = line
    return stmts


# ═══════════════════ 4. SCHEMAS & ARTIFACTS ════════════════════════

SCALARS = {"str", "int", "float", "bool", "any"}


@dataclass
class TypeExpr:
    kind: str                      # scalar | list | schema
    inner: "TypeExpr | None" = None
    name: str | None = None

    @staticmethod
    def from_ast(t) -> "TypeExpr":
        if isinstance(t, tuple):
            kind = t[0]
            if kind == "list":
                return TypeExpr("list", inner=TypeExpr.from_ast(t[1]))
            if kind == "schema":
                return TypeExpr("schema", name=t[1])
            return TypeExpr(t[1] if t[1] in SCALARS else "any")
        return TypeExpr("any")


@dataclass
class Field:
    type: TypeExpr
    optional: bool = False


@dataclass
class Schema:
    name: str
    fields: dict
    parents: tuple = ()          # supertypes declared by `patch X { is_a: Y }`
    invariants: dict = field(default_factory=dict)   # field -> [cond]


def is_subtype(child: str, parent: str, registry: dict) -> bool:
    """True when `child` is `parent` or descends from it (cycle-safe)."""
    if child == parent:
        return True
    seen, stack = set(), [child]
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        sch = registry.get(name)
        for up in (getattr(sch, "parents", ()) or ()):
            if up == parent:
                return True
            stack.append(up)
    return False


_INV_OPS = {"<": operator.lt, ">": operator.gt, "<=": operator.le,
            ">=": operator.ge, "==": operator.eq, "!=": operator.ne}


def _inv_path(record, dotted: str):
    """Walk `a.b.c` through nested dicts; None when the path is absent."""
    value = record
    for part in str(dotted).split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _measure_target(value, dotted: str):
    """Resolve `lines.price` - the tail is mapped over a list of records."""
    parts = str(dotted).split(".")
    current = value
    for index, part in enumerate(parts):
        if isinstance(current, dict):
            if part not in current:
                return None, f"unknown field {part!r}"
            current = current[part]
        elif isinstance(current, list):
            rest = ".".join(parts[index:])
            items = []
            for item in current:
                found, problem = _measure_target(item, rest)
                if problem is not None:
                    return None, problem
                items.append(found)
            return items, None
        else:
            return None, f"{dotted!r} is not a record or a list"
    return current, None


def _aggregate(kind: str, value):
    """→ (number, problem). One place for len/sum/min/max/avg."""
    if kind == "len":
        if not hasattr(value, "__len__"):
            return None, f"len() needs a string or a list, got {type(value).__name__}"
        return len(value), None
    if not isinstance(value, (list, tuple)):
        return None, f"{kind}() needs a list, got {type(value).__name__}"
    numbers = [v for v in value if isinstance(v, (int, float))
               and not isinstance(v, bool)]
    if len(numbers) != len(value):
        return None, f"{kind}() needs a list of numbers"
    if not numbers:
        return 0 if kind == "sum" else (0.0 if kind == "avg" else None), (
            None if kind in ("sum", "avg") else f"{kind}() of an empty list")
    if kind == "sum":
        return sum(numbers), None
    if kind == "avg":
        return sum(numbers) / len(numbers), None
    if kind == "min":
        return min(numbers), None
    if kind == "max":
        return max(numbers), None
    return None, f"unknown measure {kind!r}"


def _inv_value(node, record):
    if isinstance(node, Name):
        return record.get(node.id)
    if node.__class__.__name__ == "Str":
        return node.value
    return node


def _inv_holds(cond, record) -> bool:
    """Evaluate one `where` clause against a record. Unknown → True."""
    if isinstance(cond, Comparison):
        if isinstance(cond.left, Measure):
            target, problem = _measure_target(record, cond.left.target)
            if problem is not None or target is None:
                return True
            value, problem = _aggregate(cond.left.kind, target)
            if problem is not None:
                return True
            left = value
        elif isinstance(cond.left, str):
            left = _inv_path(record, cond.left)
        else:
            left = _inv_value(cond.left, record)
        if isinstance(cond.right, str):
            right = _inv_path(record, cond.right)
        else:
            right = _inv_value(cond.right, record)
        if left is None or right is None:
            return True                     # cannot judge: stay silent
        op = _INV_OPS.get(cond.op)
        try:
            return bool(op(left, right)) if op else True
        except TypeError:
            return True
    if isinstance(cond, GateAnd):
        return all(_inv_holds(p, record) for p in cond.parts)
    if isinstance(cond, GateOr):
        return any(_inv_holds(p, record) for p in cond.parts)
    return True


def _invariant_errors(value, schema, path) -> list:
    errs = []
    for fname, conds in (getattr(schema, "invariants", {}) or {}).items():
        if fname not in value or value[fname] is None:
            continue
        for cond in conds:
            if not _inv_holds(cond, value):
                errs.append(f"{path}.{fname}: invariant "
                            f"{_gtext(cond)!r} does not hold")
    return errs


def validate(value, t: TypeExpr, registry: dict, path: str = "$") -> list[str]:
    if t.kind == "any":
        return []
    if t.kind == "schema":
        sch = registry.get(t.name)
        if sch is None:
            return [f"{path}: unknown schema '{t.name}'"]
        if not isinstance(value, dict):
            return [f"{path}: expected object for '{t.name}', "
                    f"got {type(value).__name__}"]
        errs = []
        for fname, f in sch.fields.items():
            if fname not in value or value[fname] is None:
                if not f.optional:
                    errs.append(f"{path}.{fname}: missing required field")
            else:
                errs += validate(value[fname], f.type, registry,
                                 f"{path}.{fname}")
        errs += _invariant_errors(value, sch, path)
        return errs
    if t.kind == "list":
        if not isinstance(value, list):
            return [f"{path}: expected list, got {type(value).__name__}"]
        return [e for i, item in enumerate(value)
                for e in validate(item, t.inner, registry, f"{path}[{i}]")]
    py = {"str": str, "int": int, "float": (int, float),
          "bool": bool}[t.kind]
    if isinstance(value, bool) and t.kind != "bool":
        return [f"{path}: expected {t.kind}, got bool"]
    if not isinstance(value, py):
        return [f"{path}: expected {t.kind}, got {type(value).__name__}"]
    return []


class _ValueFrame:
    """Lets `_eval_gate` judge a bare value (the body result of `until`)."""

    def __init__(self, value):
        self.last_insight = value


class TypedArtifact:
    def __init__(self, data: dict, schema: Schema, registry: dict):
        self.data, self.schema, self.registry = data, schema, registry

    def get(self, dotted: str):
        v = self.data
        for part in dotted.split("."):
            v = v[part]
        return v

    def as_text(self) -> str:
        return json.dumps(self.data, ensure_ascii=False, indent=2)

    def __repr__(self):
        return f"{self.schema.name}{self.data}"


def parse_json_output(text: str):
    t = re.sub(r"^```(\w*)\n?|```\s*$", "", text.strip()).strip()
    try:
        return json.loads(t), None
    except ValueError as e:
        return None, f"output is not valid JSON: {e}"


def _example(t: TypeExpr, registry: dict):
    if t.kind == "schema":
        return {n: _example(f.type, registry)
                for n, f in registry[t.name].fields.items()}
    if t.kind == "list":
        return []
    # 0.75 so demo thresholds like `confidence > 0.7` pass in mock mode
    return {"str": "example", "int": 0, "float": 0.75, "bool": True,
            "any": "example"}[t.kind]


def build_contract(schema: Schema, registry: dict) -> str:
    t = TypeExpr("schema", name=schema.name)
    ex = json.dumps(_example(t, registry), ensure_ascii=False)
    return (f"\n\n[CONTRACT] Respond with ONLY a JSON object with exactly "
            f"these fields:\n{ex}\n"
            f"Example shape: AILANG-MOCK-JSON:{ex}\n"
            f"No prose, no markdown fences.")


# ═══════════════════ 5. TOOLS (Python bridge) ══════════════════════

def _ensure_import_path(*dirs) -> None:
    """Local modules (py agents, tools) must resolve no matter how
    ailang.py was launched: script, console script, isolated mode."""
    for d in dirs:
        try:
            entry = str(Path(d).resolve()) if d else ""
        except OSError:
            entry = ""
        if entry and entry not in sys.path:
            sys.path.insert(0, entry)


TOOL_CALL = re.compile(r'@tool\s+(\w+)\s+(\{.*?\})\s*$', re.M)


@dataclass
class Tool:
    name: str
    target: str                     # "module.attr.func"
    params: dict                    # name -> TypeExpr
    desc: str = ""

    def _resolve(self):
        _ensure_import_path(os.getcwd(), Path(__file__).resolve().parent)
        mod, _, attr = self.target.rpartition(".")
        obj = importlib.import_module(mod)
        for part in attr.split("."):
            obj = getattr(obj, part)
        return obj

    def run(self, args: dict, registry: dict) -> str:
        errs = []
        for k, t in self.params.items():
            if k not in args:
                errs.append(f"missing param '{k}'")
            else:
                errs += validate(args[k], t, registry, f"args.{k}")
        if errs:
            raise ValueError("bad tool args: " + "; ".join(errs))
        result = self._resolve()(**args)
        return result if isinstance(result, str) else json.dumps(
            result, ensure_ascii=False, default=str)


def build_toolkit(tools: list, registry: dict) -> str:
    lines = ["", "[TOOLKIT] You may call tools by replying with ONE line "
             'in the format: @tool <name> {"param": value}']
    for t in tools:
        sig = ", ".join(f"{n}" for n in t.params)
        lines.append(f"[TOOLKIT] {t.name}({sig}) — {t.desc}")
    return "\n".join(lines)


def parse_tool_call(text: str):
    m = TOOL_CALL.search(text)
    if not m:
        return None
    try:
        return m.group(1), json.loads(m.group(2))
    except ValueError:
        return None


# ═══════════════════ 6. PROVIDERS ══════════════════════════════════

# USD per 1M tokens: engine -> (input, output). `None` means no price is
# known: reports print "?" and never invent a number.
PRICE_TABLE = {
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.6),
    "claude-3-5-sonnet": (3.0, 15.0),      # approx, public list price
    "claude-3-haiku": (0.25, 1.25),        # approx
    "claude-sonnet-4": (3.0, 15.0),        # approx
    "claude-opus-4": (15.0, 75.0),         # approx
    "gemini-1.5-pro": (1.25, 5.0),         # approx
    "gemini-1.5-flash": (0.075, 0.3),      # approx
    "gemini-2.0-flash": (0.1, 0.4),        # approx
    "mock": (0.0, 0.0),
    "echo": (0.0, 0.0),
    "py": (0.0, 0.0),
    "human": (0.0, 0.0),
}

# Prefix rules for model families that are priced like their flagship.
PRICE_PREFIXES = (("claude", (3.0, 15.0)), ("gemini", (1.25, 5.0)))

# Context windows in tokens. Engines absent here have no window at all
# (py, human, echo) or an unknown one (custom endpoints).
CONTEXT_WINDOWS = {"gpt-4o": 128000, "gpt-4o-mini": 128000, "mock": 8192}
CONTEXT_PREFIXES = (("claude", 200000), ("gemini", 1000000),
                    ("gpt-4o", 128000))

# Output estimate for an agent whose out-schema is unknown.
DEFAULT_OUT_TOKENS = 128


def estimate_tokens(text) -> int:
    """Deterministic OFFLINE estimate: max(words, ceil(chars / 4)).

    It is an estimate, not a tokenizer: reports mark it with `~`. The
    point is a stable, key-free comparison between agents.
    """
    s = text if isinstance(text, str) else _text(text)
    if not s:
        return 0
    return max(len(s.split()), (len(s) + 3) // 4)


def lookup_price(engine, price_in=None, price_out=None):
    """→ (usd_in, usd_out, known) per 1M tokens; agent prices win."""
    engine = str(engine)
    base_in, base_out = PRICE_TABLE.get(engine, (None, None))
    if base_in is None:
        for prefix, pair in PRICE_PREFIXES:
            if engine.startswith(prefix):
                base_in, base_out = pair
                break
    if price_in is not None or price_out is not None:
        return (price_in if price_in is not None else base_in,
                price_out if price_out is not None else base_out, True)
    if base_in is None:
        return None, None, False
    return float(base_in), float(base_out), True


def context_window(engine, override=None):
    """Tokens of context, or None when the engine has no window."""
    if override is not None:
        return int(override)
    engine = str(engine)
    if engine in CONTEXT_WINDOWS:
        return CONTEXT_WINDOWS[engine]
    for prefix, size in CONTEXT_PREFIXES:
        if engine.startswith(prefix):
            return size
    return None

@dataclass
class Completion:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0


META_SYSTEM = ("You are the Architect of Personalities. You synthesize "
               "system prompts of hybrid agents. Reply ONLY with the "
               "resulting system prompt text.")

ALIGNMENT_TEMPLATE = """Synthesize one balanced system prompt for a hybrid
agent from two agent DNAs.

=== DNA 1: agent "{name_a}" ===
{prompt_a}

=== DNA 2: agent "{name_b}" ===
{prompt_b}

Requirements: keep the strengths of both personalities; smooth direct
contradictions into a balanced strategy; 3-6 sentences; output ONLY the
resulting system prompt."""


PROVIDER_ENV = {"openai": "OPENAI_API_KEY",
                "anthropic": "ANTHROPIC_API_KEY",
                "google": "GOOGLE_API_KEY"}

PROVIDER_BASE = {"openai": "https://api.openai.com/v1",
                 "anthropic": "https://api.anthropic.com/v1",
                 "google": "https://generativelanguage.googleapis.com/v1beta"}


def settings_path() -> Path:
    """Where keys live: `~/.ailang/config.json` (or $AILANG_CONFIG)."""
    override = os.environ.get("AILANG_CONFIG")
    if override:
        return Path(override)
    return Path.home() / ".ailang" / "config.json"


def load_settings() -> dict:
    """Read the settings file; a broken file is an error, not a silence."""
    path = settings_path()
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError:
        return {"providers": {}}
    try:
        data = json.loads(raw)
    except ValueError:
        raise AILangRuntimeError(
            f"config: {path} is not valid JSON") from None
    if not isinstance(data, dict):
        raise AILangRuntimeError(f"config: {path} must hold an object")
    data.setdefault("providers", {})
    return data


def save_settings(data: dict) -> Path:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8", newline="\n")
    return path


def provider_entry(name: str) -> dict:
    return dict((load_settings().get("providers") or {}).get(name) or {})


def provider_key(name: str) -> str | None:
    """The pasted key wins; the environment variable is the fallback."""
    entry = provider_entry(name)
    if entry.get("key"):
        return str(entry["key"])
    env = PROVIDER_ENV.get(name)
    if env and os.environ.get(env):
        return os.environ[env]
    return None


def provider_base(name: str, default: str | None = None) -> str:
    entry = provider_entry(name)
    return str(entry.get("base_url")
               or PROVIDER_BASE.get(name)
               or default or "").rstrip("/")


def _mask(secret: str) -> str:
    secret = str(secret)
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}…{secret[-4:]}"


def _build_provider(name: str):
    """The provider object for a name, or None when it has no key."""
    if name == "openai":
        return OpenAIProvider("gpt-4o-mini")
    if name == "anthropic":
        return AnthropicProvider("claude-3-haiku")
    if name == "google":
        return GoogleProvider("gemini-1.5-flash")
    return None


def _probe(name: str) -> tuple:
    """One minimal call. → (ok, message)."""
    import time
    try:
        provider = _build_provider(name)
    except AILangRuntimeError as e:
        return False, str(e)
    cfg = AIlangConfig(live=True, dry_run=False, cache=False,
                       max_tokens=16, request_timeout=20)
    started = time.monotonic()
    try:
        comp = provider.complete_detailed(
            "You are a health check. Reply with one word.", "ping", [], 0.0,
            cfg)
    except AILangRuntimeError as e:
        return False, str(e)
    ms = int((time.monotonic() - started) * 1000)
    text = (comp.text or "").strip().replace("\n", " ")[:40]
    return True, (f"{ms} ms, {comp.tokens_in} in / {comp.tokens_out} out, "
                  f"${comp.cost_usd:.6f}, replied {text!r}")


def cmd_setup(args):
    """Put a key or an endpoint where the language expects it."""
    if getattr(args, "test", None):
        names = ["openai", "anthropic", "google"] \
            if args.test == "all" else [args.test]
        ready = [n for n in names if provider_key(n)]
        if not ready:
            print("setup: no key to test - run `ailang setup --openai sk-...` "
                  "first", file=sys.stderr)
            return 1
        bad = 0
        for name in ready:
            ok, message = _probe(name)
            print(f"{name:<10}{'ok  ' if ok else 'FAIL'}  {message}")
            bad += 0 if ok else 1
        return 1 if bad else 0
    data = load_settings()
    providers = data.setdefault("providers", {})
    changed = []

    for name in ("openai", "anthropic", "google"):
        value = getattr(args, name, None)
        if value:
            providers.setdefault(name, {})["key"] = value
            changed.append(f"{name} key")
    if getattr(args, "endpoint", None):
        providers.setdefault("openai-compatible", {})["base_url"] = \
            args.endpoint
        if getattr(args, "key", None):
            providers["openai-compatible"]["key"] = args.key
        changed.append("custom endpoint")
    for name in getattr(args, "clear", None) or []:
        key = "openai-compatible" if name == "endpoint" else name
        providers.pop(key, None)
        changed.append(f"cleared {name}")

    if changed:
        path = save_settings(data)
        stream = sys.stderr if getattr(args, "json", False) else sys.stdout
        print(f"saved to {path}: " + ", ".join(changed), file=stream)
        if not getattr(args, "json", False):
            print()

    if not getattr(args, "json", False):
        print(f"config     {settings_path()}")
        for name in ("openai", "anthropic", "google"):
            entry = providers.get(name) or {}
            key = entry.get("key")
            env = PROVIDER_ENV.get(name, "")
            state = ("pasted " + _mask(key)) if key else (
                f"from ${env}" if os.environ.get(env) else "not set")
            print(f"  {name:<14}{state}")
        custom = providers.get("openai-compatible") or {}
        if custom.get("base_url"):
            print(f"  endpoint      {custom['base_url']}"
                  + ("  (key set)" if custom.get("key") else ""))
        print()
        print("next:")
        print("  ailang run main.ai --live      # use the keys above")
        print("  ailang doctor                  # what is configured")
        return 0

    print(json.dumps({"schema": 1, "config": str(settings_path()),
                      "providers": {name: {
                          "key": bool((providers.get(name) or {}).get("key")),
                          "env": bool(os.environ.get(
                              PROVIDER_ENV.get(name, ""))),
                          "base_url": (providers.get(name) or {})
                          .get("base_url")}
                          for name in ("openai", "anthropic", "google",
                                       "openai-compatible")}},
                     ensure_ascii=False, indent=1))
    return 0


class LLMProvider:
    def __init__(self, model: str):
        self.model = model
        self.last_usage = None      # Completion with tokens/cost of a stream

    def complete(self, system_prompt, user_message, history, temperature,
                 config) -> str:
        return self.complete_detailed(system_prompt, user_message, history,
                                      temperature, config).text

    def complete_detailed(self, system_prompt, user_message, history,
                          temperature, config) -> Completion:
        raise NotImplementedError

    def stream_detailed(self, system_prompt, user_message, history,
                        temperature, config):
        """Yield text deltas. Default: one honest chunk (no streaming API)."""
        yield self.complete_detailed(system_prompt, user_message, history,
                                     temperature, config).text


class MockProvider(LLMProvider):
    """Offline provider. Understands gates, routers, contracts, tools."""

    _PERSONAS = (
        (r"edit|critic|merciless", "a skeptical edit of the artifact"),
        (r"polish", "a polished final version"),
        (r"idea|generate|muse", "a bold idea with a hook"),
        (r"research", "a researched summary"),
    )

    def complete_detailed(self, system_prompt, user_message, history,
                          temperature, config):
        # 1) hybrid synthesis (the `+` operator's meta-model)
        if "Architect of Personalities" in system_prompt:
            return Completion(text="You are a balanced hybrid: first "
                                   "diverge boldly, then filter ruthlessly.")
        # 2) gate verdicts — BEFORE contracts, so typed agents can audit
        if "PASS or FAIL" in user_message:
            return Completion(text="PASS\n(mock verdict: criterion "
                                   "considered satisfied)")
        # 3) semantic routing
        m = re.search(r"one of: ([^.]+)\.", user_message)
        if m and "label only" in user_message:
            return Completion(text=m.group(1).split(",")[0].strip())
        # 4) output contracts
        if "AILANG-MOCK-JSON:" in system_prompt:
            m = re.search(r"AILANG-MOCK-JSON:(\{.*\})", system_prompt, re.S)
            return Completion(text=m.group(1))
        # 5) tool protocol (exercised exactly once per invoke)
        tk = re.search(r"\[TOOLKIT\] (\w+)\(", system_prompt)
        if tk and "[TOOL" not in user_message:
            param = re.search(r"\[TOOLKIT\] \w+\((\w+)\)", system_prompt)
            p = param.group(1) if param else "arg"
            return Completion(text=f'@tool {tk.group(1)} '
                                   f'{{"{p}": "mock-value"}}')
        # 6) default persona
        persona = next((p for rx, p in self._PERSONAS
                        if re.search(rx, system_prompt, re.I)),
                       "a thoughtful answer in character")
        snippet = user_message[:160] + ("…" if len(user_message) > 160 else "")
        return Completion(text=f"[MOCK · {self.model}] Playing {persona}.\n"
                              f"Input: \"{snippet}\"")

    def stream_detailed(self, system_prompt, user_message, history,
                        temperature, config):
        text = self.complete_detailed(system_prompt, user_message, history,
                                      temperature, config).text
        step = 12                       # deterministic chunks, offline
        for i in range(0, len(text), step):
            yield text[i:i + step]


def _usage_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """Cost of one call from PRICE_TABLE; 0.0 when the model is unpriced."""
    pin, pout, known = lookup_price(model)
    if not known or pin is None or pout is None:
        return 0.0
    return (tokens_in * pin + tokens_out * pout) / 1_000_000


def _open_http(req, timeout: float):
    """urlopen with the failure turned into a sentence a user can act on."""
    import urllib.error
    import urllib.request
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:240].replace("\n", " ")
        raise AILangRuntimeError(
            f"HTTP {e.code} from the provider: {detail}") from e
    except Exception as e:                       # noqa: BLE001 — transport
        raise AILangRuntimeError(f"provider call failed: {e}") from e


class EndpointProvider(LLMProvider):
    """Any OpenAI-compatible chat API at a custom base URL: Ollama,
    OpenRouter, Groq, Together, vLLM, LiteLLM, DeepSeek... stdlib only."""

    def __init__(self, model: str, base_url: str,
                 env_var: str | None = None, key: str | None = None):
        super().__init__(model)
        self.base_url = base_url.rstrip("/")
        self.env_var = env_var
        self.key = key

    def _key(self) -> str:
        if self.key:
            return self.key
        return os.environ.get(self.env_var, "") if self.env_var else ""

    def complete_detailed(self, system_prompt, user_message, history,
                          temperature, config):
        import urllib.request
        headers = {"Content-Type": "application/json"}
        key = self._key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        payload = {"model": self.model, "temperature": temperature,
                   "max_tokens": config.max_tokens,
                   "messages": [{"role": "system",
                                 "content": system_prompt},
                                *history,
                                {"role": "user",
                                 "content": user_message}]}
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers, method="POST")
        with _open_http(req, config.request_timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        usage = data.get("usage") or {}
        tokens_in = usage.get("prompt_tokens", 0) or 0
        tokens_out = usage.get("completion_tokens", 0) or 0
        return Completion(
            text=data["choices"][0]["message"].get("content") or "",
            tokens_in=tokens_in, tokens_out=tokens_out,
            cost_usd=_usage_cost(self.model, tokens_in, tokens_out))

    def stream_detailed(self, system_prompt, user_message, history,
                        temperature, config):
        import urllib.request
        headers = {"Content-Type": "application/json"}
        key = self._key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        payload = {"model": self.model, "temperature": temperature,
                   "max_tokens": config.max_tokens, "stream": True,
                   "stream_options": {"include_usage": True},
                   "messages": [{"role": "system",
                                 "content": system_prompt},
                                *history,
                                {"role": "user",
                                 "content": user_message}]}
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers, method="POST")
        with _open_http(req, config.request_timeout) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                    usage = obj.get("usage")
                    if usage:
                        tin = usage.get("prompt_tokens", 0) or 0
                        tout = usage.get("completion_tokens", 0) or 0
                        self.last_usage = Completion(
                            text="", tokens_in=tin, tokens_out=tout,
                            cost_usd=_usage_cost(self.model, tin, tout))
                    delta = (obj["choices"][0].get("delta") or {}) \
                        .get("content") or ""
                except (ValueError, KeyError, IndexError):
                    continue
                if delta:
                    yield delta


class OpenAIProvider(EndpointProvider):
    """The real OpenAI API: an OpenAI-compatible endpoint with a key."""

    PRICES = {"gpt-4o": (2.5, 10.0), "gpt-4o-mini": (0.15, 0.6)}

    def __init__(self, model: str):
        key = provider_key("openai")
        if not key:
            raise AILangRuntimeError(
                "openai: no key. Run `ailang setup --openai sk-...` or set "
                "OPENAI_API_KEY")
        super().__init__(model, provider_base("openai"), key=key)


def _post_json(url: str, headers: dict, payload: dict, timeout: float):
    import urllib.error
    import urllib.request
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:240].replace("\n", " ")
        raise AILangRuntimeError(
            f"HTTP {e.code} from the provider: {detail}") from e
    except Exception as e:                       # noqa: BLE001 — transport
        raise AILangRuntimeError(f"provider call failed: {e}") from e


class AnthropicProvider(LLMProvider):
    """The Messages API over plain HTTP; no SDK, no extra dependency."""

    def __init__(self, model: str):
        super().__init__(model)
        self.key = provider_key("anthropic")
        if not self.key:
            raise AILangRuntimeError(
                "anthropic: no key. Run `ailang setup --anthropic sk-ant-...` "
                "or set ANTHROPIC_API_KEY")
        self.base_url = provider_base("anthropic")

    def complete_detailed(self, system_prompt, user_message, history,
                          temperature, config):
        data = _post_json(
            self.base_url + "/messages",
            {"x-api-key": self.key, "anthropic-version": "2023-06-01"},
            {"model": self.model, "max_tokens": config.max_tokens,
             "temperature": temperature, "system": system_prompt,
             "messages": [*history,
                          {"role": "user", "content": user_message}]},
            config.request_timeout)
        blocks = data.get("content") or []
        text = "".join(block.get("text", "") for block in blocks
                       if isinstance(block, dict))
        usage = data.get("usage") or {}
        tin = usage.get("input_tokens", 0) or 0
        tout = usage.get("output_tokens", 0) or 0
        return Completion(text=text, tokens_in=tin, tokens_out=tout,
                          cost_usd=_usage_cost(self.model, tin, tout))


class GoogleProvider(LLMProvider):
    """generateContent over plain HTTP; no SDK, no extra dependency."""

    def __init__(self, model: str):
        super().__init__(model)
        self.key = provider_key("google")
        if not self.key:
            raise AILangRuntimeError(
                "google: no key. Run `ailang setup --google AIza...` or set "
                "GOOGLE_API_KEY")
        self.base_url = provider_base("google")

    def complete_detailed(self, system_prompt, user_message, history,
                          temperature, config):
        url = (f"{self.base_url}/models/{self.model}:generateContent"
               f"?key={self.key}")
        data = _post_json(
            url, {},
            {"contents": [*history, {"role": "user",
                                     "parts": [{"text": user_message}]}],
             "systemInstruction": {"parts": [{"text": system_prompt}]},
             "generationConfig": {"temperature": temperature,
                                  "maxOutputTokens": config.max_tokens}},
            config.request_timeout)
        candidates = data.get("candidates") or [{}]
        parts = ((candidates[0].get("content") or {}).get("parts")) or []
        text = "".join(part.get("text", "") for part in parts
                       if isinstance(part, dict))
        usage = data.get("usageMetadata") or {}
        tin = usage.get("promptTokenCount", 0) or 0
        tout = usage.get("candidatesTokenCount", 0) or 0
        return Completion(text=text, tokens_in=tin, tokens_out=tout,
                          cost_usd=_usage_cost(self.model, tin, tout))


class CachedProvider(LLMProvider):
    def __init__(self, inner: LLMProvider):
        super().__init__(inner.model)
        self.inner, self.dir = inner, Path.home() / ".ailang-cache"
        self.dir.mkdir(parents=True, exist_ok=True)

    def complete_detailed(self, system_prompt, user_message, history,
                          temperature, config):
        blob = json.dumps([self.model, system_prompt, temperature,
                           user_message, history], ensure_ascii=False)
        key = hashlib.sha256(blob.encode()).hexdigest()
        path = self.dir / f"{key}.json"
        if path.exists():
            rec = json.loads(path.read_text(encoding="utf-8"))
            return Completion(**rec)
        comp = self.inner.complete_detailed(system_prompt, user_message,
                                            history, temperature, config)
        path.write_text(json.dumps(comp.__dict__, ensure_ascii=False),
                        encoding="utf-8")
        return comp


def resolve_provider(engine: str, config: AIlangConfig,
                     endpoint: str | None = None,
                     env: str | None = None) -> LLMProvider:
    real = None
    if not config.dry_run and config.live:
        if endpoint:                   # custom OpenAI-compatible endpoint
            real = EndpointProvider(engine, endpoint, env)
            return CachedProvider(real) if config.cache else real
        e = engine.lower()
        if e.startswith(("gpt", "o1", "chatgpt")):
            real = OpenAIProvider(engine)
        elif e.startswith("claude"):
            real = AnthropicProvider(engine)
        elif e.startswith("gemini"):
            real = GoogleProvider(engine)
        else:
            raise RuntimeError(f"Unknown engine '{engine}' in live mode "
                               f"(prefixes: gpt*, claude*, gemini*)")
    provider = real or MockProvider(engine)
    if config.cache:
        return CachedProvider(provider)
    return provider


# ═══════════════════ 7. MEMORY & COMPOSITES ════════════════════════

@dataclass
class MemoryEntry:
    author: str
    role: str
    content: str
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc)
                    .isoformat(timespec="seconds"))


class AgentMemory:
    def __init__(self, entries=None):
        self._entries = list(entries or [])

    def log(self, author, role, content):
        self._entries.append(MemoryEntry(author, role, content))

    def as_history(self, window=8, drop_last_role=None):
        entries = self._entries
        if drop_last_role and entries and entries[-1].role == drop_last_role:
            entries = entries[:-1]
        history = []
        for e in entries[-window:]:
            role = "assistant" if e.role == "output" else "user"
            chunk = f"[{e.author}] {e.content}"
            if history and history[-1]["role"] == role:
                history[-1]["content"] += "\n\n" + chunk
            else:
                history.append({"role": role, "content": chunk})
        return history

    def merge(self, other: "AgentMemory") -> "AgentMemory":
        return AgentMemory(sorted(self._entries + other._entries,
                                  key=lambda e: e.ts))

    def __len__(self):
        return len(self._entries)


@dataclass
class AIAgent:
    name: str
    type: str = "custom"
    engine: str = "mock"
    prompt: str = ""
    temperature: float = 0.7
    config: AIlangConfig = field(default_factory=AIlangConfig, repr=False)
    memory: AgentMemory = field(default_factory=AgentMemory, repr=False)
    last_insight: object = None
    lineage: tuple = ()
    in_schema: Schema | None = None
    out_schema: Schema | None = None
    tools: list = field(default_factory=list)
    timeout: float | None = None
    casts: int = 2
    endpoint: str | None = None      # OpenAI-compatible base URL (live)
    env: str | None = None           # env var holding the API key
    reasoning: bool = False          # o1/o3/R1: system folds into user
    judge: str | None = None         # who audits this agent's self-review
    budget_usd: float | None = None  # wage cap for ONE call, USD
    salary_usd: float | None = None  # wage cap for the WHOLE run, USD
    window: int | None = None        # context window override, tokens
    price_in: float | None = None    # USD per 1M input tokens
    price_out: float | None = None   # USD per 1M output tokens
    _provider: LLMProvider | None = field(default=None, repr=False,
                                          compare=False)

    def __post_init__(self):
        if self._provider is None:
            self._provider = resolve_provider(self.engine, self.config,
                                              endpoint=self.endpoint,
                                              env=self.env)

    def __repr__(self):
        return (f"Agent({self.name!r}, model={self.engine!r}, "
                f"out={self.out_schema.name if self.out_schema else '—'})")


class Composite:
    def __init__(self, *agents):
        self.agents = list(agents)
        self.name = " ? ".join(getattr(a, "name", "?") for a in agents)
        self.last_insight = None


class ForkValue(str):
    """`a || b` result: renders as the joined text, keeps the parts."""

    def __new__(cls, text, parts):
        obj = super().__new__(cls, text)
        obj.parts = dict(parts)
        return obj

    def get(self, name, default=None):
        return self.parts.get(name, default)


class Chain(Composite):   pass       # a & b
class Fork(Composite):    pass       # a || b
class Backup(Composite):  pass       # a ?? b


class RaceRunner:                      # race (a || b)
    def __init__(self, fork: Fork):
        self.fork, self.name, self.last_insight = fork, "race", None


class Human:
    name = "human"

    def __init__(self, out=print, input_fn=input):
        self.out, self.input_fn = out, input_fn
        self.last_insight = None

    def invoke(self, task, source="human"):
        self.out(f"\n[human is asked]\n{task}\n")
        self.last_insight = self.input_fn("you> ")
        return self.last_insight


class Echo:
    name = "echo"
    last_insight = None

    def invoke(self, task, source="human"):
        self.last_insight = task
        return task


@dataclass
class Attachment:
    path: str


@dataclass
class FlowValue:
    name: str
    param: str
    body: list


# ═══════════════════ 8. JOURNAL & REPLAY ═══════════════════════════

def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class Journal:
    """Append-only JSONL record. Opens/closes per write (Windows-safe).

    v2 starts the file with a header (`$journal`) that names the program and
    its hash, so `ailang replay` can find the program again on its own.
    """

    def __init__(self, path: str, embed_source: bool = False):
        self.path = str(path)
        self.program = ""
        self.source_hash = ""
        self.source = ""
        self.embed_source = bool(embed_source)
        self.statements = 0
        self.legacy = False
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        try:
            existed = Path(self.path).stat().st_size > 0
        except OSError:
            existed = False
        if self._has_header():
            return
        if existed:
            # a v1 journal: append to it, never rewrite its history
            self.legacy = True
            return
        self._write_header()

    def _has_header(self) -> bool:
        try:
            with open(self.path, encoding="utf-8") as f:
                first = f.readline().strip()
        except OSError:
            return False
        return first.startswith('{"$journal"')

    def _write_header(self) -> None:
        head = {"$journal": 3 if self.source else 2, "ailang": __version__,
                "grammar": grammar_fingerprint(), "ts": time.time(),
                "program": self.program, "source_hash": self.source_hash,
                "statements": self.statements}
        if self.source:
            head["source"] = self.source
        with open(self.path, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(head, ensure_ascii=False) + "\n")

    def note_program(self, path, source: str) -> None:
        """Remember what is being replayed; refresh the header if empty."""
        self.program = str(path or "")
        if self.embed_source:
            self.source = str(source or "")
        self.source_hash = hashlib.sha256(
            str(source).encode("utf-8")).hexdigest()[:16]
        try:
            self.statements = len(parse_located(str(source)))
        except AILangSyntaxError:
            self.statements = 0
        if self.legacy:
            return
        if self._has_header():
            rows = Path(self.path).read_text(encoding="utf-8").splitlines()
            if len(rows) == 1:
                self._write_header()          # still empty: refresh it

    def log(self, agent, engine, in_digest, out, comp, latency_ms, note="",
            dry=False):
        rec = {"ts": time.time(), "agent": agent, "engine": engine,
               "in": in_digest, "out": out, "tokens_in": comp.tokens_in,
               "tokens_out": comp.tokens_out, "cost_usd": comp.cost_usd,
               "latency_ms": latency_ms, "note": note, "dry": dry}
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


class Replay:
    def __init__(self, path: str):
        self.map = {}
        self.header = {}
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if "$journal" in rec:
                    self.header = rec
                    continue
                self.map[(rec["agent"], rec["in"])] = rec["out"]

    def lookup(self, agent, in_digest):
        return self.map.get((agent, in_digest))


# ═══════════════════ 9. RUNTIME ════════════════════════════════════

class AILangRuntimeError(RuntimeError): pass
class ArtifactError(AILangRuntimeError): pass
class BudgetError(AILangRuntimeError): pass
class SalaryError(AILangRuntimeError):
    """A whole-run wage cap (`salary:`) was exceeded."""


# ═════════════════ 9.5 KIND REGISTRY (v0.6.0 "Family") ═══════════════
# The species of agents is an open registry: new members join by field
# values (kind: NAME), never by grammar changes. GRAMMAR is a versioned
# contract: the fingerprint is locked in CI and in selftest.

_KIND_NAME = re.compile(r"^[a-z][a-z0-9-]*$")
# What a `policy` block may set for every agent in the file. Identity
# fields (kind, in, out, tools, run) are deliberately absent: a single
# default for them would be a bug factory.
POLICY_FIELDS = frozenset({
    "model", "endpoint", "env", "reasoning", "budget", "salary", "judge",
    "timeout", "casts", "window", "price_in", "price_out",
})

# The shape a default must have: a number, a name or a flag.
POLICY_NUMBERS = frozenset({"budget", "salary", "timeout", "casts",
                            "window", "price_in", "price_out"})
POLICY_NAMES = frozenset({"model", "endpoint", "env", "judge"})


def _policy_value_problem(key, value):
    """→ why this default is unusable, or None."""
    if key in POLICY_NAMES:
        if not isinstance(value, str):
            return f"'{key}' needs a name in quotes"
        return None
    if key in POLICY_NUMBERS:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"'{key}' needs a number"
        return None
    if key == "reasoning":
        if isinstance(value, bool):
            return None
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return None
        return "'reasoning' needs true or false"
    return None

# ── kinds ─────────────────────────────────────────────────────────
_KINDS: dict = {}                 # name -> factory(fields, config)
_BUILTIN_KINDS: set = set()
_LOADED_PLUGINS: set = set()
_KIND_SOURCES: dict = {}          # kind -> plugin file that registered it
_SUBTYPE_EDGES: set = set()      # (child, parent) pairs seen at runtime
_PLUGIN_SCAN_DONE: list = []      # one directory scan per process


def register_kind(name, factory, *, builtin=False, replace=False):
    """Add a kind to the registry. Name clashes are errors, not overwrites."""
    if not isinstance(name, str) or not _KIND_NAME.match(name):
        raise AILangRuntimeError(
            f"kind name {name!r} must match [a-z][a-z0-9-]*")
    if not callable(factory):
        raise AILangRuntimeError(f"kind {name!r}: factory must be callable")
    if name in _KINDS and not replace:
        raise AILangRuntimeError(
            f"kind {name!r} is already registered — pick another name")
    _KINDS[name] = factory
    if builtin:
        _BUILTIN_KINDS.add(name)
    return factory


class KindAPI:
    """Registration surface handed to plugins: register(api) → api.kind()."""

    def __init__(self, source=""):
        self.source = source

    def kind(self, name, cls):
        if not callable(cls) or not hasattr(cls, "invoke"):
            raise AILangRuntimeError(
                f"kind {name!r}: cls must be a class exposing "
                f".invoke(task, source) and .last_insight")
        register_kind(name, cls)
        if self.source:
            _KIND_SOURCES[name] = self.source


class PyAgent:
    """Deterministic agent: run: "module.func" — zero LLM, zero cents."""

    def __init__(self, name="py", run="", config=None):
        self.name, self.run, self.config = name, run, config
        self.last_insight = None

    def invoke(self, task, source="human"):
        mod, _, attr = self.run.rpartition(".")
        if not mod or not attr:
            raise AILangRuntimeError(
                f"py agent {self.name!r}: run must be \"module.func\", "
                f"got {self.run!r}")
        _ensure_import_path(os.getcwd(), Path(__file__).resolve().parent)
        try:
            obj = importlib.import_module(mod)
        except ImportError as exc:
            raise AILangRuntimeError(
                f"py agent {self.name!r}: cannot import {mod!r}: "
                f"{exc}") from exc
        for part in attr.split("."):
            obj = getattr(obj, part)
        result = obj(task)
        if not isinstance(result, str):
            result = json.dumps(result, ensure_ascii=False, default=str)
        self.last_insight = result
        return result


def _chat_kind(fields, config):
    f = dict(fields)
    missing = [k for k in ("model", "role") if k not in f]
    if missing:
        raise AILangRuntimeError(
            f"Agent of kind 'chat': missing field(s) {missing}")
    raw = f.get("reasoning", "false")
    if isinstance(raw, str):
        if raw.lower() not in ("true", "false"):
            raise AILangRuntimeError(
                f"Agent of kind 'chat': reasoning must be \"true\" or "
                f"\"false\", got {raw!r}")
        reasoning = raw.lower() == "true"
    else:
        reasoning = bool(raw)
    return AIAgent(
        name=str(f.get("model", "agent")), engine=f["model"],
        prompt=f["role"],
        temperature=float(f["temp"]) if "temp" in f else 0.7,
        config=config, in_schema=f.get("in"), out_schema=f.get("out"),
        tools=list(f.get("tools", [])),
        timeout=float(f["timeout"]) if "timeout" in f else None,
        casts=int(f["casts"]) if "casts" in f else 2,
        endpoint=f.get("endpoint"), env=f.get("env"),
        reasoning=reasoning,
        judge=str(f["judge"]) if "judge" in f else None,
        budget_usd=float(f["budget"]) if "budget" in f else None,
        salary_usd=float(f["salary"]) if "salary" in f else None,
        window=int(f["window"]) if "window" in f else None,
        price_in=float(f["price_in"]) if "price_in" in f else None,
        price_out=float(f["price_out"]) if "price_out" in f else None)


def _py_kind(fields, config):
    run = fields.get("run")
    if not run:
        raise AILangRuntimeError(
            "Agent of kind 'py': missing field run: \"module.func\"")
    return PyAgent(run=str(run), config=config)


def _human_kind(fields, config):
    return Human(print, input)


def _echo_kind(fields, config):
    return Echo()


register_kind("chat", _chat_kind, builtin=True)
register_kind("py", _py_kind, builtin=True)
register_kind("human", _human_kind, builtin=True)
register_kind("echo", _echo_kind, builtin=True)


class CostLedger:
    def __init__(self):
        self._lock = threading.Lock()
        self.spent = 0.0

    def add(self, v):
        with self._lock:
            self.spent += v


def _text(v) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, TypedArtifact):
        return v.as_text()
    if isinstance(v, Attachment):
        return f"<<attachment:{v.path}>>"
    if hasattr(v, "last_insight") and v.last_insight is not None:
        return _text(v.last_insight)
    if hasattr(v, "last_insight"):
        return f"({getattr(v, 'name', 'agent')} has no insight yet)"
    return str(v)


def _resolve_field(value, dotted: str):
    """Resolve 'a.b.c' through an artifact or a fork of artifacts.

    A ForkValue step picks a branch by agent name; an artifact step picks
    a schema field. Raises KeyError when the path does not exist.
    """
    current = value
    for part in str(dotted).split("."):
        if isinstance(current, TypedArtifact):
            current = current.get(part)      # may raise KeyError/TypeError
        elif isinstance(current, ForkValue):
            if part not in current.parts:
                raise KeyError(dotted)
            current = current.parts[part]
        elif isinstance(current, dict):
            if part not in current:
                raise KeyError(dotted)
            current = current[part]
        else:
            raise KeyError(dotted)
    return current


def _gtext(g) -> str:
    if isinstance(g, Measure):
        return f"{g.kind}({g.target})"
    if isinstance(g, Name):
        return g.id
    if isinstance(g, str):
        return g
    if g.__class__.__name__ == "Str":
        return g.value
    if hasattr(g, "op"):
        r = g.right.id if hasattr(g.right, "id") else g.right
        return f"{_gtext(g.left)} {g.op} {r}"
    if hasattr(g, "parts"):
        j = " and " if isinstance(g, GateAnd) else " or "
        return j.join(_gtext(p) for p in g.parts)
    return "?"


_SENTINEL = object()
# Sources that mean "this call is an audit verdict, not an artifact".
_AUDIT_SOURCES = frozenset({"gate", "until", "match"})


def _located(err: Exception, st) -> Exception:
    """Prefix a runtime error with the line of the statement that raised."""
    line = getattr(st, "_line", None)
    if not line or getattr(err, "_located", False):
        return err
    try:
        wrapped = type(err)(f"line {line}: {err}")
    except Exception:                     # noqa: BLE001 — exotic subclasses
        return err
    wrapped._located = True
    return wrapped

_VERDICT_HEAD = re.compile(r"^[\s*_`#>\"'\-]*([A-Za-z]+)")


def _verdict_pass(text) -> bool:
    """True only for an unambiguous PASS at the start of the verdict.

    Tolerates markdown/quoting noise ("**PASS** — ship it") and refuses
    anything ambiguous ("probably PASS") — a gate fails closed."""
    m = _VERDICT_HEAD.match(str(text))
    return bool(m) and m.group(1).upper() == "PASS"


def _stdout_writer(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


class AsyncInterpreter:
    def __init__(self, config=None, out=print, input_fn=input,
                 journal=None, replay=None, stream_out=None):
        self.config = config or AIlangConfig()
        self.out = out
        self.stream_out = stream_out or _stdout_writer
        self.journal, self.replay = journal, replay
        self.env: dict = {}
        self.ledger = CostLedger()
        self.gates: dict = {}          # named criteria: gate strict = …
        self.policy: dict = {}         # file-level agent defaults
        self._salary_spent: dict = {}  # agent -> USD spent this run
        self.declared: set[str] = set()
        self._last_comp = Completion(text="")
        self._use_stack: list[str] = []
        self._source_dir: str | None = None
        self.builtins = {"human": Human(out, input_fn), "echo": Echo()}

    # ── program ──────────────────────────────────────────
    async def arun(self, source: str):
        last = None
        stmts = parse_located(source)
        for st in stmts:                   # the policy applies to the file
            if isinstance(st, Policy):
                self.apply_policy(st)
        for st in stmts:
            try:
                last = await self.aexec(st)
            except AILangRuntimeError as e:
                raise _located(e, st) from e
        return last

    async def arun_image(self, image: dict):
        """Execute a compiled image: no parsing, no analysis."""
        if not isinstance(image, dict) \
                or image.get("format") != "ailang-image":
            raise AILangRuntimeError("not an AI-lang image")
        stamp = image.get("grammar")
        if stamp and stamp != grammar_fingerprint():
            raise AILangRuntimeError(
                f"image was built for grammar {stamp}, this runtime has "
                f"{grammar_fingerprint()} - rebuild it")
        for plugin in image.get("plugins") or []:
            if not Path(plugin).exists():
                raise AILangRuntimeError(
                    f"image needs plugin {plugin!r} - not found")
            self._load_plugin(plugin)
        stmts = dict_to_ast(image.get("statements") or [])
        for st, line in zip(stmts, image.get("lines") or []):
            if line:
                st._line = line
        last = None
        for st in stmts:
            try:
                last = await self.aexec(st)
            except AILangRuntimeError as e:
                raise _located(e, st) from e
        return last

    async def aexec(self, st):
        if isinstance(st, Assignment):
            value = (await self._make_agent(st.name, st.expr)
                     if isinstance(st.expr, AgentDecl)
                     else await self.aeval(st.expr))
            self.env[st.name] = value
            self.declared.add(st.name)
            return value
        if isinstance(st, Print):
            self.out(_text(await self.aeval(st.expr)))
            return None
        if isinstance(st, Assert):
            if not self._eval_cond(st.cond):
                raise AILangRuntimeError(
                    f"assertion failed: {_gtext(st.cond)}")
            return True
        if isinstance(st, Until):
            return await self._until(st)
        if isinstance(st, Match):
            return await self._match(st)
        if isinstance(st, SchemaDecl):
            self.env[st.name] = Schema(
                st.name, {n: Field(TypeExpr.from_ast(t), opt)
                          for n, t, opt in st.fields},
                invariants=dict(getattr(st, "invariants", {}) or {}))
            self.declared.add(st.name)
            return None
        if isinstance(st, ToolDecl):
            self.env[st.name] = Tool(st.name, st.target,
                                     {n: TypeExpr.from_ast(t)
                                      for n, t in st.params}, st.desc)
            self.declared.add(st.name)
            return None
        if isinstance(st, FlowDecl):
            self.env[st.name] = FlowValue(st.name, st.param, st.body)
            self.declared.add(st.name)
            return None
        if isinstance(st, Policy):
            self.apply_policy(st)          # already applied in arun
            return None
        if isinstance(st, GateDecl):
            if st.name in self.gates:
                raise AILangRuntimeError(
                    f"gate {st.name!r} is already declared (AIL039)")
            self.gates[st.name] = st.criterion
            self.declared.add(st.name)
            return None
        if isinstance(st, Patch):
            return self._patch(st)
        if isinstance(st, UseStmt):
            await self._use(st.path)
            return None
        return await self.aeval(st)

    # ── expressions ──────────────────────────────────────
    async def aeval(self, node):
        if isinstance(node, Name):
            if node.id in self.env and node.id in self.builtins:
                log.warning("name %r shadows a builtin agent", node.id)
            if node.id in self.env:
                return self.env[node.id]
            if node.id in self.builtins:
                return self.builtins[node.id]
            raise AILangRuntimeError(f"Unknown name: {node.id!r}")
        if isinstance(node, Attr):
            base = self.env.get(node.base)
            if base is None:
                raise AILangRuntimeError(f"Unknown name: {node.base!r}")
            target = (base if isinstance(base, (TypedArtifact, ForkValue))
                      else getattr(base, "last_insight", None))
            try:
                return _resolve_field(target, node.field_name)
            except (KeyError, TypeError):
                raise AILangRuntimeError(
                    f"{node.base}.{node.field_name}: no such field") from None
        if isinstance(node, Str):
            return self._interpolate(node.value)
        if isinstance(node, AgentDecl):
            return await self._make_agent(f"anon@{id(node):x}", node)
        if isinstance(node, Call):
            callee = self.env.get(node.name)
            if isinstance(callee, FlowValue):
                return await self._call_flow(callee, node)
            if node.name == "file" and "file" not in self.env:
                p = _text(await self.aeval(node.arg))
                return Attachment(path=p)
            r = self._runnable(callee if callee is not None
                               else self.builtins.get(node.name), "()")
            return await self._ainvoke(r, await self.aeval(node.arg), "call")
        if isinstance(node, Match):
            return await self._match(node)
        if isinstance(node, If):
            return await self._aif(node)
        if isinstance(node, Merge):
            agents = [self._agent(await self.aeval(p)) for p in node.parts]
            return reduce(self._merge, agents)
        if isinstance(node, Sequence):
            return Chain(*[self._runnable(await self.aeval(p), "&")
                           for p in node.parts])
        if isinstance(node, Parallel):
            return Fork(*[self._runnable(await self.aeval(p), "||")
                          for p in node.parts])
        if isinstance(node, Fallback):
            return Backup(*[self._runnable(await self.aeval(p), "??")
                            for p in node.parts])
        if isinstance(node, Race):
            return RaceRunner(self._runnable(await self.aeval(node.inner),
                                             "race"))
        if isinstance(node, Flow):
            return (await self._aflow(node))[1]
        if isinstance(node, (Gate, Comparison, GateAnd, GateOr)):
            raise AILangRuntimeError(
                "A gate (`? …`) only makes sense inside a flow `>>`")
        raise AILangRuntimeError(f"Unknown AST node: {type(node).__name__}")

    # ── the single dispatch point ────────────────────────
    async def _ainvoke(self, r, task, source):
        if isinstance(r, AIAgent):
            if r.timeout is not None:
                try:
                    return await asyncio.wait_for(
                        self._ainvoke_agent(r, task, source), r.timeout)
                except asyncio.TimeoutError as e:
                    raise TimeoutError(
                        f"agent {r.name!r}: deadline {r.timeout}s "
                        f"exceeded (clause timeout)") from e
            return await self._ainvoke_agent(r, task, source)
        if isinstance(r, Chain):
            x = task
            for a in r.agents:
                x = await self._ainvoke(a, x, source)
            r.last_insight = x
            return x
        if isinstance(r, Fork):
            outs = await asyncio.gather(*[
                self._ainvoke(a, task, source) for a in r.agents])
            parts = {getattr(a, "name", f"#{i}"): o
                     for i, (a, o) in enumerate(zip(r.agents, outs), 1)}
            r.last_insight = ForkValue(
                "\n\n---\n\n".join(f"[{name}] {_text(o)}"
                                    for name, o in parts.items()), parts)
            return r.last_insight
        if isinstance(r, Backup):
            errors = []
            for a in r.agents:
                try:
                    r.last_insight = await self._ainvoke(a, task, source)
                    return r.last_insight
                except Exception as exc:      # noqa: BLE001 — ?? semantics
                    errors.append(f"{getattr(a, 'name', '?')}: {exc}")
            raise AILangRuntimeError(
                "all branches of ?? failed:\n" + "\n".join(errors))
        if isinstance(r, RaceRunner):
            tasks = [asyncio.ensure_future(
                self._ainvoke(a, task, source)) for a in r.fork.agents]
            pending = set(tasks)
            try:
                last = None
                while pending:
                    done, pending = await asyncio.wait(
                        pending, return_when=asyncio.FIRST_COMPLETED)
                    # declaration order breaks ties when several branches
                    # finish within the same event-loop batch
                    for t in tasks:
                        if t not in done:
                            continue
                        try:
                            r.last_insight = t.result()
                            return r.last_insight
                        except Exception as exc:      # noqa: BLE001
                            last = exc
                raise last or AILangRuntimeError("race: all agents failed")
            finally:
                for t in pending:
                    t.cancel()
        if hasattr(r, "invoke"):
            r.last_insight = await asyncio.to_thread(r.invoke, task, source)
            return r.last_insight
        raise AILangRuntimeError(f"Cannot invoke {type(r).__name__}")

    # ── agent invocation pipeline ────────────────────────
    async def _ainvoke_agent(self, agent: AIAgent, task, source):
        """One call = one wage slip: casts and repairs are billed to it."""
        before = self.ledger.spent
        value = await self._ainvoke_agent_inner(agent, task, source)
        spent = self.ledger.spent - before
        cap = getattr(agent, "budget_usd", None)
        if cap is not None and spent > cap + 1e-9:
            raise BudgetError(
                f"agent {agent.name!r}: wage cap ${cap:.6f} "
                f"exceeded — this call cost ${spent:.6f} "
                f"(clause budget)")
        salary = getattr(agent, "salary_usd", None)
        if salary is not None:
            total = self._salary_spent.get(agent.name, 0.0) + spent
            self._salary_spent[agent.name] = total
            if total > salary + 1e-9:
                raise SalaryError(
                    f"agent {agent.name!r}: salary ${salary:.6f} exhausted "
                    f"— the run has spent ${total:.6f} on it "
                    f"(clause salary)")
        return value

    async def _ainvoke_agent_inner(self, agent: AIAgent, task, source):
        registry = {k: v for k, v in self.env.items()
                    if isinstance(v, Schema)}
        user = _text(task)
        self._check_in_schema(agent, task, source, registry)
        agent.memory.log(source, "input", user)
        t0 = time.monotonic()
        audit = source in _AUDIT_SOURCES
        if self.replay is not None:
            rec = self.replay.lookup(agent.name, digest(user))
            if rec is not None:
                value = rec if audit else self._finalize(agent, rec, registry)
                agent.last_insight = value
                agent.memory.log(agent.name, "output", _text(value))
                if self.journal:
                    self.journal.log(agent.name, agent.engine, digest(user),
                                     _text(value), Completion(text=rec), 0,
                                     f"replay:{source}",
                                     dry=self.config.dry_run)
                return value
        if (self.config.budget_usd is not None
                and self.ledger.spent >= self.config.budget_usd):
            raise BudgetError(f"budget ${self.config.budget_usd} exhausted "
                              f"(${self.ledger.spent:.4f} spent)")
        value = await self._produce(agent, user, registry,
                                    cast=not audit,
                                    stream=bool(self.config.stream
                                                and not audit))
        agent.last_insight = value
        agent.memory.log(agent.name, "output", _text(value))
        if self.journal:
            self.journal.log(agent.name, agent.engine, digest(user),
                             _text(value), self._last_comp,
                             int((time.monotonic() - t0) * 1000),
                             note=str(source), dry=self.config.dry_run)
        return value

    async def _call(self, agent, system, user, history, stream=False):
        cfg = self.config
        reasoning = getattr(agent, "reasoning", False)
        if reasoning:
            # o1/o3/R1-style models: the system prompt folds into the
            # user message and temperature is pinned to 1
            user, system = f"{system}\n\n{user}", ""
        temp = 1.0 if reasoning else agent.temperature

        if stream and getattr(cfg, "stream", False):
            def _sync_stream():
                parts = []
                agent._provider.last_usage = None
                for delta in agent._provider.stream_detailed(
                        system, user, history, temp, cfg):
                    parts.append(delta)
                    self.stream_out(delta)
                self.stream_out("\n")
                usage = getattr(agent._provider, "last_usage", None)
                if usage is None:
                    return Completion(text="".join(parts))
                self.ledger.add(usage.cost_usd)
                return Completion(text="".join(parts),
                                  tokens_in=usage.tokens_in,
                                  tokens_out=usage.tokens_out,
                                  cost_usd=usage.cost_usd)
            self._last_comp = await asyncio.to_thread(_sync_stream)
            return self._last_comp

        def _sync():
            comp = agent._provider.complete_detailed(
                system, user, history, temp, cfg)
            self.ledger.add(comp.cost_usd)   # thread-side: survives cancel
            return comp
        self._last_comp = await asyncio.to_thread(_sync)
        return self._last_comp

    async def _produce(self, agent, user, registry, cast=True, stream=False):
        system = agent.prompt
        if agent.out_schema:
            system += build_contract(agent.out_schema, registry)
        if agent.tools:
            system += build_toolkit(agent.tools, registry)
        history = agent.memory.as_history(self.config.memory_window,
                                          drop_last_role="input")
        text, steps = user, 0
        while True:
            comp = await self._call(agent, system, text, history,
                                    stream=stream)
            call = parse_tool_call(comp.text)
            if call and steps < 3:
                name, args = call
                tool = next((t for t in agent.tools if t.name == name), None)
                if tool is None:
                    text += f"\n[TOOL {name}] unknown tool"
                elif self.config.dry_run:
                    text += (f"\n[TOOL {name}] [dry-run: tool not "
                             f"executed, args={json.dumps(args)}]")
                else:
                    text += "\n[TOOL %s] " % name + await asyncio.to_thread(
                        tool.run, args, registry)
                steps += 1
                continue
            text = comp.text
            if not cast:
                return text
            return await self._cast(agent, user, text, registry)

    async def _cast(self, agent, task, text, registry):
        if not agent.out_schema:
            return text
        t = TypeExpr("schema", name=agent.out_schema.name)
        system = agent.prompt + build_contract(agent.out_schema, registry)
        errs = []
        for attempt in range(agent.casts + 1):
            value, err = parse_json_output(text)
            errs = validate(value, t, registry) if value is not None else [err]
            if not errs:
                return TypedArtifact(value, agent.out_schema, registry)
            if attempt >= agent.casts:
                break
            comp = await self._call(
                agent, system,
                f"{task}\n[CONTRACT VIOLATION] {'; '.join(errs)}\n"
                f"Your previous answer:\n{text}\n"
                f"Return ONLY corrected JSON.", [])
            text = comp.text
        raise ArtifactError(f"agent {agent.name!r} failed its out-contract "
                            f"after {agent.casts} casts: {'; '.join(errs)}")

    def _finalize(self, agent, text, registry):
        if not agent.out_schema:
            return text
        t = TypeExpr("schema", name=agent.out_schema.name)
        value, err = parse_json_output(text)
        errs = validate(value, t, registry) if value is not None else [err]
        if errs:
            raise ArtifactError(f"replay record for {agent.name!r} violates "
                                f"the current contract — schema drift?")
        return TypedArtifact(value, agent.out_schema, registry)

    def _check_in_schema(self, agent, task, source, registry):
        if not agent.in_schema:
            return
        t = TypeExpr("schema", name=agent.in_schema.name)
        if isinstance(task, TypedArtifact):
            data, produced = task.data, task.schema.name
            # A subtype must be structurally usable where the parent is
            # expected; the compiler guarantees it for checked programs.
        else:
            data, err = parse_json_output(_text(task))
            produced = "text"
            if data is None:
                raise ArtifactError(
                    f"edge {source!r} -> {agent.name!r}: consumer expects "
                    f"in:{agent.in_schema.name}, upstream sent non-JSON text"
                    f" (clause in)")
        errs = validate(data, t, registry)
        if errs:
            why = (f"{produced} is not a subtype of "
                   f"{agent.in_schema.name}")
            raise ArtifactError(
                f"edge {source!r} -> {agent.name!r}: producer yields "
                f"{produced}, consumer expects in:{agent.in_schema.name} "
                f"({why}; {'; '.join(errs)}) (clause in)")

    # ── flows & gates ────────────────────────────────────
    async def _aflow(self, node):
        src = await self.aeval(node.source)
        if isinstance(src, (str, Attachment, TypedArtifact)):
            cursor = [None, _text(src)]
        elif hasattr(src, "last_insight"):
            if src.last_insight is None:
                raise AILangRuntimeError(
                    f"{getattr(src, 'name', 'agent')!r} has no insight yet — "
                    'feed it first: "text" >> agent')
            cursor = [src, src.last_insight]
        else:
            raise AILangRuntimeError(
                "A flow source must be text or an agent")
        return await self._run_targets(node.targets, cursor)

    async def _run_targets(self, targets, cursor):
        for t in targets:
            if isinstance(t, Gate):
                if cursor[0] is None:
                    raise AILangRuntimeError(
                        "A gate must follow an agent, not raw text")
                auditor = self._runnable(await self.aeval(t.unit), "?")
                cursor = [cursor[0], await self._agate(
                    auditor, t.criterion, cursor[0])]
            elif isinstance(t, Fallback) and any(
                    isinstance(p, Gate) for p in t.parts):
                cursor = await self._aescalation(t.parts, cursor)
            else:
                target = self._runnable(await self.aeval(t), ">>")
                insight = await self._ainvoke(
                    target, cursor[1],
                    getattr(cursor[0], "name", "text") if cursor[0]
                    else "human")
                cursor = [target, insight]
        return cursor

    async def _aescalation(self, parts, cursor):
        """`x >> a? "crit" ?? b`: a answers x, the gate judges it; on
        failure the fallback receives the ORIGINAL task, not a's answer.
        Deterministic gates never call a model."""
        errors = []
        source = (getattr(cursor[0], "name", "text") if cursor[0]
                  else "human")
        for part in parts:
            try:
                if isinstance(part, Gate):
                    unit = self._runnable(await self.aeval(part.unit), "?")
                    await self._ainvoke(unit, cursor[1], source)
                    answer = unit.last_insight
                    det_ok, feedback, semantic = self._eval_gate(
                        part.criterion, unit)
                    if not det_ok:
                        raise AILangRuntimeError(
                            f"deterministic check failed: {feedback}")
                    if semantic:
                        semantic = self._interpolate(semantic)
                        judge = getattr(unit, "judge", None)
                        auditor = (self._resolve_judge(unit, judge)
                                   if judge else unit)
                        verdict = await self._ainvoke(auditor, (
                            f"AUDIT. Criterion: {semantic!r}\n"
                            f"Artifact:\n{_text(answer)}\n\n"
                            f"First line: PASS or FAIL, then reasoning."),
                            "gate")
                        unit.last_insight = answer
                        if not _verdict_pass(verdict):
                            raise AILangRuntimeError(
                                f"agent {getattr(unit, 'name', '?')!r}: "
                                f"gate verdict: "
                                + verdict.strip().splitlines()[0][:80])
                    return [unit, answer]
                target = self._runnable(await self.aeval(part), "??")
                return [target,
                        await self._ainvoke(target, cursor[1], source)]
            except Exception as exc:      # noqa: BLE001 — ?? semantics
                label = (f"gate {_gtext(part.criterion)}"
                         if isinstance(part, Gate)
                         else getattr(part, "id", part))
                errors.append(f"{label!r}: {exc}")
        raise AILangRuntimeError(
            "all branches of ?? failed:\n" + "\n".join(errors))

    async def _agate(self, auditor, gexpr, producer):
        gexpr = self._resolve_gate(gexpr)
        judge = getattr(producer, "judge", None)
        if judge and auditor is producer:
            auditor = self._resolve_judge(producer, judge)
        for attempt in range(1, 4):
            det_ok, feedback, semantic = self._eval_gate(gexpr, producer)
            if not det_ok:
                await self._arefine(producer,
                                    f"deterministic check failed: {feedback}")
                continue
            if semantic:
                semantic = self._interpolate(semantic)
                verdict = await self._ainvoke(auditor, (
                    f"AUDIT (attempt {attempt}). Criterion: "
                    f"{semantic!r}\nArtifact:\n{_text(producer.last_insight)}"
                    f"\n\nFirst line: PASS or FAIL, then reasoning; "
                    f"on FAIL — concrete fixes."), "gate")
                if not _verdict_pass(verdict):
                    await self._arefine(producer, verdict)
                    continue
            return producer.last_insight
        raise AILangRuntimeError(
            f"agent {getattr(producer, 'name', '?')!r}: gate not passed "
            f"within 3 attempts, clause gate {_gtext(gexpr)!r} "
            f"(auditor: {getattr(auditor, 'name', '?')})")

    def _resolve_gate(self, criterion, depth: int = 0):
        """`qa? strict` stands for the declared criterion, recursively."""
        if depth > 20:
            raise AILangRuntimeError(
                "gate references nest too deeply - check for a cycle")
        if isinstance(criterion, Name):
            target = self.gates.get(criterion.id)
            if target is None:
                raise AILangRuntimeError(
                    f"unknown gate {criterion.id!r} - declare it first: "
                    f'gate {criterion.id} = "criterion"')
            return self._resolve_gate(target, depth + 1)
        if isinstance(criterion, GateAnd):
            return GateAnd([self._resolve_gate(p, depth + 1)
                            for p in criterion.parts])
        if isinstance(criterion, GateOr):
            return GateOr([self._resolve_gate(p, depth + 1)
                           for p in criterion.parts])
        return criterion

    def _eval_gate(self, gexpr, producer):
        """→ (det_ok, det_feedback, semantic_text | None)."""
        gexpr = self._resolve_gate(gexpr)
        if isinstance(gexpr, str):
            return True, None, gexpr
        if gexpr.__class__.__name__ == "Str":
            return True, None, gexpr.value
        art = producer.last_insight
        if isinstance(gexpr, Comparison):
            if not isinstance(art, (TypedArtifact, ForkValue)):
                return (False, "a deterministic gate needs a typed "
                               "producer (declare out: schema)", None)
            target = (gexpr.left.target if isinstance(gexpr.left, Measure)
                      else gexpr.left)
            if isinstance(gexpr.left, Measure):
                left, problem = _measure_target(
                    art.data if isinstance(art, TypedArtifact) else dict(art),
                    target)
                if problem is not None:
                    return False, problem, None
            else:
                try:
                    left = _resolve_field(art, target)
                except (KeyError, TypeError):
                    return False, f"unknown field {target!r}", None
            if isinstance(gexpr.left, Measure):
                value, problem = _aggregate(gexpr.left.kind, left)
                if problem is not None:
                    return False, problem, None
                left = value
            right = gexpr.right
            if isinstance(right, Name):
                rid = right.id
                if isinstance(art, TypedArtifact) and rid in art.data:
                    right = art.data[rid]
                elif rid in self.env:
                    v = self.env[rid]
                    right = (v.data if isinstance(v, TypedArtifact)
                             else getattr(v, "last_insight", v))
                else:
                    return False, f"unknown reference {rid!r} in gate", None
            ops = {"<": operator.lt, ">": operator.gt, "<=": operator.le,
                   ">=": operator.ge, "==": operator.eq, "!=": operator.ne}
            ok = ops[gexpr.op](left, right)
            return (ok, None if ok else
                    f"{gexpr.left}={left!r} failed {gexpr.op} {right!r}",
                    None)
        if isinstance(gexpr, GateAnd):
            sems = []
            for part in gexpr.parts:
                ok, fb, sem = self._eval_gate(part, producer)
                if not ok:
                    return False, fb, None
                if sem:
                    sems.append(sem)
            return True, None, " and ".join(sems) if sems else None
        if isinstance(gexpr, GateOr):
            feedbacks, sems = [], []
            for part in gexpr.parts:
                ok, fb, sem = self._eval_gate(part, producer)
                if ok and sem:
                    sems.append(sem)
                elif ok and not sems:
                    return True, None, None
                elif not ok:
                    feedbacks.append(fb)
            if sems:
                return True, None, " or ".join(sems)
            return False, "; ".join(feedbacks), None
        raise AILangRuntimeError(f"Bad gate expression: {gexpr!r}")

    def _resolve_judge(self, producer, judge_name):
        """An agent may name an independent auditor: judge: qa."""
        target = self.env.get(judge_name)
        if target is None:
            raise AILangRuntimeError(
                f"judge {judge_name!r} of "
                f"{getattr(producer, 'name', '?')!r} is not defined")
        return self._runnable(target, "judge")

    async def _arefine(self, producer, feedback):
        return await self._ainvoke(
            producer,
            f"Your artifact failed a check.\n{feedback}\n"
            f"Produce a revised artifact.", "refine")

    # ── semantic control flow ────────────────────────────
    async def _until(self, st):
        """`until a? crit max N { … }` - a deterministic criterion is
        evaluated here, not handed to a model; only a semantic one is
        audited."""
        if not isinstance(st.gate, Gate):
            raise AILangRuntimeError(
                'until needs a gate: until agent? "criterion" { … }')
        auditor = self._runnable(await self.aeval(st.gate.unit), "until")
        criterion = self._resolve_gate(st.gate.criterion)
        saved = self.env.get("feedback", _SENTINEL)
        self.env["feedback"] = ""
        try:
            value = None
            for attempt in range(1, st.max_iter + 1):
                value = None
                for item in st.body:
                    value = await self.aexec(item)
                det_ok, feedback, semantic = self._eval_gate(
                    criterion, _ValueFrame(value))
                if semantic:
                    text = self._interpolate(semantic)
                    verdict = await self._ainvoke(auditor, (
                        f"AUDIT (attempt {attempt}/{st.max_iter}). "
                        f"Criterion: {text!r}\nArtifact:\n"
                        f"{_text(value)}\n\nFirst line: PASS or FAIL, "
                        f"then reasoning."), "until")
                    if _verdict_pass(verdict):
                        return value
                    self.env["feedback"] = verdict
                    continue
                if det_ok:
                    return value
                if not isinstance(value, (TypedArtifact, ForkValue)):
                    raise AILangRuntimeError(f"until: {feedback}")
                self.env["feedback"] = feedback or "deterministic check failed"
            last = self.env.get("feedback") or ""
            raise AILangRuntimeError(
                f"until: gate not passed in {st.max_iter} attempts"
                + (f" (last: {last})" if last else ""))
        finally:
            if saved is _SENTINEL:
                self.env.pop("feedback", None)
            else:
                self.env["feedback"] = saved

    async def _match(self, st):
        router = self._runnable(await self.aeval(Name(st.router.name)),
                                "match")
        payload = _text(await self.aeval(st.router.arg))
        labels = [c.label for c in st.cases if c.label]
        if not labels:
            raise AILangRuntimeError("match needs at least one labeled case")
        reply = await self._ainvoke(
            router, f"{payload}\n\n[ROUTING] Classify the text above into "
            f"exactly one of: {', '.join(labels)}. "
            f"Reply with the label only.", "match")
        choice = reply.strip().strip(".").lower()
        case = next((c for c in st.cases
                     if c.label and c.label.lower() == choice), None)
        if case is None:
            case = next((c for c in st.cases if c.label is None), None)
        if case is None:
            raise AILangRuntimeError(
                f"router replied {choice!r}: no such case and no default _")
        return (await self._run_targets(case.chain, [None, payload]))[1]

    # ── deterministic branching ──────────────────────────
    def _cond_ref(self, dotted: str):
        """Resolve a condition operand through the environment."""
        parts = str(dotted).split(".")
        head = parts[0]
        if head in self.env:
            value = self.env[head]
        elif head in self.builtins:
            value = self.builtins[head]
        else:
            raise AILangRuntimeError(f"if: unknown name {head!r}")
        if len(parts) > 1:
            art = (value if isinstance(value, (TypedArtifact, ForkValue))
                   else getattr(value, "last_insight", None))
            if not isinstance(art, (TypedArtifact, ForkValue)):
                raise AILangRuntimeError(
                    f"if: {head!r} carries no artifact fields")
            try:
                return _resolve_field(art, ".".join(parts[1:]))
            except (KeyError, TypeError):
                raise AILangRuntimeError(
                    f"if: no field {dotted!r}") from None
        if isinstance(value, TypedArtifact):
            raise AILangRuntimeError(
                f"if: compare a field of {head!r}, not the whole artifact")
        if isinstance(value, AIAgent):
            return getattr(value, "last_insight", None)
        return value

    def _cond_measure(self, measure):
        """`sum(prices)` inside an `if` - read from the nearest binding."""
        value = self._cond_ref(measure.target)
        number, problem = _aggregate(measure.kind, value)
        if problem is not None:
            raise AILangRuntimeError(f"if: {problem}")
        return number

    def _eval_cond(self, cond) -> bool:
        if isinstance(cond, Comparison):
            left = (self._cond_measure(cond.left)
                    if isinstance(cond.left, Measure)
                    else self._cond_ref(cond.left))
            right = cond.right
            if isinstance(right, Name):
                right = self._cond_ref(right.id)
            ops = {"<": operator.lt, ">": operator.gt, "<=": operator.le,
                   ">=": operator.ge, "==": operator.eq, "!=": operator.ne}
            try:
                return bool(ops[cond.op](left, right))
            except TypeError as e:
                raise AILangRuntimeError(
                    f"if: cannot compare {left!r} {cond.op} {right!r}: "
                    f"{e}") from None
        if isinstance(cond, GateAnd):
            return all(self._eval_cond(p) for p in cond.parts)
        if isinstance(cond, GateOr):
            return any(self._eval_cond(p) for p in cond.parts)
        raise AILangRuntimeError(f"if: bad condition {_gtext(cond)!r}")

    async def _aif(self, node):
        branch = node.then if self._eval_cond(node.cond) else node.other
        if branch is None:
            return None
        value = None
        for st in branch:
            value = await self.aexec(st)
        return value

    async def _call_flow(self, fv: FlowValue, node):
        arg = await self.aeval(node.arg)
        saved = self.env.get(fv.param, _SENTINEL)
        self.env[fv.param] = arg
        try:
            value = None
            for s in fv.body:
                value = await self.aexec(s)
            return value
        finally:
            if saved is _SENTINEL:
                self.env.pop(fv.param, None)
            else:
                self.env[fv.param] = saved

    @staticmethod
    def _field_at(schema, dotted, registry):
        """Resolve a (possibly nested) field path against a schema."""
        parts = dotted.split(".")
        sch, found = schema, None
        for i, part in enumerate(parts):
            found = sch.fields.get(part)
            if found is None:
                return None
            if i < len(parts) - 1:
                if found.type.kind != "schema":
                    return None
                sch = registry.get(found.type.name)
                if sch is None:
                    return None
        return found

    @staticmethod
    def _set_path(data, dotted, value):
        parts = dotted.split(".")
        node = data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    def _patch(self, st):
        art = self.env.get(st.target)
        if isinstance(art, Schema):
            return self._patch_schema(art, st)
        if not isinstance(art, TypedArtifact):
            raise AILangRuntimeError(
                f"patch: {st.target!r} is not a typed artifact")
        for fname, v in st.fields:
            f = self._field_at(art.schema, fname, art.registry)
            if f is None:
                raise AILangRuntimeError(
                    f"patch: schema {art.schema.name} has no field "
                    f"{fname!r} (clause in)")
            errs = validate(v, f.type, art.registry, f"$.{fname}")
            if errs:
                raise AILangRuntimeError(
                    f"patch {st.target}.{fname}: {'; '.join(errs)}")
            self._set_path(art.data, fname, v)
        t = TypeExpr("schema", name=art.schema.name)
        errs = validate(art.data, t, art.registry)
        if errs:
            raise AILangRuntimeError(
                f"patch {st.target}: {'; '.join(errs)} (clause where)")
        return art

    def _patch_schema(self, schema: Schema, st):
        """`patch Summary { is_a: Text }` - a compile-time qualification."""
        parents = list(schema.parents)
        for fname, value in st.fields:
            if fname != "is_a":
                raise AILangRuntimeError(
                    f"patch {schema.name}: a schema patch only accepts "
                    f"is_a: <schema>, got {fname!r} (AIL100)")
            parent = str(value)
            if parent not in self.env:
                raise AILangRuntimeError(
                    f"patch {schema.name}: unknown parent schema "
                    f"{parent!r} (AIL031)")
            if parent == schema.name:
                raise AILangRuntimeError(
                    f"patch {schema.name}: a schema cannot inherit from "
                    f"itself (AIL030)")
            if parent not in parents:
                parents.append(parent)
                _SUBTYPE_EDGES.add((schema.name, parent))
        schema.parents = tuple(parents)
        return schema

    # ── merge (+), agents, use ───────────────────────────
    def _merge(self, a: AIAgent, b: AIAgent) -> AIAgent:
        named = [x.out_schema for x in (a, b) if x.out_schema]
        if len(named) == 2 and named[0].name != named[1].name:
            raise AILangRuntimeError(
                "`+` requires equal out-schemas on both parents")
        out = named[0] if named else None
        provider = resolve_provider(self.config.meta_engine, self.config)
        comp = provider.complete_detailed(
            META_SYSTEM,
            ALIGNMENT_TEMPLATE.format(name_a=a.name, prompt_a=a.prompt,
                                      name_b=b.name, prompt_b=b.prompt),
            [], 0.3, self.config)
        self.ledger.add(comp.cost_usd)
        return AIAgent(
            name=f"{a.name}+{b.name}", type="hybrid",
            engine=self.config.meta_engine, prompt=comp.text.strip(),
            temperature=round((a.temperature + b.temperature) / 2, 2),
            config=self.config, memory=a.memory.merge(b.memory),
            lineage=a.lineage + (a.name,) + b.lineage + (b.name,),
            out_schema=out)

    def apply_policy(self, policy) -> None:
        """A `policy` block sets defaults for every agent in the file."""
        for key, value in policy.fields:
            if key not in POLICY_FIELDS:
                raise AILangRuntimeError(
                    f"policy: '{key}' cannot be a default "
                    f"(allowed: {', '.join(sorted(POLICY_FIELDS))})")
            value = value if not isinstance(value, Ref) else str(value)
            problem = _policy_value_problem(key, value)
            if problem is not None:
                raise AILangRuntimeError(f"policy: {problem}")
            self.policy[key] = value

    async def _make_agent(self, name, decl):
        f = dict(self.policy)
        f.update(dict(decl.fields))
        kind = f.pop("kind", "chat")
        # resolve schema/tool references once — every kind gets objects
        for key in ("in", "out"):
            if key in f:
                sch = self.env.get(f[key])
                if not isinstance(sch, Schema):
                    raise AILangRuntimeError(
                        f"Agent {name!r}: {key}: {f[key]!r} is not a "
                        f"declared schema")
                f[key] = sch
        if "tools" in f:
            tools = []
            for tname in f.get("tools", []):
                t = self.env.get(tname)
                if not isinstance(t, Tool):
                    raise AILangRuntimeError(
                        f"Agent {name!r}: unknown tool {tname!r}")
                tools.append(t)
            f["tools"] = tools
        factory = _KINDS.get(kind)
        if factory is None:
            raise AILangRuntimeError(
                f"Agent {name!r}: unknown kind {kind!r} "
                f"(registered: {', '.join(sorted(_KINDS))})")
        try:
            obj = factory(f, self.config)
        except AILangRuntimeError:
            raise
        except Exception as e:              # noqa: BLE001 — kind guard
            raise AILangRuntimeError(
                f"Agent {name!r} (kind {kind!r}): {e}") from e
        obj.name = name
        return obj

    def _resolve_use_path(self, path: str) -> str:
        """Relative to the importing file first, then to the cwd."""
        p = Path(path)
        if p.is_absolute():
            return str(p)
        if self._source_dir:
            cand = Path(self._source_dir) / path
            if cand.exists():
                return str(cand)
        return str(p)

    async def _use(self, path: str):
        if path.endswith(".py"):
            self._load_plugin(self._resolve_use_path(path))
            return
        target = self._resolve_use_path(path)
        if path.startswith("github:"):
            repo = path.split(":", 1)[1]
            pkgs = Path.home() / ".ailang" / "packages"
            pkgs.mkdir(parents=True, exist_ok=True)
            target = pkgs / repo.replace("/", "__")
            if not target.exists():
                try:
                    subprocess.run(
                        ["git", "clone", "--depth", "1",
                         f"https://github.com/{repo}.git", str(target)],
                        check=True, capture_output=True)
                except Exception as e:              # noqa: BLE001
                    raise AILangRuntimeError(
                        f"use: git clone failed for {repo}: {e}") from e
            target = target / "main.ai"
        try:
            resolved = str(Path(target).resolve())
            src = _read_source(resolved)
        except OSError as e:
            raise AILangRuntimeError(
                f"use: cannot read {target!r}: {e}") from e
        if resolved in self._use_stack:
            chain = " -> ".join(Path(p).name
                                for p in self._use_stack + [resolved])
            raise AILangRuntimeError(f"use cycle: {chain}")
        child = AsyncInterpreter(self.config, self.out, journal=self.journal,
                                 replay=self.replay)
        child.env = dict(self.env)
        child._use_stack = self._use_stack + [resolved]
        child._source_dir = str(Path(resolved).parent)
        await child.arun(src)
        clash = sorted(child.declared & set(self.env))
        if clash:
            raise AILangRuntimeError(f"use {path!r}: redefines {clash}")
        for k in child.declared:
            self.env[k] = child.env[k]

    @staticmethod
    def _load_plugin(path: str):
        """Execute a plugin file: register(api) adds kinds to the registry."""
        p = Path(path)
        key = str(p.resolve())
        if key in _LOADED_PLUGINS:
            return
        try:
            src = p.read_text(encoding="utf-8")
        except OSError as e:
            raise AILangRuntimeError(
                f"use: cannot read plugin {path!r}: {e}") from e
        ns = {"__name__": f"ailang_plugin_{p.stem}", "__file__": str(p)}
        try:
            exec(compile(src, str(p), "exec"), ns)   # noqa: S102 — by design
        except Exception as e:              # noqa: BLE001 — plugin guard
            raise AILangRuntimeError(
                f"use: plugin {path!r} crashed: {e}") from e
        register = ns.get("register")
        if not callable(register):
            raise AILangRuntimeError(
                f"use: plugin {path!r} defines no register(api) function")
        try:
            register(KindAPI(source=str(p)))
        except AILangRuntimeError:
            raise
        except Exception as e:              # noqa: BLE001 — plugin guard
            raise AILangRuntimeError(
                f"use: plugin {path!r}: register(api) failed: {e}") from e
        _LOADED_PLUGINS.add(key)

    def _interpolate(self, text: str) -> str:
        def sub(m):
            parts = m.group(1).split(".")
            if parts[0] not in self.env:
                raise AILangRuntimeError(
                    f"Unknown interpolation: ${{{m.group(1)}}}")
            v = self.env[parts[0]]
            for p in parts[1:]:
                if isinstance(v, TypedArtifact):
                    try:
                        v = v.get(p)
                    except (KeyError, TypeError):
                        raise AILangRuntimeError(
                            f"no field {p!r} in ${{{m.group(1)}}}") from None
                else:
                    v = getattr(v, "last_insight", None)
                    if not isinstance(v, TypedArtifact):
                        raise AILangRuntimeError(
                            f"no field {p!r} in ${{{m.group(1)}}}")
            return _text(v)
        return re.sub(r"\$\{(\w+(?:\.\w+)*)\}", sub, text)

    @staticmethod
    def _runnable(v, op):
        if isinstance(v, (AIAgent, Chain, Fork, Backup, RaceRunner)) or (
                hasattr(v, "invoke") and not isinstance(v, type)):
            return v
        raise AILangRuntimeError(
            f"Operator {op!r} works on agents, got {type(v).__name__}")

    @staticmethod
    def _agent(v):
        if isinstance(v, AIAgent):
            return v
        raise AILangRuntimeError(
            f"`+` works on plain Agents, got {type(v).__name__}")


class Interpreter(AsyncInterpreter):
    """Synchronous facade."""

    def run(self, source: str, filename: str | None = None):
        if filename:
            self._source_dir = str(Path(filename).resolve().parent)
        if self.journal is not None:
            self.journal.note_program(filename or "<string>", source)
        return self._run_async(self.arun(source))

    def run_image(self, image: dict):
        return self._run_async(self.arun_image(image))

    def exec(self, st):
        return self._run_async(self.aexec(st))

    @staticmethod
    def _run_async(coro):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            return ex.submit(asyncio.run, coro).result()


def build_ir(stmts):
    nodes, edges = [], []

    def node(kind, label, **meta):
        nid = f"n{len(nodes) + 1}"
        nodes.append({"nid": nid, "kind": kind, "label": label,
                      "meta": meta})
        return nid

    def edge(a, b, label="", style="solid"):
        edges.append({"src": a, "dst": b, "label": label, "style": style})

    def agent_of(ref):
        if isinstance(ref, AgentDecl):
            d = dict(ref.fields)
            return node("agent", getattr(ref, "_hint", "anon"),
                        model=d.get("model", ""))
        if isinstance(ref, Name):
            for n in nodes:
                if n["kind"] == "agent" and n["label"] == ref.id:
                    return n["nid"]
            return node("agent", ref.id)
        if isinstance(ref, Str):
            return node("text", ref.value[:30])
        if isinstance(ref, Sequence):
            ids = [agent_of(p) for p in ref.parts]
            for a, b in zip(ids, ids[1:]):
                edge(a, b)
            return ids[-1]
        if isinstance(ref, (Parallel, Race)):
            tag = "||" if isinstance(ref, Parallel) else "race"
            join = node("join", tag)
            for p in ref.parts:
                edge(agent_of(p), join, tag)
            return join
        if isinstance(ref, Merge):
            m = node("merge", "+")
            for p in ref.parts:
                edge(agent_of(p), m, "dna")
            return m
        if isinstance(ref, Fallback):
            fb = node("backup", "??")
            for p in ref.parts:
                edge(agent_of(p), fb, "try")
            return fb
        return node("agent", "?")

    def wire(flow):
        prev = agent_of(flow.source)
        for t in flow.targets:
            if isinstance(t, Gate):
                auditor = agent_of(t.unit)
                g = node("gate", f"? {_gtext(t.criterion)}")
                edge(prev, g)
                edge(auditor, g, "judges", "dashed")
                prev = g
            else:
                nid = agent_of(t)
                edge(prev, nid)
                prev = nid

    def first_agent_in(block):
        for st in block:
            expr = st.expr if isinstance(st, Assignment) else st
            if isinstance(expr, Flow):
                return agent_of(expr.source)
        return None

    def wire_if(expr):
        br = node("branch", f"if {_gtext(expr.cond)}")
        for label, branch in (("then", expr.then), ("else", expr.other)):
            if not branch:
                continue
            first = first_agent_in(branch)
            if first:
                edge(br, first, label, "dashed")

    def wire_match(m):
        router = agent_of(Name(m.router.name))
        for c in m.cases:
            first = c.chain[0]
            dst = agent_of(first.unit if isinstance(first, Gate) else first)
            edge(router, dst, c.label or "_")

    for st in stmts:
        if isinstance(st, SchemaDecl):
            node("schema", st.name)
        elif isinstance(st, ToolDecl):
            node("tool", st.name)
        elif isinstance(st, FlowDecl):
            node("flow", f"flow {st.name}({st.param})")
        elif isinstance(st, Assignment):
            if isinstance(st.expr, AgentDecl):
                st.expr._hint = st.name
                agent_of(st.expr)
            elif isinstance(st.expr, Flow):
                wire(st.expr)
            elif isinstance(st.expr, (Sequence, Parallel, Fallback,
                                      Merge, Race)):
                agent_of(st.expr)
        elif isinstance(st, Flow):
            wire(st)
        elif isinstance(st, Assignment) and isinstance(st.expr, Match):
            wire_match(st.expr)
        elif isinstance(st, Assignment) and isinstance(st.expr, If):
            wire_if(st.expr)
        elif isinstance(st, If):
            wire_if(st)
        elif isinstance(st, Match):
            wire_match(st)
    return nodes, edges


def to_mermaid(nodes, edges) -> str:
    lines = ["graph TD"]
    for n in nodes:
        extra = (f" - {n['meta']['model']}"
                 if n["kind"] == "agent" and n["meta"].get("model") else "")
        lines.append(f'    {n["nid"]}["{n["label"]}{extra}"]')
    for e in edges:
        arrow = "-.->" if e["style"] == "dashed" else "-->"
        lbl = f"|{e['label']}|" if e["label"] else ""
        lines.append(f'    {e["src"]} {arrow}{lbl} {e["dst"]}')
    return "\n".join(lines)


# ══════════════════ 12. FORMATTER (ailang fmt) ══════════════════════

class FormatError(AILangRuntimeError):
    pass


def _lit(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    return repr(v) if isinstance(v, float) else str(v)


def _type_text(t) -> str:
    if isinstance(t, tuple):
        if t[0] == "list":
            return f"[{_type_text(t[1])}]"
        return str(t[1])
    return str(t)


def _emit_ref(node) -> str:
    """A comparison's left side: field path, measure or identifier."""
    if isinstance(node, Measure):
        return f"{node.kind}({node.target})"
    if isinstance(node, str):
        return node
    if isinstance(node, Name):
        return node.id
    return _lit(node)


def _emit_gate(g, nested: bool = False) -> str:
    if isinstance(g, Name):
        return g.id
    if isinstance(g, Str):
        return _lit(g.value)
    if isinstance(g, str):
        return _lit(g)
    if isinstance(g, Comparison):
        right = g.right.id if isinstance(g.right, Name) else _lit(g.right)
        return f"{_emit_ref(g.left)} {g.op} {right}"
    if isinstance(g, GateAnd):
        return " and ".join(
            f"({_emit_gate(p)})" if isinstance(p, GateOr) else _emit_gate(p)
            for p in g.parts)
    if isinstance(g, GateOr):
        text = " or ".join(_emit_gate(p) for p in g.parts)
        return f"({text})" if nested else text
    raise FormatError(f"fmt: cannot print gate {_gtext(g)!r}")


def _emit_cond(cond) -> str:
    if isinstance(cond, Comparison):
        right = cond.right.id if isinstance(cond.right, Name) \
            else _lit(cond.right)
        return f"{_emit_ref(cond.left)} {cond.op} {right}"
    if isinstance(cond, GateAnd):
        return " and ".join(
            f"({_emit_cond(p)})" if isinstance(p, GateOr) else _emit_cond(p)
            for p in cond.parts)
    if isinstance(cond, GateOr):
        return " or ".join(_emit_cond(p) for p in cond.parts)
    raise FormatError(f"fmt: cannot print condition {_gtext(cond)!r}")


def _emit_agent_decl(decl, first_prefix: str, ind: int) -> list:
    pad = "    " * ind
    lines = [f"{pad}{first_prefix}Agent {{"]
    for key, value in decl.fields.items():
        if isinstance(value, list):
            rendered = "[" + ", ".join(str(v) for v in value) + "]"
        elif isinstance(value, bool):
            rendered = _lit(value)
        elif isinstance(value, Ref):
            rendered = str(value)
        elif isinstance(value, str):
            rendered = _lit(value)
        else:
            rendered = str(value)
        lines.append(f"{pad}    {key}: {rendered}")
    lines.append(f"{pad}}}")
    return lines


def _emit_postfix(node) -> str:
    if isinstance(node, Gate):
        unit = "?" if node.unit is None else _emit_expr(node.unit)
        return f"{unit}? {_emit_gate(node.criterion)}"
    return _emit_expr(node)


def _emit_expr(node) -> str:
    if isinstance(node, Name):
        return node.id
    if isinstance(node, Str):
        return _lit(node.value)
    if isinstance(node, Attr):
        return f"{node.base}.{node.field_name}"
    if isinstance(node, Call):
        return f"{node.name}({_emit_expr(node.arg)})"
    if isinstance(node, Race):
        return f"race ({_emit_expr(node.inner)})"
    if isinstance(node, Flow):
        parts = [_emit_expr(node.source)] + [_emit_postfix(t)
                                             for t in node.targets]
        return " >> ".join(parts)
    if isinstance(node, Fallback):
        return " ?? ".join(_emit_postfix(p) for p in node.parts)
    if isinstance(node, Parallel):
        return " || ".join(_emit_postfix(p) for p in node.parts)
    if isinstance(node, Sequence):
        return " & ".join(_emit_postfix(p) for p in node.parts)
    if isinstance(node, Merge):
        return " + ".join(_emit_postfix(p) for p in node.parts)
    if isinstance(node, Gate):
        return _emit_postfix(node)
    if isinstance(node, If):
        raise FormatError("fmt: an if inside an expression is not supported;"
                          " assign it first")
    if isinstance(node, Match):
        raise FormatError("fmt: a match inside an expression is not"
                          " supported; assign it first")
    raise FormatError(f"fmt: cannot print {type(node).__name__}")


def _emit_block(body, ind: int) -> list:
    pad = "    " * ind
    if not body:
        return [pad + "{ }"]
    lines = [pad + "{"]
    for st in body:
        lines += _emit_stmt(st, ind + 1)
    lines.append(pad + "}")
    return lines


def _emit_control(node, ind: int, prefix: str) -> list:
    pad = "    " * ind
    if isinstance(node, Match):
        router = (f"{node.router.name}("
                  f"{_emit_expr(node.router.arg)})")
        lines = [f"{pad}{prefix}match {router} {{"]
        for case in node.cases:
            label = _lit(case.label) if case.label else "_"
            chain = " >> ".join(_emit_postfix(x) for x in case.chain)
            lines.append(f"{pad}    {label} >> {chain}")
        lines.append(f"{pad}}}")
        return lines
    lines = [f"{pad}{prefix}if {_emit_cond(node.cond)} {{"]
    for st in node.then:
        lines += _emit_stmt(st, ind + 1)
    if node.other is not None:
        lines.append(f"{pad}}} else {{")
        for st in node.other:
            lines += _emit_stmt(st, ind + 1)
    lines.append(f"{pad}}}")
    return lines


def _emit_field_value(value) -> str:
    if isinstance(value, Ref):
        return str(value)
    if isinstance(value, str):
        return _lit(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _emit_stmt(st, ind: int = 0) -> list:
    pad = "    " * ind
    if isinstance(st, Assignment):
        rhs = st.expr
        if isinstance(rhs, AgentDecl):
            return _emit_agent_decl(rhs, f"{st.name} = ", ind)
        if isinstance(rhs, (Match, If)):
            return _emit_control(rhs, ind, f"{st.name} = ")
        return [f"{pad}{st.name} = {_emit_expr(rhs)}"]
    if isinstance(st, Print):
        if isinstance(st.expr, (Match, If)):
            return _emit_control(st.expr, ind, "print ")
        return [f"{pad}print {_emit_expr(st.expr)}"]
    if isinstance(st, Assert):
        return [f"{pad}assert {_emit_cond(st.cond)}"]
    if isinstance(st, SchemaDecl):
        invariants = getattr(st, "invariants", {}) or {}
        fields = [f"{name}: {_type_text(typ)}" + ("?" if optional else "")
                  + "".join(f" where {_emit_gate(c)}"
                            for c in invariants.get(name, []))
                  for name, typ, optional in st.fields]
        inner = "  ".join(fields)
        return [f"{pad}schema {st.name} {{ {inner} }}" if inner
                else f"{pad}schema {st.name} {{ }}"]
    if isinstance(st, GateDecl):
        return [f"{pad}gate {st.name} = {_emit_gate(st.criterion)}"]
    if isinstance(st, Policy):
        fields = "  ".join(f"{k}: {_emit_field_value(v)}"
                           for k, v in st.fields)
        return [f"{pad}policy {{ {fields} }}"]
    if isinstance(st, ToolDecl):
        line = f'{pad}tool {st.name} = py {_lit(st.target)}'
        if st.params:
            params = "  ".join(f"{n}: {_type_text(t)}"
                               for n, t in st.params)
            line += f" params {{ {params} }}"
        else:
            line += " params { }"
        if st.desc:
            line += f"  desc {_lit(st.desc)}"
        return [line]
    if isinstance(st, FlowDecl):
        lines = [f"{pad}flow {st.name}({st.param}) {{"]
        for s in st.body:
            lines += _emit_stmt(s, ind + 1)
        lines.append(f"{pad}}}")
        return lines
    if isinstance(st, Patch):
        fields = "  ".join(
            f"{k}: {v if isinstance(v, Ref) else _lit(v)}"
            for k, v in st.fields)
        return [f"{pad}patch {st.target} {{ {fields} }}"]
    if isinstance(st, Until):
        unit = _emit_expr(st.gate.unit)
        head = (f"until {unit}? {_emit_gate(st.gate.criterion)} "
                f"max {st.max_iter}")
        lines = [f"{pad}{head} {{"]
        for s in st.body:
            lines += _emit_stmt(s, ind + 1)
        lines.append(f"{pad}}}")
        return lines
    if isinstance(st, Match):
        return _emit_control(st, ind, "")
    if isinstance(st, If):
        return _emit_control(st, ind, "")
    if isinstance(st, UseStmt):
        return [f'{pad}use {_lit(st.path)}']
    return [f"{pad}{_emit_expr(st)}"]


def _has_comment(line: str) -> bool:
    """True when a '#' starts a comment on this line (outside a string)."""
    quote = False
    for ch in line:
        if ch == '"':
            quote = not quote
        elif ch == "#" and not quote:
            return True
    return False


def _split_comments(src: str):
    """Leading comment block survives; other comment lines are reported."""
    lines = src.splitlines()
    header, i = [], 0
    while i < len(lines) and (not lines[i].strip()
                              or lines[i].lstrip().startswith("#")):
        header.append(lines[i].rstrip())
        i += 1
    while header and not header[-1].strip():
        header.pop()
    dropped = [n for n, line in enumerate(lines[i:], start=i + 1)
               if _has_comment(line)]
    return header, dropped


def format_source(src: str):
    """Return (canonical text, line numbers of dropped comments)."""
    stmts = parse(src)
    header, dropped = _split_comments(src)
    lines: list = list(header)
    if header and stmts:
        lines.append("")
    for st in stmts:
        lines += _emit_stmt(st, 0)
    return "\n".join(lines).rstrip() + "\n", dropped


# ═══════════ 14. COMPILER: diagnostics, check, build, images ════════

COMPILER_BUILTINS = frozenset({"human", "echo", "file"})
_AST_TYPES: dict = {}


def grammar_fingerprint() -> str:
    """Short stable hash of GRAMMAR, stamped into every compiled image."""
    return hashlib.sha256(GRAMMAR.encode("utf-8")).hexdigest()[:16]


@dataclass
class Diagnostic:
    code: str
    message: str
    line: int | None = None
    column: int | None = None
    length: int = 1
    help: str = ""
    severity: str = "error"
    file: str = ""

    def render(self, lines=None) -> str:
        where_file = f" ({self.file})" if self.file else ""
        out = [f"{self.severity}[{self.code}]: {self.message}"
               f"{where_file}"]
        if self.line:
            where = f" --> line {self.line}"
            if self.column:
                where += f", column {self.column}"
            out.append(where)
            if lines and 0 < self.line <= len(lines):
                source = lines[self.line - 1]
                gutter = f"  {self.line:>4} | "
                out.append(gutter + source)
                if self.column:
                    out.append(" " * (len(gutter) + self.column - 1)
                               + "^" * max(1, self.length))
        if self.help:
            out.append(f"  help: {self.help}")
        return "\n".join(out)


class CompileError(AILangRuntimeError):
    """A build that produced diagnostics instead of an image."""

    def __init__(self, diagnostics):
        self.diagnostics = list(diagnostics)
        summary = "; ".join(f"{d.code}: {d.message}"
                            for d in self.diagnostics
                            if d.severity == "error")
        super().__init__(summary or "compilation failed")


# ═════════ 8b. STATIC FACTS: THE ORG CHART, FLOW AND PAYROLL ════════

@dataclass
class AgentFact:
    """One declared agent, as the compiler sees it (no runtime objects)."""

    name: str
    fields: dict
    line: int | None = None

    def get(self, key, default=None):
        value = self.fields.get(key, default)
        return default if value is None else value

    @property
    def engine(self) -> str:
        return str(self.get("model", "mock"))

    @property
    def kind(self) -> str:
        return str(self.get("kind", "chat"))

    @property
    def role(self) -> str:
        return str(self.get("role", ""))

    @property
    def in_schema(self):
        v = self.fields.get("in")
        return str(v) if v is not None else None

    @property
    def out_schema(self):
        v = self.fields.get("out")
        return str(v) if v is not None else None

    @property
    def budget(self):
        v = self.fields.get("budget")
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None          # AIL043 already reported it

    @property
    def salary(self):
        v = self.fields.get("salary")
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None          # AIL043 already reported it

    @property
    def window(self):
        v = self.fields.get("window")
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None          # AIL043 already reported it

    @property
    def price_in(self):
        v = self.fields.get("price_in")
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None          # AIL043 already reported it

    @property
    def price_out(self):
        v = self.fields.get("price_out")
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None          # AIL043 already reported it

    @property
    def casts(self) -> int:
        v = self.fields.get("casts")
        try:
            return int(v) if v is not None else 2
        except (TypeError, ValueError):
            return 2                    # AIL043 already reported it

    @property
    def timeout(self):
        v = self.fields.get("timeout")
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None          # AIL043 already reported it

    @property
    def judge(self):
        v = self.fields.get("judge")
        return str(v) if v is not None else None


def _schema_registry(decls) -> dict:
    reg = {}
    for st in decls:
        reg[st.name] = Schema(
            st.name, {n: Field(TypeExpr.from_ast(t), opt)
                      for n, t, opt in st.fields},
            invariants=dict(getattr(st, "invariants", {}) or {}))
    return reg


def _is_subtype_name(child: str, parent: str, parents: dict) -> bool:
    if child == parent:
        return True
    seen, stack = set(), [child]
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        for up in parents.get(name, ()):  # noqa: PLC0206 — sets
            if up == parent:
                return True
            stack.append(up)
    return False


def _example_tokens(schema_name, registry) -> int:
    """Tokens of the example JSON a schema-shaped answer has."""
    if schema_name and schema_name in registry:
        try:
            example = _example(TypeExpr("schema", name=schema_name), registry)
            return estimate_tokens(json.dumps(example, ensure_ascii=False))
        except (KeyError, TypeError):
            return DEFAULT_OUT_TOKENS
    return DEFAULT_OUT_TOKENS


class ProgramFacts:
    """Everything `flow`, `payroll` and `contract` need, computed once."""

    def __init__(self):
        self.agents: dict = {}          # name -> AgentFact (declaration order)
        self.inline = 0
        self.chains: list = []          # [{"start": int, "steps": [names]}]
        self.parents: dict = {}         # schema -> set(direct parents)
        self.parent_lines: dict = {}    # (child, parent) -> line
        self.policy: dict = {}          # file-level agent defaults
        self.registry: dict = {}        # schema name -> Schema
        self.gates: dict = {}           # agent -> [criterion text]
        self.gate_defs: dict = {}       # gate name -> criterion AST
        self.gate_checks: list = []     # (producer, criterion, line)
        self.issues: list = []          # (code, message, line, token)
        self.var_schema: dict = {}      # variable -> schema name
        self.var_agent: dict = {}       # variable -> agent name
        self.artifact_vars: dict = {}   # variable -> (agent, schema)

    def issue(self, code, message, line=None, anchor=None):
        self.issues.append((code, message, line, anchor))

    # ── reporting helpers ────────────────────────────────
    def calls(self):
        """→ {agent: {"calls", "tok_in", "max_in", "tok_out"}} in order."""
        stats = {name: {"calls": 0, "tok_in": 0, "max_in": 0,
                        "tok_out": DEFAULT_OUT_TOKENS}
                 for name in self.agents}
        for chain in self.chains:
            mult = int(chain.get("mult", 1)) or 1
            prev = chain["start"]
            for step in chain["steps"]:
                fact = self.agents.get(step["name"])
                if fact is None:
                    prev = DEFAULT_OUT_TOKENS
                    continue
                tok_in = estimate_tokens(fact.role) + prev
                row = stats[step["name"]]
                row["calls"] += mult
                row["tok_in"] += tok_in * mult
                row["max_in"] = max(row["max_in"], tok_in)
                row["tok_out"] = _example_tokens(fact.out_schema,
                                                 self.registry)
                if not step["keep"]:
                    prev = row["tok_out"]
        return stats

    def total_input(self) -> int:
        return sum(row["tok_in"] for row in self.calls().values())

    def agent_for(self, node):
        if isinstance(node, AgentDecl):
            return None
        if isinstance(node, Name):
            name = self.var_agent.get(node.id)
            return self.agents.get(name) if name else None
        return None


def _semantic_gate(criterion, defs=None) -> bool:
    """True when the gate needs an auditor (a string criterion).

    A named gate is looked up in `defs`, so `qa? readable` counts exactly
    like the inline `qa? "reads well"` it stands for.
    """
    if criterion is None:
        return False
    if isinstance(criterion, Name) and defs is not None:
        target = defs.get(criterion.id)
        return False if target is None else _semantic_gate(target, defs)
    if isinstance(criterion, str):
        return True
    if criterion.__class__.__name__ == "Str":
        return True
    if isinstance(criterion, (GateAnd, GateOr)):
        return any(_semantic_gate(part) for part in criterion.parts)
    return False


def _literal_tokens(node) -> int:
    if isinstance(node, str):
        return estimate_tokens(node)
    return DEFAULT_OUT_TOKENS


def collect_facts(stmts, filename: str = "<program>") -> ProgramFacts:
    """Walk the AST once. Pure: no runtime, no network, no side effects."""
    facts = ProgramFacts()
    decls = [st for st in stmts if isinstance(st, SchemaDecl)]
    facts.policy = _policy_defaults(stmts)
    facts.registry = _schema_registry(decls)
    for st in decls:
        facts.parents.setdefault(st.name, set())

    def walk(node, line=None, mult=1):
        line = getattr(node, "_line", None) or line
        if isinstance(node, list):
            for sub in node:
                walk(sub, line, mult)
            return
        if isinstance(node, Assignment):
            target = node.expr
            if isinstance(target, AgentDecl):
                inline_decl(target, node.name, line)
            elif isinstance(target, Flow):
                walk_flow(target, line, mult)
                last = last_agent_of(target)
                if last is not None:
                    facts.var_agent[node.name] = last.name
                    facts.var_schema[node.name] = last.out_schema
                    facts.artifact_vars[node.name] = (last.name,
                                                      last.out_schema)
            else:
                facts.var_schema[node.name] = schema_of(target)
                agent = facts.agent_for(target)
                if agent:
                    facts.var_agent[node.name] = agent.name
                walk(target, line, mult)
            return
        if isinstance(node, GateDecl):
            facts.gate_defs[node.name] = node.criterion
            return
        if isinstance(node, Patch):
            patch_schema(node, line)
            return
        if isinstance(node, Flow):
            walk_flow(node, line, mult)
            return
        if isinstance(node, Match):
            for case in node.cases:
                producers, steps = [], []
                for item in case.chain:
                    if isinstance(item, Gate):
                        producer = producers[-1] if producers else None
                        if producer is not None \
                                and item.criterion is not None:
                            facts.gate_checks.append(
                                (producer.name, item.criterion, line))
                        auditor = facts.agent_for(item.unit)
                        if auditor is not None \
                                and _semantic_gate(item.criterion, facts.gate_defs):
                            steps.append({"name": auditor.name,
                                          "keep": True})
                        walk(item, line, mult)
                        continue
                    agent = facts.agent_for(item)
                    producers.append(agent)
                    if agent is not None:
                        steps.append({"name": agent.name, "keep": False})
                    walk(item, line, mult)
                check_pairs(producers, line)
                if steps:
                    facts.chains.append({"start": DEFAULT_OUT_TOKENS,
                                         "steps": steps, "line": line,
                                         "mult": mult})
            root = facts.agents.get(node.router.name)
            if root is not None:
                facts.chains.append({
                    "start": _literal_tokens(node.router.arg),
                    "steps": [{"name": root.name, "keep": False}],
                    "line": line, "mult": mult})
            walk(node.router.arg, line, mult)
            return
        if isinstance(node, (Merge, Sequence, Parallel, Fallback, Race)):
            for part in getattr(node, "parts", None) or [node.inner]:
                walk(part, line, mult)
            return
        if isinstance(node, Gate):
            # the criterion belongs to the inspected post, not to the
            # inspector: walk_flow records it against the producer
            walk(node.unit, line, mult)
            return
        if isinstance(node, Call):
            walk(node.arg, line, mult)
            return
        if isinstance(node, If):
            # both branches are walked: the report is a worst-case bound
            walk(node.then, line, mult)
            walk(node.other or [], line, mult)
            return
        if isinstance(node, Until):
            # the body runs up to `max N` times: bill it that many times
            walk(node.body, line, mult * max(1, int(node.max_iter)))
            return
        if isinstance(node, FlowDecl):
            walk(node.body, line, mult)
            return
        if isinstance(node, UseStmt):
            return

    def inline_decl(decl, name, line):
        if name is None:
            facts.inline += 1
            name = f"<inline@{line or facts.inline}>"
        merged = dict(facts.policy)
        merged.update(dict(decl.fields))
        facts.agents[name] = AgentFact(name, merged, line)
        facts.var_agent[name] = name

    def schema_of(node):
        if isinstance(node, Name):
            return facts.var_schema.get(node.id)
        if isinstance(node, Flow):
            last = last_agent_of(node)
            if last is not None:
                return last.out_schema
            return schema_of(node.source)
        return None

    def last_agent_of(flow):
        for target in reversed(flow.targets):
            if isinstance(target, Gate):
                continue
            if isinstance(target, AgentDecl):
                inline_decl(target, None, getattr(target, "_line", None))
                return AgentFact("<inline>", dict(target.fields))
            return facts.agent_for(target)
        return facts.agent_for(flow.source)

    def record_chain(steps, start, line, mult=1):
        steps = [step for step in steps if step is not None]
        if steps:
            facts.chains.append({"start": start, "steps": steps,
                                 "line": line, "mult": mult})

    def check_pairs(seq, line):
        prev, cur = None, None
        for agent in seq:
            if agent is None:
                prev, cur = None, None
                continue
            if (prev is not None and cur in facts.registry
                    and agent.in_schema in facts.registry
                    and not _is_subtype_name(cur, agent.in_schema,
                                             facts.parents)):
                facts.issue(
                    "AIL032",
                    f'agent "{prev.name}" produces {cur}, but '
                    f'"{agent.name}" expects {agent.in_schema}: '
                    f"{cur} is not a subtype of {agent.in_schema}",
                    line, agent.name)
            prev, cur = agent, agent.out_schema

    def walk_flow(node, line, mult=1):
        artifact = (facts.artifact_vars.get(node.source.id)
                    if isinstance(node.source, Name) else None)
        first = None if artifact else facts.agent_for(node.source)
        producers = [first]
        if artifact is not None:
            # the artifact came from somewhere: keep it for the schema check
            producers = [facts.agents.get(artifact[0])]
        steps = [{"name": first.name, "keep": False}] if first else []
        if artifact is not None:
            prev_name = artifact[0]
            start = _example_tokens(artifact[1], facts.registry)
        else:
            prev_name = first.name if first else None
            start = _literal_tokens(node.source)
        for target in node.targets:
            if isinstance(target, Gate):
                if prev_name and target.criterion is not None:
                    facts.gates.setdefault(prev_name, []).append(
                        _gate_label(target.criterion, facts.gate_defs))
                if prev_name and target.criterion is not None:
                    facts.gate_checks.append(
                        (prev_name, target.criterion, line))
                auditor = facts.agent_for(target.unit)
                if auditor is not None and _semantic_gate(target.criterion, facts.gate_defs):
                    steps.append({"name": auditor.name, "keep": True})
                walk(target, line, mult)
                continue
            if isinstance(target, AgentDecl):
                facts.inline += 1
                anonymous = AgentFact(f"<inline@{line or facts.inline}>",
                                      dict(target.fields), line)
                facts.agents[anonymous.name] = anonymous
                agent = anonymous
            else:
                agent = facts.agent_for(target)
            producers.append(agent)
            if agent is not None:
                steps.append({"name": agent.name, "keep": False})
                prev_name = agent.name
            else:
                prev_name = None
            walk(target, line, mult)
        check_pairs(producers, line)
        record_chain(steps, start, line, mult)

    def patch_schema(st, line):
        if st.target not in facts.registry:
            return
        for fname, value in st.fields:
            if fname != "is_a":
                facts.issue(
                    "AIL100",
                    f"patch {st.target}: a schema patch only accepts "
                    f"is_a: <schema>, got {fname!r}", line, fname)
                continue
            parent = str(value)
            if parent not in facts.registry:
                facts.issue(
                    "AIL031",
                    f"patch {st.target}: unknown parent schema {parent!r}",
                    line, parent)
                continue
            if parent == st.target:
                facts.issue(
                    "AIL030",
                    f"schema {st.target}: inheritance cycle "
                    f"{st.target} -> {parent}", line, st.target)
                continue
            facts.parents.setdefault(st.target, set()).add(parent)
            facts.parent_lines[(st.target, parent)] = line

    for statement in stmts:
        walk(statement)

    # cycles, reported once per cycle
    reported = set()
    for start in sorted(facts.parents):
        path = _cycle_path(start, facts.parents)
        if not path:
            continue
        key = frozenset(path)
        if key in reported:
            continue
        reported.add(key)
        facts.issue("AIL030",
                    "schema " + " -> ".join(path) + ": inheritance cycle",
                    None, start)
    return facts


def _cycle_path(start: str, parents: dict) -> list:
    stack = [(start, [start])]
    while stack:
        node, path = stack.pop()
        for up in parents.get(node, ()):  # noqa: PLC0206 — sets
            if up == start:
                return path + [start]
            if up not in path:
                stack.append((up, path + [up]))
    return []


def call_cost(fact: AgentFact, tok_in: int, tok_out: int,
              worst_case: bool = True):
    """→ (usd or None, known) for ONE call, retries included by default."""
    pin, pout, known = lookup_price(fact.engine, fact.price_in,
                                    fact.price_out)
    if not known or pin is None or pout is None:
        return None, False
    per_call = (tok_in * pin + tok_out * pout) / 1_000_000
    if worst_case:
        per_call *= 1 + max(0, fact.casts)
    return per_call, True


def _type_label(t) -> str:
    if t.kind == "list":
        return f"[{_type_label(t.inner)}]"
    if t.kind == "schema":
        return t.name
    return t.kind


def _type_compatible(child_t, parent_t, registry: dict,
                     parents: dict, depth: int = 0) -> bool:
    """Does a field of type child_t satisfy a slot of type parent_t?"""
    if depth > 20:
        return True                      # pathological nesting: stay silent
    if parent_t.kind == "any":
        return True
    if child_t.kind != parent_t.kind:
        return False
    if child_t.kind == "list":
        return _type_compatible(child_t.inner, parent_t.inner, registry,
                                parents, depth + 1)
    if child_t.kind == "schema":
        if child_t.name == parent_t.name:
            return True
        return _is_subtype_name(child_t.name, parent_t.name, parents)
    return True


def conformance_issues(facts: ProgramFacts) -> list:
    """`is_a` is a promise: the child must be usable wherever the parent is."""
    issues = []
    for child, parents in facts.parents.items():
        child_schema = facts.registry.get(child)
        if child_schema is None:
            continue
        for parent in parents:
            parent_schema = facts.registry.get(parent)
            if parent_schema is None or parent == child:
                continue
            line = facts.parent_lines.get((child, parent))
            for fname, field in parent_schema.fields.items():
                if fname not in child_schema.fields:
                    if field.optional:
                        continue
                    issues.append((
                        "AIL036",
                        f'schema "{child}" declares is_a: {parent} but does '
                        f'not supply field "{fname}": '
                        f"{_type_label(field.type)}",
                        line, child))
                    continue
                mine = child_schema.fields[fname].type
                if not _type_compatible(mine, field.type, facts.registry,
                                        facts.parents):
                    issues.append((
                        "AIL036",
                        f'schema "{child}" declares is_a: {parent} but field '
                        f'"{fname}" is {_type_label(mine)}, expected '
                        f"{_type_label(field.type)}",
                        line, fname))
    return issues


def _measure_problem(kind: str, t, mapped: bool = False) -> str | None:
    """Is a measure legal on a field of this type?"""
    if t is None or t.kind == "any":
        return None
    if mapped:
        # `sum(lines.price)`: the leaf values arrive as one list
        if t.kind in ("int", "float"):
            return None
        return f"{kind}() needs numbers, got {_type_label(t)}"
    if kind == "len":
        if t.kind in ("str", "list"):
            return None
        return f"len() needs a string or a list, got {_type_label(t)}"
    if t.kind != "list":
        return f"{kind}() needs a list, got {_type_label(t)}"
    inner = t.inner
    if inner is not None and inner.kind not in ("int", "float", "any"):
        return f"{kind}() needs a list of numbers, got [{_type_label(inner)}]"
    return None


def _comparisons(criterion):
    if isinstance(criterion, Comparison):
        yield criterion
    elif isinstance(criterion, (GateAnd, GateOr)):
        for part in criterion.parts:
            yield from _comparisons(part)


def _literal_kind(node):
    if isinstance(node, bool):
        return "bool"
    if isinstance(node, int):
        return "int"
    if isinstance(node, float):
        return "float"
    if isinstance(node, str):
        return "str"
    if node.__class__.__name__ == "Str":
        return "str"
    return None


def _named_criterion(criterion, defs: dict, depth: int = 0):
    """Follow `gate a = b` chains down to the concrete criterion."""
    while isinstance(criterion, Name) and depth < 20:
        criterion = defs.get(criterion.id)
        depth += 1
    return criterion


def _gate_label(criterion, defs: dict) -> str:
    if isinstance(criterion, Name):
        target = _named_criterion(criterion, defs)
        if target is None:
            return criterion.id
        return f"{criterion.id}: {_gtext(target)}"
    return _gtext(criterion)


def _resolve_field_chain(dotted: str, schema_name: str, registry: dict):
    """→ (TypeExpr | None, missing segment | None, mapped_over_a_list)."""
    schema = registry.get(schema_name)
    if schema is None:
        return None, None, False
    parts = [part for part in str(dotted).split(".") if part]
    current, mapped = schema, False
    for index, part in enumerate(parts):
        field = current.fields.get(part)
        if field is None:
            return None, part, mapped
        kind = field.type
        if index == len(parts) - 1:
            return kind, None, mapped
        if kind.kind == "schema":
            nxt = registry.get(kind.name)
            if nxt is None:
                return None, None, mapped
            current = nxt
        elif kind.kind == "list" and kind.inner is not None \
                and kind.inner.kind == "schema":
            nxt = registry.get(kind.inner.name)
            if nxt is None:
                return None, None, mapped
            current, mapped = nxt, True
        else:
            return None, ".".join(parts[:index + 2]), mapped
    return None, None, mapped


def gate_field_issues(facts: ProgramFacts) -> list:
    """Gate criteria must address real, comparable fields."""
    issues = []
    for producer, criterion, line in facts.gate_checks:
        fact = facts.agents.get(producer)
        if fact is None or not fact.out_schema:
            continue                           # untyped producer: silence
        criterion = _named_criterion(criterion, facts.gate_defs)
        if criterion is None:
            continue                           # unknown gate: AIL038 says it
        for comparison in _comparisons(criterion):
            if isinstance(comparison.left, Measure):
                target = comparison.left.target
                kind, missing, mapped = _resolve_field_chain(
                    target, fact.out_schema, facts.registry)
                if missing is not None:
                    issues.append((
                        "AIL037",
                        f'gate {_gtext(criterion)!r}: '
                        f'{fact.out_schema} has no field "{missing}" '
                        f'(producer "{producer}")',
                        line, missing))
                else:
                    problem = _measure_problem(comparison.left.kind, kind,
                                               mapped)
                    if problem is not None:
                        issues.append((
                            "AIL037",
                            f'gate {_gtext(criterion)!r}: {problem} '
                            f'("{target}")',
                            line, target))
                literal = _literal_kind(comparison.right)
                if literal is not None and literal not in ("int", "float"):
                    issues.append((
                        "AIL037",
                        f'gate {_gtext(criterion)!r}: '
                        f'{comparison.left.kind}() is a number, compared '
                        f"with {literal}",
                        line, target))
                continue
            left = str(comparison.left)
            first = left.split(".")[0]
            if (first in facts.registry or first in facts.agents
                    or first in facts.var_schema):
                continue                       # a name, not a field
            kind, missing, _mapped = _resolve_field_chain(
                left, fact.out_schema, facts.registry)
            if missing is not None:
                issues.append((
                    "AIL037",
                    f'gate {_gtext(criterion)!r}: {fact.out_schema} has no '
                    f'field "{missing}" (producer "{producer}")',
                    line, missing))
                continue
            literal = _literal_kind(comparison.right)
            if kind is None or literal is None:
                continue
            if {kind.kind, literal} <= {"int", "float"}:
                continue
            if kind.kind != literal:
                issues.append((
                    "AIL037",
                    f'gate {_gtext(criterion)!r}: field "{left}" is '
                    f"{_type_label(kind)}, compared with {literal}",
                    line, left.split(".")[-1]))
    return issues


def analyze_facts(facts: ProgramFacts) -> list:
    """Contract checks that need only the static facts."""
    diags = []
    stats = facts.calls()
    for name, fact in facts.agents.items():
        row = stats.get(name) or {"max_in": 0,
                                  "tok_out": DEFAULT_OUT_TOKENS}
        if fact.judge and fact.judge not in facts.agents:
            diags.append(Diagnostic(
                "AIL033",
                f'agent "{name}": judge {fact.judge!r} is not a declared '
                f"agent", fact.line, None, len(fact.judge),
                help="declare it first or drop the judge field"))
        if fact.timeout is not None and fact.timeout <= 0:
            diags.append(Diagnostic(
                "AIL034", f'agent "{name}": timeout must be > 0, '
                f"got {fact.timeout:g}", fact.line, None, 7,
                help="clause timeout: seconds before the deadline"))
        if fact.casts is not None and fact.casts < 1:
            diags.append(Diagnostic(
                "AIL034", f'agent "{name}": casts must be >= 1, '
                f"got {fact.casts}", fact.line, None, 6,
                help="clause casts: repair attempts after a contract "
                     "violation"))
        if fact.salary is not None and row["max_in"] and row["calls"]:
            run_cost, known = call_cost(fact, row["max_in"], row["tok_out"])
            if known and run_cost is not None:
                run_cost *= row["calls"]
                if run_cost > fact.salary:
                    diags.append(Diagnostic(
                        "AIL041",
                        f'agent "{name}": salary ${fact.salary:.6f} cannot '
                        f"cover the run: {row['calls']} call(s) cost about "
                        f"${run_cost:.6f}",
                        fact.line, None, len(name),
                        help="raise salary, cut casts, or call it less",
                        severity="warning"))
        if fact.budget is not None and row["max_in"]:
            cost, known = call_cost(fact, row["max_in"], row["tok_out"])
            if known and cost is not None and cost > fact.budget:
                diags.append(Diagnostic(
                    "AIL035",
                    f'agent "{name}": contract cannot be satisfied: '
                    f"wage ${fact.budget:.6f} < estimated cost "
                    f"${cost:.6f} (worst case, {1 + fact.casts} attempts)",
                    fact.line, None, len(name),
                    help="raise budget, cut casts, or use a cheaper engine",
                    severity="warning"))
    return diags


def _diag_column(lines, line, anchor) -> int | None:
    if line and anchor and 0 < line <= len(lines):
        found = lines[line - 1].find(str(anchor))
        if found >= 0:
            return found + 1
    return None


def analyze(stmts, source: str = "", filename: str = "<program>") -> list:
    """Static pass over the AST: names, kinds, schemas, tools, imports.

    Everything here is decidable without running an agent, so `ailang
    check` and `ailang build` can reject a program before any model call.
    """
    lines = source.splitlines()
    diags: list = []
    facts = collect_facts(stmts, filename)
    facts.issues.extend(conformance_issues(facts))
    facts.issues.extend(gate_field_issues(facts))
    for code, message, line, anchor in facts.issues:
        diags.append(Diagnostic(
            code, message, line, _diag_column(lines, line, anchor),
            len(str(anchor)) if anchor else 1))
    diags.extend(analyze_facts(facts))
    schemas: set = set()
    tools: set = set()
    names: set = set()
    policy_defaults = _policy_defaults(stmts)
    gate_defs: dict = {}
    all_schemas = {st.name for st in stmts if isinstance(st, SchemaDecl)}
    plugin_used = False

    def add(code, message, line=None, name=None, help=""):
        column = None
        if line and name and 0 < line <= len(lines):
            found = lines[line - 1].find(str(name))
            if found >= 0:
                column = found + 1
        diags.append(Diagnostic(code, message, line, column,
                                length=len(str(name)) if name else 1,
                                help=help))

    def check_ref(name, line, what="name", required=True):
        if name in names or name in COMPILER_BUILTINS:
            return
        add("AIL001", f"unknown {what} '{name}'", line, name,
            help=f"declare it before use, e.g. {name} = Agent "
                 f"{{ model: \"gpt-4o\"  role: \"...\" }}")

    def check_cond(cond, line):
        if isinstance(cond, Comparison):
            head = (cond.left.target.split(".")[0]
                    if isinstance(cond.left, Measure)
                    else str(cond.left).split(".")[0])
            check_ref(head, line)
            if isinstance(cond.right, Name):
                check_ref(cond.right.id, line)
        elif isinstance(cond, (GateAnd, GateOr)):
            for part in cond.parts:
                check_cond(part, line)

    def check_gate(gate, line, seen=None):
        if gate.unit is not None:
            check_expr(gate.unit, line)
        criterion = gate.criterion
        if isinstance(criterion, Name):
            if criterion.id not in gate_defs:
                add("AIL038", f"unknown gate '{criterion.id}'", line,
                    criterion.id,
                    help=f'declare it first: gate {criterion.id} = '
                         f'"criterion"')
            return
        seen = seen or set()
        if criterion.__class__.__name__ == "Str":
            return
        if isinstance(criterion, Comparison):
            head = str(criterion.left).split(".")[0]
            if head in names:        # a scope variable, e.g. crowd.one.v
                check_ref(head, line)
            # otherwise it is a field of the producer artifact: no check
            if isinstance(criterion.right, Name):
                check_ref(criterion.right.id, line)
        elif isinstance(criterion, (GateAnd, GateOr)):
            for part in criterion.parts:
                check_gate(Gate(unit=None, criterion=part), line)

    def check_agent(decl, line):
        fields = dict(policy_defaults)
        fields.update(dict(decl.fields))
        kind = str(fields.get("kind", "chat"))
        if kind not in _KINDS and not plugin_used:
            add("AIL002", f"unknown kind '{kind}'", line, kind,
                help="built-in kinds: " + ", ".join(sorted(_BUILTIN_KINDS))
                     + '; add one with use "agents/x.py"')
        for key in ("in", "out"):
            if key in fields:
                ref = str(fields[key])
                if ref not in schemas:
                    add("AIL004", f"{key}: '{ref}' is not a declared "
                        f"schema", line, ref,
                        help=f"declare it before the agent: schema {ref} "
                             f"{{ field: str }}")
        for tool_name in fields.get("tools", []) or []:
            if str(tool_name) not in tools:
                add("AIL006", f"unknown tool '{tool_name}'", line,
                    str(tool_name),
                    help=f"declare it first: tool {tool_name} = py "
                         f"\"module.func\"")

    def check_expr(node, line):
        if isinstance(node, Gate):
            check_gate(node, line)
            return
        if isinstance(node, Name):
            check_ref(node.id, line)
        elif isinstance(node, Attr):
            check_ref(node.base, line)
        elif isinstance(node, Call):
            if node.name != "file":
                check_ref(node.name, line, "callable")
            check_expr(node.arg, line)
        elif isinstance(node, Flow):
            check_expr(node.source, line)
            for target in node.targets:
                if isinstance(target, Gate):
                    check_gate(target, line)
                else:
                    check_expr(target, line)
        elif isinstance(node, (Merge, Sequence, Parallel, Fallback)):
            for part in node.parts:
                check_expr(part, line)
        elif isinstance(node, Race):
            check_expr(node.inner, line)
        elif isinstance(node, Match):
            check_ref(node.router.name, line, "router")
            check_expr(node.router.arg, line)
            for case in node.cases:
                for item in case.chain:
                    if isinstance(item, Gate):
                        add("AIL010", "a gate cannot start a match branch",
                            line, None,
                            help='run an agent first: "label" >> agent')
                    else:
                        check_expr(item, line)
        elif isinstance(node, If):
            check_cond(node.cond, line)
            for inner in node.then:
                check_stmt(inner, line)
            for inner in node.other or []:
                check_stmt(inner, line)
        elif isinstance(node, AgentDecl):
            check_agent(node, line)

    def check_invariants(decl, line):
        known = {n for n, _t, _o in decl.fields}
        types = {n: TypeExpr.from_ast(t) for n, t, _o in decl.fields}
        for fname, conds in (getattr(decl, "invariants", {}) or {}).items():
            for cond in conds:
                for cmp in _comparisons(cond):
                    if isinstance(cmp.left, Measure):
                        target = cmp.left.target.split(".")[0]
                        if target not in known:
                            add("AIL042", f"schema {decl.name}: invariant "
                                f"on '{fname}' measures unknown field "
                                f"'{target}'", line, target)
                            continue
                        problem = (None if "." in cmp.left.target
                                   else _measure_problem(cmp.left.kind,
                                                         types[target]))
                        if problem is not None:
                            add("AIL042", f"schema {decl.name}: {problem} "
                                f"in the invariant on '{fname}'", line,
                                target)
                        literal = _literal_kind(cmp.right)
                        if literal is not None \
                                and literal not in ("int", "float"):
                            add("AIL042", f"schema {decl.name}: invariant "
                                f"compares {cmp.left.kind}() with {literal}",
                                line, target)
                        continue
                    if "." in str(cmp.left):
                        # `inner.score >= 0`: the head is a record, the
                        # literal belongs to the leaf; check the head only.
                        head = str(cmp.left).split(".")[0]
                        if head not in known:
                            add("AIL042", f"schema {decl.name}: invariant "
                                f"on '{fname}' names unknown field "
                                f"'{head}'", line, head)
                        continue
                    left = str(cmp.left).split(".")[0]
                    if left not in known:
                        add("AIL042", f"schema {decl.name}: invariant on "
                            f"'{fname}' names unknown field '{left}'",
                            line, left,
                            help="invariants may use fields of the same "
                                 "schema and literals")
                        continue
                    literal = _literal_kind(cmp.right)
                    kind = types[left].kind
                    if literal and kind not in ("any",) \
                            and not {kind, literal} <= {"int", "float"} \
                            and kind != literal:
                        add("AIL042", f"schema {decl.name}: invariant "
                            f"compares '{left}' ({kind}) with {literal}",
                            line, left,
                            help="use a literal of the field's own type")

    def check_schema(decl, line):
        check_invariants(decl, line)
        for field_name, type_tuple, _optional in decl.fields:
            if isinstance(type_tuple, tuple) and type_tuple[0] == "schema":
                ref = type_tuple[1]
                if ref not in all_schemas and ref not in schemas:
                    add("AIL005", f"unknown schema '{ref}' in field "
                        f"'{field_name}'", line, ref,
                        help=f"declare schema {ref} {{ ... }}")
            elif isinstance(type_tuple, tuple) and type_tuple[0] == "list":
                inner = type_tuple[1]
                if isinstance(inner, tuple) and inner[0] == "schema" \
                        and inner[1] not in all_schemas \
                        and inner[1] not in schemas:
                    add("AIL005", f"unknown schema '{inner[1]}' in field "
                        f"'{field_name}'", line, inner[1])

    def check_stmt(st, line=None):
        nonlocal plugin_used
        line = getattr(st, "_line", None) or line
        if isinstance(st, Assignment):
            check_expr(st.expr, line)
            names.add(st.name)
        elif isinstance(st, Print):
            check_expr(st.expr, line)
        elif isinstance(st, Assert):
            check_cond(st.cond, line)
        elif isinstance(st, SchemaDecl):
            check_schema(st, line)
            schemas.add(st.name)
            names.add(st.name)
        elif isinstance(st, Policy):
            for key, value in st.fields:
                if key not in POLICY_FIELDS:
                    add("AIL043", f"policy: '{key}' cannot be a default",
                        line, key,
                        help="allowed: " + ", ".join(sorted(POLICY_FIELDS)))
                    continue
                if isinstance(value, Ref):
                    value = str(value)
                problem = _policy_value_problem(key, value)
                if problem is not None:
                    add("AIL043", f"policy: {problem}", line, key,
                        help="a default must be a literal the runtime can "
                             "use")
        elif isinstance(st, GateDecl):
            if st.name in gate_defs:
                add("AIL039", f"gate '{st.name}' is already declared",
                    line, st.name,
                    help="rename one of them; gates are unique per program")
            gate_defs[st.name] = st.criterion
            names.add(st.name)
        elif isinstance(st, ToolDecl):
            tools.add(st.name)
            names.add(st.name)
        elif isinstance(st, FlowDecl):
            names.add(st.name)
            names.add(st.param)
            for inner in st.body:
                check_stmt(inner, line)
        elif isinstance(st, Patch):
            if st.target not in names:
                add("AIL001", f"unknown patch target '{st.target}'",
                    line, st.target,
                    help="patch needs a variable holding a typed artifact")
        elif isinstance(st, Until):
            check_gate(st.gate, line)
            for inner in st.body:
                check_stmt(inner, line)
        elif isinstance(st, UseStmt):
            check_import(st, line)
        elif isinstance(st, (Match, If)):
            check_expr(st, line)
        else:
            check_expr(st, line)

    def check_import(st, line):
        nonlocal plugin_used
        target = st.path
        if target.endswith(".py"):
            plugin_used = True
            return
        path = Path(target)
        if not path.is_absolute() and filename not in ("<program>",
                                                      "<string>"):
            candidate = Path(filename).parent / target
            if candidate.exists():
                path = candidate
        if not path.exists():
            add("AIL020", f"cannot read library '{target}'", line,
                help="check the path or run from the project root")
            return
        try:
            inner = parse_located(_read_source(path))
        except (OSError, AILangSyntaxError) as e:
            add("AIL020", f"library '{target}' cannot be read: {e}", line)
            return
        for sub in inner:
            if isinstance(sub, (Assignment, SchemaDecl, ToolDecl, FlowDecl)):
                names.add(sub.name)
                if isinstance(sub, SchemaDecl):
                    schemas.add(sub.name)
                elif isinstance(sub, ToolDecl):
                    tools.add(sub.name)

    for statement in stmts:
        check_stmt(statement)
    return diags


def _ast_types() -> dict:
    if not _AST_TYPES:
        for value in list(globals().values()):
            if isinstance(value, type) and hasattr(value,
                                                   "__dataclass_fields__"):
                _AST_TYPES[value.__name__] = value
    return _AST_TYPES


def ast_to_dict(node):
    if isinstance(node, list):
        return [ast_to_dict(x) for x in node]
    if isinstance(node, tuple):
        return {"$tuple": [ast_to_dict(x) for x in node]}
    if isinstance(node, dict):
        return {"$dict": {str(k): ast_to_dict(v)
                          for k, v in node.items()}}
    if isinstance(node, Ref):
        return {"$ref": str(node)}
    if hasattr(node, "__dataclass_fields__"):
        return {"$type": type(node).__name__,
                "fields": {k: ast_to_dict(getattr(node, k))
                           for k in node.__dataclass_fields__}}
    return node


def dict_to_ast(data):
    if isinstance(data, list):
        return [dict_to_ast(x) for x in data]
    if isinstance(data, dict):
        if "$type" in data:
            cls = _ast_types().get(data["$type"])
            if cls is None:
                raise AILangRuntimeError(
                    f"image: unknown node type {data['$type']!r}")
            return cls(**{k: dict_to_ast(v)
                          for k, v in data["fields"].items()})
        if "$ref" in data:
            return Ref(data["$ref"])
        if "$tuple" in data:
            return tuple(dict_to_ast(x) for x in data["$tuple"])
        if "$dict" in data:
            return {k: dict_to_ast(v) for k, v in data["$dict"].items()}
        raise AILangRuntimeError("image: malformed node")
    return data


def link_imports(stmts, filename: str = "<program>", seen=None):
    """Splice `use "*.ai"` libraries into the program (recursively).

    The result runs on its own: the image no longer needs the library
    files. Plugin `.py` imports stay as they are - they are code, not
    program text.
    """
    seen = seen if seen is not None else set()
    out, libraries = [], []
    for st in stmts:
        if isinstance(st, UseStmt) and not st.path.endswith(".py"):
            path = Path(st.path)
            if not path.is_absolute() and filename not in ("<program>",
                                                          "<string>"):
                candidate = Path(filename).parent / st.path
                if candidate.exists():
                    path = candidate
            if not path.exists():
                out.append(st)          # analyze() reports AIL020
                continue
            key = str(path.resolve())
            if key in seen:
                continue                # already spliced once
            seen.add(key)
            try:
                inner = parse_located(_read_source(path))
            except (OSError, AILangSyntaxError):
                out.append(st)          # analyze() reports AIL020
                continue
            spliced, sub_libs = link_imports(inner, str(path), seen)
            out.extend(spliced)
            libraries.append(key)
            libraries.extend(sub_libs)
        else:
            out.append(st)
    return out, libraries


def build_fingerprint(source: str, filename: str = "<program>") -> str:
    """Cheap content hash: program + linked libraries + grammar + version."""
    digest = hashlib.sha256()
    digest.update(grammar_fingerprint().encode())
    digest.update(__version__.encode())
    digest.update(source.encode("utf-8"))
    for body in _used_ai_bodies(source, filename):
        digest.update(body.encode("utf-8"))
    return digest.hexdigest()[:16]


def _used_ai_bodies(source: str, filename: str, seen=None) -> list:
    """Read every .ai library reachable through `use` (no parsing)."""
    seen = seen if seen is not None else set()
    bodies = []
    for match in re.finditer(r'use\s+"([^"]+)"', source):
        target = match.group(1)
        if target.endswith(".py"):
            continue
        path = Path(target)
        if not path.is_absolute() and filename not in ("<program>",
                                                       "<string>"):
            candidate = Path(filename).parent / target
            if candidate.exists():
                path = candidate
        if not path.exists():
            continue
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        try:
            body = _read_source(path)
        except OSError:
            continue
        bodies.append(body)
        bodies.extend(_used_ai_bodies(body, str(path), seen))
    return bodies


def build_image(source: str, filename: str = "<program>") -> dict:
    """Compile: parse, link imports, analyse, serialise a program image."""
    linked, libraries = link_imports(parse_located(source), filename)
    errors = [d for d in analyze(linked, source, filename)
              if d.severity == "error"]
    if errors:
        raise CompileError(errors)
    used_kinds = {str(dict(fields).get("kind", "chat"))
                  for _name, fields in _iter_agent_decls(linked)}
    plugins = sorted({_KIND_SOURCES[kind] for kind in used_kinds
                      if kind in _KIND_SOURCES})
    return {
        "format": "ailang-image",
        "format_version": 1,
        "ailang": __version__,
        "grammar": grammar_fingerprint(),
        "source": filename,
        "libraries": libraries,
        "plugins": plugins,
        "source_hash": build_fingerprint(source, filename),
        "lines": [getattr(st, "_line", None) for st in linked],
        "statements": ast_to_dict(linked),
    }


DEMO = """
# AI-lang v0.5.2 demo - runs fully offline (mock mode)

schema Pitch { title: str  hook: str  words: int }

muse = Agent {
    model: "gpt-4o"
    role: "You generate startup ideas with a concrete hook."
    out: Pitch
}
critic = Agent { model: "claude-3-5-sonnet"  role: "You are a merciless editor." }
polisher = Agent { model: "gpt-4o"  role: "You polish pitches into final form."  out: Pitch  temp: 0.4 }
fast = Agent { model: "gpt-4o-mini"  role: "Fast take." }
smart = Agent { model: "claude-3-5-sonnet"  role: "Careful take."  timeout: 30 }

team = muse & critic & polisher

pitch = "AI-native coffee shops" >> team >> critic? "hook is concrete" and words < 12

synthesis = "AI-native coffee shops" >> race (fast || smart)

hybrid = muse + critic
verdict = pitch >> hybrid

patch pitch { words: 11 }

print pitch
"""


def selftest(_args=None) -> int:
    import tempfile

    CFG = AIlangConfig()
    results = []

    class _Scripted:
        def __init__(self, *replies):
            self.replies, self.seen = list(replies), []

        def complete_detailed(self, system, user, history, temperature, cfg):
            self.seen.append(user)
            return Completion(text=self.replies.pop(0))

    class _Slow:
        def complete_detailed(self, *a, **k):
            time.sleep(0.3)
            return Completion(text="late")

    def quiet():
        return Interpreter(CFG, out=lambda s: None)

    def check(name, fn):
        try:
            fn()
            results.append((name, True, ""))
        except Exception as e:  # noqa: BLE001
            results.append((name, False, f"{type(e).__name__}: {e}"))

    def t_grammar():
        assert isinstance(parse(
            'w = Agent { model: "gpt-4o"  role: "r" }')[0], Assignment)
        assert isinstance(parse('"q" >> w >> c? "ok" and v < 5')[0], Flow)
        assert isinstance(parse('x = a & b')[0].expr, Sequence)
        assert isinstance(parse('x = a || b')[0].expr, Parallel)
        assert isinstance(parse('x = a ?? b')[0].expr, Fallback)
        assert isinstance(parse('x = a + b')[0].expr, Merge)
        assert isinstance(parse('until q? "ok" max 2 { x = "y" >> w }')[0],
                          Until)
        assert isinstance(parse('match r("t") { "a" >> w  _ >> w }')[0],
                          Match)
        assert isinstance(parse('schema S { a: str  b: int? }')[0],
                          SchemaDecl)
        assert isinstance(parse(
            'tool f = py "os.getcwd" params { x: str }')[0], ToolDecl)
        assert isinstance(parse('flow f(x) { x >> w }')[0], FlowDecl)
        assert isinstance(parse('patch p { a: 1 }')[0], Patch)

    def t_contract():
        interp = quiet()
        interp.run("""
            schema N { v: int }
            mk = Agent { model: "x"  role: "m"  out: N }
            n = "q" >> mk
        """)
        assert isinstance(interp.env["n"], TypedArtifact)
        assert interp.env["n"].data["v"] == 0

    def t_cast_retry():
        stub = _Scripted('{"v": "oops"}', '{"v": 7}')
        agent = AIAgent(name="mk", engine="x", prompt="p", config=CFG,
                        out_schema=Schema("N", {"v": Field(TypeExpr("int"))}),
                        _provider=stub)
        interp = AsyncInterpreter(CFG, out=lambda s: None)
        interp.env["N"] = agent.out_schema
        out = Interpreter._run_async(
            interp._ainvoke_agent(agent, "ORIGINAL TASK", "human"))
        assert out.data == {"v": 7}
        assert "ORIGINAL TASK" in stub.seen[-1]

    def t_gate_det_pass():
        interp = quiet()
        interp.run("""
            schema N { v: int }
            mk = Agent { model: "x"  role: "m"  out: N }
            qa = Agent { model: "x"  role: "q" }
            out = "q" >> mk >> qa? v >= 0
        """)
        assert isinstance(interp.env["out"], TypedArtifact)

    def t_gate_det_fail():
        interp = quiet()
        try:
            interp.run("""
                schema N { v: int }
                mk = Agent { model: "x"  role: "m"  out: N }
                qa = Agent { model: "x"  role: "q" }
                out = "q" >> mk >> qa? v > 99
            """)
            raise AssertionError("gate should have failed")
        except AILangRuntimeError:
            pass

    def t_gate_semantic():
        interp = quiet()
        interp.run("""
            w = Agent { model: "x"  role: "w" }
            c = Agent { model: "x"  role: "c" }
            out = "q" >> w >> c? "reads well"
        """)
        assert isinstance(interp.env["out"], str)

    def t_gate_hybrid():
        interp = quiet()
        interp.run("""
            schema N { v: int }
            w = Agent { model: "x"  role: "w"  out: N }
            c = Agent { model: "x"  role: "c" }
            out = "q" >> w >> c? "ok" and v < 12
        """)
        assert isinstance(interp.env["out"], TypedArtifact)

    def t_race():
        class Slow:
            name = "slow"
            def invoke(self, t, s="h"):
                time.sleep(0.2)
                return "late"

        class Fast:
            name = "fast"
            def invoke(self, t, s="h"):
                return "early"

        interp = AsyncInterpreter(CFG, out=lambda s: None)
        r = RaceRunner(Fork(Slow(), Fast()))
        out = Interpreter._run_async(interp._ainvoke(r, "t", "h"))
        assert out == "early"

    def t_timeout_fallback():
        slow = AIAgent(name="slow", engine="x", prompt="p", config=CFG,
                       timeout=0.05, _provider=_Slow())
        fast = AIAgent(name="fast", engine="x", prompt="p", config=CFG,
                       _provider=_Scripted("saved!"))
        interp = AsyncInterpreter(CFG, out=lambda s: None)
        b = Backup(slow, fast)
        out = Interpreter._run_async(interp._ainvoke(b, "q", "h"))
        assert out == "saved!"

    def t_use():
        with tempfile.TemporaryDirectory() as td:
            lib = Path(td) / "lib.ai"
            lib.write_text('helper = Agent { model: "x"  role: "h" }',
                           encoding="utf-8")
            interp = quiet()
            interp.run(f'use "{lib}"\nout = "q" >> helper')
            assert "helper" in interp.env

    def t_flow_first_class():
        interp = quiet()
        interp.run("""
            w = Agent { model: "x"  role: "w" }
            flow polish(input) { input >> w }
            r = polish("hello")
        """)
        assert isinstance(interp.env["r"], str)

    def t_patch():
        interp = quiet()
        interp.run("""
            schema N { v: int }
            mk = Agent { model: "x"  role: "m"  out: N }
            n = "q" >> mk
            patch n { v: 42 }
        """)
        assert interp.env["n"].data["v"] == 42

    def t_match():
        interp = quiet()
        r = interp.run("""
            rt = Agent { model: "x"  role: "router" }
            a = Agent { model: "x"  role: "A" }
            b = Agent { model: "x"  role: "B" }
            match rt("hello") { "first" >> a  _ >> b }
        """)
        assert isinstance(r, str)

    def t_until():
        interp = quiet()
        r = interp.run("""
            d = Agent { model: "x"  role: "d" }
            q = Agent { model: "x"  role: "q" }
            until q? "short" max 3 { m = "x ${feedback}" >> d }
        """)
        assert isinstance(r, str)

    def t_merge():
        interp = quiet()
        interp.run("""
            schema S1 { a: str }
            schema S2 { b: int }
            x = Agent { model: "m"  role: "r"  out: S1 }
            y = Agent { model: "m"  role: "r" }
            h = x + y
        """)
        assert interp.env["h"].out_schema is interp.env["S1"]
        try:
            interp.run("""
                z = Agent { model: "m"  role: "r"  out: S2 }
                bad = x + z
            """)
            raise AssertionError("merge should have failed")
        except AILangRuntimeError:
            pass

    def t_in_schema():
        good = AIAgent(name="mk", engine="x", prompt="p", config=CFG,
                       out_schema=Schema("N", {"v": Field(TypeExpr("int"))}),
                       _provider=_Scripted('{"v": 1}'))
        bad = AIAgent(name="cs", engine="x", prompt="p", config=CFG,
                      in_schema=Schema("OTHER",
                                       {"z": Field(TypeExpr("str"))}),
                      _provider=_Scripted("ok"))
        interp = AsyncInterpreter(CFG, out=lambda s: None)
        interp.env["N"] = good.out_schema
        interp.env["OTHER"] = bad.in_schema
        art = Interpreter._run_async(
            interp._ainvoke_agent(good, "make", "human"))
        assert isinstance(art, TypedArtifact)
        try:
            Interpreter._run_async(interp._ainvoke_agent(bad, art, "mk"))
            raise AssertionError("in-schema should have failed")
        except ArtifactError:
            pass

    def t_replay():
        with tempfile.TemporaryDirectory() as td:
            j = str(Path(td) / "r.jsonl")
            first = Interpreter(CFG, out=lambda s: None, journal=Journal(j))
            a = first.run('w = Agent { model: "x"  role: "w" }\n'
                          'out = "q" >> w')
            second = Interpreter(CFG, out=lambda s: None, replay=Replay(j))
            b = second.run('w = Agent { model: "x"  role: "w" }\n'
                           'out = "q" >> w')
            assert a == b and Replay(j).map

    def t_ir():
        stmts = parse("""
            w = Agent { model: "gpt-4o"  role: "w" }
            c = Agent { model: "x"  role: "c" }
            t = w & c
            out = "topic" >> t >> c? "ok"
        """)
        nodes, edges = build_ir(stmts)
        m = to_mermaid(nodes, edges)
        assert "gpt-4o" in m and "-->" in m

    def t_demo():
        buf = []
        Interpreter(CFG, out=buf.append).run(DEMO)
        assert any("title" in b for b in buf)

    def t_kinds_default():
        interp = quiet()
        interp.run('w = Agent { model: "gpt-4o"  role: "r" }')
        w = interp.env["w"]
        assert isinstance(w, AIAgent) and w.engine == "gpt-4o"
        assert w.endpoint is None and w.env is None and w.reasoning is False
        assert isinstance(interp.run('o = "q" >> w'), str)

    def t_kinds_py():
        interp = quiet()
        interp.run("""
            enc = Agent { kind: py  run: "json.dumps" }
            out = "abc" >> enc
        """)
        assert interp.env["out"] == '"abc"'

    def t_kinds_fields():
        interp = quiet()
        interp.run("""
            w = Agent {
                model: "gpt-4o"
                role: "r"
                endpoint: "http://localhost:11434/v1"
                env: "OLLAMA_API_KEY"
                reasoning: "true"
            }
        """)
        w = interp.env["w"]
        assert isinstance(w, AIAgent)
        assert w.endpoint == "http://localhost:11434/v1"
        assert w.env == "OLLAMA_API_KEY"
        assert w.reasoning is True
        assert isinstance(w._provider, MockProvider)
        assert isinstance(interp.run('o = "hi" >> w'), str)

    def t_plugins():
        root = Path(__file__).resolve().parent
        plugin = (root / "agents" / "hello_kind.py").as_posix()
        interp = quiet()
        interp.run(f'use "{plugin}"')
        interp.run("""
            sh = Agent { kind: shout  role: "loud" }
            out = "hello world" >> sh
        """)
        assert interp.env["out"] == "HELLO WORLD"
        try:
            register_kind("shout", lambda fields, config: None)
            raise AssertionError("kind clash must raise")
        except AILangRuntimeError:
            pass

    def t_kinds_cli():
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["kinds"])
        text = buf.getvalue()
        assert rc == 0
        listed = {ln.split()[0] for ln in text.splitlines()
                  if ln.startswith("  ")}
        assert {"chat", "py", "human", "echo", "shout"} <= listed, text

    def t_corpus():
        root = Path(__file__).resolve().parent
        programs = sorted((root / "corpus").glob("*.ai"))
        assert 6 <= len(programs) <= 8, \
            f"corpus should hold 6-8 programs, found {len(programs)}"
        cwd = os.getcwd()
        os.chdir(root)
        try:
            for p in programs:
                src = p.read_text(encoding="utf-8")
                assert parse(src), f"{p.name} parsed to nothing"
                Interpreter(CFG, out=lambda s: None).run(src)
        finally:
            os.chdir(cwd)

    def t_escalation_pass():
        interp = quiet()
        interp.run("""
            schema N { v: int }
            mk = Agent { model: "x"  role: "m"  out: N }
            res = Agent { model: "x"  role: "r" }
            out = "TASK" >> mk? v >= 0 ?? res
        """)
        assert isinstance(interp.env["out"], TypedArtifact)
        assert interp.env["res"].last_insight is None

    def t_escalation_fail():
        interp = quiet()
        interp.run("""
            schema N { v: int }
            mk = Agent { model: "x"  role: "m"  out: N }
            res = Agent { model: "x"  role: "r" }
            out = "TASK" >> mk? v > 99 ?? res
        """)
        out = interp.env["out"]
        assert isinstance(out, str) and "TASK" in out
        assert interp.env["res"].last_insight == out

    def t_stats():
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as td:
            j = str(Path(td) / "demo.jsonl")
            Interpreter(CFG, out=lambda s: None,
                        journal=Journal(j)).run(DEMO)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_stats(argparse.Namespace(file=j))
            text = buf.getvalue()
            assert rc == 0, text
            assert "TOTAL" in text and "muse" in text and "cost" in text

    def t_examples():
        root = Path(__file__).resolve().parent
        cwd = os.getcwd()
        os.chdir(root)
        try:
            for name in ("pr_review.ai", "triage.ai", "digest.ai",
                         "maintainer.ai"):
                p = root / "examples" / name
                assert p.exists(), f"missing example {name}"
                buf = []
                Interpreter(CFG, out=buf.append).run(
                    p.read_text(encoding="utf-8"))
                assert any(b.strip() for b in buf), f"{name}: no output"
        finally:
            os.chdir(cwd)

    def t_check_offline():
        import io
        from contextlib import redirect_stdout
        root = Path(__file__).resolve().parent
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cmd_check(argparse.Namespace(
                file=str(root / "examples" / "triage.ai"), offline=True))
        text = buf.getvalue()
        assert rc == 0 and "offline-safe" in text, text
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cloud.ai"
            p.write_text('w = Agent { model: "gpt-4o"  role: "r"  '
                         'endpoint: "https://api.openai.com/v1" }\n'
                         'o = "q" >> w\n', encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_check(argparse.Namespace(file=str(p),
                                                  offline=True))
            text = buf.getvalue()
            assert rc == 0 and "networked" in text and "w" in text, text

    def t_offline_strict():
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "acme.ai"
            p.write_text('w = Agent { model: "acme-llm"  role: "r" }\n'
                         'o = "q" >> w\n', encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_check(argparse.Namespace(file=str(p),
                                                  offline=True))
            text = buf.getvalue()
            assert rc == 0 and "networked" in text and "acme-llm" in text
            p2 = Path(td) / "mocky.ai"
            p2.write_text('w = Agent { model: "mock-fast"  role: "r" }\n'
                          'o = "q" >> w\n', encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_check(argparse.Namespace(file=str(p2),
                                                  offline=True))
            assert rc == 0 and "offline-safe" in buf.getvalue()

    def t_typed_auditor():
        interp = quiet()
        interp.run("""
            schema N { v: int }
            mk = Agent { model: "x"  role: "m"  out: N }
            qa = Agent { model: "x"  role: "q"  out: N }
            out = "q" >> mk >> qa? "reads well"
        """)
        assert isinstance(interp.env["out"], TypedArtifact)

    def t_stream():
        chunks = []
        cfg = AIlangConfig(stream=True)
        interp = Interpreter(cfg, out=lambda s: None,
                             stream_out=chunks.append)
        answer = interp.run("""
            w = Agent { model: "x"  role: "w" }
            c = Agent { model: "x"  role: "c" }
            out = "hello" >> w >> c? "reads well"
        """)
        streamed = "".join(chunks)
        assert streamed.strip() == answer
        assert "PASS" not in streamed          # audits stay quiet
        assert len(chunks) > 1                 # streamed in pieces

    def t_stats_mock_note():
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as td:
            j = str(Path(td) / "run.jsonl")
            Interpreter(CFG, out=lambda s: None,
                        journal=Journal(j)).run(
                'w = Agent { model: "x"  role: "w" }\no = "q" >> w')
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_stats(argparse.Namespace(file=j))
            text = buf.getvalue()
            assert rc == 0 and "dry-run" in text and "TOTAL" in text

    def t_verdict_parse():
        class _Styled:
            def __init__(self, text):
                self.text = text

            def complete_detailed(self, *a, **k):
                return Completion(text=self.text)

        def run_with(verdict):
            interp = quiet()
            interp.run("""
                schema N { v: int }
                mk = Agent { model: "x"  role: "m"  out: N }
                qa = Agent { model: "x"  role: "q" }
            """)
            interp.env["mk"]._provider = _Styled('{"v": 0}')
            interp.env["qa"]._provider = _Styled(verdict)
            return interp.run('out = "q" >> mk >> qa? "reads well"')

        assert run_with("**PASS** — ship it") is not None
        try:
            run_with("Hmm, probably PASS")     # ambiguous → fail closed
            raise AssertionError("ambiguous verdict must fail")
        except AILangRuntimeError:
            pass

    def t_stream_usage():
        class _Streamer:
            def __init__(self):
                self.last_usage = None

            def stream_detailed(self, system, user, history, temp, cfg):
                yield "he"
                yield "llo"
                self.last_usage = Completion(text="", tokens_in=11,
                                             tokens_out=7, cost_usd=0.001)

        cfg = AIlangConfig(stream=True)
        agent = AIAgent(name="s", engine="x", prompt="p", config=cfg,
                        _provider=_Streamer())
        interp = AsyncInterpreter(cfg, out=lambda s: None,
                                  stream_out=lambda s: None)
        out = Interpreter._run_async(
            interp._ainvoke_agent(agent, "q", "human"))
        assert out == "hello"
        comp = interp._last_comp
        assert comp.tokens_in == 11 and comp.tokens_out == 7
        assert comp.cost_usd > 0

    def t_offline_uses():
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as td:
            lib = Path(td) / "lib.ai"
            lib.write_text('helper = Agent { model: "gpt-4o"  role: "r" }\n',
                           encoding="utf-8")
            main_ai = Path(td) / "main.ai"
            main_ai.write_text('use "lib.ai"\nout = "q" >> helper\n',
                               encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_check(argparse.Namespace(file=str(main_ai),
                                                  offline=True))
            text = buf.getvalue()
            assert rc == 0 and "networked" in text and "helper" in text, text

    def t_stats_json():
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as td:
            j = str(Path(td) / "run.jsonl")
            Interpreter(CFG, out=lambda s: None,
                        journal=Journal(j)).run(
                'w = Agent { model: "x"  role: "w" }\no = "q" >> w')
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_stats(argparse.Namespace(file=j, json=True))
            data = json.loads(buf.getvalue())
            assert rc == 0 and data["total"]["calls"] == 1
            assert data["agents"]["w"]["calls"] == 1
            assert data["mode"] == "mock"

    def t_judge_field():
        interp = quiet()
        interp.run("""
            schema N { v: int }
            mk = Agent { model: "x"  role: "m"  out: N  judge: qa }
            qa = Agent { model: "x"  role: "judge" }
            res = Agent { model: "x"  role: "r" }
            out = "TASK" >> mk? "reads well" ?? res
        """)
        assert isinstance(interp.env["out"], TypedArtifact)
        assert interp.env["qa"].last_insight is not None    # the judge spoke
        assert interp.env["res"].last_insight is None       # no fallback

    def t_run_errors():
        import io
        from contextlib import redirect_stderr
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "bad.ai"
            p.write_text('out = "q" >> nobody\n', encoding="utf-8")
            buf = io.StringIO()
            with redirect_stderr(buf):
                rc = cmd_run(argparse.Namespace(
                    file=str(p), journal=None, replay=None, live=False,
                    budget=None, stream=False))
            text = buf.getvalue()
            assert rc == 2 and "Unknown name" in text, (rc, text)

    def t_repl_multiline():
        assert _needs_more('m = Agent { model: "x"')
        assert _needs_more('flow f(x) { input >> w')
        assert _needs_more('f = Agent { role: "unclosed')
        assert not _needs_more('m = Agent { model: "x"  role: "r" }')
        assert not _needs_more('until q? "ok" max 2 { x = "y" >> w }')

    def t_match_expr():
        interp = quiet()
        interp.run("""
            rt = Agent { model: "x"  role: "router" }
            a = Agent { model: "x"  role: "A" }
            b = Agent { model: "x"  role: "B" }
            desk = match rt("hello") { "first" >> a  _ >> b }
            again = desk
        """)
        desk = interp.env["desk"]
        assert isinstance(desk, str) and desk
        assert desk == interp.env["again"]
        printed = []
        Interpreter(CFG, out=printed.append).run(
            'rt = Agent { model: "x"  role: "router" }\n'
            'w = Agent { model: "x"  role: "W" }\n'
            'print match rt("hi") { "only" >> w }')
        assert printed and isinstance(printed[0], str)

    def t_hyphen_kind():
        root = Path(__file__).resolve().parent
        plugin = (root / "agents" / "hello_kind.py").as_posix()
        interp = quiet()
        interp.run(f'use "{plugin}"')
        interp.run("""
            loud = Agent { kind: shout-loud  role: "loud" }
            out = "hello" >> loud
        """)
        assert interp.env["out"] == "HELLO!!!"

    def t_fork_value():
        interp = quiet()
        interp.run("""
            a = Agent { model: "x"  role: "A" }
            b = Agent { model: "x"  role: "B" }
            crowd = "topic" >> (a || b)
        """)
        v = interp.env["crowd"]
        assert isinstance(v, ForkValue) and isinstance(v, str)
        assert set(v.parts) == {"a", "b"}, v.parts
        assert "[a]" in v and "[b]" in v

    def t_nested_gate():
        interp = quiet()
        interp.run("""
            schema Order { total: int  currency: str }
            schema R { order: Order  ok: bool }
            mk = Agent { model: "x"  role: "m"  out: R }
            qa = Agent { model: "x"  role: "q" }
            out = "q" >> mk >> qa? order.total >= 0 and order.currency != ""
        """)
        assert isinstance(interp.env["out"], TypedArtifact)

    def t_use_cycle():
        with tempfile.TemporaryDirectory() as td:
            a = Path(td) / "a.ai"
            b = Path(td) / "b.ai"
            a.write_text(f'use "{b.as_posix()}"\n', encoding="utf-8")
            b.write_text(f'use "{a.as_posix()}"\n', encoding="utf-8")
            interp = quiet()
            try:
                interp.run(a.read_text(encoding="utf-8"))
                raise AssertionError("use cycle must raise")
            except AILangRuntimeError as e:
                assert "use cycle" in str(e), e

    def t_use_sibling():
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "pkg"
            d.mkdir()
            (d / "core.ai").write_text(
                'base = Agent { model: "x"  role: "base" }\n',
                encoding="utf-8")
            main_ai = d / "main.ai"
            main_ai.write_text('use "core.ai"\nout = "q" >> base\n',
                               encoding="utf-8")
            interp = quiet()
            interp.run(main_ai.read_text(encoding="utf-8"), str(main_ai))
            assert "base" in interp.env

    def t_budget():
        cfg = AIlangConfig(budget_usd=0.0)
        agent = AIAgent(name="a", engine="x", prompt="p", config=cfg)
        interp = AsyncInterpreter(cfg, out=lambda s: None)
        try:
            Interpreter._run_async(
                interp._ainvoke_agent(agent, "q", "human"))
            raise AssertionError("budget must raise")
        except BudgetError:
            pass

    def t_plugin_errors():
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "broken.py"
            p.write_text("x = 1\n", encoding="utf-8")
            interp = quiet()
            try:
                interp.run(f'use "{p.as_posix()}"')
                raise AssertionError("plugin without register must raise")
            except AILangRuntimeError as e:
                assert "register(api)" in str(e), e

    def t_check_strict():
        import io
        from contextlib import redirect_stdout
        root = Path(__file__).resolve().parent
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cmd_check(argparse.Namespace(
                file=str(root / "examples" / "pr_review.ai"),
                offline=True, strict=True))
        assert rc == 1, buf.getvalue()
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cmd_check(argparse.Namespace(
                file=str(root / "examples" / "triage.ai"),
                offline=True, strict=True))
        assert rc == 0, buf.getvalue()

    def t_patch_nested():
        interp = quiet()
        interp.run("""
            schema Order { total: int }
            schema R { order: Order }
            mk = Agent { model: "x"  role: "m"  out: R }
            n = "q" >> mk
            patch n { order.total: 42 }
        """)
        assert interp.env["n"].data["order"]["total"] == 42
        try:
            interp.run('patch n { order.total: "bad" }')
            raise AssertionError("bad nested patch type must fail")
        except AILangRuntimeError:
            pass
        try:
            interp.run("patch n { order.nope: 1 }")
            raise AssertionError("unknown nested field must fail")
        except AILangRuntimeError:
            pass

    def t_if_basic():
        interp = quiet()
        interp.run("""
            schema Cart { total: int }
            mk = Agent { model: "x"  role: "m"  out: Cart }
            a = Agent { model: "x"  role: "A" }
            b = Agent { model: "x"  role: "B" }
            cart = "q" >> mk
            patch cart { total: 250 }
            ship = if cart.total > 100 { "fast" >> a } else { "slow" >> b }
            cheap = if cart.total < 100 { "fast" >> a } else { "slow" >> b }
        """)
        assert "fast" in interp.env["ship"]
        assert "slow" in interp.env["cheap"]

    def t_if_logic():
        interp = quiet()
        interp.run("""
            schema C { n: int  flag: str  on: bool }
            mk = Agent { model: "x"  role: "m"  out: C }
            v = "q" >> mk
            patch v {
                n: 5
                flag: "ok"
                on: true
            }
            hit = if v.n > 1 and v.flag == "ok" { "yes" } else { "no" }
            miss = if v.n > 9 or v.flag == "bad" { "yes" } else { "no" }
            flag = if v.on == true { "on" } else { "off" }
        """)
        assert interp.env["hit"] == "yes"
        assert interp.env["miss"] == "no"
        assert interp.env["flag"] == "on"

    def t_if_errors():
        interp = quiet()
        interp.run("""
            schema C { n: int }
            mk = Agent { model: "x"  role: "m"  out: C }
            v = "q" >> mk
            nothing = if v.n > 5 { "yes" }
        """)
        assert interp.env["nothing"] is None
        try:
            interp.run('bad = if ghost.n > 1 { "x" }')
            raise AssertionError("unknown name must raise")
        except AILangRuntimeError as e:
            assert "unknown name" in str(e), e

    def t_fmt_roundtrip():
        root = Path(__file__).resolve().parent
        files = (sorted((root / "corpus").glob("*.ai"))
                 + sorted((root / "examples").glob("*.ai"))
                 + sorted((root / "tests").glob("*.ai")))
        assert len(files) >= 15, len(files)
        for p in files:
            src = p.read_text(encoding="utf-8")
            out, _ = format_source(src)
            assert parse(out) == parse(src), f"{p.name}: AST differs"
            assert out == src, f"{p.name}: not canonical - run fmt --write"
            again, _ = format_source(out)
            assert again == out, f"{p.name}: not idempotent"
        demo_out, _ = format_source(DEMO)
        assert parse(demo_out) == parse(DEMO)
        tricky = ('v = "q" >> w\n'
                  'print if v == "q" { "yes" } else { "no" }\n'
                  'print match rt("t") { "a" >> w }\n')
        tricky_out, _ = format_source(tricky)
        assert parse(tricky_out) == parse(tricky)
        assert "print if" in tricky_out and "print match" in tricky_out
        assert format_source(tricky_out)[0] == tricky_out

    def t_fmt_cli():
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "messy.ai"
            p.write_text('# header comment\n\nw = Agent { model: "x"\n'
                         '   role: "r" }\nout = "q" >> w\n',
                         encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_fmt(argparse.Namespace(file=str(p), check=True,
                                                write=False))
            assert rc == 1
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_fmt(argparse.Namespace(file=str(p), check=False,
                                                write=True))
            assert rc == 0
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_fmt(argparse.Namespace(file=str(p), check=True,
                                                write=False))
            assert rc == 0, "fmt --write must leave the file canonical"
            text = p.read_text(encoding="utf-8")
            assert text.startswith("# header comment")
            assert "Agent {" in text

    def t_fmt_comments():
        src = '# top\nw = Agent { model: "x"  role: "r" }  # trailing\n'
        out, dropped = format_source(src)
        assert out.startswith("# top")
        assert dropped == [2], dropped
        assert "trailing" not in out

    def t_editor():
        ed = Editor()
        for ch in "hello":
            ed.feed(ch)
        assert ed.buffer == "hello" and ed.cursor == 5
        ed.feed("LEFT")
        assert ed.cursor == 4
        ed.feed("BACKSPACE")
        assert ed.buffer == "helo"
        ed.feed("HOME")
        ed.feed("X")
        assert ed.buffer == "Xhelo" and ed.cursor == 1
        ed.feed("END")
        assert ed.cursor == len(ed.buffer)
        assert ed.feed("ENTER") == "submit"
        assert ed.history == ["Xhelo"]
        ed.feed("a")
        ed.feed("ENTER")
        ed.feed("UP")
        assert ed.buffer == "a"
        ed.feed("UP")
        assert ed.buffer == "Xhelo"
        ed.feed("DOWN")
        assert ed.buffer == "a"
        ed.feed("DOWN")
        assert ed.buffer == ""
        assert ed.feed("CTRL-C") == "cancel" and ed.buffer == ""

    def t_editor_complete():
        names = ["schema", "scope", "print"]
        ed = Editor(completer=lambda p: [n for n in names
                                        if n.startswith(p)])
        for ch in "sch":
            ed.feed(ch)
        ed.feed("TAB")
        assert ed.buffer == "schema"
        ed.reset()
        for ch in "s":
            ed.feed(ch)
        ed.feed("TAB")
        assert ed.buffer == "sc"
        assert ed.candidates == ["schema", "scope"]

    def t_shell():
        sh = Shell(AIlangConfig())
        out = sh.run_line('x = "hi"')
        assert out and out[0].startswith("str"), out
        out = sh.run_line('w = Agent { model: "gpt-4o"  role: "r" }')
        assert out and out[0].startswith("agent"), out
        assert sh.run_line('print x') == ["hi"]
        out = sh.run_line(":agents")
        assert any("w" in line and "agent" in line for line in out), out
        out = sh.run_line(":help")
        assert any(":kinds" in line for line in out), out
        out = sh.run_line('bad = "q" >> nobody')
        assert out and out[0].startswith("✗"), out
        out = sh.run_line(":quit")
        assert out == ["bye"] and sh.running is False

    def t_shell_multiline():
        sh = Shell(AIlangConfig())
        first = sh.run_line('w = Agent { model: "gpt-4o"')
        assert first == ["…"] and sh.pending
        second = sh.run_line('  role: "r" }')
        assert second and second[0].startswith("agent"), second
        assert not sh.pending

    def t_shell_edit_loop():
        class _Keys:
            def __init__(self, keys):
                self.keys = list(keys)

            def read(self):
                return self.keys.pop(0)

        import io
        from contextlib import redirect_stdout
        sh = Shell(AIlangConfig())
        with redirect_stdout(io.StringIO()):
            line, event = sh._edit(
                "> ", False,
                _Keys(["p", "r", "i", "n", "t", " ", "x", "ENTER"]))
        assert line == "print x" and event == "submit"
        with redirect_stdout(io.StringIO()):
            line, event = sh._edit("> ", False, _Keys(["a", "CTRL-C"]))
        assert line == "" and event == "cancel"
        with redirect_stdout(io.StringIO()):
            line, event = sh._edit("> ", False, _Keys(["CTRL-D"]))
        assert event == "eof"

    def t_fork_parts():
        interp = quiet()
        interp.run("""
            schema S { v: int }
            one = Agent { model: "x"  role: "one"  out: S }
            two = Agent { model: "x"  role: "two"  out: S }
            qa = Agent { model: "x"  role: "qa" }
            crowd = "q" >> (one || two)
            checked = "q" >> (one || two) >> qa? one.v >= 0 and two.v >= 0
            pick = if crowd.one.v >= 0 { "ok" } else { "bad" }
        """)
        assert isinstance(interp.env["crowd"], ForkValue)
        assert isinstance(interp.env["checked"], ForkValue)
        assert interp.env["pick"] == "ok"

    def t_editor_keys():
        ed = Editor()
        for ch in "one two":
            ed.feed(ch)
        ed.feed("CTRL-W")
        assert ed.buffer == "one " and ed.cursor == 4
        ed.feed("CTRL-K")
        assert ed.buffer == "one "
        ed.feed("CTRL-A")
        assert ed.cursor == 0
        ed.feed("CTRL-U")
        assert ed.buffer == "one "     # nothing before the cursor
        ed.feed("CTRL-E")
        ed.feed("CTRL-U")
        assert ed.buffer == ""
        ed.feed("a")
        ed.feed("CTRL-E")
        assert ed.cursor == 1

    def t_key_decoding():
        assert KeySource.decode("nt", "H") == "UP"
        assert KeySource.decode("nt", "S") == "DELETE"
        assert KeySource.decode("posix", "[D") == "LEFT"
        assert KeySource.decode("posix", "[Z") == ""
        assert KeySource.ctrl("\x15") == "CTRL-U"
        assert KeySource.ctrl("\x17") == "CTRL-W"
        assert KeySource.ctrl("a") == ""

    def t_shell_history():
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "hist"
            sh = Shell(AIlangConfig(), history_path=str(path))
            for line in ('x = "a"', 'y = "b"'):
                sh.editor.history.append(line)
            assert sh.save_history() is True
            fresh = Shell(AIlangConfig(), history_path=str(path))
            assert fresh.load_history() == ['x = "a"', 'y = "b"']
            fresh.editor.history = fresh.load_history()
            out = fresh.run_line(":history")
            assert any('x = "a"' in line for line in out), out

    def t_error_lines():
        import io
        from contextlib import redirect_stderr
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "bad.ai"
            p.write_text('w = Agent { model: "x"  role: "r" }\n'
                         'good = "q" >> w\n'
                         'bad = "q" >> nobody\n', encoding="utf-8")
            buf = io.StringIO()
            with redirect_stderr(buf):
                rc = cmd_run(argparse.Namespace(
                    file=str(p), journal=None, replay=None, live=False,
                    budget=None, stream=False))
            text = buf.getvalue()
            assert rc == 2 and "line 3" in text, text
            assert "Unknown name" in text, text
            buf = io.StringIO()
            with redirect_stderr(buf):
                rc = cmd_run(argparse.Namespace(
                    file=str(Path(td) / "absent.ai"), journal=None,
                    replay=None, live=False, budget=None, stream=False))
            assert rc == 1 and "cannot read" in buf.getvalue()

    def t_bom_source():
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "bom.ai"
            p.write_bytes(b"\xef\xbb\xbf"
                          b'w = Agent { model: "x"  role: "r" }\n'
                          b'out = "q" >> w\n')
            src = _read_source(p)
            assert not src.startswith("\ufeff")
            interp = quiet()
            assert isinstance(interp.run(src), str)

    def t_diagnostics():
        import io
        from contextlib import redirect_stderr, redirect_stdout
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "broken.ai"
            p.write_text('w = Agent { model: "x"  role: "r" }\n'
                         'bad = "q" >> ghost\n', encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(buf):
                rc = cmd_check(argparse.Namespace(file=str(p), offline=False))
            text = buf.getvalue()
            assert rc == 1, text
            assert "AIL001" in text and "ghost" in text, text
            assert "line 2" in text and "^" in text, text

    def t_static_checks():
        import io
        from contextlib import redirect_stderr, redirect_stdout
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "kinds.ai"
            p.write_text('a = Agent { model: "x"  role: "r"  out: NoSuch }\n'
                         'b = Agent { kind: nonsense  model: "x"  role: "r" }\n',
                         encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(buf):
                rc = cmd_check(argparse.Namespace(file=str(p), offline=False))
            text = buf.getvalue()
            assert rc == 1
            assert "AIL004" in text and "AIL002" in text, text

    def t_build_image():
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "prog.ai"
            src.write_text('schema N { v: int }\n'
                           'mk = Agent { model: "x"  role: "m"  out: N }\n'
                           'out = "q" >> mk\nprint out\n', encoding="utf-8")
            out = Path(td) / "prog.ailc"
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_build(argparse.Namespace(file=str(src),
                                                  output=str(out)))
            assert rc == 0 and out.exists(), buf.getvalue()
            image = json.loads(out.read_text(encoding="utf-8"))
            assert image["format"] == "ailang-image"
            assert image["grammar"] == grammar_fingerprint()
            assert image["lines"][2] == 3
            direct, from_image = [], []
            Interpreter(CFG, out=direct.append).run(
                src.read_text(encoding="utf-8"), str(src))
            Interpreter(CFG, out=from_image.append).run_image(image)
            assert direct == from_image and direct

    def t_build_rejects():
        import io
        from contextlib import redirect_stderr
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "bad.ai"
            bad.write_text('x = "q" >> ghost\n', encoding="utf-8")
            out = Path(td) / "bad.ailc"
            buf = io.StringIO()
            with redirect_stderr(buf):
                rc = cmd_build(argparse.Namespace(file=str(bad),
                                                  output=str(out)))
            text = buf.getvalue()
            assert rc == 2 and not out.exists(), text
            assert "AIL001" in text and "nothing was built" in text, text

    def t_image_guard():
        interp = Interpreter(CFG, out=lambda s: None)
        try:
            interp.run_image({"format": "something-else"})
            raise AssertionError("a foreign file must be rejected")
        except AILangRuntimeError as e:
            assert "not an AI-lang image" in str(e), e
        image = build_image('x = "q" >> echo\n', "<t>")
        image["grammar"] = "deadbeef"
        try:
            interp.run_image(image)
            raise AssertionError("a stale grammar stamp must be rejected")
        except AILangRuntimeError as e:
            assert "rebuild" in str(e), e

    def t_assert():
        interp = quiet()
        interp.run('x = "a"\nassert x != ""')
        try:
            interp.run('y = "a"\nassert y == "b"')
            raise AssertionError("a false assert must raise")
        except AILangRuntimeError as e:
            assert "assertion failed" in str(e), e
            assert "line 2" in str(e), e

    def t_test_runner():
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as td:
            good = Path(td) / "good.ai"
            good.write_text('x = "a"\nassert x != ""\n', encoding="utf-8")
            bad = Path(td) / "bad.ai"
            bad.write_text('x = "q" >> ghost\n', encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_test(argparse.Namespace(paths=[td]))
            text = buf.getvalue()
            assert rc == 1 and "ok" in text and "FAIL" in text, text
            assert "1/2 passed" in text, text

    def t_repo_tests():
        import io
        from contextlib import redirect_stdout
        root = Path(__file__).resolve().parent
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cmd_test(argparse.Namespace(paths=[str(root / "tests")]))
        assert rc == 0, buf.getvalue()

    def t_image_links():
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as td:
            lib = Path(td) / "lib.ai"
            lib.write_text('base = Agent { model: "x"  role: "base" }\n',
                           encoding="utf-8")
            main_ai = Path(td) / "main.ai"
            main_ai.write_text('use "lib.ai"\nout = "q" >> base\n'
                               'print out\n', encoding="utf-8")
            out = Path(td) / "main.ailc"
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_build(argparse.Namespace(file=str(main_ai),
                                                  output=str(out)))
            assert rc == 0, buf.getvalue()
            image = json.loads(out.read_text(encoding="utf-8"))
            assert image["libraries"], image
            assert "UseStmt" not in json.dumps(image["statements"])
            lib.unlink()
            printed = []
            Interpreter(CFG, out=printed.append).run_image(image)
            assert printed, "the linked image produced no output"

    def t_repo_hygiene():
        root = Path(__file__).resolve().parent
        cyr = re.compile("[\u0400-\u04FF]")
        bad = []
        for p in root.rglob("*"):
            if not p.is_file() or p.suffix not in {".py", ".md", ".ai",
                                                   ".toml"}:
                continue
            if ".zwork" in p.parts or "__pycache__" in p.parts:
                continue
            rel = p.relative_to(root)
            if any(part.startswith("_") for part in rel.parts[:-1]):
                continue
            raw = p.read_bytes()
            if raw.startswith(b"\xef\xbb\xbf"):
                bad.append(f"{p.name}: BOM")
            text = raw.decode("utf-8", "replace")
            if cyr.search(text):
                bad.append(f"{p.name}: Cyrillic")
            if "\r" in text:
                bad.append(f"{p.name}: CR (use LF)")
            if p.suffix == ".md":
                body = re.sub(r"```.*?```", "CODE", text, flags=re.S)
                if "\n\n\n" in body:
                    bad.append(f"{p.name}: blank-line run")
        assert not bad, "; ".join(bad)

    def t_build_incremental():
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as td:
            lib = Path(td) / "lib.ai"
            lib.write_text('base = Agent { model: "x"  role: "b" }\n',
                           encoding="utf-8")
            src = Path(td) / "main.ai"
            src.write_text('use "lib.ai"\nprint "q" >> base\n',
                           encoding="utf-8")
            out = Path(td) / "main.ailc"
            args = argparse.Namespace(file=str(src), output=str(out),
                                      force=False)
            buf = io.StringIO()
            with redirect_stdout(buf):
                assert cmd_build(args) == 0, buf.getvalue()
            assert "compiled" in buf.getvalue()
            buf = io.StringIO()
            with redirect_stdout(buf):
                assert cmd_build(args) == 0, buf.getvalue()
            assert "up to date" in buf.getvalue(), buf.getvalue()
            lib.write_text('base = Agent { model: "x"  role: "b2" }\n',
                           encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                assert cmd_build(args) == 0, buf.getvalue()
            assert "compiled" in buf.getvalue(), buf.getvalue()

    def t_inspect():
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "p.ai"
            src.write_text('print "hello"\n', encoding="utf-8")
            out = Path(td) / "p.ailc"
            with redirect_stdout(io.StringIO()):
                cmd_build(argparse.Namespace(file=str(src),
                                             output=str(out), force=False))
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_inspect(argparse.Namespace(file=str(out)))
            text = buf.getvalue()
            assert rc == 0 and "ailang-image v1" in text, text
            assert "statements 1" in text and "matches" in text, text
            image = json.loads(out.read_text(encoding="utf-8"))
            image["grammar"] = "deadbeef"
            out.write_text(json.dumps(image), encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                cmd_inspect(argparse.Namespace(file=str(out)))
            assert "STALE" in buf.getvalue(), buf.getvalue()

    def t_run_offline():
        import io
        from contextlib import redirect_stderr
        with tempfile.TemporaryDirectory() as td:
            cloud = Path(td) / "cloud.ai"
            cloud.write_text('w = Agent { model: "gpt-4o"  role: "r" }\n'
                             'print "q" >> w\n', encoding="utf-8")
            buf = io.StringIO()
            with redirect_stderr(buf):
                rc = cmd_run(argparse.Namespace(
                    file=str(cloud), journal=None, replay=None, live=False,
                    budget=None, stream=False, offline=True))
            text = buf.getvalue()
            assert rc == 2 and "refusing" in text, text
            local = Path(td) / "local.ai"
            local.write_text('w = Agent { model: "mock-fast"  role: "r" }\n'
                             'print "q" >> w\n', encoding="utf-8")
            assert cmd_run(argparse.Namespace(
                file=str(local), journal=None, replay=None, live=False,
                budget=None, stream=False, offline=True)) == 0

    def t_fmt_directory():
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as td:
            messy = Path(td) / "messy.ai"
            messy.write_text('w = Agent { model: "x"\n   role: "r" }\n',
                             encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_fmt(argparse.Namespace(file=[td], check=True,
                                                write=False))
            assert rc == 1, buf.getvalue()
            with redirect_stdout(io.StringIO()):
                rc = cmd_fmt(argparse.Namespace(file=[td], check=False,
                                                write=True))
            assert rc == 0
            with redirect_stdout(io.StringIO()):
                rc = cmd_fmt(argparse.Namespace(file=[td], check=True,
                                                write=False))
            assert rc == 0

    def t_doctor():
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cmd_doctor(argparse.Namespace())
        text = buf.getvalue()
        assert rc == 0, text
        for key in ("ailang", "python", "lark", "grammar", "kinds",
                    "mode"):
            assert key in text, text

    def t_grammar_stamp():
        import io
        from contextlib import redirect_stderr, redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cmd_grammar(argparse.Namespace(expect=None))
        assert rc == 0 and grammar_fingerprint() in buf.getvalue()
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            assert cmd_grammar(argparse.Namespace(expect="deadbeef")) == 1
        with redirect_stdout(io.StringIO()):
            assert cmd_grammar(
                argparse.Namespace(expect=grammar_fingerprint())) == 0

    def t_error_codes():
        assert error_code(AILangRuntimeError(
            "Unknown name: 'x'")) == "AIL001"
        assert error_code(ArtifactError(
            "agent 'a' failed its out-contract")) == "AIL110"
        assert error_code(BudgetError("budget $0 exhausted")) == "AIL111"
        assert error_code(AILangRuntimeError(
            "use cycle: a.ai -> a.ai")) == "AIL020"
        assert error_code(AILangRuntimeError(
            "assertion failed: x == 1")) == "AIL101"
        assert error_code(AILangRuntimeError("something odd")) == "AIL100"
        import io
        from contextlib import redirect_stderr
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "a.ai"
            p.write_text('x = "a"\nassert x == "b"\n', encoding="utf-8")
            buf = io.StringIO()
            with redirect_stderr(buf):
                rc = cmd_run(argparse.Namespace(
                    file=str(p), journal=None, replay=None, live=False,
                    budget=None, stream=False, offline=False))
            text = buf.getvalue()
            assert rc == 2 and "AIL101" in text and "line 2" in text, text

    def t_check_cache():
        import io
        from contextlib import redirect_stderr, redirect_stdout
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ok.ai"
            p.write_text('w = Agent { model: "x"  role: "r" }\n'
                         'print "q" >> w\n', encoding="utf-8")

            def run():
                buf = io.StringIO()
                with redirect_stdout(buf), redirect_stderr(buf):
                    cmd_check(argparse.Namespace(file=str(p), offline=False,
                                                 cache_dir=td))
                return buf.getvalue()

            assert "cache: miss" in run()
            assert "cache: hit" in run()
            p.write_text('w = Agent { model: "x"  role: "r2" }\n'
                         'print "q" >> w\n', encoding="utf-8")
            assert "cache: miss" in run()

    def t_check_cache_grammar():
        import io
        from contextlib import redirect_stderr, redirect_stdout
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ok.ai"
            p.write_text('print "hello"\n', encoding="utf-8")
            src = p.read_text(encoding="utf-8")
            cache_dir = Path(td) / "cache"
            folder = cache_dir / "check"
            folder.mkdir(parents=True)
            key = _check_key(src)
            (folder / f"{key}.json").write_text(json.dumps({
                "key": key, "grammar": "old-grammar",
                "diagnostics": [],
            }), encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(buf):
                cmd_check(argparse.Namespace(file=str(p), offline=False,
                                             cache_dir=str(cache_dir)))
            assert "cache: miss" in buf.getvalue(), buf.getvalue()

    def t_plugin_dir():
        root = Path(__file__).resolve().parent
        load_agent_plugins()
        assert "count-words" in _KINDS, sorted(_KINDS)
        assert _KIND_SOURCES.get("count-words", "").endswith("counter.py")
        interp = quiet()
        interp.run('wc = Agent { kind: count-words  role: "c" }\n'
                   'n = "one two three" >> wc')
        assert interp.env["n"] == "3"
        assert (root / "plugins" / "counter.py").exists()

    def t_image_plugin():
        import io
        from contextlib import redirect_stdout
        load_agent_plugins()
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "p.ai"
            src.write_text('wc = Agent { kind: count-words  role: "c" }\n'
                           'print "a b" >> wc\n', encoding="utf-8")
            out = Path(td) / "p.ailc"
            with redirect_stdout(io.StringIO()):
                assert cmd_build(argparse.Namespace(file=str(src),
                                                    output=str(out),
                                                    force=True)) == 0
            image = json.loads(out.read_text(encoding="utf-8"))
            assert image["plugins"], image
            assert image["plugins"][0].endswith("counter.py")
            printed = []
            Interpreter(CFG, out=printed.append).run_image(image)
            assert printed == ["2"], printed
            buf = io.StringIO()
            with redirect_stdout(buf):
                cmd_inspect(argparse.Namespace(file=str(out)))
            assert "counter.py" in buf.getvalue()

    def t_test_json():
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as td:
            good = Path(td) / "good.ai"
            good.write_text('x = "a"\nassert x != ""\n', encoding="utf-8")
            bad = Path(td) / "bad.ai"
            bad.write_text('x = "q" >> ghost\n', encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_test(argparse.Namespace(paths=[td], json=True))
            data = json.loads(buf.getvalue())
            assert rc == 1
            assert data["total"] == 2 and data["failed"] == 1
            assert {t["status"] for t in data["tests"]} == {"ok", "fail"}
            assert any(t["error"] for t in data["tests"])

    def t_doctor_json():
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            assert cmd_doctor(argparse.Namespace(json=True)) == 0
        data = json.loads(buf.getvalue())
        for key in ("ailang", "python", "lark", "grammar", "kinds",
                    "mode"):
            assert key in data, data
        assert data["grammar"] == grammar_fingerprint()

    def t_check_json():
        import io
        from contextlib import redirect_stdout, redirect_stderr
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "bad.ai"
            p.write_text('x = "q" >> ghost\n', encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(buf):
                rc = cmd_check(argparse.Namespace(file=str(p), offline=False,
                                                  json=True, cache_dir=td))
            text = buf.getvalue()
            payload = text[text.find("{"):]
            data = json.loads(payload)
            assert rc == 1
            assert data["errors"] == 1 and data["cached"] is False
            assert data["diagnostics"][0]["code"] == "AIL001"

    import io
    from contextlib import redirect_stderr, redirect_stdout

    # ── v0.19.0: governance ──────────────────────────────
    def t_subtype_parse():
        src = ('schema Text { body: str }\n'
               'schema Summary { body: str, date: str }\n'
               'patch Summary { is_a: Text }\n')
        facts = collect_facts(parse_located(src))
        assert facts.parents["Summary"] == {"Text"}, facts.parents
        assert not facts.issues, facts.issues

    def t_subtype_merge():
        src = ('schema A { a: str }\n'
               'schema B { b: str }\n'
               'schema C { a: str, b: str }\n'
               'patch C { is_a: A }\n'
               'patch C { is_a: A  is_a: B }\n'
               'patch C { bonus: 1 }\n')
        facts = collect_facts(parse_located(src))
        assert facts.parents["C"] == {"A", "B"}, facts.parents
        assert [i[0] for i in facts.issues] == ["AIL100"], facts.issues

    def t_subtype_cycle():
        src = ('schema A { x: str }\n'
               'schema B { x: str }\n'
               'patch A { is_a: B }\n'
               'patch B { is_a: A }\n')
        diags = analyze(parse_located(src), src, "t.ai")
        assert any(d.code == "AIL030" for d in diags), [d.code for d in diags]
        assert "cycle" in next(d.message for d in diags
                               if d.code == "AIL030")

    def t_subtype_unknown_parent():
        src = ('schema A { x: str }\n'
               'patch A { is_a: Ghost }\n')
        diags = analyze(parse_located(src), src, "t.ai")
        assert any(d.code == "AIL031" for d in diags), [d.code for d in diags]

    def t_subtype_chain_ok():
        src = ('schema Text { body: str }\n'
               'schema Summary { body: str, date: str }\n'
               'patch Summary { is_a: Text }\n'
               'w = Agent { model: "mock"  role: "write"  out: Summary }\n'
               'r = Agent { model: "mock"  role: "read"  in: Text }\n'
               '"seed" >> w >> r\n')
        diags = analyze(parse_located(src), src, "t.ai")
        assert [d.code for d in diags] == [], [d.code for d in diags]

    def t_subtype_chain_error():
        src = ('schema Quote { line: str }\n'
               'schema Research { body: str, sources: str }\n'
               'poet = Agent { model: "mock"  role: "r"  out: Quote }\n'
               'analyst = Agent { model: "mock"  role: "r"  in: Research }\n'
               's = "seed" >> poet\n'
               'r = s >> analyst\n')
        diags = analyze(parse_located(src), src, "t.ai")
        bad = [d for d in diags if d.code == "AIL032"]
        assert len(bad) == 1, [d.code for d in diags]
        assert bad[0].message == (
            'agent "poet" produces Quote, but "analyst" expects Research: '
            'Quote is not a subtype of Research'), bad[0].message

    def t_subtype_runtime_accepts():
        interp = quiet()
        interp.run('schema Text { body: str }\n'
                   'schema Summary { body: str, date: str }\n'
                   'patch Summary { is_a: Text }\n'
                   'w = Agent { model: "mock"  role: "write"  out: Summary }\n'
                   'r = Agent { model: "mock"  role: "read"  in: Text }\n'
                   'a = "seed" >> w\n'
                   'b = a >> r\n')
        assert interp.env["r"].last_insight is not None

    def t_subtype_runtime_rejects():
        interp = quiet()
        try:
            interp.run('schema Research { body: str, sources: str }\n'
                       'r = Agent { model: "mock"  role: "x"  in: Research }\n'
                       '"{\\"body\\": \\"b\\"}" >> r\n')
        except ArtifactError as e:
            assert "not a subtype" in str(e), str(e)
            assert "clause in" in str(e), str(e)
        else:
            raise AssertionError("a foreign artifact must be rejected")

    def t_estimate_tokens():
        assert estimate_tokens("") == 0
        assert estimate_tokens("one two three") == 4
        assert estimate_tokens("x" * 40) == 10
        again = estimate_tokens("one two three")
        assert again == 4, again          # deterministic

    def t_flow_report():
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "f.ai"
            p.write_text('a = Agent { model: "mock"  role: "short" }\n'
                         'b = Agent { model: "mock"  role: "also short" }\n'
                         '"seed" >> a >> b\n', encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_flow(argparse.Namespace(file=str(p), json=True,
                                                 strict=True))
            data = json.loads(buf.getvalue())
            assert rc == 0, buf.getvalue()
            assert data["total_tok_in"] > 0
            assert [row["agent"] for row in data["agents"]] == ["a", "b"]
            assert all(row["fits"] for row in data["agents"])
            assert data["overflow"] == []

    def t_flow_overflow():
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "big.ai"
            p.write_text('big = Agent { model: "mock"  role: "r"  '
                         'window: 5 }\n'
                         '"' + "word " * 50 + '" >> big\n', encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(buf):
                rc = cmd_flow(argparse.Namespace(file=str(p), json=True,
                                                 strict=True))
            assert rc == 1, buf.getvalue()
            data = json.loads(buf.getvalue())
            assert data["overflow"] == ["big"]
            assert data["agents"][0]["max_tok_in"] > 5

    def t_flow_window():
        assert context_window("mock") == 8192
        assert context_window("mock", 64000) == 64000
        assert context_window("gpt-4o") == 128000
        assert context_window("claude-3-5-sonnet") == 200000
        assert context_window("gemini-1.5-pro") == 1000000
        assert context_window("py") is None

    def t_flow_sink():
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sink.ai"
            fat = " ".join(["word"] * 120)
            p.write_text(f'a = Agent {{ model: "mock"  role: "{fat}" }}\n'
                         'b = Agent { model: "mock"  role: "tiny" }\n'
                         '"seed" >> a\n'
                         '"seed" >> b\n', encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                cmd_flow(argparse.Namespace(file=str(p), json=True,
                                            strict=False))
            data = json.loads(buf.getvalue())
            assert data["attention_sinks"] == ["a"], data["agents"]

    def t_payroll_mock():
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "m.ai"
            p.write_text('a = Agent { model: "mock"  role: "r" }\n'
                         '"seed" >> a\n', encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_payroll(argparse.Namespace(file=str(p), json=True,
                                                    strict=False, budget=None))
            data = json.loads(buf.getvalue())
            assert rc == 0
            assert data["total_usd"] == 0.0, data
            assert data["agents"][0]["usd_per_call"] == 0.0

    def t_payroll_prices():
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "p.ai"
            p.write_text('a = Agent { model: "mock"  role: "r"  '
                         'price_in: 2.5  price_out: 10.0  casts: 1 }\n'
                         '"seed" >> a\n', encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                cmd_payroll(argparse.Namespace(file=str(p), json=True,
                                               strict=False, budget=None))
            data = json.loads(buf.getvalue())
            row = data["agents"][0]
            assert row["usd_per_call"] > 0, row
            assert data["total_usd"] > 0
            assert row["cast_attempts"] == 2

    def t_payroll_unknown():
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "u.ai"
            p.write_text('a = Agent { model: "acme-9000"  role: "r" }\n'
                         '"seed" >> a\n', encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_payroll(argparse.Namespace(file=str(p), json=True,
                                                    strict=False, budget=None))
            data = json.loads(buf.getvalue())
            assert rc == 0
            assert data["unknown_priced"] == ["a"], data
            assert data["agents"][0]["usd_per_call"] is None
            assert data["total_usd"] == 0.0

    def t_budget_cap():
        class _Costly:
            last_usage = None

            def complete_detailed(self, *a, **k):
                return Completion(text="ok", tokens_in=10, tokens_out=5,
                                  cost_usd=0.01)

        agent = AIAgent(name="earner", engine="mock", prompt="p",
                        config=CFG, budget_usd=0.001, _provider=_Costly())
        interp = quiet()
        try:
            asyncio.run(interp._ainvoke_agent(agent, "hi", "human"))
        except BudgetError as e:
            text = str(e)
            assert "earner" in text and "0.001000" in text, text
            assert "0.010000" in text, text
            assert "clause budget" in text, text
            assert error_code(e) == "AIL111"
        else:
            raise AssertionError("the wage cap must fire")

    def t_payroll_budget_strict():
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "b.ai"
            p.write_text('a = Agent { model: "gpt-4o"  role: "r" }\n'
                         '"seed" >> a\n', encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_payroll(argparse.Namespace(file=str(p), json=True,
                                                    strict=True, budget=0.0))
            data = json.loads(buf.getvalue())
            assert data["over_budget"] is True, data
            assert rc == 1

    def t_payroll_json():
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "j.ai"
            p.write_text('a = Agent { model: "mock"  role: "r" }\n'
                         '"seed" >> a\n', encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                cmd_payroll(argparse.Namespace(file=str(p), json=True,
                                               strict=False, budget=1.0))
            data = json.loads(buf.getvalue())
            for key in ("file", "agents", "total_usd", "unknown_priced",
                        "budget", "over_budget"):
                assert key in data, data
            row = data["agents"][0]
            for key in ("agent", "engine", "tok_in", "tok_out",
                        "usd_per_call", "usd_worst_case", "budget"):
                assert key in row, row

    def t_hungry_contract():
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "h.ai"
            src = ('schema Big { a: str, b: str, c: str, d: str }\n'
                   'a = Agent { model: "gpt-4o"  role: "a long role "  '
                   'out: Big  budget: 0.000001 }\n'
                   '"seed" >> a\n')
            p.write_text(src, encoding="utf-8")
            diags = analyze(parse_located(src), src, str(p))
            hungry = [d for d in diags if d.code == "AIL035"]
            assert hungry, [d.code for d in diags]
            assert hungry[0].severity == "warning"
            assert "contract cannot be satisfied" in hungry[0].message
            assert "wage" in hungry[0].message

    def t_contract_card():
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "c.ai"
            p.write_text('qa = Agent { model: "mock"  role: "audit" }\n'
                         'w = Agent { model: "mock"  role: "write"  '
                         'timeout: 30  casts: 1  budget: 0.5  judge: qa  '
                         'window: 64000 }\n'
                         '"seed" >> w >> qa? "no cliches"\n',
                         encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_contract(argparse.Namespace(file=str(p), json=True))
            data = json.loads(buf.getvalue())
            assert rc == 0, buf.getvalue()
            card = [a for a in data["agents"] if a["agent"] == "w"][0]
            assert card["supervisor"] == "qa"
            assert card["deadline"] == 30
            assert card["retries"] == 1
            assert card["wage_cap"] == 0.5
            assert card["window"] == 64000
            assert card["gates"] == ["no cliches"], card

    def t_contract_judge():
        src = ('w = Agent { model: "mock"  role: "x"  judge: ghost }\n'
               '"seed" >> w\n')
        diags = analyze(parse_located(src), src, "t.ai")
        bad = [d for d in diags if d.code == "AIL033"]
        assert bad and "ghost" in bad[0].message, [d.code for d in diags]

    def t_contract_numbers():
        src = ('w = Agent { model: "mock"  role: "x"  timeout: 0  '
               'casts: 0 }\n'
               '"seed" >> w\n')
        diags = analyze(parse_located(src), src, "t.ai")
        bad = [d for d in diags if d.code == "AIL034"]
        assert len(bad) == 2, [d.message for d in diags]
        text = " | ".join(d.message for d in bad)
        assert "timeout must be > 0" in text and "casts must be >= 1" in text

    def t_error_clauses():
        interp = quiet()
        try:
            interp.run('schema N { v: int }\n'
                       'w = Agent { model: "mock"  role: "x"  out: N }\n'
                       'qa = Agent { model: "mock"  role: "audit" }\n'
                       '"seed" >> w >> qa? v > 5\n')
        except AILangRuntimeError as e:
            text = str(e)
            assert "'w'" in text and "clause gate" in text, text
        else:
            raise AssertionError("a failing gate must raise")

        class _Slow:
            last_usage = None

            def complete_detailed(self, *a, **k):
                time.sleep(0.2)
                return Completion(text="late")

        agent = AIAgent(name="slowpoke", engine="mock", prompt="p",
                        config=CFG, timeout=0.01, _provider=_Slow())
        interp = quiet()
        try:
            asyncio.run(interp._ainvoke(agent, "hi", "human"))
        except TimeoutError as e:
            assert "slowpoke" in str(e) and "clause timeout" in str(e), str(e)
            assert error_code(e) == "AIL112"
        else:
            raise AssertionError("the deadline must fire")

    def t_contract_json():
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "k.ai"
            p.write_text('w = Agent { model: "mock"  role: "x" }\n'
                         '"seed" >> w\n', encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_contract(argparse.Namespace(file=str(p), json=True))
            data = json.loads(buf.getvalue())
            assert rc == 0
            assert set(data) == {"schema", "file", "agents",
                                 "diagnostics"}, data
            card = data["agents"][0]
            for key in ("role", "engine", "wage_cap", "deadline",
                        "supervisor", "retries", "gates", "in_schema",
                        "out_schema", "out_supertypes"):
                assert key in card, card

    def t_doctor_governance():
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_doctor(argparse.Namespace(json=True))
        data = json.loads(buf.getvalue())
        assert isinstance(data["subtypes"], int), data
        assert data["price_table"] == len(PRICE_TABLE), data
        assert data["windows"] == len(CONTEXT_WINDOWS)

    def t_replay_timeline():
        with tempfile.TemporaryDirectory() as td:
            j = Path(td) / "r.jsonl"
            Interpreter(CFG, out=lambda s: None,
                        journal=Journal(str(j))).run(
                'w = Agent { model: "x"  role: "w" }\n'
                'out = "q" >> w\n')
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_replay(argparse.Namespace(file=str(j), step=False,
                                                   json=True, program=None))
            data = json.loads(buf.getvalue())
            assert rc == 0
            assert data["steps"] == len(data["timeline"]) == 1, data
            assert data["agents"] == ["w"]
            assert data["dry"] is True
            assert data["timeline"][0]["n"] == 1
            assert data["timeline"][0]["in"]

    def t_replay_step():
        with tempfile.TemporaryDirectory() as td:
            j = Path(td) / "s.jsonl"
            Interpreter(CFG, out=lambda s: None,
                        journal=Journal(str(j))).run(
                'a = Agent { model: "x"  role: "a" }\n'
                'b = Agent { model: "x"  role: "b" }\n'
                '"seed" >> a >> b\n')
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_replay(argparse.Namespace(file=str(j), step=True,
                                                   json=False, program=None))
            lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
            assert rc == 0
            assert len(lines) == 2, lines
            assert lines[0].startswith("#1 a") and lines[1].startswith("#2 b")

    def t_replay_program():
        with tempfile.TemporaryDirectory() as td:
            j = Path(td) / "p.jsonl"
            src = ('w = Agent { model: "x"  role: "w" }\n'
                   'out = "q" >> w\nprint out\n')
            first_out = []
            Interpreter(CFG, out=first_out.append,
                        journal=Journal(str(j))).run(src)
            recorded = Path(td) / "p.ai"
            recorded.write_text(src, encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_replay(argparse.Namespace(
                    file=str(j), step=False, json=False,
                    program=str(recorded)))
            assert rc == 0
            assert first_out and first_out[0] in buf.getvalue(), first_out

    def t_replay_bad_journal():
        with tempfile.TemporaryDirectory() as td:
            j = Path(td) / "bad.jsonl"
            j.write_text("{not json}\n", encoding="utf-8")
            err = io.StringIO()
            with redirect_stderr(err):
                rc = cmd_replay(argparse.Namespace(file=str(j), step=False,
                                                   json=False, program=None))
            assert rc == 1
            assert "line 1" in err.getvalue(), err.getvalue()

    def t_facts_use():
        root = Path(__file__).resolve().parent
        _src, _stmts, facts = _facts_for(str(root / "corpus" / "flows.ai"))
        assert "helper" in facts.agents, list(facts.agents)
        assert facts.agents["helper"].engine == "gpt-4o"

    def t_payroll_idle():
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "i.ai"
            p.write_text('busy = Agent { model: "gpt-4o"  role: "r" }\n'
                         'idle = Agent { model: "gpt-4o"  role: "r" }\n'
                         '"seed" >> busy\n', encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                cmd_payroll(argparse.Namespace(file=str(p), json=True,
                                               strict=False, budget=None))
            data = json.loads(buf.getvalue())
            assert data["agents"][1]["calls"] == 0, data["agents"]
            used = data["agents"][0]["usd_worst_case"]
            assert abs(data["total_usd"] - used) < 1e-9, data["total_usd"]

    def t_flow_semantic_auditor():
        det = ('schema N { v: int }\n'
               'w = Agent { model: "mock"  role: "w"  out: N }\n'
               'qa = Agent { model: "mock"  role: "qa" }\n'
               '"seed" >> w >> qa? v > 5\n')
        assert collect_facts(parse_located(det)).calls()["qa"]["calls"] == 0
        sem = det.replace("qa? v > 5", 'qa? "reads well"')
        assert collect_facts(parse_located(sem)).calls()["qa"]["calls"] == 1

    def t_keyword_prefixes():
        for src in ('useful = "q"', 'printer = "y"', 'patched = "z"',
                    'android = "a"', 'iffy = "b"', 'matchx = "c"'):
            assert parse_located(src), src

    def t_keywords_reserved():
        src = ('schema N { v: int }\n'
               'w = Agent { model: "mock"  role: "r"  out: N }\n'
               'x = "s" >> w >> w? v > 5\n'
               'if x.v > 1 { print "big" } else { print "small" }\n'
               'r = match w("s") { "yes" >> w  _ >> w }\n'
               'until w? "ok" max 2 { print "loop" }\n')
        stmts = parse_located(src)
        assert len(stmts) == 6, len(stmts)

    def t_conformance_missing():
        src = ('schema Text { body: str, tone: str }\n'
               'schema Summary { body: str }\n'
               'patch Summary { is_a: Text }\n')
        diags = analyze(parse_located(src), src, "t.ai")
        bad = [d for d in diags if d.code == "AIL036"]
        assert bad, [d.code for d in diags]
        assert '"Summary"' in bad[0].message and '"tone"' in bad[0].message
        assert bad[0].severity == "error"

    def t_conformance_type():
        src = ('schema Text { body: str }\n'
               'schema Bad { body: int }\n'
               'patch Bad { is_a: Text }\n')
        diags = analyze(parse_located(src), src, "t.ai")
        bad = [d for d in diags if d.code == "AIL036"]
        assert bad and "expected str" in bad[0].message, [d.code for d in diags]

    def t_conformance_nested():
        src = ('schema Note { txt: str }\n'
               'schema Rich { txt: str, score: int }\n'
               'patch Rich { is_a: Note }\n'
               'schema Wrap { note: Note }\n'
               'schema Wrap2 { note: Rich }\n'
               'patch Wrap2 { is_a: Wrap }\n'
               'schema Opt { body: str, extra: str? }\n'
               'schema Slim { body: str }\n'
               'patch Slim { is_a: Opt }\n')
        diags = analyze(parse_located(src), src, "t.ai")
        assert [d.code for d in diags] == [], [d.code for d in diags]

    def t_contract_conformance():
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "c.ai"
            p.write_text('schema Text { body: str, tone: str }\n'
                         'schema Summary { body: str }\n'
                         'patch Summary { is_a: Text }\n'
                         'w = Agent { model: "mock"  role: "x"  out: Summary }\n'
                         '"seed" >> w\n', encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_contract(argparse.Namespace(file=str(p), json=True))
            data = json.loads(buf.getvalue())
            assert rc == 1, buf.getvalue()
            assert any(d["code"] == "AIL036" for d in data["diagnostics"]), data

    def t_journal_header():
        with tempfile.TemporaryDirectory() as td:
            j = Path(td) / "h.jsonl"
            program = Path(td) / "h.ai"
            src = 'w = Agent { model: "x"  role: "w" }\n"q" >> w\n'
            program.write_text(src, encoding="utf-8")
            Interpreter(CFG, out=lambda s: None,
                        journal=Journal(str(j))).run(src, str(program))
            lines = j.read_text(encoding="utf-8").splitlines()
            head = json.loads(lines[0])
            assert head["$journal"] == 2, head
            assert head["program"].replace("\\", "/").endswith("h.ai")
            assert head["source_hash"] and head["statements"] == 2
            assert len(lines) == 2, lines      # one call recorded
            buf = io.StringIO()
            with redirect_stdout(buf):
                assert cmd_stats(argparse.Namespace(file=str(j))) == 0
            text = buf.getvalue()
            assert "TOTAL" in text and "w" in text, text

    def t_replay_auto_program():
        with tempfile.TemporaryDirectory() as td:
            j = Path(td) / "a.jsonl"
            program = Path(td) / "a.ai"
            src = ('w = Agent { model: "x"  role: "w" }\n'
                   'out = "q" >> w\nprint out\n')
            program.write_text(src, encoding="utf-8")
            first = []
            Interpreter(CFG, out=first.append,
                        journal=Journal(str(j))).run(src, str(program))
            buf, err = io.StringIO(), io.StringIO()
            with redirect_stdout(buf), redirect_stderr(err):
                rc = cmd_replay(argparse.Namespace(
                    file=str(j), step=False, json=False, program=None,
                    timeline=False))
            assert rc == 0
            assert "recorded in the journal" in err.getvalue(), err.getvalue()
            assert first and first[0] in buf.getvalue(), buf.getvalue()

    def t_replay_timeline_flag():
        with tempfile.TemporaryDirectory() as td:
            j = Path(td) / "t.jsonl"
            program = Path(td) / "t.ai"
            src = 'w = Agent { model: "x"  role: "w" }\n"q" >> w\n'
            program.write_text(src, encoding="utf-8")
            Interpreter(CFG, out=lambda s: None,
                        journal=Journal(str(j))).run(src, str(program))
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_replay(argparse.Namespace(
                    file=str(j), step=False, json=False, program=None,
                    timeline=True))
            assert rc == 0
            assert "steps      1" in buf.getvalue(), buf.getvalue()

    def t_replay_old_journal():
        with tempfile.TemporaryDirectory() as td:
            j = Path(td) / "old.jsonl"
            j.write_text(json.dumps({"agent": "w", "engine": "mock",
                                     "in": "abc12345", "out": "hi",
                                     "tokens_in": 1, "tokens_out": 1,
                                     "cost_usd": 0.0, "dry": True}) + "\n",
                         encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_replay(argparse.Namespace(
                    file=str(j), step=False, json=False, program=None,
                    timeline=False))
            assert rc == 0
            assert "steps      1" in buf.getvalue(), buf.getvalue()

    def t_journal_legacy_append():
        with tempfile.TemporaryDirectory() as td:
            j = Path(td) / "v1.jsonl"
            old = json.dumps({"agent": "old", "engine": "mock", "in": "aaa",
                              "out": "first", "tokens_in": 0,
                              "tokens_out": 0, "cost_usd": 0.0,
                              "dry": True})
            j.write_text(old + "\n", encoding="utf-8")
            src = 'w = Agent { model: "x"  role: "w" }\n"q" >> w\n'
            Interpreter(CFG, out=lambda s: None,
                        journal=Journal(str(j))).run(src)
            lines = j.read_text(encoding="utf-8").splitlines()
            assert len(lines) == 2, lines
            assert json.loads(lines[0]).get("agent") == "old", lines[0]
            assert "$journal" not in lines[1]
            buf = io.StringIO()
            with redirect_stdout(buf):
                assert cmd_stats(argparse.Namespace(file=str(j))) == 0
            assert "old" in buf.getvalue()

    def t_gate_field_typo():
        src = ('schema Note { txt: str  score: int }\n'
               'mk = Agent { model: "mock"  role: "m"  out: Note }\n'
               'qa = Agent { model: "mock"  role: "q" }\n'
               '"t" >> mk >> qa? scroe >= 0\n')
        diags = analyze(parse_located(src), src, "t.ai")
        bad = [d for d in diags if d.code == "AIL037"]
        assert bad, [d.code for d in diags]
        assert '"scroe"' in bad[0].message, bad[0].message

    def t_gate_field_nested():
        src = ('schema Note { txt: str  score: int }\n'
               'schema Wrap { note: Note  ok: bool }\n'
               'mk = Agent { model: "mock"  role: "m"  out: Wrap }\n'
               'qa = Agent { model: "mock"  role: "q" }\n'
               '"t" >> mk >> qa? note.score >= 0\n')
        diags = analyze(parse_located(src), src, "t.ai")
        assert [d.code for d in diags] == [], [d.code for d in diags]

    def t_gate_type_mismatch():
        src = ('schema Note { txt: str }\n'
               'mk = Agent { model: "mock"  role: "m"  out: Note }\n'
               'qa = Agent { model: "mock"  role: "q" }\n'
               '"t" >> mk >> qa? txt > 5\n')
        diags = analyze(parse_located(src), src, "t.ai")
        bad = [d for d in diags if d.code == "AIL037"]
        assert bad and "compared with int" in bad[0].message, [d.message for d in diags]

    def t_gate_field_corpus():
        root = Path(__file__).resolve().parent
        for name in ("corpus/gates.ai", "corpus/schemas.ai",
                     "examples/org_review.ai"):
            src = (root / name).read_text(encoding="utf-8")
            codes = [d.code for d in analyze(parse_located(src), src, name)]
            assert "AIL037" not in codes, (name, codes)
            assert "AIL036" not in codes, (name, codes)

    def t_audit_ready():
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ok.ai"
            p.write_text('w = Agent { model: "mock"  role: "r" }\n'
                         '"seed" >> w\n', encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_audit(argparse.Namespace(file=str(p), json=True,
                                                  strict=False, budget=None))
            data = json.loads(buf.getvalue())
            assert rc == 0, buf.getvalue()
            assert data["verdict"] == "READY"
            assert data["syntax"] is True and data["errors"] == []
            assert data["flow"]["agents"] == 1
            assert data["payroll"]["total_usd"] == 0.0
            assert data["contracts"]["agents"] == 1
            assert data["schema"] == 1

    def t_audit_blocked():
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "bad.ai"
            p.write_text('schema Quote { line: str }\n'
                         'schema Research { body: str, sources: str }\n'
                         'poet = Agent { model: "mock"  role: "r"  out: Quote }\n'
                         'analyst = Agent { model: "mock"  role: "r"  in: Research }\n'
                         's = "seed" >> poet\n'
                         'r = s >> analyst\n', encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_audit(argparse.Namespace(file=str(p), json=True,
                                                  strict=False, budget=None))
            data = json.loads(buf.getvalue())
            assert rc == 1
            assert data["verdict"] == "BLOCKED"
            assert any(e["code"] == "AIL032" for e in data["errors"]), data

    def t_audit_strict():
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "warn.ai"
            p.write_text('schema Big { a: str, b: str, c: str, d: str }\n'
                         'a = Agent { model: "gpt-4o"  role: "a long role "  '
                         'out: Big  budget: 0.000001 }\n'
                         '"seed" >> a\n', encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                lenient = cmd_audit(argparse.Namespace(file=str(p), json=True,
                                                       strict=False,
                                                       budget=None))
            data = json.loads(buf.getvalue())
            assert lenient == 0 and data["warnings"], data
            buf = io.StringIO()
            with redirect_stdout(buf):
                hard = cmd_audit(argparse.Namespace(file=str(p), json=True,
                                                    strict=True,
                                                    budget=None))
            assert hard == 1, buf.getvalue()
            assert json.loads(buf.getvalue())["verdict"] == "BLOCKED"

    def t_audit_syntax():
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "broken.ai"
            p.write_text('w = Agent { model: "mock"\n', encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_audit(argparse.Namespace(file=str(p), json=False,
                                                  strict=False, budget=None))
            assert rc == 1
            out = buf.getvalue()
            assert "syntax     FAIL" in out, out
            assert "BLOCKED by 1 error(s)" in out, out

    def t_audit_budget():
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "money.ai"
            p.write_text('a = Agent { model: "gpt-4o"  role: "r" }\n'
                         '"seed" >> a\n', encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_audit(argparse.Namespace(file=str(p), json=True,
                                                  strict=True, budget=0.0))
            data = json.loads(buf.getvalue())
            assert rc == 1, data
            assert data["payroll"]["over_budget"] is True

    def t_loops_billed():
        with tempfile.TemporaryDirectory() as td:
            loop = Path(td) / "loop.ai"
            loop.write_text('schema N { v: int }\n'
                            'w = Agent { model: "gpt-4o"  role: "worker"  '
                            'out: N  casts: 1 }\n'
                            'until w? "done" max 3 {\n'
                            '    "seed" >> w\n'
                            '}\n', encoding="utf-8")
            single = Path(td) / "single.ai"
            single.write_text('schema N { v: int }\n'
                              'w = Agent { model: "gpt-4o"  role: "worker"  '
                              'out: N  casts: 1 }\n'
                              '"seed" >> w\n', encoding="utf-8")
            reports = []
            for path in (loop, single):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    cmd_payroll(argparse.Namespace(
                        file=str(path), json=True, strict=False, budget=None))
                reports.append(json.loads(buf.getvalue()))
            looped, once = reports
            assert looped["agents"][0]["calls"] == 3, looped
            assert once["agents"][0]["calls"] == 1
            assert abs(looped["total_usd"] - 3 * once["total_usd"]) < 1e-12

    def t_loops_nested():
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "n.ai"
            p.write_text('schema N { v: int }\n'
                         'w = Agent { model: "mock"  role: "worker"  out: N }\n'
                         'until w? "a" max 2 {\n'
                         '    until w? "b" max 3 {\n'
                         '        "seed" >> w\n'
                         '    }\n'
                         '}\n', encoding="utf-8")
            _src, _stmts, facts = _facts_for(str(p))
            assert facts.calls()["w"]["calls"] == 6, facts.calls()

    def t_artifact_var_single_count():
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "a.ai"
            p.write_text('schema N { v: int }\n'
                         'w = Agent { model: "mock"  role: "first"  out: N }\n'
                         'w2 = Agent { model: "mock"  role: "second" }\n'
                         'a = "seed" >> w\n'
                         'b = a >> w2\n', encoding="utf-8")
            _src, _stmts, facts = _facts_for(str(p))
            calls = facts.calls()
            assert calls["w"]["calls"] == 1, calls
            assert calls["w2"]["calls"] == 1, calls
            assert calls["w2"]["tok_in"] < 100, calls["w2"]

    def t_journal_v3_source():
        with tempfile.TemporaryDirectory() as td:
            j = Path(td) / "v3.jsonl"
            program = Path(td) / "p.ai"
            src = 'w = Agent { model: "x"  role: "w" }\n"q" >> w\n'
            program.write_text(src, encoding="utf-8")
            Interpreter(CFG, out=lambda s: None,
                        journal=Journal(str(j), embed_source=True)
                        ).run(src, str(program))
            head = json.loads(j.read_text(encoding="utf-8").splitlines()[0])
            assert head["$journal"] == 3, head
            assert head["source"] == src
            assert head["program"].replace("\\", "/").endswith("p.ai")

    def t_replay_without_file():
        with tempfile.TemporaryDirectory() as td:
            j = Path(td) / "solo.jsonl"
            program = Path(td) / "gone.ai"
            src = ('w = Agent { model: "x"  role: "w" }\n'
                   'out = "q" >> w\nprint out\n')
            program.write_text(src, encoding="utf-8")
            printed = []
            Interpreter(CFG, out=printed.append,
                        journal=Journal(str(j), embed_source=True)
                        ).run(src, str(program))
            program.unlink()                   # the program is gone
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_replay(argparse.Namespace(
                    file=str(j), step=False, json=False, program=None,
                    timeline=False))
            assert rc == 0, buf.getvalue()
            assert printed and printed[0] in buf.getvalue()

    def t_journal_v2_keeps_path():
        with tempfile.TemporaryDirectory() as td:
            j = Path(td) / "v2.jsonl"
            program = Path(td) / "k.ai"
            src = 'w = Agent { model: "x"  role: "w" }\n"q" >> w\n'
            program.write_text(src, encoding="utf-8")
            Interpreter(CFG, out=lambda s: None,
                        journal=Journal(str(j))).run(src, str(program))
            head = json.loads(j.read_text(encoding="utf-8").splitlines()[0])
            assert head["$journal"] == 2, head
            assert "source" not in head

    def t_check_library_cache():
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            lib = d / "lib.ai"
            lib.write_text('helper = Agent { model: "mock"  role: "h" }\n',
                           encoding="utf-8")
            main = d / "main.ai"
            main.write_text('use "lib.ai"\n"seed" >> helper\n',
                            encoding="utf-8")

            def run():
                buf = io.StringIO()
                with redirect_stdout(buf), redirect_stderr(buf):
                    cmd_check(argparse.Namespace(
                        file=str(main), offline=False, json=False,
                        cache_dir=str(d / "cache")))
                return buf.getvalue()

            assert "cache: miss" in run()
            assert "cache: hit" in run()
            lib.write_text(
                'helper = Agent { model: "mock"  role: "h"  out: Ghost }\n',
                encoding="utf-8")
            after = run()
            assert "cache: miss" in after, after
            assert "AIL004" in after and "lib.ai" in after, after

    def t_check_library_errors():
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "lib.ai").write_text(
                'helper = Agent { model: "mock"  role: "h"  out: Ghost }\n',
                encoding="utf-8")
            main = d / "main.ai"
            main.write_text('use "lib.ai"\n"seed" >> helper\n',
                            encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(buf):
                rc = cmd_check(argparse.Namespace(
                    file=str(main), offline=False, json=True,
                    cache_dir=str(d / "cache")))
            text = buf.getvalue()
            data = json.loads(text[text.find("{"):])
            assert rc == 1, text
            assert data["libraries"], data
            hit = [x for x in data["diagnostics"] if x["code"] == "AIL004"]
            assert hit and hit[0]["file"].endswith("lib.ai"), hit

    def t_check_library_clean():
        root = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as td:
            buf = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(buf):
                rc = cmd_check(argparse.Namespace(
                    file=str(root / "corpus" / "flows.ai"), offline=False,
                    json=True, cache_dir=td))
            text = buf.getvalue()
            data = json.loads(text[text.find("{"):])
            assert rc == 0, text
            assert any(str(x).endswith("lib.ai")
                       for x in data["libraries"]), data
            assert data["diagnostics"] == [], data

    def t_audit_multi():
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            first = d / "one.ai"
            first.write_text('a = Agent { model: "mock"  role: "a" }\n'
                             '"seed" >> a\n', encoding="utf-8")
            second = d / "two.ai"
            second.write_text('b = Agent { model: "mock"  role: "b" }\n'
                              'c = Agent { model: "mock"  role: "c" }\n'
                              '"seed" >> b >> c\n', encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_audit(argparse.Namespace(
                    file=[str(first), str(second)], json=True,
                    strict=False, budget=None))
            data = json.loads(buf.getvalue())
            assert rc == 0, buf.getvalue()
            assert data["verdict"] == "READY"
            assert data["totals"]["programs"] == 2
            assert data["totals"]["agents"] == 3, data["totals"]
            assert len(data["programs"]) == 2
            assert data["totals"]["tok_in"] == sum(
                p["flow"]["total_tok_in"] for p in data["programs"])
            assert "file" not in data          # aggregate shape

    def t_audit_multi_blocked():
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            good = d / "good.ai"
            good.write_text('a = Agent { model: "mock"  role: "a" }\n'
                            '"seed" >> a\n', encoding="utf-8")
            bad = d / "bad.ai"
            bad.write_text('schema Quote { line: str }\n'
                           'schema Research { body: str, sources: str }\n'
                           'poet = Agent { model: "mock"  role: "r"  out: Quote }\n'
                           'analyst = Agent { model: "mock"  role: "r"  in: Research }\n'
                           's = "seed" >> poet\n'
                           'r = s >> analyst\n', encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_audit(argparse.Namespace(
                    file=[str(good), str(bad)], json=True,
                    strict=False, budget=None))
            data = json.loads(buf.getvalue())
            assert rc == 1, buf.getvalue()
            assert data["verdict"] == "BLOCKED"
            assert data["totals"]["errors"] == 1, data["totals"]
            assert len(data["programs"]) == 2
            assert data["programs"][0]["errors"] == []

    def t_audit_single_flat():
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "one.ai"
            p.write_text('a = Agent { model: "mock"  role: "a" }\n'
                         '"seed" >> a\n', encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                cmd_audit(argparse.Namespace(file=str(p), json=True,
                                             strict=False, budget=None))
            data = json.loads(buf.getvalue())
            for key in ("file", "syntax", "flow", "payroll", "contracts",
                        "verdict", "programs", "totals"):
                assert key in data, (key, sorted(data))
            assert data["totals"]["programs"] == 1

    def t_audit_missing_file():
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            good = d / "good.ai"
            good.write_text('a = Agent { model: "mock"  role: "a" }\n'
                            '"seed" >> a\n', encoding="utf-8")
            buf = io.StringIO()
            err = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(err):
                rc = cmd_audit(argparse.Namespace(
                    file=[str(d / "nope.ai"), str(good)], json=True,
                    strict=False, budget=None))
            data = json.loads(buf.getvalue())
            assert rc == 1, buf.getvalue()
            assert data["programs"][0]["syntax"] is False
            assert data["programs"][1]["syntax"] is True

    def t_audit_directory():
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "one.ai").write_text('a = Agent { model: "mock"  role: "a" }\n'
                                      '"seed" >> a\n', encoding="utf-8")
            (d / "two.ai").write_text('b = Agent { model: "mock"  role: "b" }\n'
                                      '"seed" >> b\n', encoding="utf-8")
            hidden = d / "_archive"
            hidden.mkdir()
            (hidden / "old.ai").write_text('x = Agent { model: "mock"  role: "x" }\n',
                                           encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_audit(argparse.Namespace(file=[str(d)], json=True,
                                                  strict=False, budget=None))
            data = json.loads(buf.getvalue())
            assert rc == 0, buf.getvalue()
            assert data["totals"]["programs"] == 2, data["totals"]

    def t_audit_directory_blocked():
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "good.ai").write_text('a = Agent { model: "mock"  role: "a" }\n'
                                       '"seed" >> a\n', encoding="utf-8")
            (d / "bad.ai").write_text(
                'schema Quote { line: str }\n'
                'schema Research { body: str, sources: str }\n'
                'poet = Agent { model: "mock"  role: "r"  out: Quote }\n'
                'analyst = Agent { model: "mock"  role: "r"  in: Research }\n'
                's = "seed" >> poet\n'
                'r = s >> analyst\n', encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_audit(argparse.Namespace(file=[str(d)], json=True,
                                                  strict=False, budget=None))
            data = json.loads(buf.getvalue())
            assert rc == 1, buf.getvalue()
            assert data["totals"]["errors"] == 1
            named = [p["file"] for p in data["programs"]]
            assert any(x.endswith("bad.ai") for x in named), named

    def t_audit_empty_directory():
        with tempfile.TemporaryDirectory() as td:
            err = io.StringIO()
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                rc = cmd_audit(argparse.Namespace(file=[td], json=True,
                                                  strict=False, budget=None))
            assert rc == 1
            assert "no .ai programs" in err.getvalue(), err.getvalue()

    def t_image_audit():
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            src = d / "p.ai"
            src.write_text('a = Agent { model: "mock"  role: "a" }\n'
                           '"seed" >> a\n', encoding="utf-8")
            image = d / "p.ailc"
            with redirect_stdout(io.StringIO()):
                assert cmd_build(argparse.Namespace(
                    file=str(src), output=str(image), force=True)) == 0
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_audit(argparse.Namespace(file=str(image),
                                                  json=True, strict=False,
                                                  budget=None))
            data = json.loads(buf.getvalue())
            assert rc == 0, buf.getvalue()
            assert data["verdict"] == "READY"
            assert data["kind"] == "image", data.get("kind")
            assert data["statements"] == 2

    def t_image_stale():
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            src = d / "p.ai"
            src.write_text('a = Agent { model: "mock"  role: "a" }\n'
                           '"seed" >> a\n', encoding="utf-8")
            image = d / "p.ailc"
            with redirect_stdout(io.StringIO()):
                cmd_build(argparse.Namespace(file=str(src), output=str(image),
                                             force=True))
            blob = json.loads(image.read_text(encoding="utf-8"))
            blob["grammar"] = "deadbeefdeadbeef"
            image.write_text(json.dumps(blob), encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_audit(argparse.Namespace(file=str(image), json=True,
                                                  strict=False, budget=None))
            data = json.loads(buf.getvalue())
            assert rc == 1, buf.getvalue()
            assert data["verdict"] == "BLOCKED"
            assert data["errors"][0]["code"] == "AIL120", data["errors"]

    def t_image_flow():
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            src = d / "p.ai"
            src.write_text('a = Agent { model: "mock"  role: "a" }\n'
                           'b = Agent { model: "mock"  role: "b" }\n'
                           '"seed" >> a >> b\n', encoding="utf-8")
            image = d / "p.ailc"
            with redirect_stdout(io.StringIO()):
                cmd_build(argparse.Namespace(file=str(src), output=str(image),
                                             force=True))
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_flow(argparse.Namespace(file=str(image), json=True,
                                                 strict=False))
            data = json.loads(buf.getvalue())
            assert rc == 0, buf.getvalue()
            assert [row["agent"] for row in data["agents"]] == ["a", "b"]

    def t_flow_sink_text():
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "s.ai"
            fat = " ".join(["word"] * 80)
            p.write_text(f'a = Agent {{ model: "mock"  role: "{fat}" }}\n'
                         'b = Agent { model: "mock"  role: "tiny" }\n'
                         '"seed" >> a\n"seed" >> b\n', encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_flow(argparse.Namespace(file=str(p), json=False,
                                                 strict=False))
            out = buf.getvalue()
            assert rc == 0, out
            assert "attention sink: a" in out, out
            assert "heuristic" in out

    def t_cli_empty_program():
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "empty.ai"
            p.write_text("", encoding="utf-8")
            for cmd, extra in ((cmd_check, {"offline": False, "json": False,
                                            "cache_dir": td}),
                               (cmd_audit, {"json": False, "strict": False,
                                            "budget": None}),
                               (cmd_flow, {"json": False, "strict": False}),
                               (cmd_payroll, {"json": False, "strict": False,
                                              "budget": None}),
                               (cmd_contract, {"json": False})):
                buf, err = io.StringIO(), io.StringIO()
                with redirect_stdout(buf), redirect_stderr(err):
                    rc = cmd(argparse.Namespace(file=str(p), **extra))
                assert rc == 0, (cmd.__name__, buf.getvalue(), err.getvalue())
                assert "Traceback" not in err.getvalue()

    def t_cli_odd_sources():
        cases = {
            "comments": "# only a comment\n# another\n",
            "blank": "\n\n\n   \n",
            "bom": "\ufeff",
            "crlf": 'a = Agent { model: "mock"  role: "r" }\r\n"x" >> a\r\n',
        }
        for name, content in cases.items():
            with tempfile.TemporaryDirectory() as td:
                p = Path(td) / f"{name}.ai"
                p.write_text(content, encoding="utf-8", newline="")
                buf, err = io.StringIO(), io.StringIO()
                with redirect_stdout(buf), redirect_stderr(err):
                    rc = cmd_check(argparse.Namespace(
                        file=str(p), offline=False, json=False,
                        cache_dir=td))
                assert rc == 0, (name, buf.getvalue(), err.getvalue())
                assert "Traceback" not in err.getvalue()

    def t_fmt_skips_ignored():
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "a.ai").write_text('a  =  "q"\n', encoding="utf-8")
            hidden = d / "_archive"
            hidden.mkdir()
            (hidden / "b.ai").write_text('b  =  "q"\n', encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_fmt(argparse.Namespace(file=[str(d)], check=True,
                                                write=False))
            out = buf.getvalue()
            assert rc == 1, out
            assert "a.ai" in out, out
            assert "b.ai" not in out, out

    def t_gate_decl_run():
        interp = quiet()
        interp.run('schema N { v: int }\n'
                   'gate positive = v >= 0\n'
                   'w = Agent { model: "mock"  role: "w"  out: N }\n'
                   'qa = Agent { model: "mock"  role: "qa" }\n'
                   'ok = "seed" >> w >> qa? positive\n')
        assert interp.env["ok"] is not None
        assert "positive" in interp.gates

    def t_gate_decl_unknown():
        src = ('schema N { v: int }\n'
               'w = Agent { model: "mock"  role: "w"  out: N }\n'
               'qa = Agent { model: "mock"  role: "qa" }\n'
               '"seed" >> w >> qa? missing\n')
        diags = analyze(parse_located(src), src, "t.ai")
        bad = [d for d in diags if d.code == "AIL038"]
        assert bad and "missing" in bad[0].message, [d.code for d in diags]

    def t_gate_decl_duplicate():
        src = ('gate twice = v >= 0\n'
               'gate twice = v > 1\n')
        diags = analyze(parse_located(src), src, "t.ai")
        bad = [d for d in diags if d.code == "AIL039"]
        assert bad, [d.code for d in diags]
        assert error_code(AILangRuntimeError(
            "gate 'twice' is already declared (AIL039)")) == "AIL039"

    def t_gate_decl_semantic():
        src = ('schema N { v: int }\n'
               'gate readable = "the note reads well"\n'
               'w = Agent { model: "mock"  role: "w"  out: N }\n'
               'qa = Agent { model: "mock"  role: "qa" }\n'
               '"seed" >> w >> qa? readable\n')
        facts = collect_facts(parse_located(src))
        assert facts.calls()["qa"]["calls"] == 1, facts.calls()
        assert facts.gates["w"] == ['readable: the note reads well'], facts.gates

    def t_gate_decl_fmt():
        src = ('gate positive = v >= 0\n'
               'gate both = positive and "reads well"\n')
        out, _dropped = format_source(src)
        assert out == src, out
        assert [type(x).__name__ for x in parse_located(out)] == [
            "GateDecl", "GateDecl"]
        assert parse(out) == parse(src)

    def t_docs_counts():
        import re
        root = Path(__file__).resolve().parent
        real = len(globals().get("_SELFTEST_CHECKS") or [])
        assert real, "the check list is not exposed"
        readme = (root / "README.md").read_text(encoding="utf-8")
        found = {int(n) for n in re.findall(r"(\d+)/\d+ checks", readme)}
        found |= {int(n) for n in re.findall(r"\u2713 (\d+) checks", readme)}
        assert found == {real}, f"README says {sorted(found)}, real {real}"
        contributing = (root / "CONTRIBUTING.md").read_text(encoding="utf-8")
        said = {int(n) for n in re.findall(r"(\d+)/\d+, exit 0",
                                           contributing)}
        assert said == {real}, f"CONTRIBUTING says {sorted(said)}"

    def t_salary_runtime():
        class _Costly:
            last_usage = None

            def complete_detailed(self, *a, **k):
                return Completion(text="ok", tokens_in=10, tokens_out=5,
                                  cost_usd=0.01)

        agent = AIAgent(name="earner", engine="mock", prompt="p",
                        config=CFG, salary_usd=0.015, _provider=_Costly())
        interp = quiet()
        asyncio.run(interp._ainvoke_agent(agent, "hi", "human"))
        try:
            asyncio.run(interp._ainvoke_agent(agent, "again", "human"))
        except SalaryError as e:
            message = str(e)
            assert "earner" in message, message
            assert "0.015000" in message and "0.020000" in message, message
            assert "clause salary" in message, message
            assert error_code(e) == "AIL113"
        else:
            raise AssertionError("salary must stop the agent")

    def t_salary_static():
        src = ('a = Agent { model: "gpt-4o"  role: "r"  salary: 0.000001 }\n'
               '"seed" >> a\n')
        diags = analyze(parse_located(src), src, "t.ai")
        bad = [d for d in diags if d.code == "AIL041"]
        assert bad, [d.code for d in diags]
        assert bad[0].severity == "warning"
        assert "cannot cover the run" in bad[0].message

    def t_salary_reports():
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "s.ai"
            p.write_text('a = Agent { model: "mock"  role: "r"  '
                         'salary: 5.0 }\n"seed" >> a\n', encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                cmd_payroll(argparse.Namespace(file=str(p), json=True,
                                               strict=False, budget=None))
            row = json.loads(buf.getvalue())["agents"][0]
            assert row["salary"] == 5.0, row
            buf = io.StringIO()
            with redirect_stdout(buf):
                cmd_contract(argparse.Namespace(file=str(p), json=True))
            card = json.loads(buf.getvalue())["agents"][0]
            assert card["salary"] == 5.0, card

    def t_salary_free_agent():
        interp = quiet()
        interp.run('schema N { v: int }\n'
                   'w = Agent { model: "mock"  role: "w"  out: N  '
                   'salary: 0 }\n'
                   '"s" >> w\n"t" >> w\n')
        assert interp._salary_spent["w"] == 0.0

    def t_invariant_patch():
        interp = quiet()
        try:
            interp.run('schema Price { amount: float where amount >= 0 }\n'
                       'p = Agent { model: "mock"  role: "p"  out: Price }\n'
                       'x = "seed" >> p\n'
                       'patch x { amount: -5 }\n')
        except AILangRuntimeError as e:
            message = str(e)
            assert "invariant" in message, message
            assert "clause where" in message, message
        else:
            raise AssertionError("a negative amount must be rejected")

    def t_invariant_passes():
        interp = quiet()
        interp.run('schema Price { amount: float where amount >= 0 }\n'
                   'p = Agent { model: "mock"  role: "p"  out: Price }\n'
                   'x = "seed" >> p\n'
                   'patch x { amount: 5 }\n')
        assert interp.env["x"].data["amount"] == 5

    def t_invariant_static():
        typo = 'schema Price { amount: float where ammount >= 0 }\n'
        diags = analyze(parse_located(typo), typo, "t.ai")
        assert [d.code for d in diags] == ["AIL042"], [d.code for d in diags]
        mismatch = 'schema Note { body: str where body > 5 }\n'
        diags = analyze(parse_located(mismatch), mismatch, "t.ai")
        assert any(d.code == "AIL042" for d in diags), [d.code for d in diags]

    def t_invariant_cast():
        interp = quiet()
        try:
            interp.run('schema Big { n: float where n > 100 }\n'
                       'a = Agent { model: "mock"  role: "a"  out: Big }\n'
                       '"seed" >> a\n')
        except ArtifactError as e:
            assert "invariant" in str(e), str(e)
        else:
            raise AssertionError("the cast must enforce the invariant")

    def t_signed_literals():
        assert parse_located('patch x { amount: -5 }')[0].fields == [
            ("amount", -5)]
        gate = parse_located('gate ok = v > -1')[0]
        assert gate.criterion.right == -1, gate.criterion
        src = 'schema P { amount: float where amount >= -2 }\n'
        assert analyze(parse_located(src), src, "t.ai") == []

    def t_invariant_fmt():
        src = 'schema Price { amount: float where amount >= 0  note: str? }\n'
        out, _dropped = format_source(src)
        assert out == src, out
        assert parse(out) == parse(src)
        assert format_source(out)[0] == out

    def t_gate_named_rendering():
        src = ('schema Brief { topic: str  budget: float }\n'
               'gate has_topic = topic != ""\n'
               'gate priced = budget >= 0\n'
               'w = Agent { model: "mock"  role: "w"  out: Brief  '
               'salary: 0.5 }\n'
               'qa = Agent { model: "mock"  role: "q" }\n'
               '"seed" >> w >> qa? has_topic and priced\n')
        facts = collect_facts(parse_located(src))
        assert facts.gates["w"] == ['has_topic and priced'], facts.gates
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "s.ai"
            p.write_text(src, encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                cmd_contract(argparse.Namespace(file=str(p), json=False))
            out = buf.getvalue()
            assert "has_topic and priced" in out, out
            assert "salary      $0.5000 per run" in out, out

    def t_len_gate():
        interp = quiet()
        interp.run('schema S { tags: [str] }\n'
                   'gate any_tag = len(tags) >= 0\n'
                   'mk = Agent { model: "mock"  role: "m"  out: S }\n'
                   'qa = Agent { model: "mock"  role: "q" }\n'
                   'ok = "t" >> mk >> qa? any_tag\n')
        assert interp.env["ok"] is not None
        try:
            interp.run('schema S { tags: [str] }\n'
                       'gate needs_tag = len(tags) > 0\n'
                       'mk = Agent { model: "mock"  role: "m"  out: S }\n'
                       'qa = Agent { model: "mock"  role: "q" }\n'
                       '"t" >> mk >> qa? needs_tag\n')
        except AILangRuntimeError as e:
            assert "len(" in str(e) or "failed" in str(e), str(e)
        else:
            raise AssertionError("an empty list must fail len(tags) > 0")

    def t_len_static():
        typo = ('schema S { tags: [str] }\n'
                'mk = Agent { model: "mock"  role: "m"  out: S }\n'
                'qa = Agent { model: "mock"  role: "q" }\n'
                '"t" >> mk >> qa? len(tag) > 0\n')
        codes = [d.code for d in analyze(parse_located(typo), typo, "t.ai")]
        assert "AIL037" in codes, codes
        wrong = ('schema S { tags: [str] }\n'
                 'mk = Agent { model: "mock"  role: "m"  out: S }\n'
                 'qa = Agent { model: "mock"  role: "q" }\n'
                 '"t" >> mk >> qa? len(tags) > "x"\n')
        codes = [d.code for d in analyze(parse_located(wrong), wrong, "t.ai")]
        assert "AIL037" in codes, codes

    def t_len_invariant():
        interp = quiet()
        try:
            interp.run('schema S { tags: [str] where len(tags) > 3 }\n'
                       'a = Agent { model: "mock"  role: "a"  out: S }\n'
                       '"t" >> a\n')
        except ArtifactError as e:
            assert "len(tags) > 3" in str(e), str(e)
        else:
            raise AssertionError("the invariant must block the cast")
        interp = quiet()
        interp.run('schema S { tags: [str] where len(tags) >= 0 }\n'
                   'a = Agent { model: "mock"  role: "a"  out: S }\n'
                   '"t" >> a\n')

    def t_until_deterministic():
        src = ('schema N { v: int }\n'
               'w = Agent { model: "mock"  role: "w"  out: N }\n'
               'bad = Agent { kind: py  run: "no_such_module.f" }\n'
               'until bad? v >= 0 max 2 {\n'
               '    n = "seed" >> w\n'
               '}\n')
        interp = quiet()
        interp.run(src)                      # the py auditor is never used
        assert interp.env["n"].data["v"] == 0

    def t_until_feedback():
        src = ('schema N { v: int }\n'
               'w = Agent { model: "mock"  role: "w"  out: N }\n'
               'bad = Agent { kind: py  run: "no_such_module.f" }\n'
               'until bad? v >= 9 max 2 {\n'
               '    n = "seed" >> w\n'
               '}\n')
        interp = quiet()
        try:
            interp.run(src)
        except AILangRuntimeError as e:
            assert "2 attempts" in str(e), str(e)
            assert ">= 9" in str(e), str(e)
        else:
            raise AssertionError("a failing criterion must raise")

    def t_review_empty():
        with tempfile.TemporaryDirectory() as td:
            journal = Path(td) / "quiet.jsonl"
            journal.write_text(
                json.dumps({"agent": "w", "engine": "mock", "in": "a",
                            "out": "text", "tokens_in": 1, "tokens_out": 1,
                            "cost_usd": 0.0, "latency_ms": 1,
                            "note": "human", "dry": True}) + "\n",
                encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_review(argparse.Namespace(
                    file=[str(journal)], json=True, min_rate=None))
            data = json.loads(buf.getvalue())
            assert rc == 0, buf.getvalue()
            assert data["gates"] == [] and data["checks"] == 0, data

    def t_review_rates():
        with tempfile.TemporaryDirectory() as td:
            journal = Path(td) / "g.jsonl"
            rows = [
                {"agent": "qa", "engine": "mock", "in": "a", "out": "PASS ok",
                 "tokens_in": 5, "tokens_out": 2, "cost_usd": 0.001,
                 "latency_ms": 10, "note": "gate", "dry": True},
                {"agent": "qa", "engine": "mock", "in": "b",
                 "out": "FAIL because", "tokens_in": 5, "tokens_out": 3,
                 "cost_usd": 0.001, "latency_ms": 20, "note": "gate",
                 "dry": True},
                {"agent": "w", "engine": "mock", "in": "c", "out": "text",
                 "tokens_in": 5, "tokens_out": 1, "cost_usd": 0.0,
                 "latency_ms": 5, "note": "human", "dry": True},
            ]
            journal.write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n",
                encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_review(argparse.Namespace(
                    file=[str(journal)], json=True, min_rate=None))
            data = json.loads(buf.getvalue())
            assert rc == 0, buf.getvalue()
            gate = data["gates"][0]
            assert gate["agent"] == "qa" and gate["checks"] == 2, gate
            assert gate["passed"] == 1 and gate["failed"] == 1, gate
            assert gate["pass_rate"] == 0.5, gate
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                rc = cmd_review(argparse.Namespace(
                    file=[str(journal)], json=False, min_rate=0.9))
            assert rc == 1
            assert "below the 90% floor" in err.getvalue(), err.getvalue()

    def t_aggregate_static():
        strsum = ('schema S { tags: [str] }\n'
                  'mk = Agent { model: "mock"  role: "m"  out: S }\n'
                  'qa = Agent { model: "mock"  role: "q" }\n'
                  '"t" >> mk >> qa? sum(tags) > 0\n')
        codes = [d.code for d in analyze(parse_located(strsum), strsum,
                                         "t.ai")]
        assert "AIL037" in codes, codes
        scalar = ('schema S { n: int }\n'
                  'mk = Agent { model: "mock"  role: "m"  out: S }\n'
                  'qa = Agent { model: "mock"  role: "q" }\n'
                  '"t" >> mk >> qa? max(n) > 0\n')
        codes = [d.code for d in analyze(parse_located(scalar), scalar,
                                         "t.ai")]
        assert "AIL037" in codes, codes

    def t_aggregate_runtime():
        interp = quiet()
        interp.run('schema S { scores: [float] }\n'
                   'gate fine = sum(scores) >= 0 and avg(scores) >= 0\n'
                   'mk = Agent { model: "mock"  role: "m"  out: S }\n'
                   'qa = Agent { model: "mock"  role: "q" }\n'
                   'ok = "t" >> mk >> qa? fine\n')
        assert interp.env["ok"] is not None
        try:
            interp.run('schema S { scores: [float] }\n'
                       'gate bad = max(scores) > 5\n'
                       'mk = Agent { model: "mock"  role: "m"  out: S }\n'
                       'qa = Agent { model: "mock"  role: "q" }\n'
                       '"t" >> mk >> qa? bad\n')
        except AILangRuntimeError as e:
            assert "max" in str(e) or "failed" in str(e), str(e)
        else:
            raise AssertionError("max() of an empty list must fail")

    def t_invariant_dotted():
        ok_src = ('schema Inner { score: int }\n'
                  'schema Outer { inner: Inner where inner.score >= 0 }\n'
                  'a = Agent { model: "mock"  role: "a"  out: Outer }\n'
                  '"t" >> a\n')
        interp = quiet()
        interp.run(ok_src)
        bad_src = ('schema Inner { score: int }\n'
                   'schema Outer { inner: Inner where inner.score >= 99 }\n'
                   'a = Agent { model: "mock"  role: "a"  out: Outer }\n'
                   '"t" >> a\n')
        interp = quiet()
        try:
            interp.run(bad_src)
        except ArtifactError as e:
            assert "inner.score" in str(e), str(e)
        else:
            raise AssertionError("the nested invariant must block the cast")

    def t_review_window():
        with tempfile.TemporaryDirectory() as td:
            journal = Path(td) / "w.jsonl"
            rows = [
                {"ts": 1000.0, "agent": "qa", "engine": "mock", "in": "a",
                 "out": "PASS", "tokens_in": 1, "tokens_out": 1,
                 "cost_usd": 0.0, "latency_ms": 1, "note": "gate"},
                {"ts": 2000000000.0, "agent": "qa", "engine": "mock",
                 "in": "b", "out": "FAIL", "tokens_in": 1, "tokens_out": 1,
                 "cost_usd": 0.0, "latency_ms": 1, "note": "gate"},
            ]
            journal.write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n",
                encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_review(argparse.Namespace(
                    file=[str(journal)], json=True, min_rate=None,
                    since="2020-01-01", until=None))
            data = json.loads(buf.getvalue())
            assert rc == 0, buf.getvalue()
            gate = data["gates"][0]
            assert gate["checks"] == 1 and gate["failed"] == 1, gate

    def t_grammar_doc():
        text = _grammar_doc()
        assert "keywords" in text and "terminals" in text, text
        assert "gate" in text and "where" in text, text
        assert "NAME" in text and "STRING" in text, text

    def t_policy_merge():
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "p.ai"
            p.write_text('schema N { v: int }\n'
                         'policy { model: "mock"  salary: 0.5  timeout: 30 }\n'
                         'w = Agent { role: "writer"  out: N }\n'
                         '"seed" >> w\n', encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                cmd_contract(argparse.Namespace(file=str(p), json=True))
            card = json.loads(buf.getvalue())["agents"][0]
            assert card["salary"] == 0.5, card
            assert card["deadline"] == 30, card
            assert card["engine"] == "mock", card
            buf = io.StringIO()
            with redirect_stdout(buf):
                cmd_payroll(argparse.Namespace(file=str(p), json=True,
                                               strict=False, budget=None))
            row = json.loads(buf.getvalue())["agents"][0]
            assert row["salary"] == 0.5, row

    def t_policy_override():
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "o.ai"
            p.write_text('schema N { v: int }\n'
                         'policy { model: "mock"  timeout: 30  salary: 1.0 }\n'
                         'w = Agent { role: "writer"  out: N  timeout: 5 }\n'
                         '"seed" >> w\n', encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                cmd_contract(argparse.Namespace(file=str(p), json=True))
            card = json.loads(buf.getvalue())["agents"][0]
            assert card["deadline"] == 5, card
            assert card["salary"] == 1.0, card

    def t_policy_forbidden():
        src = ('policy { kind: py  run: "x.y" }\n'
               'w = Agent { model: "mock"  role: "w" }\n')
        codes = [d.code for d in analyze(parse_located(src), src, "t.ai")]
        assert codes.count("AIL043") == 2, codes
        interp = quiet()
        try:
            interp.run(src)
        except AILangRuntimeError as e:
            assert "cannot be a default" in str(e), str(e)
        else:
            raise AssertionError("a forbidden default must raise")

    def t_policy_fmt():
        src = ('policy { model: "mock"  salary: 0.5  timeout: 30 }\n'
               'schema N { v: int }\n')
        out, _dropped = format_source(src)
        assert out == src, out
        assert parse(out) == parse(src)

    def t_policy_register():
        interp = quiet()
        interp.run('schema N { v: int }\n'
                   'policy { model: "mock"  salary: 0.25 }\n'
                   'w = Agent { role: "w"  out: N }\n'
                   '"seed" >> w\n')
        agent = interp.env["w"]
        assert agent.salary_usd == 0.25, agent.salary_usd
        assert agent.engine == "mock", agent.engine

    def t_if_measure():
        interp = quiet()
        interp.run('schema Cart { prices: [float] }\n'
                   'mk = Agent { model: "mock"  role: "m"  out: Cart }\n'
                   'c = "x" >> mk\n'
                   'if sum(c.prices) <= 100 { print "cheap" } else '
                   '{ print "dear" }\n')
        src = ('schema Cart { prices: [float] }\n'
               'mk = Agent { model: "mock"  role: "m"  out: Cart }\n'
               'c = "x" >> mk\n'
               'if sum(c.prices) <= 100 { print "cheap" } else '
               '{ print "dear" }\n')
        codes = [d.code for d in analyze(parse_located(src), src, "t.ai")]
        assert codes == [], codes

    def t_invariant_dotted_static():
        src = ('schema Item { score: int }\n'
               'schema Outer { inner: Item  total: int '
               'where inner.score >= 0 }\n')
        codes = [d.code for d in analyze(parse_located(src), src, "t.ai")]
        assert codes == [], codes

    def t_policy_type():
        src = 'policy { model: "mock"  timeout: "soon" }\n'
        diags = analyze(parse_located(src), src, "t.ai")
        bad = [d for d in diags if d.code == "AIL043"]
        assert bad and "needs a number" in bad[0].message, [d.message
                                                            for d in diags]
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "bad.ai"
            p.write_text(src + 'w = Agent { role: "w" }\n', encoding="utf-8")
            buf, err = io.StringIO(), io.StringIO()
            with redirect_stdout(buf), redirect_stderr(err):
                rc = cmd_check(argparse.Namespace(
                    file=str(p), offline=False, json=False, cache_dir=td))
            assert rc == 1
            out = buf.getvalue() + err.getvalue()
            assert "AIL043" in out and "Traceback" not in out, out

    def t_review_until_day():
        with tempfile.TemporaryDirectory() as td:
            journal = Path(td) / "today.jsonl"
            import time as _time
            journal.write_text(json.dumps({
                "ts": _time.time(), "agent": "qa", "engine": "mock",
                "in": "a", "out": "PASS", "tokens_in": 1, "tokens_out": 1,
                "cost_usd": 0.0, "latency_ms": 1, "note": "gate"}) + "\n",
                encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_review(argparse.Namespace(
                    file=[str(journal)], json=True, min_rate=None,
                    since=None, until=_time.strftime("%Y-%m-%d")))
            data = json.loads(buf.getvalue())
            assert rc == 0, buf.getvalue()
            assert data["checks"] == 1, data

    def t_review_bad_date():
        with tempfile.TemporaryDirectory() as td:
            journal = Path(td) / "j.jsonl"
            journal.write_text("\n", encoding="utf-8")
            err = io.StringIO()
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                rc = cmd_review(argparse.Namespace(
                    file=[str(journal)], json=True, min_rate=None,
                    since="yesterday", until=None))
            assert rc == 1
            assert "cannot read the date" in err.getvalue(), err.getvalue()
            assert "Traceback" not in err.getvalue()

    def t_init_project():
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "org"
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_init(argparse.Namespace(path=str(target),
                                                 force=False))
            assert rc == 0, buf.getvalue()
            for name in ("main.ai", "lib.ai", "tests/smoke.ai",
                         "README.md", ".gitignore"):
                assert (target / name).exists(), name
            main = target / "main.ai"
            printed = []
            Interpreter(CFG, out=printed.append).run(
                main.read_text(encoding="utf-8"), str(main))
            assert printed, "main.ai printed nothing"
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_audit(argparse.Namespace(file=[str(target)],
                                                  json=True, strict=True,
                                                  budget=None))
            data = json.loads(buf.getvalue())
            assert rc == 0 and data["verdict"] == "READY", data
            assert data["totals"]["errors"] == 0, data
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_fmt(argparse.Namespace(file=[str(target)],
                                                check=True, write=False))
            assert rc == 0, buf.getvalue()

    def t_init_non_empty():
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "keep.txt").write_text("x", encoding="utf-8")
            err = io.StringIO()
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                rc = cmd_init(argparse.Namespace(path=td, force=False))
            assert rc == 1
            assert "not empty" in err.getvalue(), err.getvalue()

    def t_library_schema_field():
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "lib.ai").write_text('schema Note { body: str }\n',
                                      encoding="utf-8")
            main = d / "main.ai"
            main.write_text('use "lib.ai"\n'
                            'schema Brief { note: Note  topic: str }\n',
                            encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(buf):
                rc = cmd_check(argparse.Namespace(file=str(main),
                                                  offline=False, json=True,
                                                  cache_dir=str(d)))
            data = json.loads(buf.getvalue()[buf.getvalue().find("{"):])
            assert rc == 0, data
            assert data["diagnostics"] == [], data

    def t_cache_revision_guard():
        src = 'print "x"\n'
        before = _check_key(src, None)
        old = ANALYZER_REVISION
        try:
            globals()["ANALYZER_REVISION"] = old + 1
            after = _check_key(src, None)
        finally:
            globals()["ANALYZER_REVISION"] = old
        assert before != after, "an analyser change must miss the cache"

    def t_salary_across_runs():
        class _Costly:
            last_usage = None

            def complete_detailed(self, *a, **k):
                return Completion(text='{"v": 0}', tokens_in=10,
                                  tokens_out=5, cost_usd=0.01)

        interp = quiet()
        interp.run('schema N { v: int }\n'
                   'w = Agent { model: "mock"  role: "w"  out: N  '
                   'salary: 0.005 }\n'
                   '"a" >> w\n')
        interp.env["w"]._provider = _Costly()
        try:
            interp.run('"b" >> w\n')
        except SalaryError as e:
            assert "0.005000" in str(e) and "0.010000" in str(e), str(e)
        else:
            raise AssertionError("a second run must exhaust the salary")

    def t_provider_http():
        """The real HTTP path, end to end, against a local stub server."""
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
                seen.append(payload)
                body = json.dumps({
                    "choices": [{"message": {"content": '{"v": 3}'}}],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 10},
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            cfg = AIlangConfig(live=True, dry_run=False)
            interp = Interpreter(cfg, out=lambda s: None)
            interp.run('schema N { v: int }\n'
                       'a = Agent { model: "gpt-4o"  role: "r"  out: N  '
                       f'endpoint: "http://127.0.0.1:{port}/v1" }}\n'
                       '"x" >> a\n')
            art = interp.env["a"].last_insight
            assert art.data["v"] == 3, art.data
            assert seen and seen[0]["model"] == "gpt-4o", seen[:1]
            assert seen[0]["messages"][0]["role"] == "system", seen[:1]
            # 20 in * 2.5 + 10 out * 10.0, per million tokens
            assert abs(interp.ledger.spent - 0.00015) < 1e-9, interp.ledger.spent
        finally:
            server.shutdown()

    def t_provider_stream_http():
        """SSE streaming through the same endpoint, usage included."""
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                json.loads(self.rfile.read(length) or b"{}")
                chunks = [
                    {"choices": [{"delta": {"content": '{"v": '}}]},
                    {"choices": [{"delta": {"content": "7}"}}]},
                    {"choices": [{"delta": {}}],
                     "usage": {"prompt_tokens": 8, "completion_tokens": 4}},
                ]
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for chunk in chunks:
                    self.wfile.write(b"data: " + json.dumps(chunk).encode()
                                     + b"\n\n")
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

            def log_message(self, *a):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        deltas = []
        try:
            cfg = AIlangConfig(live=True, dry_run=False, stream=True)
            interp = Interpreter(cfg, out=lambda s: None)
            interp.stream_out = deltas.append
            interp.run('schema N { v: int }\n'
                       'a = Agent { model: "gpt-4o-mini"  role: "r"  out: N  '
                       f'endpoint: "http://127.0.0.1:{port}/v1" }}\n'
                       '"x" >> a\n')
            assert interp.env["a"].last_insight.data["v"] == 7
            assert "".join(deltas).strip().endswith("7}"), deltas
            assert interp.ledger.spent > 0, interp.ledger.spent
        finally:
            server.shutdown()

    def t_unpriced_endpoint_is_free():
        assert _usage_cost("acme-9000", 1000, 1000) == 0.0
        assert _usage_cost("gpt-4o", 1_000_000, 0) == 2.5
        assert _usage_cost("gpt-4o", 0, 1_000_000) == 10.0

    def t_fmt_fuzz():
        """A deterministic generator: if a program parses, fmt is a fixpoint."""
        import random
        rng = random.Random(7)

        def schema(i):
            fields = []
            for j in range(rng.randint(1, 3)):
                kind = rng.choice(["str", "int", "float", "bool"])
                where = ""
                if rng.random() < 0.3:
                    if kind == "str":
                        where = ' where f%d != ""' % j
                    elif kind in ("int", "float"):
                        where = " where f%d >= 0" % j
                fields.append(f"f{j}: {kind}{where}")
            return f"schema S{i} {{ " + "  ".join(fields) + " }"

        def agent(i):
            schema_name = f"S{i % 3}"
            kind = rng.choice(["chat", "chat", "py"])
            if kind == "py":
                return f'a{i} = Agent {{ kind: py  run: "json.dumps" }}'
            return (f'a{i} = Agent {{ model: "mock"  role: "r{i}"  '
                    f"out: {schema_name} }}")

        def expr(i):
            left = f"a{i % 3}"
            shape = rng.choice(["flow", "gate", "until", "if", "match",
                                "fork", "merge", "escalate"])
            if shape == "flow":
                return f'x{i} = "seed" >> {left}'
            if shape == "gate":
                return f'x{i} = "seed" >> {left} >> {left}? f0 >= 0'
            if shape == "until":
                return (f'until {left}? "ok" max 2 {{ x{i} = "seed" >> '
                        f"{left} }}")
            if shape == "if":
                return (f'if f0 != "x" {{ print "a" }} else {{ print "b" }}')
            if shape == "match":
                return f'x{i} = match {left}("t") {{ "a" >> {left} }}'
            if shape == "fork":
                return f"x{i} = {left} || {left}"
            if shape == "merge":
                return f"x{i} = {left} + {left}"
            return f'x{i} = "seed" >> {left}? "fine" ?? {left}'

        parsed = 0
        for trial in range(120):
            block = [schema(0), schema(1), schema(2)]
            block += [agent(0), agent(1), agent(2)]
            if rng.random() < 0.4:
                block.append('gate fine = f0 != ""')
            if rng.random() < 0.4:
                block.append('policy { model: "mock"  salary: 0.5 }')
            block += [expr(0), expr(1), expr(2)]
            src = "\n".join(block) + "\n"
            try:
                first = parse(src)
            except AILangSyntaxError:
                continue                      # the generator is not a fuzzer
            parsed += 1
            out, dropped = format_source(src)
            assert parse(out) == first, f"trial {trial}: AST changed by fmt"
            assert format_source(out)[0] == out, f"trial {trial}: not idempotent"
            assert not dropped, f"trial {trial}: comments dropped"
        assert parsed >= 40, f"only {parsed} of 120 programs parsed"

    def t_error_catalog():
        import re
        root = Path(__file__).resolve().parent
        source = (root / "ailang.py").read_text(encoding="utf-8")
        used = set(re.findall(r"AIL\d{3}", source))
        catalog = set(ERROR_CODES)
        unknown = sorted(used - catalog)
        assert not unknown, f"codes used but not documented: {unknown}"
        for code, (severity, meaning) in ERROR_CODES.items():
            assert severity in ("error", "warning"), (code, severity)
            assert len(meaning) > 8, (code, meaning)
        readme = (root / "README.md").read_text(encoding="utf-8")
        missing = [c for c in sorted(catalog) if f"`{c}`" not in readme]
        assert not missing, f"README lacks: {missing}"
        assert "AIL021" in source

    def t_report_fuzz():
        """No generated program may crash the analysis or the reports."""
        import random
        rng = random.Random(11)

        def program():
            parts = ["schema S0 { v: int  tag: str where len(tag) >= 0 }",
                     'gate ok = v >= 0',
                     'policy { model: "mock"  salary: 0.5  timeout: 30 }']
            parts.append('a0 = Agent { role: "r0"  out: S0 }')
            parts.append('a1 = Agent { role: "r1"  out: S0  judge: a0 }')
            shape = rng.choice(["flow", "gate", "until", "if", "fork",
                                "merge", "escalate", "match"])
            if shape == "flow":
                parts.append('x = "s" >> a0 >> a1')
            elif shape == "gate":
                parts.append('x = "s" >> a0 >> a1? ok')
            elif shape == "until":
                parts.append('until a1? "ok" max 2 { y = "s" >> a0 }')
            elif shape == "if":
                parts.append('if v >= 0 { print "a" } else { print "b" }')
            elif shape == "fork":
                parts.append("x = a0 || a1")
            elif shape == "merge":
                parts.append("x = a0 + a1")
            elif shape == "escalate":
                parts.append('x = "s" >> a0? "fine" ?? a1')
            else:
                parts.append('x = match a0("s") { "a" >> a1 }')
            return "\n".join(parts) + "\n"

        for _ in range(60):
            src = program()
            try:
                stmts = parse_located(src)
            except AILangSyntaxError:
                continue
            analyze(stmts, src, "fuzz.ai")          # must not raise
            facts = collect_facts(stmts)
            flow_report(facts)
            payroll_report(facts, 1.0)
            for diag in analyze_facts(facts):
                assert diag.code in ERROR_CODES, diag.code

    def t_version_consistency():
        import tomllib
        root = Path(__file__).resolve().parent
        meta = tomllib.loads((root / "pyproject.toml").read_text(
            encoding="utf-8"))
        stated = meta["project"]["version"]
        assert stated == __version__, (stated, __version__)
        changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
        assert f"## [{__version__}]" in changelog, __version__
        assert "Unreleased" not in changelog.split("## [")[1][:40]

    def t_entry_point():
        import io as _io
        from contextlib import redirect_stdout as _out
        buf = _io.StringIO()
        try:
            with _out(buf):
                rc = main(["--version"])
        except SystemExit as exit_code:          # argparse exits on --version
            rc = exit_code.code or 0
        assert rc == 0, rc
        assert __version__ in buf.getvalue(), buf.getvalue()

    def t_run_fuzz_mock():
        """Generated programs must finish in mock mode, not hang."""
        import threading
        import random
        rng = random.Random(23)

        def program():
            parts = ['schema S { v: int  tag: str where len(tag) >= 0 }',
                     'gate ok = v >= 0',
                     'policy { model: "mock"  salary: 1.0 }',
                     'a = Agent { role: "r"  out: S }',
                     'b = Agent { role: "q" }']
            parts.append(rng.choice([
                'x = "s" >> a >> b? ok',
                'until b? "ok" max 2 { y = "s" >> a }',
                'x = "s" >> a? "fine" ?? b',
                'x = "s" >> a\ny = "s" >> a',
                'z = a || a',
            ]))
            return "\n".join(parts) + "\n"

        for _ in range(12):
            src = program()
            box = {}

            def go():
                try:
                    Interpreter(CFG, out=lambda s: None).run(src)
                    box["ok"] = True
                except AILangRuntimeError as e:
                    box["ok"] = True
                    box["error"] = str(e)
                except Exception as e:          # noqa: BLE001 — the point
                    box["ok"] = False
                    box["error"] = f"{type(e).__name__}: {e}"

            thread = threading.Thread(target=go, daemon=True)
            thread.start()
            thread.join(15)
            assert not thread.is_alive(), f"hang on:\n{src}"
            assert box.get("ok"), box.get("error")

    def _with_config(fn):
        """Run fn with an isolated settings file and clean env keys."""
        import os as _os
        import tempfile
        saved = {k: _os.environ.get(k) for k in
                 ("AILANG_CONFIG", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                  "GOOGLE_API_KEY")}
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / "config.json")
            _os.environ["AILANG_CONFIG"] = path
            for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                        "GOOGLE_API_KEY"):
                _os.environ.pop(key, None)
            try:
                fn(path)
            finally:
                for key, value in saved.items():
                    if value is None:
                        _os.environ.pop(key, None)
                    else:
                        _os.environ[key] = value

    def t_settings_roundtrip():
        def body(path):
            import os as _os
            assert provider_key("openai") is None
            save_settings({"providers": {"openai": {"key": "sk-pasted"}}})
            assert provider_key("openai") == "sk-pasted"
            # the pasted key wins, the environment is the fallback
            _os.environ["OPENAI_API_KEY"] = "sk-env"
            assert provider_key("openai") == "sk-pasted"
            save_settings({"providers": {}})
            assert provider_key("openai") == "sk-env"
            assert provider_base("openai").startswith("https://api.openai")
            save_settings({"providers": {"openai": {"base_url":
                                                    "http://localhost:1/v1"}}})
            assert provider_base("openai") == "http://localhost:1/v1"
        _with_config(body)

    def t_setup_cli():
        def body(path):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_setup(argparse.Namespace(
                    openai="sk-abcdefghijklmnop", anthropic=None, google=None,
                    endpoint=None, key=None, clear=None, json=False))
            assert rc == 0, buf.getvalue()
            assert "sk-a…mnop" in buf.getvalue(), buf.getvalue()
            data = load_settings()
            assert data["providers"]["openai"]["key"] == "sk-abcdefghijklmnop"
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_setup(argparse.Namespace(
                    openai=None, anthropic=None, google=None, endpoint=None,
                    key=None, clear=["openai"], json=True))
            payload = json.loads(buf.getvalue())
            assert rc == 0 and payload["providers"]["openai"]["key"] is False
            assert load_settings()["providers"] == {}
        _with_config(body)

    def t_provider_headers():
        """Each provider speaks its own dialect to a local stub."""
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                seen.append((self.path, dict(self.headers), body))
                if self.path.startswith("/messages"):
                    reply = {"content": [{"type": "text", "text": "hello"}],
                             "usage": {"input_tokens": 12,
                                       "output_tokens": 4}}
                else:
                    reply = {"choices": [{"message": {"content": "hi"}}],
                             "usage": {"prompt_tokens": 10,
                                       "completion_tokens": 2},
                             "candidates": [{"content": {"parts": [
                                 {"text": "g-hi"}]}}],
                             "usageMetadata": {"promptTokenCount": 5,
                                               "candidatesTokenCount": 3}}
                data = json.dumps(reply).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{port}"

        def body(path):
            save_settings({"providers": {
                "openai": {"key": "sk-oai", "base_url": base},
                "anthropic": {"key": "sk-ant", "base_url": base},
                "google": {"key": "AIza-g", "base_url": base}}})
            cfg = AIlangConfig(live=True, dry_run=False)
            def headers():
                return {k.lower(): v for k, v in seen[-1][1].items()}

            oai = OpenAIProvider("gpt-4o")
            comp = oai.complete_detailed("sys", "user", [], 0.2, cfg)
            assert comp.text == "hi", (comp.text, seen[-1][0])
            assert headers().get("authorization") == "Bearer sk-oai", \
                headers()
            assert abs(comp.cost_usd - 0.000045) < 1e-9, comp.cost_usd
            ant = AnthropicProvider("claude-3-5-sonnet")
            comp = ant.complete_detailed("sys", "user", [], 0.2, cfg)
            assert comp.text == "hello", comp.text
            assert headers().get("x-api-key") == "sk-ant", headers()
            assert headers().get("anthropic-version") == "2023-06-01"
            assert comp.tokens_in == 12 and comp.tokens_out == 4, \
                (comp.tokens_in, comp.tokens_out)
            goog = GoogleProvider("gemini-1.5-pro")
            comp = goog.complete_detailed("sys", "user", [], 0.2, cfg)
            assert comp.text == "g-hi", comp.text
            assert "key=AIza-g" in seen[-1][0], seen[-1][0]
            assert comp.tokens_in == 5 and comp.tokens_out == 3, \
                (comp.tokens_in, comp.tokens_out, seen[-1][0])

        try:
            _with_config(body)
        finally:
            server.shutdown()

    def t_provider_needs_key():
        def body(path):
            for factory, name in ((lambda: OpenAIProvider("gpt-4o"), "openai"),
                                  (lambda: AnthropicProvider("claude-3"), "a"),
                                  (lambda: GoogleProvider("gemini"), "g")):
                try:
                    factory()
                except AILangRuntimeError as e:
                    assert "ailang setup" in str(e), str(e)
                else:
                    raise AssertionError(f"{name} must refuse without a key")
        _with_config(body)

    def t_setup_probe():
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                data = json.dumps({
                    "choices": [{"message": {"content": "pong"}}],
                    "usage": {"prompt_tokens": 8, "completion_tokens": 1},
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()

        def body(path):
            save_settings({"providers": {"openai": {
                "key": "sk-probe", "base_url": f"http://127.0.0.1:{port}"}}})
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_setup(argparse.Namespace(
                    test="openai", openai=None, anthropic=None, google=None,
                    endpoint=None, key=None, clear=None, json=False))
            out = buf.getvalue()
            assert rc == 0, out
            assert "ok" in out and "pong" in out, out

        try:
            _with_config(body)
        finally:
            server.shutdown()

    def t_setup_probe_failure():
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                data = b'{"error": "invalid api key"}'
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()

        def body(path):
            save_settings({"providers": {"openai": {
                "key": "sk-bad", "base_url": f"http://127.0.0.1:{port}"}}})
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_setup(argparse.Namespace(
                    test="openai", openai=None, anthropic=None, google=None,
                    endpoint=None, key=None, clear=None, json=False))
            out = buf.getvalue()
            assert rc == 1, out
            assert "HTTP 401" in out, out
            assert "invalid api key" in out, out

        try:
            _with_config(body)
        finally:
            server.shutdown()

    def t_review_trend():
        with tempfile.TemporaryDirectory() as td:
            journal = Path(td) / "t.jsonl"
            import time as _time
            now = _time.time()
            rows = [
                {"ts": now - 172800, "agent": "qa", "engine": "mock",
                 "in": "a", "out": "PASS", "tokens_in": 1, "tokens_out": 1,
                 "cost_usd": 0.0, "latency_ms": 1, "note": "gate"},
                {"ts": now - 86400, "agent": "qa", "engine": "mock",
                 "in": "b", "out": "FAIL", "tokens_in": 1, "tokens_out": 1,
                 "cost_usd": 0.0, "latency_ms": 1, "note": "gate"},
                {"ts": now, "agent": "qa", "engine": "mock", "in": "c",
                 "out": "PASS", "tokens_in": 1, "tokens_out": 1,
                 "cost_usd": 0.0, "latency_ms": 1, "note": "gate"},
            ]
            journal.write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n",
                encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_review(argparse.Namespace(
                    file=[str(journal)], json=True, min_rate=None,
                    since=None, until=None, trend=True))
            data = json.loads(buf.getvalue())
            assert rc == 0, buf.getvalue()
            assert len(data["trend"]) == 3, data["trend"]
            assert [d["pass_rate"] for d in data["trend"]] == [1.0, 0.0, 1.0], \
                data["trend"]

    def t_measure_mapped():
        src = ('schema Line { price: float }\n'
               'schema Invoice { lines: [Line] }\n'
               'i = Agent { model: "mock"  role: "m"  out: Invoice }\n'
               'qa = Agent { model: "mock"  role: "q" }\n'
               '"x" >> i >> qa? sum(lines.price) >= 0\n')
        codes = [d.code for d in analyze(parse_located(src), src, "t.ai")]
        assert codes == [], codes
        interp = quiet()
        interp.run(src)
        assert interp.env["i"].last_insight is not None

    def t_measure_mapped_typo():
        src = ('schema Line { price: float }\n'
               'schema Invoice { lines: [Line] }\n'
               'i = Agent { model: "mock"  role: "m"  out: Invoice }\n'
               'qa = Agent { model: "mock"  role: "q" }\n'
               '"x" >> i >> qa? sum(lines.prices) > 0\n')
        codes = [d.code for d in analyze(parse_located(src), src, "t.ai")]
        assert "AIL037" in codes, codes

    def t_invariant_mapped():
        ok_src = ('schema Line { price: float }\n'
                  'schema Invoice { lines: [Line] '
                  'where sum(lines.price) >= 0 }\n'
                  'a = Agent { model: "mock"  role: "a"  out: Invoice }\n'
                  '"x" >> a\n')
        interp = quiet()
        interp.run(ok_src)
        bad_src = ('schema Line { price: float }\n'
                   'schema Invoice { lines: [Line] '
                   'where sum(lines.price) > 5 }\n'
                   'a = Agent { model: "mock"  role: "a"  out: Invoice }\n'
                   '"x" >> a\n')
        interp = quiet()
        try:
            interp.run(bad_src)
        except ArtifactError as e:
            assert "sum(lines.price) > 5" in str(e), str(e)
        else:
            raise AssertionError("the mapped invariant must block the cast")

    checks = [
        ("grammar: all constructs parse", t_grammar),
        ("schemas: typed artifact produced", t_contract),
        ("contracts: cast-repair uses original task", t_cast_retry),
        ("gates: deterministic pass (no LLM)", t_gate_det_pass),
        ("gates: deterministic failure raises", t_gate_det_fail),
        ("gates: semantic verdict", t_gate_semantic),
        ("gates: hybrid (semantic and field)", t_gate_hybrid),
        ("race: fast wins, slow cancelled", t_race),
        ("timeout: ?? rescues a stuck agent", t_timeout_fallback),
        ("use: library import, no false clashes", t_use),
        ("flow: first-class user morphism", t_flow_first_class),
        ("patch: validated artifact edit", t_patch),
        ("match: semantic routing", t_match),
        ("until: loop passes on verdict", t_until),
        ("merge (+): hybrid inherits schema", t_merge),
        ("in-schemas: edge contract enforced", t_in_schema),
        ("journal/replay: deterministic rerun", t_replay),
        ("graph IR: static mermaid projection", t_ir),
        ("demo: full offline program", t_demo),
        ("kinds: default (no kind) = v0.5 behaviour", t_kinds_default),
        ("kinds: py deterministic morphism", t_kinds_py),
        ("kinds: endpoint/env/reasoning kept (dry-run)", t_kinds_fields),
        ("plugins: use .py registers shout (offline)", t_plugins),
        ("kinds CLI: lists chat, py, human, echo, shout", t_kinds_cli),
        ("corpus: compatibility programs all green", t_corpus),
        ("escalation: PASS keeps the left answer", t_escalation_pass),
        ("escalation: FAIL hands over the original task", t_escalation_fail),
        ("stats: journal table has agents and totals", t_stats),
        ("examples: four flagship programs green", t_examples),
        ("check --offline: local vs networked agents", t_check_offline),
        ("check --offline: unknown model is networked", t_offline_strict),
        ("audit: typed agent can judge (no cast)", t_typed_auditor),
        ("stream: chunks via stream_out, audits quiet", t_stream),
        ("stats: mock journal is labelled dry-run", t_stats_mock_note),
        ("gates: verdict parsing tolerates markdown", t_verdict_parse),
        ("stream: usage tokens and cost are kept", t_stream_usage),
        ("check --offline: follows use libraries", t_offline_uses),
        ("stats: --json emits machine-readable totals", t_stats_json),
        ("judge: independent auditor beats self-review", t_judge_field),
        ("run: language errors exit 2 with a message", t_run_errors),
        ("repl: open braces keep reading more lines", t_repl_multiline),
        ("match: expression form binds the routed answer", t_match_expr),
        ("kinds: hyphenated name parses and runs", t_hyphen_kind),
        ("fork: || keeps per-agent parts in a string", t_fork_value),
        ("gates: nested field comparison works", t_nested_gate),
        ("use: cycles are reported, not recursed", t_use_cycle),
        ("use: paths resolve next to the importer", t_use_sibling),
        ("budget: exhausted budget raises", t_budget),
        ("plugins: missing register(api) is a clear error", t_plugin_errors),
        ("check --strict: non-zero for networked agents", t_check_strict),
        ("patch: nested field edit is validated", t_patch_nested),
        ("if: deterministic branch picks a value", t_if_basic),
        ("if: and/or/bool conditions decide", t_if_logic),
        ("if: missing else is None, unknown names raise", t_if_errors),
        ("fmt: round-trips corpus and examples", t_fmt_roundtrip),
        ("fmt: --check/--write keep files canonical", t_fmt_cli),
        ("fmt: header comments survive, inline ones reported", t_fmt_comments),
        ("shell: editor handles keys and history", t_editor),
        ("shell: tab completion inserts and lists", t_editor_complete),
        ("shell: values, commands and errors", t_shell),
        ("shell: open braces continue the line", t_shell_multiline),
        ("shell: edit loop submits, cancels, exits", t_shell_edit_loop),
        ("shell: Ctrl-U/K/W/A/E edit the line", t_editor_keys),
        ("shell: key codes decode per platform", t_key_decoding),
        ("shell: history persists across sessions", t_shell_history),
        ("fork: parts are addressable by branch name", t_fork_parts),
        ("errors: runtime failures carry the line number", t_error_lines),
        ("sources: a UTF-8 BOM is tolerated", t_bom_source),
        ("check: diagnostics carry codes and carets", t_diagnostics),
        ("check: unknown kind and schema caught statically", t_static_checks),
        ("build: image runs exactly like the source", t_build_image),
        ("build: static errors block the image", t_build_rejects),
        ("image: foreign or stale images are rejected", t_image_guard),
        ("assert: false conditions fail with a line", t_assert),
        ("test: runner reports pass and failure", t_test_runner),
        ("test: the repository suite is green", t_repo_tests),
        ("build: libraries are linked into the image", t_image_links),
        ("repo: sources are clean (English, LF, no BOM)", t_repo_hygiene),
        ("build: unchanged sources report up to date", t_build_incremental),
        ("inspect: header and staleness are reported", t_inspect),
        ("run --offline: networked agents are refused", t_run_offline),
        ("fmt: directories are formatted in place", t_fmt_directory),
        ("doctor: environment report is complete", t_doctor),
        ("grammar: fingerprint and --expect guard", t_grammar_stamp),
        ("errors: runtime failures carry codes", t_error_codes),
        ("check: results are cached and invalidated", t_check_cache),
        ("check: a grammar change invalidates the cache", t_check_cache_grammar),
        ("plugins: bundles in plugins/ are discovered", t_plugin_dir),
        ("build: plugin kinds travel inside the image", t_image_plugin),
        ("test: --json reports every file", t_test_json),
        ("doctor: --json reports the environment", t_doctor_json),
        ("check: --json is machine readable", t_check_json),
        ("types: patch declares a subtype", t_subtype_parse),
        ("types: repeated is_a edges merge", t_subtype_merge),
        ("types: inheritance cycle is AIL030", t_subtype_cycle),
        ("types: unknown parent is AIL031", t_subtype_unknown_parent),
        ("types: a subtype chain type-checks", t_subtype_chain_ok),
        ("types: a foreign chain is AIL032", t_subtype_chain_error),
        ("types: runtime accepts a qualified artifact",
         t_subtype_runtime_accepts),
        ("types: runtime rejects a stranger",
         t_subtype_runtime_rejects),
        ("flow: the token estimator is stable", t_estimate_tokens),
        ("flow: the report covers every agent", t_flow_report),
        ("flow: overflow is a warning, --strict fails", t_flow_overflow),
        ("flow: windows come from the registry or the field",
         t_flow_window),
        ("flow: an attention sink is flagged", t_flow_sink),
        ("payroll: mock work is free", t_payroll_mock),
        ("payroll: agent rates are honoured", t_payroll_prices),
        ("payroll: unknown engines print ?", t_payroll_unknown),
        ("payroll: the wage cap stops the call", t_budget_cap),
        ("payroll: --budget --strict exits 1", t_payroll_budget_strict),
        ("payroll: --json is machine readable", t_payroll_json),
        ("payroll: AIL035 hungry contract", t_hungry_contract),
        ("contract: the card lists every clause", t_contract_card),
        ("contract: an unknown judge is AIL033", t_contract_judge),
        ("contract: bad timeout/casts are AIL034", t_contract_numbers),
        ("contract: errors name the agent and the clause", t_error_clauses),
        ("contract: --json is machine readable", t_contract_json),
        ("doctor: governance lines are present", t_doctor_governance),
        ("facts: agents from `use` join the report", t_facts_use),
        ("payroll: idle agents are not billed", t_payroll_idle),
        ("flow: only semantic gates bill the auditor",
         t_flow_semantic_auditor),
        ("grammar: keyword prefixes are identifiers", t_keyword_prefixes),
        ("grammar: keywords stay reserved", t_keywords_reserved),
        ("types: conformance needs every parent field", t_conformance_missing),
        ("types: conformance checks field types", t_conformance_type),
        ("types: nested and optional fields are compatible",
         t_conformance_nested),
        ("contract: conformance issues are reported",
         t_contract_conformance),
        ("journal: v2 header names the program", t_journal_header),
        ("replay: the recorded program is used by default",
         t_replay_auto_program),
        ("replay: --timeline keeps the step view", t_replay_timeline_flag),
        ("replay: journals without a header still work",
         t_replay_old_journal),
        ("journal: a v1 file is appended, not rewritten",
         t_journal_legacy_append),
        ("gates: an unknown field is AIL037", t_gate_field_typo),
        ("gates: nested fields resolve", t_gate_field_nested),
        ("gates: a type mismatch is AIL037", t_gate_type_mismatch),
        ("gates: the corpus stays free of false positives",
         t_gate_field_corpus),
        ("audit: a clean program is READY", t_audit_ready),
        ("audit: a type error blocks the verdict", t_audit_blocked),
        ("audit: --strict promotes warnings", t_audit_strict),
        ("audit: a syntax error is reported plainly", t_audit_syntax),
        ("audit: --budget decides the verdict", t_audit_budget),
        ("audit: several programs share one verdict", t_audit_multi),
        ("audit: one broken program blocks the batch",
         t_audit_multi_blocked),
        ("audit: a single file keeps the flat report",
         t_audit_single_flat),
        ("audit: a missing file is reported, not fatal",
         t_audit_missing_file),
        ("audit: a directory expands to its programs", t_audit_directory),
        ("audit: a broken file in a directory blocks",
         t_audit_directory_blocked),
        ("audit: an empty directory is a clear error",
         t_audit_empty_directory),
        ("images: audit reads a compiled image", t_image_audit),
        ("images: a stale image blocks the audit", t_image_stale),
        ("images: flow reads a compiled image", t_image_flow),
        ("flow: the text report names its sink", t_flow_sink_text),
        ("cli: an empty program is handled everywhere",
         t_cli_empty_program),
        ("cli: comments, blanks, BOM and CRLF parse",
         t_cli_odd_sources),
        ("fmt: ignored folders are skipped",
         t_fmt_skips_ignored),
        ("gates: a declared gate is reusable", t_gate_decl_run),
        ("gates: an unknown name is AIL038",
         t_gate_decl_unknown),
        ("gates: a duplicate declaration is AIL039",
         t_gate_decl_duplicate),
        ("gates: a named semantic gate calls the auditor",
         t_gate_decl_semantic),
        ("gates: fmt round-trips declarations",
         t_gate_decl_fmt),
        ("salary: a run is stopped when it is exhausted",
         t_salary_runtime),
        ("salary: a too-small salary is AIL041",
         t_salary_static),
        ("salary: payroll and contract report it",
         t_salary_reports),
        ("salary: a free agent is never stopped",
         t_salary_free_agent),
        ("schemas: a where clause is enforced",
         t_invariant_patch),
        ("schemas: a satisfied invariant passes",
         t_invariant_passes),
        ("schemas: invariant typos are AIL042",
         t_invariant_static),
        ("schemas: a cast enforces the invariant",
         t_invariant_cast),
        ("grammar: negative literals parse",
         t_signed_literals),
        ("schemas: fmt keeps where clauses",
         t_invariant_fmt),
        ("gates: named criteria render by name",
         t_gate_named_rendering),
        ("docs: the stated check count is real", t_docs_counts),
        ("len: a list length is comparable", t_len_gate),
        ("len: static checks apply", t_len_static),
        ("len: invariants can measure", t_len_invariant),
        ("until: a deterministic criterion skips the model",
         t_until_deterministic),
        ("until: the failure names the comparison",
         t_until_feedback),
        ("review: gate pass rates come from journals",
         t_review_rates),
        ("review: no auditor calls is a clean report",
         t_review_empty),
        ("aggregates: static rules are enforced",
         t_aggregate_static),
        ("aggregates: sum/avg/max run", t_aggregate_runtime),
        ("schemas: nested paths are checked",
         t_invariant_dotted),
        ("review: --since filters the window",
         t_review_window),
        ("grammar: --doc lists the language", t_grammar_doc),
        ("policy: defaults reach every agent", t_policy_merge),
        ("policy: an agent field wins", t_policy_override),
        ("policy: identity fields are refused",
         t_policy_forbidden),
        ("policy: fmt keeps the block", t_policy_fmt),
        ("policy: the runtime applies it", t_policy_register),
        ("aggregates: if conditions use them", t_if_measure),
        ("schemas: a nested where passes the check",
         t_invariant_dotted_static),
        ("policy: a bad value is AIL043, not a crash",
         t_policy_type),
        ("review: --until covers the whole day",
         t_review_until_day),
        ("review: a bad date is a clean error",
         t_review_bad_date),
        ("init: the starter project runs", t_init_project),
        ("init: a busy folder needs --force",
         t_init_non_empty),
        ("check: a library schema fits a field",
         t_library_schema_field),
        ("check: an analyser change misses the cache",
         t_cache_revision_guard),
        ("salary: a second run is still capped",
         t_salary_across_runs),
        ("provider: a real HTTP call is billed",
         t_provider_http),
        ("provider: streaming collects usage",
         t_provider_stream_http),
        ("provider: an unknown model costs nothing",
         t_unpriced_endpoint_is_free),
        ("fmt: random programs round-trip", t_fmt_fuzz),
        ("docs: every code is documented", t_error_catalog),
        ("analysis: random programs never crash",
         t_report_fuzz),
        ("release: the version is consistent",
         t_version_consistency),
        ("release: the entry point answers", t_entry_point),
        ("run: random programs finish in mock",
         t_run_fuzz_mock),
        ("setup: keys are stored and masked",
         t_settings_roundtrip),
        ("setup: the command writes the config",
         t_setup_cli),
        ("provider: each dialect speaks to its API",
         t_provider_headers),
        ("provider: a missing key says what to do",
         t_provider_needs_key),
        ("setup: --test makes one real call",
         t_setup_probe),
        ("setup: a bad key is reported, not raised",
         t_setup_probe_failure),
        ("review: --trend groups by day", t_review_trend),
        ("aggregates: a list of records is summed",
         t_measure_mapped),
        ("aggregates: a mapped typo is AIL037",
         t_measure_mapped_typo),
        ("schemas: a mapped invariant is enforced",
         t_invariant_mapped),
        ("payroll: loops are billed max N times", t_loops_billed),
        ("payroll: nested loops multiply", t_loops_nested),
        ("payroll: an artifact variable is not a second call",
         t_artifact_var_single_count),
        ("journal: v3 embeds the program source", t_journal_v3_source),
        ("replay: works when the program file is gone",
         t_replay_without_file),
        ("journal: v2 still records just the path", t_journal_v2_keeps_path),
        ("check: a library edit invalidates the cache",
         t_check_library_cache),
        ("check: library diagnostics name their file",
         t_check_library_errors),
        ("check: a clean library stays quiet",
         t_check_library_clean),
        ("replay: the timeline is read without models", t_replay_timeline),
        ("replay: --step prints one line per step", t_replay_step),
        ("replay: --program re-runs on the journal", t_replay_program),
        ("replay: a malformed journal is a clear error",
         t_replay_bad_journal),
    ]
    globals()["_SELFTEST_CHECKS"] = checks
    for name, fn in checks:
        check(name, fn)

    print(f"AI-lang {__version__} selftest")
    passed = 0
    for name, ok, err in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}"
              + ("" if ok else f"  -> {err}"))
        passed += ok
    print(f"{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


# ══════════════ 13. AI-LANG SHELL — the bespoke terminal ═══════════

SHELL_KEYWORDS = (
    "schema", "tool", "flow", "use", "Agent", "kind", "model", "role",
    "out", "in", "temp", "timeout", "tools", "judge", "endpoint", "env",
    "match", "until", "max", "print", "if", "else", "true", "false",
    "race", "patch", "params", "desc",
)

SHELL_COMMANDS = {
    ":help": "this list of commands",
    ":agents": "agents and values declared in this session",
    ":kinds": "agent kinds known to the registry",
    ":graph": "mermaid projection of this session",
    ":stats": "cost/latency table from a journal file",
    ":fmt": "print a program in canonical style",
    ":run": "execute a .ai file in this session",
    ":load": "read a .ai file as input (same as :run)",
    ":clear": "clear the screen",
    ":history": "recall of the current session",
    ":quit": "leave the shell",
}


def _enable_ansi() -> bool:
    """Turn on ANSI colours on Windows consoles; report whether we can."""
    if os.name != "nt":
        return sys.stdout.isatty()
    try:
        import ctypes
        kernel = ctypes.windll.kernel32
        handle = kernel.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        kernel.SetConsoleMode(handle, mode.value | 0x0004)
        return True
    except Exception:                       # noqa: BLE001 — best effort
        return False


class Editor:
    """Line editing for the shell: keys in, buffer and cursor out.

    Kept free of any terminal I/O so the whole editor is unit-testable: a
    test feeds logical keys ("LEFT", "ENTER", "a") and checks the buffer.
    """

    def __init__(self, history=None, completer=None):
        self.buffer = ""
        self.cursor = 0
        self.history = list(history or [])
        self.completer = completer
        self.candidates = []
        self._index = None
        self._draft = ""

    def reset(self):
        self.buffer = ""
        self.cursor = 0
        self.candidates = []
        self._index = None
        self._draft = ""

    def feed(self, key: str) -> str:
        if key == "ENTER":
            line = self.buffer
            if line.strip() and (not self.history
                                 or self.history[-1] != line):
                self.history.append(line)
            self.reset()
            return "submit"
        if key == "CTRL-C":
            self.reset()
            return "cancel"
        if key == "CTRL-D":
            return "eof" if not self.buffer else "continue"
        if key == "CTRL-L":
            return "clear"
        if key == "CTRL-A":
            self.cursor = 0
            return "continue"
        if key == "CTRL-E":
            self.cursor = len(self.buffer)
            return "continue"
        if key == "CTRL-U":
            self.buffer = self.buffer[self.cursor:]
            self.cursor = 0
            return "continue"
        if key == "CTRL-K":
            self.buffer = self.buffer[:self.cursor]
            return "continue"
        if key == "CTRL-W":
            head = self.buffer[:self.cursor].rstrip()
            cut = head.rfind(" ") + 1 if head else 0
            self.buffer = self.buffer[:cut] + self.buffer[self.cursor:]
            self.cursor = cut
            return "continue"
        if key == "BACKSPACE":
            if self.cursor > 0:
                self.buffer = (self.buffer[:self.cursor - 1]
                               + self.buffer[self.cursor:])
                self.cursor -= 1
            return "continue"
        if key == "DELETE":
            if self.cursor < len(self.buffer):
                self.buffer = (self.buffer[:self.cursor]
                               + self.buffer[self.cursor + 1:])
            return "continue"
        if key == "LEFT":
            self.cursor = max(0, self.cursor - 1)
            return "continue"
        if key == "RIGHT":
            self.cursor = min(len(self.buffer), self.cursor + 1)
            return "continue"
        if key == "HOME":
            self.cursor = 0
            return "continue"
        if key == "END":
            self.cursor = len(self.buffer)
            return "continue"
        if key == "UP":
            self._recall(-1)
            return "continue"
        if key == "DOWN":
            self._recall(1)
            return "continue"
        if key == "TAB":
            self._complete()
            return "complete"
        if len(key) == 1 and key >= " ":
            self.buffer = (self.buffer[:self.cursor] + key
                           + self.buffer[self.cursor:])
            self.cursor += 1
        return "continue"

    def _recall(self, step: int):
        if not self.history:
            return
        if self._index is None:
            self._draft = self.buffer
            self._index = len(self.history)
        self._index = max(0, min(len(self.history), self._index + step))
        self.buffer = (self._draft if self._index == len(self.history)
                       else self.history[self._index])
        self.cursor = len(self.buffer)

    def _complete(self):
        self.candidates = []
        if not self.completer:
            return
        start = self.buffer.rfind(" ", 0, self.cursor) + 1
        prefix = self.buffer[start:self.cursor]
        options = sorted({c for c in self.completer(prefix)
                          if c.startswith(prefix)})
        if not options:
            return
        if len(options) == 1:
            self._replace_word(start, options[0])
            return
        common = os.path.commonprefix(options)
        if len(common) > len(prefix):
            self._replace_word(start, common)
        self.candidates = options

    def _replace_word(self, start: int, word: str):
        self.buffer = self.buffer[:start] + word + self.buffer[self.cursor:]
        self.cursor = start + len(word)


class KeySource:
    """Raw key events from a real console (Windows) or a POSIX tty."""

    CTRL = {"\x01": "CTRL-A", "\x03": "CTRL-C", "\x04": "CTRL-D",
            "\x05": "CTRL-E", "\x0b": "CTRL-K", "\x0c": "CTRL-L",
            "\x15": "CTRL-U", "\x17": "CTRL-W"}
    WIN_SPECIAL = {"H": "UP", "P": "DOWN", "K": "LEFT", "M": "RIGHT",
                   "G": "HOME", "O": "END", "S": "DELETE"}
    POSIX_ESCAPES = {"[A": "UP", "[B": "DOWN", "[C": "RIGHT",
                     "[D": "LEFT", "[H": "HOME", "[F": "END"}

    @classmethod
    def decode(cls, platform: str, code: str) -> str:
        """Map a platform-specific special-key code to a logical key."""
        table = cls.WIN_SPECIAL if platform == "nt" else cls.POSIX_ESCAPES
        return table.get(code, "")

    @classmethod
    def ctrl(cls, ch: str) -> str:
        return cls.CTRL.get(ch, "")

    def __init__(self):
        self.windows = os.name == "nt"
        self._fd = None
        self._saved = None

    def __enter__(self):
        if not self.windows:
            import termios
            import tty
            self._fd = sys.stdin.fileno()
            self._saved = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
        return self

    def __exit__(self, *exc):
        if not self.windows and self._saved is not None:
            import termios
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)
        return False

    def read(self) -> str:
        if self.windows:
            import msvcrt
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):
                return self.decode("nt", msvcrt.getwch())
            if ch == "\r":
                return "ENTER"
            if ch == "\x08":
                return "BACKSPACE"
            if ch == "\t":
                return "TAB"
            return self.ctrl(ch) or ch
        ch = os.read(self._fd, 1).decode("utf-8", "replace")
        if ch in ("\r", "\n"):
            return "ENTER"
        if ch in ("\x7f", "\x08"):
            return "BACKSPACE"
        if ch == "\t":
            return "TAB"
        mapped = self.ctrl(ch)
        if mapped:
            return mapped
        if ch == "\x1b":
            seq = os.read(self._fd, 2).decode("utf-8", "replace")
            if seq.startswith("[3"):
                os.read(self._fd, 1)
                return "DELETE"
            return self.decode("posix", seq)
        return ch


class Shell:
    """The AI-lang shell: one session, one environment, one prompt.

    `run_line()` is the whole brain and needs no terminal, so the shell can
    be tested like any other component; `interactive()` only adds keys,
    colours and redraw.
    """

    def __init__(self, config=None, history_path=None):
        self.config = config or AIlangConfig()
        self.lines: list = []
        self.program: list = []
        self.running = True
        self.pending = ""
        self.history_path = (Path(history_path) if history_path
                             else Path.home() / ".ailang_history")
        self.interp = Interpreter(self.config, out=self._collect)
        self.editor = Editor(completer=self.complete)

    # ── history ──────────────────────────────────────────
    def load_history(self, limit: int = 500) -> list:
        try:
            lines = self.history_path.read_text(
                encoding="utf-8").splitlines()
        except OSError:
            return []
        return [ln for ln in lines[-limit:] if ln.strip()]

    def save_history(self, limit: int = 500) -> bool:
        lines = self.editor.history[-limit:]
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            body = "\n".join(lines)
            self.history_path.write_text(body + ("\n" if body else ""),
                                         encoding="utf-8", newline="\n")
        except OSError:
            return False
        return True

    # ── plumbing ─────────────────────────────────────────
    def _collect(self, text):
        self.lines.append(str(text))

    def complete(self, prefix):
        names = (set(SHELL_KEYWORDS) | set(self.interp.env)
                 | set(self.interp.builtins) | set(SHELL_COMMANDS))
        return [n for n in names if n.startswith(prefix)]

    def mode(self):
        return "live" if self.config.live else "mock"

    def prompt_text(self, colour=False):
        if not colour:
            return f"insight[{self.mode()}]> "
        return (f"\033[36minsight\033[0m"
                f"\033[2m[{self.mode()}]\033[0m> ")

    def banner(self):
        return (f"Aligment Insight Shell {__version__} - {self.mode()} mode,"
                f" offline\n:help for commands, Ctrl-D to exit")

    def render(self, value) -> str:
        if isinstance(value, AIAgent):
            return f"agent  {value.name}  ({value.engine})"
        if isinstance(value, TypedArtifact):
            body = json.dumps(value.data, ensure_ascii=False)
            return f"{value.schema.name}  {body}"
        if isinstance(value, ForkValue):
            return f"fork  {len(value.parts)} part(s)"
        if isinstance(value, Sequence):
            return f"chain  {value.name}"
        if isinstance(value, Parallel):
            return f"parallel  {value.name}"
        if isinstance(value, Fallback):
            return f"backup  {value.name}"
        if isinstance(value, RaceRunner):
            return "race"
        if isinstance(value, str):
            return f"str  {json.dumps(value, ensure_ascii=False)}"
        if value is None:
            return ""
        return f"{type(value).__name__}  {value}"

    # ── commands ─────────────────────────────────────────
    def command(self, line: str) -> list:
        parts = line.split(maxsplit=1)
        name, arg = parts[0], (parts[1].strip() if len(parts) > 1 else "")
        if name in (":quit", ":q", ":exit"):
            self.running = False
            return ["bye"]
        if name == ":help":
            width = max(len(c) for c in SHELL_COMMANDS)
            return [f"  {c:<{width}}  {d}" for c, d in
                    sorted(SHELL_COMMANDS.items())]
        if name == ":clear":
            return ["\033[2J\033[H"]
        if name == ":history":
            items = self.editor.history[-40:]
            if not items:
                return ["  (empty)"]
            start = len(self.editor.history) - len(items) + 1
            return [f"  {n:>3}  {line}" for n, line in
                    enumerate(items, start)]
        if name == ":agents":
            rows = []
            for key, value in sorted(self.interp.env.items()):
                if isinstance(value, (AIAgent, PyAgent, Human, Echo,
                                      Schema, Tool, FlowValue)):
                    rows.append(f"  {key:<16} {self.render(value)}")
            return rows or ["  (nothing declared yet)"]
        if name == ":kinds":
            load_agent_plugins()
            return [f"  {k:<12} {'built-in' if k in _BUILTIN_KINDS else 'plugin'}"
                    for k in sorted(_KINDS)]
        if name == ":graph":
            if not self.program:
                return ["  (no statements yet)"]
            nodes, edges = build_ir(self.program)
            return to_mermaid(nodes, edges).splitlines()
        if name == ":stats":
            if not arg:
                return ["  usage: :stats FILE.jsonl"]
            buf = []
            saved = sys.stdout
            try:
                sys.stdout = _ListWriter(buf)
                cmd_stats(argparse.Namespace(file=arg, json=False))
            finally:
                sys.stdout = saved
            return buf
        if name == ":fmt":
            if not arg:
                return ["  usage: :fmt FILE.ai"]
            try:
                text, _ = format_source(_read_source(arg))
            except (OSError, AILangSyntaxError, FormatError) as e:
                return [f"  {e}"]
            return text.splitlines()
        if name in (":run", ":load"):
            if not arg:
                return ["  usage: :run FILE.ai"]
            try:
                src = _read_source(arg)
            except OSError as e:
                return [f"  {e}"]
            return self.evaluate(src)
        return [f"  unknown command {name!r} - try :help"]

    # ── evaluation ───────────────────────────────────────
    def evaluate(self, source: str) -> list:
        try:
            stmts = parse(source)
        except AILangSyntaxError as e:
            return [f"✗ syntax: {e}"]
        self.program.extend(stmts)
        for st in stmts:
            try:
                value = self.interp.exec(st)
            except AILangRuntimeError as e:
                return self.lines + [f"✗ {e}"]
            except Exception as e:          # noqa: BLE001 — shell guard
                return self.lines + [f"✗ {type(e).__name__}: {e}"]
            if not isinstance(st, Print) and value is not None:
                self.lines.append(self.render(value))
        return self.lines

    def run_line(self, line: str) -> list:
        self.lines = []
        text = line.rstrip()
        if not text.strip():
            return []
        if self.pending:
            text = self.pending + "\n" + text
            self.pending = ""
        if text.lstrip().startswith(":"):
            return self.command(text.strip())
        if _needs_more(text):
            self.pending = text
            return ["…"]
        return self.evaluate(text)

    # ── the terminal loop ────────────────────────────────
    def interactive(self) -> int:
        colour = _enable_ansi()
        self.editor.history = self.load_history()
        print(self.banner())
        if not sys.stdin.isatty():
            try:
                for line in sys.stdin:
                    for out in self.run_line(line.rstrip("\n")):
                        print(out)
                    if not self.running:
                        return 0
            finally:
                self.save_history()
            return 0
        prompt = self.prompt_text(colour)
        try:
            with KeySource() as keys:
                while self.running:
                    line, event = self._edit(prompt, colour, keys)
                    if event == "eof":
                        print()
                        return 0
                    if event == "cancel":
                        print("^C")
                        continue
                    if event != "submit":
                        continue
                    for out in self.run_line(line):
                        print(out)
        except KeyboardInterrupt:
            print()
        finally:
            self.save_history()
        return 0

    def _edit(self, prompt, colour, keys):
        self.editor.reset()
        sys.stdout.write(prompt)
        sys.stdout.flush()
        while True:
            key = keys.read()
            before = self.editor.buffer
            event = self.editor.feed(key)
            if event == "submit":
                sys.stdout.write("\n")
                sys.stdout.flush()
                return before, "submit"
            if event == "cancel":
                sys.stdout.write("\n")
                sys.stdout.flush()
                return "", "cancel"
            if event == "eof":
                return "", "eof"
            if event == "clear":
                sys.stdout.write("\033[2J\033[H" + prompt + self.editor.buffer)
                sys.stdout.flush()
                continue
            self._redraw(prompt, colour)
            if event == "complete" and self.editor.candidates:
                sys.stdout.write("\n  "
                                 + "  ".join(self.editor.candidates)
                                 + "\n" + prompt + self.editor.buffer)
                back = len(self.editor.buffer) - self.editor.cursor
                if back:
                    sys.stdout.write(f"\033[{back}D")
                sys.stdout.flush()

    def _redraw(self, prompt, colour):
        text = self.editor.buffer
        back = len(text) - self.editor.cursor
        sys.stdout.write("\r\033[K" + prompt + text)
        if back:
            sys.stdout.write(f"\033[{back}D")
        sys.stdout.flush()


class _ListWriter:
    def __init__(self, sink):
        self.sink = sink

    def write(self, text):
        for line in str(text).splitlines():
            self.sink.append(line)

    def flush(self):
        pass


def cmd_shell(args) -> int:
    return Shell(_cfg_from(args)).interactive()


def _cfg_from(args) -> AIlangConfig:
    return AIlangConfig(
        dry_run=not getattr(args, "live", False),
        live=getattr(args, "live", False),
        budget_usd=getattr(args, "budget", None)
        or (float(os.environ["AILANG_BUDGET"])
            if os.environ.get("AILANG_BUDGET") else None),
        cache=os.environ.get("AILANG_CACHE", "").lower() in ("1", "true"),
        stream=bool(getattr(args, "stream", False)))


def cmd_run(args):
    load_agent_plugins()
    src = _load_program(args.file)
    if src is None:
        return 1
    is_image = (args.file.endswith(".ailc")
                or '"ailang-image"' in src[:200])
    image = None
    if is_image:
        try:
            image = json.loads(src)
        except ValueError as e:
            print(f"error: bad image: {e}", file=sys.stderr)
            return 2
    if getattr(args, "offline", False):
        try:
            stmts = (dict_to_ast(image.get("statements") or [])
                     if image is not None else parse(src))
        except (AILangRuntimeError, AILangSyntaxError,
                AttributeError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        networked = [(name, dict(fields)) for name, fields
                     in _iter_agent_decls(stmts)
                     if _agent_needs_network(fields)]
        if networked:
            print("refusing to run: these agents would use the network",
                  file=sys.stderr)
            for name, fields in networked:
                print(f"  {name:<16} {fields.get('model', '?')}",
                      file=sys.stderr)
            return 2
    jpath = args.journal or os.environ.get("AILANG_JOURNAL") or None
    journal = (Journal(jpath, getattr(args, "embed_source", False))
               if jpath else None)
    replay = Replay(args.replay) if args.replay else None
    interp = Interpreter(_cfg_from(args), journal=journal, replay=replay)
    try:
        if image is not None:
            interp.run_image(image)
        else:
            interp.run(src, args.file)
    except AILangSyntaxError as e:
        print(f"syntax error: {e}", file=sys.stderr)
        return 2
    except AILangRuntimeError as e:
        print(f"error[{error_code(e)}]: {e}", file=sys.stderr)
        return 2
    return 0


def cmd_test(args):
    """Run .ai programs as tests: a file passes when it raises nothing."""
    load_agent_plugins()
    targets = list(getattr(args, "paths", None) or ["tests"])
    files = []
    for target in targets:
        path = Path(target)
        if path.is_dir():
            for candidate in sorted(path.rglob("*.ai")):
                parts = candidate.relative_to(path).parts[:-1]
                if any(part.startswith((".", "_"))
                       or part == "__pycache__" for part in parts):
                    continue          # never run _archive/ or .zwork/
                files.append(candidate)
        elif path.exists():
            files.append(path)
        else:
            print(f"no such path: {target}", file=sys.stderr)
            return 1
    if not files:
        print("no .ai tests found", file=sys.stderr)
        return 1
    results = []
    for test in files:
        try:
            src = _read_source(test)
            Interpreter(AIlangConfig(), out=lambda s: None).run(
                src, str(test))
        except AILangSyntaxError as e:
            results.append({"name": str(test), "status": "fail",
                            "error": f"syntax: {e}"})
        except AILangRuntimeError as e:
            results.append({"name": str(test), "status": "fail",
                            "error": str(e)})
        except Exception as e:              # noqa: BLE001 — test guard
            results.append({"name": str(test), "status": "fail",
                            "error": f"{type(e).__name__}: {e}"})
        else:
            results.append({"name": str(test), "status": "ok",
                            "error": ""})
    failed = sum(1 for r in results if r["status"] != "ok")
    if getattr(args, "json", False):
        print(json.dumps({
            "schema": 1,
            "total": len(results),
            "passed": len(results) - failed,
            "failed": failed,
            "tests": results,
        }, ensure_ascii=False, indent=1))
        return 0 if failed == 0 else 1
    for item in results:
        if item["status"] == "ok":
            print(f"ok   {item['name']}")
        else:
            print(f"FAIL {item['name']}\n     {item['error']}")
    print(f"\n{len(results) - failed}/{len(results)} passed")
    return 0 if failed == 0 else 1


def cmd_build(args):
    load_agent_plugins()
    src = _load_program(args.file)
    if src is None:
        return 1
    out = Path(args.output or Path(args.file).with_suffix(".ailc"))
    fingerprint = build_fingerprint(src, args.file)
    if out.exists() and not getattr(args, "force", False):
        try:
            previous = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = None
        if isinstance(previous, dict) \
                and previous.get("source_hash") == fingerprint:
            print(f"{out} is up to date")
            return 0
    try:
        image = build_image(src, args.file)
    except AILangSyntaxError as e:
        print(f"syntax error: {e}", file=sys.stderr)
        return 2
    except CompileError as e:
        lines = src.splitlines()
        for diag in e.diagnostics:
            print(diag.render(lines), file=sys.stderr)
        print(f"{len(e.diagnostics)} error(s) - nothing was built",
              file=sys.stderr)
        return 2
    try:
        out.write_text(json.dumps(image, ensure_ascii=False, indent=1),
                       encoding="utf-8", newline="\n")
    except OSError as e:
        print(f"cannot write {out}: {e}", file=sys.stderr)
        return 1
    print(f"compiled {args.file} -> {out} "
          f"({len(image['statements'])} statements, "
          f"grammar {image['grammar']})")
    return 0


def error_code(err: BaseException) -> str:
    """Map a runtime failure to a stable, documented code."""
    text = str(err)
    if isinstance(err, SalaryError):
        return "AIL113"
    if isinstance(err, BudgetError):
        return "AIL111"
    if isinstance(err, ArtifactError):
        return "AIL110"
    if isinstance(err, TimeoutError):
        return "AIL112"
    if isinstance(err, AILangSyntaxError):
        return "AIL021"
    if "assertion failed" in text:
        return "AIL101"
    if "use cycle" in text or text.startswith("use "):
        return "AIL020"
    if "Unknown name" in text or "unknown name" in text:
        return "AIL001"
    if "gate not passed" in text or "gate verdict" in text:
        return "AIL102"
    if "is already declared" in text:
        return "AIL039"
    if "unknown gate" in text:
        return "AIL038"
    if "image" in text:
        return "AIL120"
    return "AIL100"


# One place that says what each code means. `ailang codes` prints it, the
# selftest checks that the source and the README agree with it.
ERROR_CODES = {
    "AIL001": ("error", "unknown name, callable, router or patch target"),
    "AIL002": ("error", "unknown agent kind"),
    "AIL004": ("error", "in:/out: names a schema that is not declared"),
    "AIL005": ("error", "a schema field uses an unknown schema"),
    "AIL006": ("error", "an agent lists an unknown tool"),
    "AIL010": ("error", "a gate starts a match branch"),
    "AIL020": ("error", "use cannot read or parse the library"),
    "AIL021": ("error", "the file does not parse"),
    "AIL030": ("error", "schema inheritance cycle"),
    "AIL031": ("error", "is_a names an unknown schema"),
    "AIL032": ("error", "a chain breaks the subtype hierarchy"),
    "AIL033": ("error", "judge names an agent that does not exist"),
    "AIL034": ("error", "timeout is not positive or casts is below 1"),
    "AIL035": ("warning", "wage cap is below the estimated cost"),
    "AIL036": ("error", "a subtype misses a field its parent requires"),
    "AIL037": ("error", "a gate field is unknown or mistyped"),
    "AIL038": ("error", "agent? name refers to an undeclared gate"),
    "AIL039": ("error", "a gate name is declared twice"),
    "AIL041": ("warning", "salary cannot cover the estimated run"),
    "AIL042": ("error", "a where clause is invalid"),
    "AIL043": ("error", "a policy default is invalid"),
    "AIL100": ("error", "runtime failure without a more specific code"),
    "AIL101": ("error", "a failed assert"),
    "AIL102": ("error", "a gate that never passed"),
    "AIL110": ("error", "an agent broke its out: contract"),
    "AIL111": ("error", "the per-call budget is exhausted"),
    "AIL112": ("error", "an agent hit its timeout"),
    "AIL113": ("error", "an agent exhausted its salary for the run"),
    "AIL120": ("error", "a bad, stale or foreign program image"),
}


def cmd_codes(args):
    """The error-code catalogue: what the language can tell you."""
    if getattr(args, "json", False):
        print(json.dumps({"schema": 1, "codes": [
            {"code": code, "severity": severity, "meaning": meaning}
            for code, (severity, meaning) in sorted(ERROR_CODES.items())]},
            ensure_ascii=False, indent=1))
        return 0
    print(f"{'code':<9}{'severity':<10}meaning")
    for code, (severity, meaning) in sorted(ERROR_CODES.items()):
        print(f"{code:<9}{severity:<10}{meaning}")
    print(f"\n{len(ERROR_CODES)} codes")
    return 0


def cmd_doctor(args) -> int:
    """Print what this installation actually has - for bug reports."""
    import platform
    try:
        import lark
        lark_version = getattr(lark, "__version__", "unknown")
    except Exception:                       # noqa: BLE001 - diagnostics
        lark_version = "missing"
    root = Path(__file__).resolve().parent
    plugins = []
    for folder in ("agents", "plugins"):
        d = root / folder
        if d.is_dir():
            plugins.extend(sorted(p.name for p in d.glob("*.py")))
    history = Path.home() / ".ailang_history"
    mode = "live" if not AIlangConfig().dry_run else "mock (dry-run)"
    facts = {
        "ailang": __version__,
        "python": platform.python_version(),
        "platform": platform.system(),
        "lark": lark_version,
        "grammar": grammar_fingerprint(),
        "grammar_bytes": len(GRAMMAR.encode("utf-8")),
        "kinds": len(_KINDS),
        "subtypes": len(_SUBTYPE_EDGES),
        "price_table": len(PRICE_TABLE),
        "windows": len(CONTEXT_WINDOWS),
        "builtin_kinds": len(_BUILTIN_KINDS),
        "plugins": plugins,
        "history": str(history),
        "history_present": history.exists(),
        "journal": bool(os.environ.get("AILANG_JOURNAL")),
        "mode": mode,
        "corpus": (root / "corpus").is_dir(),
        "tests": (root / "tests").is_dir(),
    }
    if getattr(args, "json", False):
        print(json.dumps({"schema": 1, **facts}, ensure_ascii=False,
                         indent=1))
        return 0
    print(f"ailang      {facts['ailang']}")
    print(f"python      {facts['python']} on {facts['platform']}")
    print(f"lark        {facts['lark']}")
    print(f"grammar     {facts['grammar']} ({facts['grammar_bytes']} bytes)")
    print(f"kinds       {facts['kinds']} ({facts['builtin_kinds']} built-in)")
    print(f"subtypes    {facts['subtypes']} (runtime edges seen)")
    print(f"prices      price table: {facts['price_table']} engines")
    print(f"windows     {facts['windows']} engine families known")
    print(f"codes       {len(ERROR_CODES)} documented")
    for name in ("openai", "anthropic", "google"):
        entry = provider_entry(name)
        state = ("pasted " + _mask(entry["key"])) if entry.get("key") else (
            "env" if os.environ.get(PROVIDER_ENV.get(name, "")) else "-")
        print(f"provider {name:<12}{state}")
    print(f"config      {settings_path()}")
    print(f"plugins     {', '.join(plugins) if plugins else 'none'}")
    print(f"history     {history} "
          f"({'present' if history.exists() else 'empty'})")
    print("journal     "
          + ("AILANG_JOURNAL is set"
             if os.environ.get("AILANG_JOURNAL") else "off"))
    print(f"mode        {mode}")
    print("files       "
          + f"{Path(__file__).name}, "
          + ("corpus ok" if (root / "corpus").is_dir() else "no corpus")
          + ", "
          + ("tests ok" if (root / "tests").is_dir() else "no tests"))
    return 0


def _grammar_doc() -> str:
    """A compact inventory of rules, keywords and terminals."""
    rules, keywords, terminals = [], [], []
    for line in GRAMMAR.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("%", "#", "|")):
            continue
        head, _, rest = stripped.partition(":")
        head, rest = head.strip(), rest.strip()
        if not rest:
            continue
        if head.startswith("_"):
            name = head[1:].split(".")[0]
            if name != "NL":
                keywords.append(name.lower())
        elif head.startswith("?"):
            rules.append(head[1:])
        elif head[:1].isupper() and head.replace(".", "").isidentifier():
            terminals.append(head.split(".")[0])
        elif head[:1].islower():
            rules.append(head)
    return "\n".join([
        f"rules      {len(rules)}",
        f"keywords   {len(keywords)}: " + ", ".join(keywords),
        f"terminals  {len(terminals)}: " + ", ".join(terminals),
    ])


def cmd_grammar(args) -> int:
    """Print the grammar fingerprint; `--expect` guards against drift."""
    stamp = grammar_fingerprint()
    print(f"grammar {stamp} ({len(GRAMMAR.encode('utf-8'))} bytes)")
    if getattr(args, "doc", False):
        print(_grammar_doc())
    expected = getattr(args, "expect", None)
    if expected and expected != stamp:
        print(f"expected {expected} - the grammar changed", file=sys.stderr)
        return 1
    return 0


def cmd_inspect(args):
    src = _load_program(args.file)
    if src is None:
        return 1
    try:
        image = json.loads(src)
    except ValueError:
        print(f"{args.file}: plain source, not a compiled image")
        return 1
    if not isinstance(image, dict) \
            or image.get("format") != "ailang-image":
        print(f"{args.file}: not an AI-lang image")
        return 1
    libs = image.get("libraries") or []
    print(f"format     ailang-image v{image.get('format_version')}")
    print(f"built by   ailang {image.get('ailang')}")
    print(f"grammar    {image.get('grammar')}")
    print(f"source     {image.get('source')}")
    print(f"statements {len(image.get('statements') or [])}")
    print(f"libraries  {len(libs)}")
    for lib in libs:
        print(f"  {Path(lib).name}")
    plugs = image.get("plugins") or []
    print(f"plugins    {len(plugs)}")
    for plug in plugs:
        print(f"  {Path(plug).name}")
    print(f"hash       {image.get('source_hash')}")
    state = ("matches this runtime"
             if image.get("grammar") == grammar_fingerprint()
             else "STALE - rebuild it")
    print(f"status     {state}")
    return 0


def _policy_defaults(stmts) -> dict:
    """Merge every `policy` block into one defaults map."""
    merged = {}
    for st in stmts:
        if isinstance(st, Policy):
            for key, value in st.fields:
                if key in POLICY_FIELDS:
                    merged[key] = value
    return merged


def _iter_agent_decls(stmts):
    """Every Agent declaration with its name hint, at any nesting level."""
    found = []

    defaults = _policy_defaults(stmts)

    def walk(node, hint=None):
        if isinstance(node, AgentDecl):
            merged = dict(defaults)
            merged.update(dict(node.fields))
            found.append((hint or "anon", merged))
            return
        if isinstance(node, Assignment):
            walk(node.expr, node.name)
            return
        if isinstance(node, (list, tuple)):
            for item in node:
                walk(item)
            return
        if hasattr(node, "__dataclass_fields__"):
            for fname in node.__dataclass_fields__:
                value = getattr(node, fname)
                if isinstance(value, (list, tuple)) \
                        or hasattr(value, "__dataclass_fields__"):
                    walk(value)

    for st in stmts:
        walk(st)
    return found


_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
_MOCK_MODELS = ("mock", "local", "stub", "test", "example", "fake")


def _agent_needs_network(fields) -> bool:
    """True when this agent would reach the network at run time.

    Local: non-chat kinds (py/human/echo/plugins), endpoint=localhost or
    *.local, or a mock-style model name. Everything else is treated as
    networked — an unknown model is assumed remote, the safe default."""
    if fields.get("kind", "chat") != "chat":
        return False                       # py / human / echo / plugins
    endpoint = fields.get("endpoint")
    if endpoint:
        host = (str(endpoint).split("//", 1)[-1].split("/", 1)[0]
                .split(":")[0].lower())
        return not (host in _LOCAL_HOSTS or host.endswith(".local"))
    model = str(fields.get("model", "")).lower()
    return not model.startswith(_MOCK_MODELS)


def _collect_program(path, seen=None):
    """Parse a program plus every .ai library it uses (cycle-safe).

    Returns (agents, plugins, errors)."""
    seen = seen if seen is not None else set()
    p = Path(path)
    try:
        p = p.resolve()
    except OSError:
        pass
    if str(p) in seen:
        return [], [], []
    seen.add(str(p))
    try:
        stmts = parse(p.read_text(encoding="utf-8"))
    except (OSError, AILangSyntaxError) as e:
        return [], [], [f"{p.name}: {e}"]
    agents = list(_iter_agent_decls(stmts))
    plugins, errors = [], []
    for st in stmts:
        if not isinstance(st, UseStmt):
            continue
        target = Path(st.path)
        if not target.is_absolute():
            candidate = p.parent / st.path
            target = candidate if candidate.exists() else target
        if not target.exists():
            errors.append(f"{p.name}: use {st.path!r} not found")
            continue
        if target.suffix == ".py":
            plugins.append(str(target))
            continue
        sub_agents, sub_plugins, sub_errors = _collect_program(target, seen)
        agents += sub_agents
        plugins += sub_plugins
        errors += sub_errors
    return agents, plugins, errors


# Bumped whenever the analysis rules change, so old cache entries miss.
ANALYZER_REVISION = 6


def _analyze_program(src: str, filename) -> tuple:
    """Analyze the file and every `use "*.ai"` library, each with its text.

    → (diagnostics, [resolved library paths]). Line numbers always refer to
    the file that produced them, so a library diagnostic is not reported
    against the importer.
    """
    stmts = parse_located(src)
    _linked, libraries = link_imports(stmts, str(filename))
    diags = analyze(stmts, src, str(filename))
    seen = set()
    for path in libraries:
        if path in seen:
            continue
        seen.add(path)
        try:
            lib_src = _read_source(path)
            lib_stmts = parse_located(lib_src)
        except (OSError, AILangSyntaxError):
            continue                      # analyze() already reports AIL020
        for diag in analyze(lib_stmts, lib_src, path):
            diag.file = path
            diags.append(diag)
    return diags, sorted(seen)


def _cache_closure(src: str, filename) -> str:
    """Root text plus every library text: a library edit must miss."""
    try:
        _linked, libraries = link_imports(parse_located(src), str(filename))
    except AILangSyntaxError:
        return src
    parts = [src]
    for path in sorted(set(libraries)):
        try:
            parts.append(_read_source(path))
        except OSError:
            parts.append("")
    return "\n\x00\n".join(parts)


def _cache_root(args) -> Path:
    base = (getattr(args, "cache_dir", None)
            or os.environ.get("AILANG_CACHE_DIR")
            or (Path.home() / ".ailang-cache"))
    path = Path(base) / "check"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _check_key(src: str, filename=None) -> str:
    """Cache key: sources (root + libraries) + grammar + revision."""
    closure = _cache_closure(src, filename) if filename else src
    blob = (f"{grammar_fingerprint()}|{ANALYZER_REVISION}|"
            f"{__version__}|{closure}")
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def cmd_check(args):
    load_agent_plugins()
    src = _load_program(args.file)
    if src is None:
        return 1
    try:
        stmts = parse(src)
    except AILangSyntaxError as e:
        print(f"SYNTAX FAIL: {e}", file=sys.stderr)
        return 1
    json_mode = bool(getattr(args, "json", False))
    if not json_mode:
        print(f"Syntax OK - {len(stmts)} statement(s)")
    lines = src.splitlines()
    key = _check_key(src, args.file)
    cache_file = _cache_root(args) / f"{key}.json"
    diags = None
    libraries = []
    if cache_file.exists():
        try:
            entry = json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            entry = None
        if isinstance(entry, dict) and entry.get("key") == key \
                and entry.get("grammar") == grammar_fingerprint():
            diags = [Diagnostic(**item)
                     for item in entry.get("diagnostics") or []]
            libraries = entry.get("libraries") or []
    if diags is None:
        diags, libraries = _analyze_program(src, args.file)
        state = "miss"
        try:
            cache_file.write_text(json.dumps({
                "key": key,
                "grammar": grammar_fingerprint(),
                "ailang": __version__,
                "file": str(args.file),
                "libraries": libraries,
                "diagnostics": [d.__dict__ for d in diags],
            }, ensure_ascii=False), encoding="utf-8", newline="\n")
        except OSError:
            pass
    else:
        state = "hit"
    if not json_mode:
        print(f"cache: {state}")
    errors = [d for d in diags if d.severity == "error"]
    if json_mode:
        print(json.dumps({
            "schema": 1,
            "file": str(args.file),
            "libraries": libraries,
            "cached": state == "hit",
            "errors": len(errors),
            "warnings": len(diags) - len(errors),
            "diagnostics": [d.__dict__ for d in diags],
        }, ensure_ascii=False, indent=1))
        return 1 if errors else 0
    for diag in diags:
        stream = sys.stderr if diag.severity == "error" else sys.stdout
        # a library diagnostic must show the library's own source line
        source = lines
        if diag.file:
            try:
                source = _read_source(diag.file).splitlines()
            except OSError:
                source = None
        print(diag.render(source), file=stream)
    if errors:
        print(f"{len(errors)} error(s)", file=sys.stderr)
    if not getattr(args, "offline", False):
        return 1 if errors else 0
    agents, plugins, problems = _collect_program(args.file)
    for problem in problems:
        print(f"  note: {problem}")
    if plugins:
        print("plugins (not analysed): "
              + ", ".join(sorted({Path(x).name for x in plugins})))
    networked = [(name, dict(fields)) for name, fields in agents
                 if _agent_needs_network(fields)]
    if networked:
        print(f"networked agents: {len(networked)}")
        for name, fields in networked:
            where = fields.get("endpoint") or "cloud model"
            print(f"  {name:<16} {fields.get('model', '?')}  ({where})")
    else:
        print("offline-safe: every agent is local "
              "(mock / py / endpoint=localhost)")
    strict = getattr(args, "strict", False)
    return 1 if (errors or (strict and networked)) else 0


def cmd_graph(args):
    src = _load_program(args.file)
    if src is None:
        return 1
    nodes, edges = build_ir(parse(src))
    print(to_mermaid(nodes, edges))
    return 0


def cmd_ask(args):
    cfg = _cfg_from(args)
    interp = Interpreter(cfg)
    agent = AIAgent(name="ask", engine=args.model,
                    prompt="You are a concise assistant.", config=cfg)
    answer = Interpreter._run_async(
        interp._ainvoke(agent, args.question, "human"))
    if not cfg.stream:               # the stream already printed it
        print(_text(answer))
    return 0


def cmd_demo(args):
    cfg = _cfg_from(args)
    print("Aligment Insight demo - "
          + ("LIVE mode (real API calls)" if cfg.live
             else "offline mock mode (no keys needed)"))
    print("-" * 60)
    journal = (Journal(args.journal or os.environ.get("AILANG_JOURNAL"),
                       getattr(args, "embed_source", False))
               if (getattr(args, "journal", None)
                   or os.environ.get("AILANG_JOURNAL")) else None)
    Interpreter(cfg, journal=journal).run(DEMO)
    return 0


def cmd_stats(args):
    """Read one or more journals (JSONL) and print the economy table."""
    recs = []
    paths = args.file if isinstance(args.file, (list, tuple)) else [args.file]
    for path in paths:
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    if "$journal" in rec:
                        continue        # v2 header, not a call
                    recs.append(rec)
        except OSError as e:
            print(f"stats: cannot read {path!r}: {e}", file=sys.stderr)
            return 1
    if not recs:
        print("stats: no journal records")
        return 0
    if all(bool(r.get("dry", True)) for r in recs):
        mock_only = True
    else:
        mock_only = False
    by_agent = {}
    for r in recs:
        row = by_agent.setdefault(r.get("agent", "?"), {
            "calls": 0, "lat": 0.0, "tin": 0, "tout": 0, "cost": 0.0})
        row["calls"] += 1
        row["lat"] += float(r.get("latency_ms", 0) or 0)
        row["tin"] += int(r.get("tokens_in", 0) or 0)
        row["tout"] += int(r.get("tokens_out", 0) or 0)
        row["cost"] += float(r.get("cost_usd", 0) or 0)
    calls = sum(r["calls"] for r in by_agent.values())
    cost = sum(r["cost"] for r in by_agent.values())
    tin = sum(r["tin"] for r in by_agent.values())
    tout = sum(r["tout"] for r in by_agent.values())
    if getattr(args, "json", False):
        print(json.dumps({
            "mode": "mock" if mock_only else "live",
            "agents": {name: {
                "calls": row["calls"],
                "avg_ms": round(row["lat"] / row["calls"], 1)
                if row["calls"] else 0.0,
                "tokens_in": row["tin"],
                "tokens_out": row["tout"],
                "cost_usd": round(row["cost"], 6)}
                for name, row in sorted(by_agent.items())},
            "total": {"calls": calls, "tokens_in": tin,
                      "tokens_out": tout, "cost_usd": round(cost, 6)},
        }, ensure_ascii=False, indent=2))
        return 0
    if mock_only:
        print("mode: mock (dry-run) — costs are zero by design")
    print(f"{'agent':<18}{'calls':>7}{'avg ms':>9}{'tokens in':>11}"
          f"{'tokens out':>12}{'cost $':>11}")
    print("-" * 68)
    for name in sorted(by_agent):
        row = by_agent[name]
        avg = row["lat"] / row["calls"] if row["calls"] else 0.0
        print(f"{name:<18}{row['calls']:>7}{avg:>9.1f}{row['tin']:>11}"
              f"{row['tout']:>12}{row['cost']:>11.6f}")
    print("-" * 68)
    calls = sum(r["calls"] for r in by_agent.values())
    cost = sum(r["cost"] for r in by_agent.values())
    tin = sum(r["tin"] for r in by_agent.values())
    tout = sum(r["tout"] for r in by_agent.values())
    print(f"{'TOTAL':<18}{calls:>7}{'':>9}{tin:>11}{tout:>12}"
          f"{cost:>11.6f}")
    return 0


def load_agent_plugins():
    """Register plugins found in agents/ and plugins/ (once per process)."""
    if _PLUGIN_SCAN_DONE:
        return []
    _PLUGIN_SCAN_DONE.append(True)
    here = Path(__file__).resolve().parent
    seen = []
    for base in (here, Path.cwd()):
        for folder in ("agents", "plugins"):
            d = base / folder
            if not d.is_dir():
                continue
            for py in sorted(d.glob("*.py")):
                if str(py.resolve()) in _LOADED_PLUGINS:
                    continue
                try:
                    AsyncInterpreter._load_plugin(str(py))
                    seen.append(py.stem)
                except Exception as e:          # noqa: BLE001 — best effort
                    log.warning("plugin %s skipped: %s", py.name, e)
    return seen


def cmd_kinds(args):
    load_agent_plugins()
    print(f"AI-lang {__version__} agent kinds")
    for name in sorted(_KINDS):
        origin = "built-in" if name in _BUILTIN_KINDS else "plugin"
        print(f"  {name:<12} {origin}")
    return 0


def _needs_more(text: str) -> bool:
    """True while braces or quotes are still open — the REPL keeps reading."""
    depth, quote, escape = 0, None, False
    for ch in text:
        if escape:
            escape = False
        elif ch == "\\":
            escape = True
        elif quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch in "{[(":
            depth += 1
        elif ch in "}])}":
            depth -= 1
    return depth > 0 or quote is not None


def cmd_fmt(args):
    targets = (args.file if isinstance(args.file, (list, tuple))
               else [args.file])
    files = []
    for target in targets:
        path = Path(target)
        if path.is_dir():
            for candidate in sorted(path.rglob("*.ai")):
                parts = candidate.relative_to(path).parts[:-1]
                if any(part.startswith((".", "_"))
                       or part == "__pycache__" for part in parts):
                    continue          # `fmt .` must not touch _archive/ or .zwork/
                files.append(candidate)
        else:
            files.append(path)
    if not files:
        print("fmt: no .ai files found", file=sys.stderr)
        return 1
    status = 0
    for path in files:
        status = max(status, _fmt_one(path, args))
    return status


def _fmt_one(path, args) -> int:
    try:
        src = _read_source(path)
    except OSError as e:
        print(f"fmt: cannot read {path!r}: {e}", file=sys.stderr)
        return 1
    try:
        formatted, dropped = format_source(src)
    except AILangSyntaxError as e:
        print(f"{path}: syntax error: {e}", file=sys.stderr)
        return 2
    except FormatError as e:
        print(f"{path}: {e}", file=sys.stderr)
        return 2
    if args.check:
        if formatted == src:
            return 0
        print(f"{path}: not formatted")
        return 1
    if args.write:
        if dropped:
            shown = ", ".join(str(n) for n in dropped[:5])
            more = "…" if len(dropped) > 5 else ""
            print(f"note: {path}: comment lines not preserved: "
                  f"{shown}{more}", file=sys.stderr)
        try:
            path.write_text(formatted, encoding="utf-8", newline="\n")
        except OSError as e:
            print(f"cannot write {path}: {e}", file=sys.stderr)
            return 1
        return 0
    sys.stdout.write(formatted)
    return 0


def cmd_repl(args):
    interp = Interpreter(_cfg_from(args))
    print(f"AI-lang {__version__} REPL - exit with empty line or 'exit'")
    print('Try: muse = Agent { model: "gpt-4o"  role: "You generate ideas." }')
    while True:
        try:
            line = input("ai-lang> ").strip()
            while _needs_more(line):
                line += "\n" + input("      ...> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line or line in {"exit", "quit"}:
            return 0
        try:
            for st in parse(line):
                result = interp.exec(st)
                if isinstance(st, Print):
                    continue
                if isinstance(st, Assignment) and isinstance(result, AIAgent):
                    print(f"OK {st.name} = Agent(model={result.engine})")
                elif isinstance(result, (str, TypedArtifact)):
                    if not interp.config.stream:   # already streamed
                        print(_text(result))
        except AILangSyntaxError as e:
            print(f"syntax error: {e}")
        except Exception as e:  # noqa: BLE001
            print(f"error: {type(e).__name__}: {e}")


# ═════════════ 8c. GOVERNANCE COMMANDS: FLOW / PAYROLL / CONTRACT ═══

def _load_program_any(path):
    """Load a program or a compiled image.

    → {"kind", "text", "stmts", "image"} or None when the file is unusable.
    Images are the deployable artefact, so every governance command must be
    able to read them, not just `run`.
    """
    text = _load_program(str(path))
    if text is None:
        return None
    head = text.lstrip()[:400]
    looks_image = (str(path).endswith(".ailc")
                   or head.startswith('{\n "format": "ailang-image"')
                   or '"ailang-image"' in head)
    if looks_image:
        try:
            image = json.loads(text)
        except ValueError as e:
            print(f"{path}: bad image: {e}", file=sys.stderr)
            return None
        if not isinstance(image, dict) or image.get("format") != \
                "ailang-image":
            print(f"{path}: not an AI-lang image", file=sys.stderr)
            return None
        try:
            stmts = dict_to_ast(image.get("statements") or [])
        except AILangRuntimeError as e:
            print(f"{path}: {e}", file=sys.stderr)
            return None
        return {"kind": "image", "text": text, "stmts": stmts,
                "image": image}
    try:
        stmts = parse_located(text)
    except AILangSyntaxError as e:
        print(f"SYNTAX FAIL: {e}", file=sys.stderr)
        return None
    return {"kind": "source", "text": text, "stmts": stmts, "image": None}


def _facts_for(path):
    """Load a source or an image and collect facts (None, None, None on
    failure). `use "lib.ai"` libraries are spliced first, so a report covers
    the whole organization and not just the file that imports it.
    """
    loaded = _load_program_any(path)
    if loaded is None:
        return None, None, None
    if loaded["kind"] == "image":
        linked = loaded["stmts"]          # images are linked at build time
    else:
        linked, _libraries = link_imports(loaded["stmts"], str(path))
    return loaded["text"], linked, collect_facts(linked, str(path))


def _row_share(tok_in, total):
    return (tok_in / total * 100.0) if total else 0.0


def flow_report(facts: ProgramFacts) -> dict:
    """Attention rows for one program: pure, reusable, printable."""
    stats = facts.calls()
    total = sum(row["tok_in"] for row in stats.values())
    rows, overflow, sinks = [], [], []
    for name, fact in facts.agents.items():
        row = stats[name]
        window = context_window(fact.engine, fact.window)
        share = _row_share(row["tok_in"], total)
        fits = True if window is None else row["max_in"] <= window
        sink = share >= 60.0
        if not fits:
            overflow.append(name)
        if sink:
            sinks.append(name)
        rows.append({
            "agent": name,
            "engine": fact.engine,
            "calls": row["calls"],
            "tok_in": row["tok_in"],
            "max_tok_in": row["max_in"],
            "tok_out": row["tok_out"],
            "share_pct": round(share, 1),
            "window": window,
            "fits": fits,
            "attention_sink": sink,
        })
    return {"total_tok_in": total, "agents": rows, "overflow": overflow,
            "sinks": sinks, "stats": stats}


def cmd_flow(args):
    """Attention distribution: who gets the context, and does it fit."""
    load_agent_plugins()
    src, stmts, facts = _facts_for(args.file)
    if facts is None:
        return 1
    report = flow_report(facts)
    rows, overflow, sinks = report["agents"], report["overflow"], report["sinks"]
    total = report["total_tok_in"]
    if getattr(args, "json", False):
        print(json.dumps({
            "schema": 1,
            "schema": 1,
            "file": str(args.file),
            "estimator": "max(words, ceil(chars/4))",
            "total_tok_in": total,
            "agents": rows,
            "attention_sinks": sinks,
            "overflow": overflow,
        }, ensure_ascii=False, indent=1))
        return 1 if (overflow and getattr(args, "strict", False)) else 0
    print(f"file       {args.file}")
    print("estimates  ~ tokens = max(words, chars/4) - deterministic, "
          "offline")
    print()
    print(f"{'agent':<16}{'engine':<20}{'~tok_in':>9}{'share':>8}"
          f"{'window':>9}  status")
    for row in rows:
        window = "-" if row["window"] is None else f"{row['window']}"
        status = "ok" if row["fits"] else "OVERFLOW"
        if row["calls"] == 0:
            status = "idle"
        if row["attention_sink"]:
            status += " sink"
        print(f"{row['agent']:<16}{row['engine']:<20}"
              f"{row['tok_in']:>9}{row['share_pct']:>7.0f}%"
              f"{window:>9}  {status}")
    print(f"\ntotal ~tok_in: {total}")
    if sinks:
        print("attention sink: " + ", ".join(
            f"{name} ({report['stats'][name]['tok_in'] / total * 100:.0f}%)"
            for name in sinks) + " - heuristic, not an error")
    for name in overflow:
        row = stats[name]
        fact = facts.agents[name]
        window = context_window(fact.engine, fact.window)
        print(f"warning: agent {name!r} needs ~{row['max_in']} tokens "
              f"per call, window is {window}", file=sys.stderr)
    return 1 if (overflow and getattr(args, "strict", False)) else 0


def payroll_report(facts: ProgramFacts, budget=None) -> dict:
    """Static wage bill rows: pure, reusable, printable."""
    stats = facts.calls()
    rows, unknown = [], []
    total = 0.0
    for name, fact in facts.agents.items():
        row = stats[name]
        tok_in = row["max_in"] or estimate_tokens(fact.role)
        tok_out = row["tok_out"]
        per_call, known = call_cost(fact, tok_in, tok_out, worst_case=False)
        worst, _ = call_cost(fact, tok_in, tok_out, worst_case=True)
        if not known:
            unknown.append(name)
        elif row["calls"]:
            total += (worst or 0.0) * row["calls"]
        pin, pout, priced = lookup_price(fact.engine, fact.price_in,
                                         fact.price_out)
        rows.append({
            "agent": name,
            "engine": fact.engine,
            "calls": row["calls"],
            "tok_in": tok_in,
            "tok_out": tok_out,
            "cast_attempts": 1 + max(0, fact.casts),
            "price_in": pin if priced else None,
            "price_out": pout if priced else None,
            "usd_per_call": None if per_call is None
            else round(per_call, 6),
            "usd_worst_case": None if worst is None else round(worst, 6),
            "budget": fact.budget,
            "salary": fact.salary,
            "known": priced,
        })
    over = budget is not None and total > budget + 1e-12
    return {"agents": rows, "total_usd": total, "unknown": unknown,
            "budget": budget, "over": over,
            "rows": stats}


def cmd_payroll(args):
    """Static wage bill: what one run of this program costs."""
    load_agent_plugins()
    src, stmts, facts = _facts_for(args.file)
    if facts is None:
        return 1
    budget = getattr(args, "budget", None)
    report = payroll_report(facts, budget)
    rows, unknown = report["agents"], report["unknown"]
    total, over = report["total_usd"], report["over"]
    if getattr(args, "json", False):
        print(json.dumps({
            "schema": 1,
            "file": str(args.file),
            "agents": rows,
            "total_usd": round(total, 6),
            "unknown_priced": unknown,
            "budget": budget,
            "over_budget": over,
        }, ensure_ascii=False, indent=1))
        return 1 if (over and getattr(args, "strict", False)) else 0
    print(f"file       {args.file}")
    print("estimates  ~ tokens; prices USD per 1M tokens; mock is $0")
    print()
    print(f"{'agent':<16}{'engine':<18}{'~in':>7}{'~out':>7}"
          f"{'$/call':>11}{'att':>5}{'calls':>7}{'worst $':>11}")
    for row in rows:
        per = "?" if row["usd_per_call"] is None else f"{row['usd_per_call']:.6f}"
        worst = ("?" if row["usd_worst_case"] is None
                 else f"{row['usd_worst_case']:.6f}")
        print(f"{row['agent']:<16}{row['engine']:<18}{row['tok_in']:>7}"
              f"{row['tok_out']:>7}{per:>11}{row['cast_attempts']:>5}"
              f"{row['calls']:>7}{worst:>11}"
              + ("  idle" if not row["calls"] else ""))
    print(f"\ntotal (worst case): ${total:.6f}")
    idle = [row["agent"] for row in rows if not row["calls"]]
    if idle:
        print("idle (never called, not billed): " + ", ".join(idle))
    if unknown:
        print("? unknown price: " + ", ".join(unknown)
              + " - not included in the total")
    if budget is not None:
        state = "OK" if not over else "OVER BUDGET"
        print(f"budget ${budget:.6f}: {state} "
              f"(estimate ${total:.6f})")
    for diag in analyze_facts(facts):
        if diag.code == "AIL035":
            print(f"{diag.severity}[{diag.code}]: {diag.message}",
                  file=sys.stderr)
    return 1 if (over and getattr(args, "strict", False)) else 0


def _card(fact, stats, facts):
    row = stats.get(fact.name) or {"max_in": 0,
                                   "tok_out": DEFAULT_OUT_TOKENS}
    window = context_window(fact.engine, fact.window)
    subs = facts.parents.get(fact.out_schema or "", set())
    return {
        "agent": fact.name,
        "role": fact.role,
        "engine": fact.engine,
        "kind": fact.kind,
        "wage_cap": fact.budget,
        "salary": fact.salary,
        "deadline": fact.timeout,
        "supervisor": fact.judge,
        "retries": fact.casts,
        "window": window,
        "gates": facts.gates.get(fact.name, []),
        "in_schema": fact.in_schema,
        "out_schema": fact.out_schema,
        "out_supertypes": sorted(subs),
        "tok_in_per_call": row["max_in"],
    }


def cmd_contract(args):
    """Every agent's labour contract, plus the static clauses it violates."""
    load_agent_plugins()
    src, stmts, facts = _facts_for(args.file)
    if facts is None:
        return 1
    stats = facts.calls()
    cards = [_card(fact, stats, facts) for fact in facts.agents.values()]
    diags = [d for d in analyze(stmts, src, args.file)
             if d.code in ("AIL033", "AIL034", "AIL035", "AIL036",
                           "AIL037")]
    if getattr(args, "json", False):
        print(json.dumps({
            "schema": 1,
            "file": str(args.file),
            "agents": cards,
            "diagnostics": [d.__dict__ for d in diags],
        }, ensure_ascii=False, indent=1))
        return 1 if any(d.severity == "error" for d in diags) else 0
    print(f"file       {args.file}")
    print(f"agents     {len(cards)}")
    for card in cards:
        print()
        print(f"agent {card['agent']}  ({card['kind']}, {card['engine']})")
        print(f"  role        {card['role']!r}")
        cap = ("-" if card["wage_cap"] is None
               else f"${card['wage_cap']:.4f}")
        print(f"  wage cap    {cap}")
        if card.get("salary") is not None:
            print(f"  salary      ${card['salary']:.4f} per run")
        deadline = ("-" if card["deadline"] is None
                    else f"{card['deadline']:g}s")
        print(f"  deadline    {deadline}   retries {card['retries']}")
        print(f"  supervisor  {card['supervisor'] or '-'}"
              f"   window {card['window'] if card['window'] else '-'}")
        shapes = (f"in: {card['in_schema'] or '-'}  "
                  f"out: {card['out_schema'] or '-'}")
        if card["out_supertypes"]:
            shapes += "  is_a: " + ", ".join(card["out_supertypes"])
        print(f"  duties      {shapes}")
        gates = card["gates"] or ["-"]
        print(f"  gates       {gates[0]!r}")
        for gate in gates[1:]:
            print(f"              {gate!r}")
    if diags:
        print()
        print("contract issues")
        for diag in diags:
            print("  " + diag.message)
    return 1 if any(d.severity == "error" for d in diags) else 0


def _read_journal(path):
    """→ (header, records); a malformed line is a clear, located error."""
    header, records = {}, []
    try:
        raw = Path(path).read_text(encoding="utf-8-sig")
    except OSError as e:
        raise AILangRuntimeError(f"replay: cannot read {path!r}: {e}")
    for number, line in enumerate(raw.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            raise AILangRuntimeError(
                f"replay: {path}: line {number} is not JSON") from None
        if not isinstance(rec, dict):
            raise AILangRuntimeError(
                f"replay: {path}: line {number} is not a journal record")
        if "$journal" in rec:
            header = rec
            continue
        if "agent" not in rec:
            raise AILangRuntimeError(
                f"replay: {path}: line {number} is not a journal record")
        records.append(rec)
    return header, records


def _short(text, limit=48):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def cmd_replay(args):
    """Replay a journal: read the timeline, or re-run the program on it."""
    try:
        header, records = _read_journal(args.file)
    except AILangRuntimeError as e:
        print(f"error[{error_code(e)}]: {e}", file=sys.stderr)
        return 1
    if not records and not header:
        print(f"replay: {args.file} has no records", file=sys.stderr)
        return 1
    explicit = getattr(args, "program", None)
    program = explicit or header.get("program") or ""
    stored = "" if explicit else (header.get("source") or "")
    timeline = bool(getattr(args, "step", False)
                    or getattr(args, "json", False)
                    or getattr(args, "timeline", False))
    if (stored or program) and not timeline:
        if explicit or not stored:
            if not program:
                print("replay: nothing to re-run", file=sys.stderr)
                return 1
            if not Path(program).exists():
                print(f"replay: the journal names {program!r}, which is "
                      f"gone; showing the timeline instead", file=sys.stderr)
                src = None
            else:
                print(f"using the program recorded in the journal: "
                      f"{program} (add --timeline to see the steps)",
                      file=sys.stderr)
                src = _load_program(program)
        else:
            print("using the source stored in the journal "
                  "(add --timeline to see the steps)", file=sys.stderr)
            src = stored
        if src:
            try:
                Interpreter(AIlangConfig(dry_run=True),
                            replay=Replay(args.file)).run(src, program or "\0")
            except AILangRuntimeError as e:
                print(f"error[{error_code(e)}]: {e}", file=sys.stderr)
                return 1
            return 0
    tokens_in = sum(int(r.get("tokens_in") or 0) for r in records)
    tokens_out = sum(int(r.get("tokens_out") or 0) for r in records)
    cost = sum(float(r.get("cost_usd") or 0.0) for r in records)
    agents = sorted({str(r.get("agent")) for r in records})
    dry = all(bool(r.get("dry")) for r in records)
    if getattr(args, "json", False):
        print(json.dumps({
            "journal": str(args.file),
            "steps": len(records),
            "agents": agents,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": round(cost, 6),
            "dry": dry,
            "timeline": [{
                "n": index,
                "agent": str(rec.get("agent")),
                "engine": str(rec.get("engine") or ""),
                "in": str(rec.get("in") or ""),
                "out": _short(rec.get("out")),
                "tokens_in": int(rec.get("tokens_in") or 0),
                "tokens_out": int(rec.get("tokens_out") or 0),
                "cost_usd": round(float(rec.get("cost_usd") or 0.0), 6),
            } for index, rec in enumerate(records, 1)],
        }, ensure_ascii=False, indent=1))
        return 0
    if getattr(args, "step", False):
        for index, rec in enumerate(records, 1):
            print(f"#{index} {rec.get('agent')} "
                  f"[{rec.get('engine') or '-'}] "
                  f"in {str(rec.get('in') or '')[:8]} "
                  f"{int(rec.get('tokens_in') or 0)}→"
                  f"{int(rec.get('tokens_out') or 0)} tok "
                  f"${float(rec.get('cost_usd') or 0.0):.6f} "
                  f"{_short(rec.get('out'))!r}")
        return 0
    print(f"journal    {args.file}")
    print(f"steps      {len(records)}")
    print(f"agents     {', '.join(agents)}")
    print(f"dry        {'yes' if dry else 'no - recorded live'}")
    print(f"source     " + (f"embedded ({len(header.get('source') or '')} "
                           f"bytes)" if header.get("source")
                           else (header.get("program") or "-")))
    print(f"tokens     {tokens_in} in / {tokens_out} out")
    print(f"cost       ${cost:.6f}")
    print()
    print(f"{'#':>3} {'agent':<14}{'engine':<12}{'tok':>10}{'$':>11}  out")
    for index, rec in enumerate(records, 1):
        print(f"{index:>3} {str(rec.get('agent')):<14}"
              f"{str(rec.get('engine') or '-'):<12}"
              f"{int(rec.get('tokens_in') or 0)}→"
              f"{int(rec.get('tokens_out') or 0):>4}"
              f"{float(rec.get('cost_usd') or 0.0):>11.6f}  "
              f"{_short(rec.get('out'))!r}")
    print(f"\nreplayed {len(records)} step(s) without calling a model")
    return 0


def _expand_paths(paths) -> tuple:
    """Files stay; directories expand to their `*.ai` programs.

    → (existing paths, missing paths). Hidden and underscored folders are
    skipped, so `_archive/` and `.zwork/` never join an audit by accident.
    """
    found, missing = [], []
    for raw in paths:
        path = Path(str(raw))
        if path.is_dir():
            for candidate in sorted(path.rglob("*.ai")):
                parts = candidate.relative_to(path).parts[:-1]
                if any(p.startswith((".", "_")) or p == "__pycache__"
                       for p in parts):
                    continue
                found.append(str(candidate))
        elif path.exists():
            found.append(str(path))
        else:
            found.append(str(path))       # keep the caller's order
            missing.append(str(path))
    seen, unique = set(), []
    for item in found:
        key = str(Path(item).resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique, missing


def _audit_program(path, budget=None):
    """Audit one program. → (report dict, printable lines)."""
    report = {"file": str(path), "kind": "source", "built_by": "",
              "syntax": True, "statements": 0,
              "libraries": 0, "errors": [], "warnings": [],
              "flow": {"total_tok_in": 0, "agents": 0, "overflow": [],
                       "attention_sinks": []},
              "payroll": {"total_usd": 0.0, "unknown_priced": [],
                          "over_budget": False, "budget": budget},
              "contracts": {"agents": 0, "with_budget": 0,
                            "with_supervisor": 0, "with_gates": 0}}
    loaded = _load_program_any(path)
    if loaded is None:
        report["syntax"] = False
        report["errors"] = [{"code": "AIL021",
                             "message": f"cannot read {path}"}]
        return report, [f"  syntax     FAIL  cannot read {path}"]
    stmts = loaded["stmts"]
    image = loaded["image"]
    report["kind"] = "image" if image is not None else "source"
    if image is not None:
        stamp = image.get("grammar")
        if stamp and stamp != grammar_fingerprint():
            report["syntax"] = False
            report["errors"] = [{
                "code": "AIL120",
                "message": f"image was built for grammar {stamp}, this "
                           f"runtime has {grammar_fingerprint()} - rebuild "
                           f"it"}]
            return report, [
                f"  image      STALE (built for grammar {stamp})",
                f"  expected   {grammar_fingerprint()}"]
        report["built_by"] = image.get("ailang") or ""
        linked = stmts                    # linked at build time
        libraries = image.get("libraries") or []
        facts = collect_facts(linked, str(path))
        diags = analyze(linked, "", str(path))
    else:
        linked, libraries = link_imports(stmts, str(path))
        facts = collect_facts(linked, str(path))
        diags, _analysed = _analyze_program(loaded["text"], path)
    errors = [d for d in diags if d.severity == "error"]
    warnings = [d for d in diags if d.severity != "error"]
    flow = flow_report(facts)
    payroll = payroll_report(facts, budget)
    cards = [_card(fact, payroll["rows"], facts)
             for fact in facts.agents.values()]
    report.update({
        "statements": len(stmts),
        "libraries": len(libraries),
        "errors": [d.__dict__ for d in errors],
        "warnings": [d.__dict__ for d in warnings],
        "flow": {"total_tok_in": flow["total_tok_in"],
                 "agents": len(flow["agents"]),
                 "overflow": flow["overflow"],
                 "attention_sinks": flow["sinks"]},
        "payroll": {"total_usd": round(payroll["total_usd"], 6),
                    "unknown_priced": payroll["unknown"],
                    "over_budget": payroll["over"],
                    "budget": budget},
        "contracts": {"agents": len(cards),
                      "with_budget": sum(1 for c in cards
                                         if c["wage_cap"] is not None),
                      "with_supervisor": sum(1 for c in cards
                                             if c["supervisor"]),
                      "with_gates": sum(1 for c in cards if c["gates"])},
    })
    attention = ("no overflow" if not flow["overflow"]
                 else "OVERFLOW: " + ", ".join(flow["overflow"]))
    sinks = (", sink: " + ", ".join(flow["sinks"]) if flow["sinks"] else "")
    money = f"${payroll['total_usd']:.6f} worst case"
    if payroll["unknown"]:
        money += (f", {len(payroll['unknown'])} unknown price(s): "
                  + ", ".join(payroll["unknown"]))
    if budget is not None:
        money += " (budget " + ("OK)" if not payroll["over"]
                                else "EXCEEDED)")
    shape = ("image" if image is not None else "ok")
    lines = [f"  syntax     {shape:<10}{len(stmts)} statements"
             + (f", {len(libraries)} libraries" if libraries else ""),
             f"  types      {'ok' if not errors else 'FAIL':<10}"
             f"{len(errors)} error(s), {len(warnings)} warning(s)"]
    for diag in errors + warnings:
        lines.append(f"  {diag.severity}[{diag.code}]: {diag.message}")
    lines += [f"  attention  {len(flow['agents'])} agent(s)  "
              f"~{flow['total_tok_in']} tok, {attention}{sinks}",
              f"  payroll    {money}",
              f"  contracts  {len(cards)} card(s), "
              f"{sum(1 for c in cards if c['supervisor'])} with a "
              f"supervisor, {sum(1 for c in cards if c['gates'])} with gates"]
    return report, lines


def cmd_audit(args):
    """One verdict for one program or a whole organization."""
    load_agent_plugins()
    raw = args.file if isinstance(args.file, (list, tuple)) else [args.file]
    paths, _missing = _expand_paths(raw)   # a missing file is kept in place
    if not paths:
        print("audit: no .ai programs found in the given paths",
              file=sys.stderr)
        return 1
    budget = getattr(args, "budget", None)
    strict = getattr(args, "strict", False)
    programs, blocks = [], []
    for path in paths:
        report, lines = _audit_program(path, budget)
        programs.append(report)
        blocks.append((path, lines))
    errors = [e for p in programs for e in p["errors"]]
    warnings = [w for p in programs for w in p["warnings"]]
    overflow = [name for p in programs for name in p["flow"]["overflow"]]
    over_budget = any(p["payroll"]["over_budget"] for p in programs)
    blocked = bool(errors) or (strict and bool(warnings or overflow
                                               or over_budget))
    totals = {
        "programs": len(programs),
        "agents": sum(p["contracts"]["agents"] for p in programs),
        "errors": len(errors),
        "warnings": len(warnings),
        "tok_in": sum(p["flow"]["total_tok_in"] for p in programs),
        "usd": round(sum(p["payroll"]["total_usd"] for p in programs), 6),
        "overflow": overflow,
    }
    payload = {"schema": 1, "verdict": "BLOCKED" if blocked else "READY",
               "programs": programs, "totals": totals,
               "errors": errors, "warnings": warnings,
               "budget": budget, "over_budget": over_budget}
    if len(programs) == 1:
        # keep the flat shape single-file callers already use
        payload.update({k: programs[0][k] for k in
                        ("file", "kind", "built_by", "syntax",
                         "statements", "libraries",
                         "flow", "payroll", "contracts")})
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        return 1 if blocked else 0
    for path, lines in blocks:
        print(f"audit      {path}")
        for line in lines:
            print(line)
    if len(programs) > 1:
        print()
        print(f"totals     {totals['programs']} programs, "
              f"{totals['agents']} agents, {totals['errors']} error(s), "
              f"~{totals['tok_in']} tok, ${totals['usd']:.6f} worst case")
    print()
    if blocked:
        reasons = []
        if errors:
            reasons.append(f"{len(errors)} error(s)")
        if strict:
            if warnings:
                reasons.append(f"{len(warnings)} warning(s)")
            if overflow:
                reasons.append("attention overflow")
            if over_budget:
                reasons.append("over budget")
        print("BLOCKED by " + ", ".join(reasons))
    else:
        print("READY - nothing blocks a run")
    return 1 if blocked else 0


def _stamp(text, end_of_day: bool = False):
    """`2026-10-21` or `2026-10-21T14:30` → epoch seconds (local).

    A bare date means midnight; with `end_of_day` it means 23:59:59.999999,
    so `--until 2026-10-21` includes that whole day.
    """
    from datetime import datetime, time
    raw = str(text)
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        raise AILangRuntimeError(
            f"review: cannot read the date {raw!r}; use 2026-10-21 "
            f"or 2026-10-21T14:30") from None
    if end_of_day and "T" not in raw and " " not in raw:
        moment = datetime.combine(moment.date(), time.max)
    return moment.timestamp()


def cmd_review(args):
    """Gate pass rates from journals: how often did the inspection hold?"""
    rows, days, total_calls = {}, {}, 0
    try:
        since = _stamp(args.since) if getattr(args, "since", None) else None
        until = (_stamp(args.until, end_of_day=True)
                 if getattr(args, "until", None) else None)
    except AILangRuntimeError as e:
        print(f"error[{error_code(e)}]: {e}", file=sys.stderr)
        return 1
    for path in args.file:
        try:
            header, records = _read_journal(path)
        except AILangRuntimeError as e:
            print(f"error[{error_code(e)}]: {e}", file=sys.stderr)
            return 1
        for rec in records:
            stamp = float(rec.get("ts") or 0.0)
            if since is not None and stamp < since:
                continue
            if until is not None and stamp > until:
                continue
            note = str(rec.get("note") or "")
            if not note.startswith(("gate", "until", "match")):
                continue
            agent = str(rec.get("agent"))
            row = rows.setdefault(agent, {"agent": agent, "checks": 0,
                                          "passed": 0, "failed": 0,
                                          "cost_usd": 0.0, "latency_ms": 0})
            passed = _verdict_pass(str(rec.get("out") or ""))
            row["checks"] += 1
            row["passed" if passed else "failed"] += 1
            row["cost_usd"] += float(rec.get("cost_usd") or 0.0)
            row["latency_ms"] += int(rec.get("latency_ms") or 0)
            day = time.strftime("%Y-%m-%d", time.localtime(stamp))
            bucket = days.setdefault(day, {"date": day, "checks": 0,
                                           "passed": 0, "failed": 0})
            bucket["checks"] += 1
            bucket["passed" if passed else "failed"] += 1
            total_calls += 1
    if not rows:
        # deterministic gates never call a model, so there is nothing to
        # audit; that is a healthy report, not a failure
        if getattr(args, "json", False):
            print(json.dumps({"schema": 1, "gates": [], "checks": 0,
                              "min_rate": getattr(args, "min_rate", None),
                              "below": []}, ensure_ascii=False, indent=1))
        else:
            print("review: no auditor calls in these journals "
                  "(deterministic gates cost nothing)")
        return 0
    table = []
    for row in sorted(rows.values(), key=lambda r: r["agent"]):
        rate = row["passed"] / row["checks"] if row["checks"] else 0.0
        table.append({**row,
                      "pass_rate": round(rate, 3),
                      "avg_ms": round(row["latency_ms"] / row["checks"], 1),
                      "cost_usd": round(row["cost_usd"], 6)})
    trend = [{**days[day],
              "pass_rate": round(days[day]["passed"] / days[day]["checks"], 3)}
             for day in sorted(days)]
    floor = getattr(args, "min_rate", None)
    below = [r["agent"] for r in table
             if floor is not None and r["pass_rate"] < floor]
    if getattr(args, "json", False):
        print(json.dumps({"schema": 1, "gates": table,
                          "checks": total_calls, "trend": trend,
                          "min_rate": floor, "below": below},
                         ensure_ascii=False, indent=1))
        return 1 if below else 0
    if getattr(args, "trend", False):
        print(f"{'date':<12}{'checks':>7}{'pass':>7}{'fail':>7}{'rate':>7}")
        for day in trend:
            print(f"{day['date']:<12}{day['checks']:>7}{day['passed']:>7}"
                  f"{day['failed']:>7}{day['pass_rate'] * 100:>6.0f}%")
        print(f"\n{total_calls} gate check(s) over {len(trend)} day(s)")
        return 1 if below else 0
    print(f"{'gate':<16}{'checks':>7}{'pass':>7}{'fail':>7}"
          f"{'rate':>7}{'avg ms':>9}{'cost $':>10}")
    for row in table:
        print(f"{row['agent']:<16}{row['checks']:>7}{row['passed']:>7}"
              f"{row['failed']:>7}{row['pass_rate'] * 100:>6.0f}%"
              f"{row['avg_ms']:>9}{row['cost_usd']:>10.6f}")
    print(f"\n{total_calls} gate check(s) across {len(table)} auditor(s)")
    if floor is not None:
        if below:
            print(f"below the {floor:.0%} floor: " + ", ".join(below),
                  file=sys.stderr)
        else:
            print(f"every auditor holds the {floor:.0%} floor")
    return 1 if below else 0


INIT_FILES = {'.gitignore': '__pycache__/\n.ailang-cache/\n*.ailc\n*.jsonl\n',
 'README.md': '# A new Aligment Insight project\n'
              '\n'
              'An organization of AI agents, in plain files. Everything runs '
              'offline.\n'
              '\n'
              '```bash\n'
              'ailang run main.ai      # one program, mock mode, $0\n'
              'ailang test            # the suites under tests/\n'
              'ailang audit .         # is the organization ready?\n'
              'ailang fmt . --write   # canonical formatting\n'
              '```\n'
              '\n'
              'Read `main.ai` next: it declares a schema with a hard limit '
              '(`where`),\n'
              'two named gates, a `policy` of defaults, and two agents on a '
              'flow.\n',
 'lib.ai': '# Shared vocabulary for the project: `use "lib.ai"` in main.ai.\n'
           '\n'
           'schema Note { body: str where len(body) > 0 }\n',
 'main.ai': 'use "lib.ai"\n'
            'schema Brief { topic: str  note: Note  budget: float where '
            'budget >= 0 }\n'
            'schema Score { points: int where points >= 0 }\n'
            'gate has_topic = topic != ""\n'
            'gate positive = points >= 0\n'
            'policy { model: "mock"  salary: 0.1  timeout: 30 }\n'
            'writer = Agent {\n'
            '    role: "Draft a one-paragraph brief."\n'
            '    out: Brief\n'
            '}\n'
            'critic = Agent {\n'
            '    role: "Score the brief."\n'
            '    out: Score\n'
            '}\n'
            'brief = "launch a coffee brand" >> writer >> critic? has_topic\n'
            'scored = brief >> critic >> critic? positive\n'
            'print scored\n',
 'tests/smoke.ai': '# Smoke test: `ailang test` runs it offline.\n'
                   '\n'
                   'schema N { v: int }\n'
                   'mk = Agent {\n'
                   '    model: "mock"\n'
                   '    role: "maker"\n'
                   '    out: N\n'
                   '}\n'
                   'n = "seed" >> mk\n'
                   'assert n.v == 0\n'}


def cmd_init(args):
    """Write a runnable project: main.ai, lib.ai, a test and a README."""
    target = Path(getattr(args, "path", None) or ".")
    if target.exists() and any(target.iterdir()) and not getattr(
            args, "force", False):
        print(f"init: {target} is not empty - pass --force to add files "
              f"anyway", file=sys.stderr)
        return 1
    written = []
    for name, body in INIT_FILES.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8", newline="\n")
        written.append(name)
    print(f"created {len(written)} file(s) in {target}:")
    for name in written:
        print(f"  {name}")
    print()
    print("next:")
    if target != Path("."):
        print(f"  cd {target}")
    print("  ailang run main.ai     # one program, offline, $0")
    print("  ailang test            # the suites under tests/")
    print("  ailang audit .         # is the organization ready?")
    return 0


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass
    p = argparse.ArgumentParser(
        prog="ailang",
        description="Aligment Insight - a language for organizations of "
                    "AI agents (package: ailang).")
    p.add_argument("--version", action="version",
                   version=f"ailang {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("selftest", help="run the built-in test suite")
    pd = sub.add_parser("demo", help="run the built-in demo program")
    pd.add_argument("--journal", help="record the demo calls to JSONL")
    sub.add_parser("kinds", help="list agent kinds (built-in + plugins)")

    pr = sub.add_parser("run", help="run a .ai program")
    pr.add_argument("file")
    pr.add_argument("--live", action="store_true",
                    help="use real API providers (needs keys)")
    pr.add_argument("--budget", type=float, help="stop at $ limit")
    pr.add_argument("--journal", help="record all calls to a JSONL file")
    pr.add_argument("--embed-source", action="store_true",
                    help="store the program inside the journal (v3)")
    pr.add_argument("--replay", help="replay deterministically")
    pr.add_argument("--stream", action="store_true",
                    help="print tokens as they arrive")
    pr.add_argument("--offline", action="store_true",
                    help="refuse to run when an agent needs the network")

    pc = sub.add_parser("check", help="compile without running (diagnostics)")
    pc.add_argument("file")
    pc.add_argument("--offline", action="store_true",
                    help="report which agents would need the network")
    pc.add_argument("--strict", action="store_true",
                    help="exit 1 when networked agents are found")
    pc.add_argument("--json", action="store_true",
                    help="machine-readable diagnostics")
    pb = sub.add_parser("build", help="compile a program into an image")
    pb.add_argument("file")
    pb.add_argument("-o", "--output", help="output file (default: *.ailc)")
    pb.add_argument("--force", action="store_true",
                    help="rebuild even when the image is up to date")
    pi = sub.add_parser("inspect", help="describe a compiled image")
    pi.add_argument("file")
    pf = sub.add_parser("flow", help="attention report: who gets the context")
    pf.add_argument("file")
    pf.add_argument("--json", action="store_true",
                    help="machine-readable report")
    pf.add_argument("--strict", action="store_true",
                    help="exit 1 when an agent overflows its window")
    pp = sub.add_parser("payroll", help="static wage bill of one run")
    pp.add_argument("file")
    pp.add_argument("--budget", type=float, help="compare against $ limit")
    pp.add_argument("--json", action="store_true",
                    help="machine-readable report")
    pp.add_argument("--strict", action="store_true",
                    help="exit 1 when the estimate exceeds the budget")
    prp = sub.add_parser("replay", help="replay a journal without models")
    prp.add_argument("file", help="journal file (JSONL)")
    prp.add_argument("--step", action="store_true",
                     help="one line per recorded step")
    prp.add_argument("--json", action="store_true",
                     help="machine-readable timeline")
    prp.add_argument("--program", help="re-run this program on the journal")
    prp.add_argument("--timeline", action="store_true",
                     help="show the recorded steps instead of re-running")
    prv = sub.add_parser("review", help="gate pass rates from journals")
    prv.add_argument("file", nargs="+", help="journal files (JSONL)")
    prv.add_argument("--json", action="store_true",
                     help="machine-readable report")
    prv.add_argument("--trend", action="store_true",
                     help="group the pass rate by day")
    prv.add_argument("--since", help="only calls on/after this date")
    prv.add_argument("--until", help="only calls on/before this date")
    prv.add_argument("--min-rate", type=float,
                     help="exit 1 when an auditor falls below this rate")
    pin = sub.add_parser("init", help="write a runnable starter project")
    pin.add_argument("path", nargs="?", default=".",
                     help="folder to create (default: here)")
    pin.add_argument("--force", action="store_true",
                     help="add files to a folder that is not empty")
    psu = sub.add_parser("setup", help="store API keys and endpoints")
    psu.add_argument("--openai", help="OpenAI key (sk-...)")
    psu.add_argument("--anthropic", help="Anthropic key (sk-ant-...)")
    psu.add_argument("--google", help="Google AI key (AIza...)")
    psu.add_argument("--endpoint", help="base URL of an OpenAI-compatible API")
    psu.add_argument("--key", help="key for that endpoint (optional)")
    psu.add_argument("--test", nargs="?", const="all", metavar="PROVIDER",
                     choices=["all", "openai", "anthropic", "google"],
                     help="make one tiny real call to check the key")
    psu.add_argument("--clear", action="append", metavar="NAME",
                     help="forget one entry (openai, anthropic, google, "
                          "endpoint)")
    psu.add_argument("--json", action="store_true",
                     help="machine-readable state")
    pcd = sub.add_parser("codes", help="what each AIL code means")
    pcd.add_argument("--json", action="store_true",
                     help="machine-readable catalogue")
    pau = sub.add_parser("audit",
                         help="one verdict: is the program ready?")
    pau.add_argument("file", nargs="+",
                     help="programs, or directories of them")
    pau.add_argument("--budget", type=float, help="compare against $ limit")
    pau.add_argument("--json", action="store_true",
                     help="machine-readable report")
    pau.add_argument("--strict", action="store_true",
                     help="warnings and overflow also block")
    pk = sub.add_parser("contract", help="labour contract card per agent")
    pk.add_argument("file")
    pk.add_argument("--json", action="store_true",
                    help="machine-readable report")
    sub.add_parser("doctor", help="report the installation state")
    pdoc = sub.choices["doctor"]
    pdoc.add_argument("--json", action="store_true",
                      help="machine-readable report")
    pg = sub.add_parser("grammar", help="print the grammar fingerprint")
    pg.add_argument("--expect", help="exit 1 unless the fingerprint matches")
    pg.add_argument("--doc", action="store_true",
                    help="list the rules, keywords and terminals")
    pt = sub.add_parser("test", help="run .ai programs as tests")
    pt.add_argument("paths", nargs="*",
                    help="files or directories (default: tests/)")
    pt.add_argument("--json", action="store_true",
                    help="machine-readable report")
    pg = sub.add_parser("graph", help="print the program graph (mermaid)")
    pg.add_argument("file")
    pa = sub.add_parser("ask", help="one-shot question to a model")
    pa.add_argument("question")
    pa.add_argument("--model", default="gpt-4o-mini")
    pa.add_argument("--live", action="store_true")
    pa.add_argument("--stream", action="store_true",
                    help="print tokens as they arrive")
    pfmt = sub.add_parser("fmt", help="format a program (canonical style)")
    pfmt.add_argument("file", nargs="+", help="files or directories")
    pfmt.add_argument("--check", action="store_true",
                      help="exit 1 when the file is not formatted")
    pfmt.add_argument("--write", action="store_true",
                      help="rewrite the file in place")
    ps = sub.add_parser("stats", help="cost & latency table from a journal")
    ps.add_argument("file", nargs="+",
                    help="one or more journal files (JSONL)")
    ps.add_argument("--json", action="store_true",
                    help="machine-readable output")
    prep = sub.add_parser("repl", help="interactive session")
    prep.add_argument("--stream", action="store_true",
                      help="print tokens as they arrive")
    psh = sub.add_parser("shell",
                         help="the AI-lang terminal (editor, history, :commands)")
    psh.add_argument("--live", action="store_true",
                     help="use real API providers (needs keys)")
    psh.add_argument("--budget", type=float, help="stop at $ limit")
    psh.add_argument("--stream", action="store_true",
                     help="print tokens as they arrive")

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    return {"selftest": selftest, "demo": cmd_demo, "run": cmd_run,
            "check": cmd_check, "graph": cmd_graph, "ask": cmd_ask,
            "repl": cmd_repl, "kinds": cmd_kinds,
            "stats": cmd_stats, "fmt": cmd_fmt,
            "shell": cmd_shell, "build": cmd_build,
            "inspect": cmd_inspect, "test": cmd_test,
            "flow": cmd_flow, "payroll": cmd_payroll,
            "contract": cmd_contract, "replay": cmd_replay,
            "audit": cmd_audit, "review": cmd_review,
            "init": cmd_init, "codes": cmd_codes, "setup": cmd_setup,
            "doctor": cmd_doctor, "grammar": cmd_grammar}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())

