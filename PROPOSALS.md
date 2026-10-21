# Proposals — improvements that need a grammar change

AI-lang's release rule: **the grammar is a versioned contract — the
fingerprint is locked in CI and in selftest, and any change is a minor
release with a CHANGELOG entry; the language grows by field values, not by
syntax.** Each item below is either solved without a grammar change (and
marked *done*) or still open with a workaround.

Open items list the pain, the smallest syntax change, the risk and the
current workaround.

---

## 1. `match` as an expression

**Status. Done in v0.8.0 — grammar change (sanctioned).**

`match_expr` is now an atom, so the routed answer can be bound and even
printed inline:

```ai
desk = match router(ticket) { "billing" >> billing  _ >> human_desk }
print desk
print match router(t) { "a" >> w }
```

**Cost.** `match` became a fully reserved word — it can no longer be a
variable or field name (the LALR lexer prefers the keyword when both
match). That is the only breaking change of 0.8.0 and it is documented in
README (Known limits).

---

## 2. An explicit judge in an escalation gate

**Status. Done in v0.7.3 — without a grammar change.**

The agent field `judge: NAME` names an independent auditor; `a? "crit" ?? b`
resolves it instead of asking `a` to judge itself. The grammar already
parsed the field, so only runtime code changed:

```ai
desk = Agent { model: "gpt-4o"  role: "billing desk"  judge: editor }
reply = ticket >> desk? "polite and specific" ?? human_desk
```

A syntax variant (`a? "crit" by judge ?? b`) remains optional; it buys a
per-gate judge rather than a per-agent one:

```ai
reply = ticket >> desk? "polite and specific" by editor ?? human_desk
```

**Risk (if ever taken).** Low for the grammar (one optional terminal), low
for the runtime: `Gate` gains a `judge` field and `_agate` already knows
how to resolve one.

---

## 3. Hyphenated kind names

**Status. Done in v0.8.0 — grammar change.**

`agent_field_value` gained `hyphen_name: NAME (MINUS NAME)+`, so
`kind: shout-loud` parses; the value reaches the registry as the string
`"shout-loud"` and matches `[a-z][a-z0-9-]*`. The `MINUS` terminal is new
but harmless: the language has no arithmetic.

**Residual.** Hyphenated *variable* names (`my-agent = …`) are still not
`NAME`; only field values accept the hyphen form.

---

## 4. Usage accounting for streamed calls

**Status. Done in v0.7.2 — provider protocol, no grammar change.**

Providers publish `last_usage` (a `Completion` with tokens and cost).
OpenAI streaming sends `stream_options.include_usage`; the endpoint
provider parses the usage chunk of any OpenAI-compatible SSE stream; the
runtime puts the numbers into the journal and `stats`. Providers that
report nothing stay at zero tokens — an honest zero.

**Pain (residual).** A provider that never reports usage cannot be
accounted for; `stats` shows zero for those calls.

---

## 5. Runtime `type` for composite values

**Status. Done in v0.8.0 (runtime only, no grammar).**

`a || b` now returns a `ForkValue` — a `str` subclass, so every existing
program keeps working — carrying `.parts` (agent name → its answer) and
`.get(name)`. Python-side consumers (plugins, `py` agents) can inspect
branches without parsing the joined text.

**Residual.** The language itself still cannot reach `.parts`: artifact
field access (`.field`) works only on `TypedArtifact`. A schema-backed
fork result remains future work.

---

## Delivered in 0.30.0

- Named gates (`gate name = …`), reusable across flows and `until`.
- `salary:` - a per-run wage cap next to the per-call `budget:`.
- Field invariants (`schema X { f: int where f >= 0 }`), checked at casts,
  patches and `in:` edges.
- Signed literals (`-5`, `-0.5`).

## Next candidates (no promise)

- Invariants across fields of *different* schemas, e.g. `where total >= sum`.
- A `policy` block that bundles gates, salary and defaults for a file.
- `ailang review` - per-gate pass rates computed from journals.

## Still open: nothing grammar-blocking

After 0.8.0 the remaining rough edges are environmental (a real
`pip install .` needs setuptools/network) or physical (`race` simultaneity
is unobservable). No further syntax change is planned.

---

## Done in 0.10.0: deterministic branching

`if <cond> { … } else { … }` — an expression whose value is the block's
last statement. Design decisions worth recording:

* **Deterministic only.** The condition reuses the gate vocabulary but
excludes semantic strings: branching must never depend on a model call.
  Semantic routing stays `match`; validation stays `a? "crit"`.
* **Operands resolve through env.** `cart.total` reads the artifact bound
to `cart`; a bare name must exist in scope. There is no implicit
  "current artifact" — explicitness beats magic in a branch.
* **Blocks are not scopes.** They assign into the same environment, like
  `until` and `flow` bodies. Uniform, and documented.
* **No `else` → `None`** — the value of a skipped branch.
* **`true`/`false` literals** joined the grammar so boolean fields can be
  compared and patched; they are reserved words now.

---

## Done in 0.13.0: addressing fork branches

`a || b` already returned a `ForkValue`; now its parts are addressable from
the language itself, in gates and in `if`:

```ai
crowd = "topic" >> (one || two)
checked = "topic" >> (one || two) >> qa? one.v >= 0 and two.v >= 0
pick = if crowd.one.v >= 0 { "ok" } else { "bad" }
```

One resolver walks artifacts, nested dicts and fork parts, so `crowd.one.v`
means "the `v` field of branch `one`". Field paths in `attr` are no longer
limited to one dot.

## Journal format (v2 in 0.21.0, v3 in 0.24.0)

`$journal: 2` carries the program path, its hash, the statement count and
the grammar fingerprint. `$journal: 3` adds the program text itself when
`run` is called with `--embed-source`, so the journal replays on its own.
Older files (v1, v2) keep replaying; no further format work is queued.
