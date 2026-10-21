# Alignment Family

[![ci](https://github.com/AligmentInsight/ailang/actions/workflows/ci.yml/badge.svg)](https://github.com/AligmentInsight/ailang/actions/workflows/ci.yml)

> Home of Aligment Insight — a programming language where every value is an
> AI agent.

**Aligment Insight** is a programming language for *organizations of AI
agents*. Schemas are job descriptions, `is_a` is a qualification, `budget`
is a wage, `judge` is a supervisor, gates are quality control and `payroll`
is the wage bill. The package and the command are called `ailang`; the
language is Aligment Insight. One file (`ailang.py`), one dependency
(`lark`), and a fully offline mock mode by default: no keys, no network,
no spend.![A run of `ailang demo` — an agent organization, offline](docs/screenshot-demo.png)

The Family today is the language and its household: the AI-lang shell, the
compiler with its AIL diagnostics and program images, the journal that
records a run and replays it, the audit pass that folds flow, payroll and
contracts into one verdict, and 231 checks that run offline in seconds.
What may join later is listed in `PROPOSALS.md` as candidates — invariants
across schemas, a fuller `policy` block — and each one enters as a field
value or a release, never as a quiet rewrite.

'Aligment' is the founder's spelling — the language keeps it as a signature. The Family spells it straight.

```ai
schema Pitch { title: str  hook: str  words: int }

muse = Agent { model: "gpt-4o"  role: "You generate startup ideas."  out: Pitch }
critic = Agent { model: "claude-3-5-sonnet"  role: "You are a merciless editor." }

pitch = "AI-native coffee shops" >> muse >> critic? "hook is concrete" and words < 12
print pitch
```

## Bring your own keys

One command stores a key, one file holds them all, and `--live` starts
spending. No SDKs are needed: OpenAI, Anthropic and Google are spoken to
over plain HTTP, so `lark` stays the only dependency.

```bash
ailang setup --openai sk-...            # or --anthropic, --google
ailang setup --test                     # one tiny real call per key
ailang setup --endpoint http://192.168.1.10:11434/v1 --key local
ailang setup                            # what is configured, keys masked
```

Keys live in `~/.ailang/config.json` (override the path with `AILANG_CONFIG`):

```json
{
 "providers": {
  "openai": {"key": "sk-..."},
  "anthropic": {"key": "sk-ant-..."},
  "google": {"key": "AIza..."},
  "openai-compatible": {"base_url": "http://192.168.1.10:11434/v1"}
 }
}
```

A pasted key wins over the environment (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
`GOOGLE_API_KEY`), so a CI runner can override without editing files. An
agent only names its model; the key and the base URL come from here.

```ai
writer = Agent { model: "gpt-4o"  role: "Draft a brief." }   # key from config
```

`ailang doctor` prints which provider is ready, and a missing key is a clear
message that names `ailang setup` instead of a stack trace.

## Status

Everything in this repository runs offline: `demo`, `selftest`, all six
examples, the governance commands and `ailang init`. Mock mode is the
default and costs nothing, so a reader can try the language without a key.

Three things are **not** verified here, and the reader deserves to know:

- **Live providers.** `--live` with a real key has never been executed in
  this repository (no key was available). The selftest drives all three
  dialects against local stub servers - request shape, headers, usage,
  accounting and SSE - but the vendors themselves are untested against this
  code.
- **CI and the installed package.** `.github/workflows/ci.yml` runs the
  selftest, the `.ai` suites, the formatter check, the governance commands and
  the demo on Ubuntu and Windows across Python 3.10, 3.12 and 3.13 - all six
  cells green. `pip install .` from a clone installs and imports cleanly;
  `ailang` is not on PyPI yet, so use the clone or the single file. Run
  `ailang selftest` from a clone: the checks read `pyproject.toml`,
  `README.md` and the examples next to `ailang.py`, so an installed copy
  reports those as failures.
- **A real terminal.** `ailang shell` decodes `msvcrt`/`termios` keys; the
  editor logic is tested, the hardware path is not.

When something looks wrong, the first command to run is `ailang doctor`:
it reports the version, the grammar fingerprint, the kinds, the price table
and the code catalogue.

## Quickstart

```bash
pip install ailang
ailang init myorg              # a runnable project: main.ai, lib.ai, a test
cd myorg && ailang run main.ai # one program, offline, $0
ailang demo                    # offline demo, prints the Pitch artifact
ailang selftest                # 231/231 checks
ailang shell                   # the bespoke terminal
ailang kinds                   # the agent family registry
```

`ailang init` writes `main.ai` (schema with a `where` limit, two named
gates, a `policy`, two agents), `lib.ai` (a shared schema imported with
`use`), `tests/smoke.ai`, a README and a `.gitignore`. The project is
canonical, passes `audit . --strict` and runs without a network.

**Fallback:** no install needed — run the single file directly:

```bash
py ailang.py demo
py ailang.py selftest
py ailang.py shell
```

## How it compares

Orchestrators manage calls. Aligment Insight manages organizations: the
compiler knows who may talk to whom, what a call may cost and who audits
the result.

| | Aligment Insight | LangChain | CrewAI | DSPy |
|---|---|---|---|---|
| semantic types (`schema`, `is_a`) | ✓ compiler-checked | — | — | — |
| compiler diagnostics (AIL0xx) | ✓ | — | — | partial |
| payroll & cost accounting | ✓ `ailang payroll` | — | partial | — |
| attention report per agent | ✓ `ailang flow` | partial | — | — |
| labour contracts (`judge`, `budget`, `timeout`) | ✓ one field each | partial | partial | — |
| offline tests, no keys, $0 | ✓ 231 checks | — | — | partial |
| versioned grammar contract | ✓ fingerprint locked in CI | — | — | — |

## Governance in practice

`examples/org_review.ai` is one program with subtypes, a supervisor, a gate
and a wage cap. Three commands read it without calling a model:

```console
$ ailang flow examples/org_review.ai
agent           engine                ~tok_in   share   window  status
researcher      mock                      143     77%     8192  ok sink
analyst         mock                       18     10%     8192  ok
editor          mock                       24     13%     8192  ok
qa              mock                        0      0%     8192  idle

total ~tok_in: 185
attention sink: researcher (77%) - heuristic, not an error

$ ailang payroll examples/org_review.ai --budget 0.01
agent           engine                ~in   ~out     $/call  att  calls    worst $
researcher      mock                  143      5   0.000000    3      1   0.000000
analyst         mock                   18     10   0.000000    3      1   0.000000
editor          mock                   24     23   0.000000    3      1   0.000000
qa              mock                   10    128   0.000000    3      0   0.000000  idle

total (worst case): $0.000000
idle (never called, not billed): qa
budget $0.010000: OK (estimate $0.000000)

$ ailang contract examples/org_review.ai
agent editor  (chat, mock)
  role        'Editor: turn the summary into a report with findings.'
  wage cap    $0.0500
  deadline    30s   retries 2
  supervisor  qa   window 8192
  duties      in: Summary  out: Report
  gates       'verdict != none'
```

`~` marks every estimate: tokens are `max(words, chars/4)`, never a real
tokenizer. Prices come from one table; an unknown engine prints `?` and is
left out of the total, because the language does not invent money.

## Semantic types

`patch` on a schema declares a subtype; nothing else in the grammar changed:

```ai
schema Text { body: str }
schema Dated { date: str }
schema Summary { body: str, date: str }
patch Summary { is_a: Text  is_a: Dated }     # multiple qualifications

writer = Agent { model: "gpt-4o"  role: "…"  out: Summary }
reader = Agent { model: "gpt-4o"  role: "…"  in: Text }

"seed" >> writer >> reader                     # accepted: Summary is a Text
```

A qualification is checked, not merely declared: the child must supply
every required field of its parent with a compatible type, otherwise
`AIL036` fires.

A schema can also state its own hard limits. A `where` clause is enforced
at every cast, every `patch` and every `in:` check, and the compiler rejects
a clause that names a field that does not exist or compares the wrong type
(`AIL042`):

```ai
schema Price { amount: float where amount >= 0 }
schema Score { points: int where points >= 0  verdict: str where verdict != "" }
```

```console
$ py ailang.py run standards.ai
error[AIL110]: patch x: $.amount: invariant 'amount >= 0' does not hold
(clause where)
```

Standards are reusable: `gate` declares a criterion once and every agent can
be held to it.

```ai
gate has_topic = topic != ""
gate priced = budget >= 0
gate accepted = has_topic and priced        # gates compose
gate billed = sum(lines.price) >= 0         # map over a list of records

brief = "launch a coffee brand" >> writer >> critic? accepted
```

`agent? has_topic` is exactly the inline `agent? topic != ""`, so the typed
checks still apply (`AIL037`), and `contract` prints the standard by name.

```console
error[AIL036]: schema "Summary" declares is_a: Text but does not supply
field "tone": str
```

Gates are typed too: the compiler knows the producer's `out:` schema, so
a misspelled field or a nonsensical comparison is caught statically. A
criterion may measure a string or a list with `len(...)`:

```ai
gate has_tags = len(tags) > 0
schema Post { title: str where len(title) > 3 }
```

`until` is as cheap as it can be: a deterministic criterion is evaluated
locally, and only a semantic one is handed to the auditor.

A `policy` block states the file's defaults once - the employment contract
every agent signs unless it negotiates its own terms:

```ai
policy {
    model: "mock"
    salary: 0.50
    timeout: 30
    judge: qa
}

writer = Agent { role: "Draft a brief."  out: Brief }   # takes all four
critic = Agent { role: "Score it."  out: Score  timeout: 5 }   # keeps its own
```

A field on the agent always wins. Identity fields (`kind`, `in`, `out`,
`tools`, `run`) cannot be defaulted - `AIL043` - because one value for all
agents is a bug factory, not a policy.

```console
error[AIL037]: gate 'scroe >= 0': Note has no field "scroe" (producer "mk")
error[AIL037]: gate 'txt > 5': field "txt" is str, compared with int
```

A chain that breaks the hierarchy is rejected before any model call:

```console
error[AIL032]: agent "poet" produces Quote, but "analyst" expects Research:
Quote is not a subtype of Research
```

Unknown parents are `AIL031`, inheritance cycles are `AIL030`, and a patch
on a schema that carries anything besides `is_a` is `AIL100`. When the
compiler is unsure — an unknown schema, a composite expression — it says
nothing.

## Operators

| operator | meaning |
|----------|---------|
| `a >> b` | flow of insight: `a`'s answer becomes `b`'s task |
| `a & b`  | chain: run in sequence |
| `a \|\| b` | parallel: gather both answers |
| `a + b`  | merge "DNA": synthesize one hybrid personality |
| `a ?? b` | insurance: try `a`, fall back to `b` on failure |
| `agent? "criterion"` | semantic gate: an auditor judges the artifact |
| `until q? "crit" max 3 { … }` | loop until the gate passes |
| `if cart.total > 100 { … } else { … }` | deterministic branch; the value is the block's last line |
| `flow f(x) { … }` | first-class user morphism |
| `match r("t") { "a" >> w  _ >> w }` | semantic routing — a statement **or** an expression |
| `patch p { field: value }` | validated artifact edit (nested fields too) |
| `assert cond` | deterministic check; a false condition fails with its line |
| `use "lib.ai"` / `use "agent.py"` | import a library or a plugin |

## Examples — real jobs, one file each

```bash
py ailang.py run examples/pr_review.ai    # three reviewers in parallel + gate
py ailang.py run examples/triage.ai       # ticket router with an escalation net
py ailang.py run examples/digest.ai       # long document -> typed digest
py ailang.py run examples/maintainer.ai   # issues.txt -> reply drafts, offline
py ailang.py run examples/org_review.ai   # subtypes, judge, salary, one verdict
py ailang.py run examples/standards.ai    # named gates + where clauses + salaries
```

All six run offline in mock mode and are covered by the selftest.

## Agents and kinds

An agent's species is a field, not new syntax:

```ai
chat  = Agent { model: "gpt-4o"  role: "…" }            # default kind
local = Agent { kind: py  run: "json.dumps" }           # deterministic, $0
```

Built-in kinds: `chat`, `py`, `human`, `echo`. `ailang kinds` lists them
together with every plugin. Kind names never change the grammar.

## Bring your own agents

### Local model through Ollama (one field)

```ai
ollama = Agent { model: "llama3.1"  role: "You write crisp copy."
                 endpoint: "http://localhost:11434/v1" }
print "slogan" >> ollama        # run with --live
```

`endpoint:` is any OpenAI-compatible base URL — Ollama, OpenRouter, Groq,
Together, vLLM, LiteLLM, DeepSeek, ... In the default dry-run everything
stays mock; with `--live` requests go to that endpoint.

### OpenRouter with the key in the environment

```ai
critic = Agent {
    model: "anthropic/claude-3.5-sonnet"
    role: "You are a merciless editor."
    endpoint: "https://openrouter.ai/api/v1"
    env: "OPENROUTER_API_KEY"
}
```

Keys live in environment variables (`env: "VAR"`), never inside programs.
`reasoning: "true"` pins temperature to 1 and folds the system prompt into
the user message for o1/o3/R1-style models.

### Your own kind — a plugin

```ai
use "agents/hello_kind.py"      # registers the "shout" kind
loud = Agent { kind: shout  role: "loud" }
print "hello" >> loud           # HELLO
```

Contract, name rules and the full example: [PLUGINS.md](PLUGINS.md).

Files dropped into `plugins/` (or `agents/`) are discovered automatically
by `run`, `check`, `build`, `test` and `kinds` - no `use` needed - and a
compiled image records which plugin file provided each kind.

## Escalation: a gate inside `??`

```ai
reply = ticket >> desk? "polite and specific" ?? human_desk
```

`desk` answers the ticket, the gate judges the answer, and on failure
`human_desk` receives the **original ticket** — not the rejected draft.
Deterministic gates (`? field > 0`) never call a model.

> The "original task" is the input that arrived at this node: in
> `x >> a? "crit" ?? b` it is `x`, and after a chain `x >> w >> a? … ?? b`
> it is `w`'s answer. Wrap the node in parentheses to pin it.

## Auditing

Any agent can audit — **including a typed one**. A verdict is text, not an
artifact, so the auditor's `out:` contract is not applied to `PASS/FAIL`.

```ai
schema Review { concerns: int  note: str }
qa = Agent { model: "gpt-4o"  role: "harsh reviewer"  out: Review }
report = diff >> reviewer >> qa? "no critical issues" and concerns < 5
```

Gates read **nested** fields too, straight from the schema shape:

```ai
schema Order  { total: int  currency: str }
schema Report { order: Order  ok: bool }
checked = "checkout" >> report >> qa? order.total >= 0 and order.currency != ""
```

When an agent has to judge its **own** answer (`a? "crit" ?? b`), name an
independent judge with a field — no new syntax, just a field value:

```ai
desk = Agent { model: "gpt-4o"  role: "billing desk"  judge: editor }
editor = Agent { model: "claude-3-5-sonnet"  role: "independent editor" }

reply = ticket >> desk? "polite and specific" ?? human_desk
```

The gate now asks `editor`, not `desk`. Verdicts are parsed strictly: a
leading `PASS` (markdown and quotes tolerated) passes, anything ambiguous
fails closed.

## Branching

`if` is deterministic and costs nothing — no model is called:

```ai
schema Cart { total: int }
cart = checkout >> cart_agent
ship = if cart.total > 100 { "free shipping" >> perks } else { "standard shipping" >> desk }
```

* The condition uses the gate vocabulary (`>`, `>=`, `==`, `!=`, `and`, `or`,
  parentheses, `true`/`false`), but **no semantic strings**: an `if` never
  calls an LLM. Semantic routing is `match`; validation is a gate `?`.
* Operands are resolved through the environment: `cart.total` reads the
  artifact bound to `cart`; a bare `total` is a name in scope.
* The branch value is the value of the block's last statement, so `if`
  works as an expression: `x = if …`, `print if …`.
* Without `else`, a false condition yields `None`.

Fork branches are addressable by name, in gates and in `if`:

```ai
crowd = "topic" >> (one || two)
checked = "topic" >> (one || two) >> qa? one.v >= 0 and two.v >= 0
pick = if crowd.one.v >= 0 { "ok" } else { "bad" }
```

## Support the Family

Aligment Insight costs $0 to try: mock mode runs the whole language
offline, no keys, no spend. If it saved you an API bill or a week
of plumbing — send back a percent of the savings.

Bitcoin (native SegWit):
![Bitcoin](donate/donate-btc.png)
`bc1qgpa4rarq6928lypdr8mhv5nzwzqnrdpyg96w3u`

Ethereum / USDT (ERC-20):
![Ethereum](donate/donate-eth.png)
`0xa2bEf6eD2AeF47fd7D11Deb675fAEF96c2c9a18d`

Solana / USDT (SPL):
![Solana](donate/donate-sol.png)
`J9RCBpkpKAJg5zk1zLfW57JFHEwmH4HQcKXtDiYLgP8S`

These are the only official addresses. They live in this repository
and nowhere else — treat any other copy as fake.

## Errors

Runtime failures name the statement that raised them:

```bash
$ py ailang.py run prog.ai
error[AIL001]: line 3: Unknown name: 'nobody'
```

Codes are stable and shared with `check`, so a CI log is searchable:

| code | meaning |
|------|---------|
| `AIL101` | a failed `assert` |
| `AIL102` | a gate that never passed |
| `AIL110` | an agent broke its `out:` contract |
| `AIL111` | the budget is exhausted |
| `AIL112` | an agent hit its `timeout:` |
| `AIL120` | a bad or foreign program image |
| `AIL100` | anything else |

Syntax errors already point at the line. Files saved with a UTF-8 BOM (a
common Windows default) are read correctly.

## The AI-lang Shell

`ailang shell` is the language's own terminal — not a wrapper around
`input()`. It owns the line editor, so it works the same everywhere:

```
Aligment Insight Shell 0.38.0 - mock mode, offline
:help for commands, Ctrl-D to exit
insight[mock]> muse = Agent { model: "gpt-4o"  role: "You generate ideas." }
agent  muse  (gpt-4o)
insight[mock]> "hi" >> muse
str  "[MOCK · gpt-4o] Playing a bold idea with a hook.\nInput: \"hi\""
insight[mock]> :agents
  muse             agent  muse  (gpt-4o)
insight[mock]> :quit
bye
```

What is inside:

* **Line editing without dependencies** — arrows, Home/End, Delete,
  Backspace, plus `Ctrl-A`/`Ctrl-E` (home/end), `Ctrl-K`/`Ctrl-U` (kill to
  end/start), `Ctrl-W` (kill word); `Ctrl-C` cancels the line, `Ctrl-D`
  exits, `Ctrl-L` clears. Windows uses `msvcrt`, POSIX a cbreak tty.
* **History** — `Up`/`Down` with the draft preserved, saved to
  `~/.ailang_history` between sessions and browsable with `:history`.
* **Completion** — `Tab` completes keywords, declared names, kinds and
  `:commands`; an ambiguous prefix lists the candidates.
* **Typed rendering** — every answer says what it is: `str`, `agent`,
  `chain`, `parallel`, `backup`, `fork`, or `Pitch {…}` for an artifact.
* **`:commands`** — `:help`, `:agents`, `:kinds`, `:graph`, `:stats`,
  `:fmt`, `:run`/`:load`, `:history`, `:clear`, `:quit`.
* **Multi-line input** — an open `{` or quote continues on the next line.

When stdin is a pipe the shell degrades to a plain line reader, so it can
be scripted: `cat session.txt | ailang shell`.

## Formatting

```bash
py ailang.py fmt examples/digest.ai            # print canonical source
py ailang.py fmt examples/digest.ai --check    # exit 1 if not formatted
py ailang.py fmt examples/digest.ai --write    # rewrite in place
```

`fmt` also accepts directories (`ailang fmt . --check` in CI).

The printer works on the AST, so `parse(fmt(x)) == parse(x)` and formatting
is idempotent — that is exactly what `--check` relies on. Leading comment
blocks survive; comments elsewhere cannot cross an AST round-trip, and
`--write` reports the lines it drops.

## Toolchain

```bash
ailang doctor     # python, lark, grammar, kinds, prices, windows, history
ailang codes      # what every AIL code means
ailang replay run.jsonl [--step] [--json]  # the time machine
ailang grammar    # the GRAMMAR fingerprint
ailang grammar --doc   # rules, keywords, terminals
ailang grammar --expect 75b9a04d8fb4fa77   # CI guard against drift
ailang flow FILE [--json] [--strict]       # attention report
ailang payroll FILE [--budget X] [--json]  # static wage bill
ailang contract FILE [--json]              # labour contract cards
ailang audit PATH... [--json] [--strict]    # one readiness verdict
ailang review JOURNAL... [--since D] [--min-rate R]   # gate history
ailang review JOURNAL... --trend        # pass rate per day
ailang init [PATH] [--force]        # a runnable starter project
```

`doctor` is what a bug report should start with; `grammar --expect`
fails the build when the grammar moves without a release note.

## Who does the work

| command | question |
|---|---|
| `ailang flow FILE` | who gets the context, and does it fit the window? |
| `ailang payroll FILE` | what does one run cost, worst case? |
| `ailang contract FILE` | what are the terms of each post? |
| `ailang audit FILE...` | is the whole organization ready to run? |
| `ailang review J...` | how often did the gates actually hold? |

All four are static: they parse, estimate and report without calling a
model. `--strict` turns their warnings into a non-zero exit code, which is
what CI uses. `audit` folds all of them into one verdict:

```console
$ ailang audit examples/org_review.ai
audit      examples/org_review.ai
syntax     ok (14 statements)
types      ok        0 error(s), 0 warning(s)
attention  4 agent(s)  ~185 tok, no overflow, sink: researcher
payroll    $0.000000 worst case
contracts  4 card(s), 1 with a supervisor, 1 with gates

READY - nothing blocks a run
```

Paths may be files, compiled images (`.ailc`) or whole directories
(`ailang audit examples/` walks them recursively and skips `_archive/`,
`.zwork/` and other hidden or underscored folders). An image built for a
different grammar is reported as `STALE` and blocks the verdict, so a
deploy artefact can be audited before it ships. Several paths are audited as one organization: each
program keeps its own block, and a `totals` line adds up agents, tokens and
the wage bill.

```console
$ ailang audit examples/org_review.ai examples/triage.ai
...
totals     2 programs, 9 agents, 0 error(s), ~896 tok, $0.000000 worst case

READY - nothing blocks a run
```

Its `--json` report carries `"schema": 1`, like every other machine
output, so CI parsers can pin the shape. With a single file the report also
carries the flat keys (`file`, `syntax`, `flow`, `payroll`, `contracts`).

## Compiler

`check` compiles without running; `build` produces a program image.

```bash
$ py ailang.py check examples/triage.ai
Syntax OK - 10 statement(s)

$ py ailang.py build examples/triage.ai -o triage.ailc
compiled examples/triage.ai -> triage.ailc (10 statements, grammar 09c1ac9b…)

$ py ailang.py run triage.ailc      # an image runs without re-parsing
$ py ailang.py inspect triage.ailc  # header, libraries, staleness
```

`check` and `build` run the same static pass, so a program that compiles
is free of the mistakes that do not need a model to find:

```
$ py ailang.py check broken.ai
error[AIL001]: unknown name 'ghost'
 --> line 2, column 14
     2 | bad = "q" >> ghost
                      ^^^^^
  help: declare it before use, e.g. ghost = Agent { model: "gpt-4o"  role: "..." }
```

| code | meaning |
|------|---------|
| `AIL001` | unknown name, callable, router or patch target |
| `AIL002` | unknown agent kind |
| `AIL004` | `in:`/`out:` names a schema that is not declared yet |
| `AIL005` | a schema field references an unknown schema |
| `AIL006` | `tools:` names an undeclared tool |
| `AIL010` | a gate starts a `match` branch |
| `AIL020` | `use` cannot read or parse the library |
| `AIL021` | the file does not parse |
| `AIL030` | schema inheritance cycle |
| `AIL031` | `patch … { is_a: X }` names an unknown schema |
| `AIL032` | a chain breaks the subtype hierarchy |
| `AIL033` | `judge:` names an agent that does not exist |
| `AIL034` | `timeout:` is not positive or `casts:` is below 1 |
| `AIL035` | warning: the wage cap is below the estimated cost |
| `AIL036` | a subtype does not supply a field its parent requires |
| `AIL037` | a gate addresses an unknown field or compares mismatched types |
| `AIL038` | `agent? name` refers to a gate that was never declared |
| `AIL039` | a gate name is declared twice |
| `AIL041` | warning: `salary:` cannot cover the estimated run |
| `AIL042` | a `where` clause names an unknown field or compares mismatched types |
| `AIL043` | a `policy` block tries to default an identity field |
| `AIL113` | the run exhausted an agent's `salary:` |

`check` analyses the file **and every `use "*.ai"` library**, each against
its own source text, so a library error is reported with the library's file
and the correct caret. Results are cached per program (keyed by the root
text, the library texts, the grammar fingerprint and the analyzer
revision): a library edit misses the cache, a grammar change invalidates
everything. `check`, `test`, `doctor` and the governance commands all take
`--json` for CI.

A compiled image is JSON: format tag, `ailang` version, a **grammar
fingerprint**, the source name, statement lines and the serialised AST.
`use "*.ai"` libraries are **linked into the image**, so an artifact runs
without its sources (plugin `.py` files stay external - they are code).
Running an image whose fingerprint does not match the interpreter is
refused with `rebuild it`, so an old artefact can never run on new
semantics by accident.

## Testing

Programs are testable without any framework: `assert` takes the same
deterministic condition as `if`, and `ailang test` runs every `.ai` file
under the given paths (default `tests/`).

```ai
# tests/language.ai
schema N { v: int }
mk = Agent { model: "x"  role: "maker"  out: N }
n = "q" >> mk
patch n { v: 7 }

assert n.v == 7
assert n.v > 3
```

```bash
$ py ailang.py test
ok   tests/language.ai
ok   tests/pipeline.ai

2/2 passed
```

A file passes when the whole program raises nothing; a failed `assert`
reports its line. `ailang test` always runs in mock mode, so the suite
never spends money and never needs a key.

## Economics: `stats`

Every run can write a journal; `stats` turns one or more of them into a
table (or JSON for scripts):

```bash
py ailang.py demo --journal demo.jsonl
py ailang.py stats demo.jsonl              # table
py ailang.py stats day1.jsonl day2.jsonl   # merge several runs
py ailang.py stats demo.jsonl --json       # machine-readable
py ailang.py replay demo.jsonl              # re-runs the recorded program
py ailang.py run prog.ai --journal d.jsonl --embed-source   # source inside
py ailang.py replay demo.jsonl --timeline    # the steps, no model calls
py ailang.py replay demo.jsonl --step       # one line per recorded step
py ailang.py replay demo.jsonl --program prog.ai   # re-run on the journal
AILANG_JOURNAL=run.jsonl py ailang.py run prog.ai   # auto-journal
```

Mock journals are labelled `mode: mock (dry-run) — costs are zero by
design`; live runs (and streams that return usage) carry real token and
cost numbers.

## Speed: `--stream`

```bash
py ailang.py run examples/digest.ai --stream
py ailang.py repl --stream
```

Mock streams deterministic chunks (offline and testable); live providers
use their streaming API where available and fall back to a single honest
chunk otherwise. Only agent *productions* stream — gate verdicts, routers
and cast repairs stay quiet.

## Offline check

```bash
py ailang.py check examples/triage.ai --offline
# offline-safe: every agent is local (mock / py / endpoint=localhost)

py ailang.py check examples/pr_review.ai --offline --strict
# networked agents: 5   (exit 1, useful in CI)

py ailang.py run examples/triage.ai --offline
# refuses to start if any agent would need the network
```

The check follows `use "lib.ai"` imports, reports plugins it cannot
analyse, and treats an unknown model without `endpoint:` as networked —
the safe default.

## Libraries

`use "lib.ai"` resolves **relative to the importing file first**, then to
the current directory, and remembers the chain: a file that imports itself
(directly or through a cycle) fails fast with
`use cycle: a.ai -> b.ai -> a.ai` instead of recursing forever.

## Compatibility corpus

`corpus/` holds one program per family of constructs (operators, gates,
until/match, schemas, flows, race, tools). Selftest check #25 runs them all
before every release: regressions in the parser or runtime fail the suite
before a release can ship.

## Versioning and the grammar contract

The grammar is a versioned contract: the fingerprint is locked in CI and in
selftest; any change is a minor release with a CHANGELOG entry. Within a
minor line the language grows by **field values** (`kind:`, `endpoint:`,
`judge:`, …), never by syntax. When the contract does move, the release note
says so:

* **0.8.0** — `match` is an expression (`x = match r(t) { … }`), agent
  field values may be hyphenated (`kind: shout-loud`), and `||` yields a
  `ForkValue` (still a `str`, now with `.parts`). `match` became fully
  reserved: it can no longer be used as a variable or field name.
* **0.9.0** — gates and `patch` address nested fields (`order.total >= 0`,
  `patch p { order.total: 42 }`); `use` resolves next to the importing
  file and detects cycles; `check --offline --strict` exits non-zero for CI.
* **0.10.0** — deterministic branching `if <cond> { … } else { … }` and
  boolean literals `true`/`false` (in conditions and `patch`). The branch
  value is the block's last statement; no model is ever called.
* **0.11.0** — `ailang fmt` (canonical printer with `--check`/`--write`).
  Agent field values now keep the identifier/string distinction (`Ref`),
  so `out: Pitch` never comes back quoted.
* **0.14.0** — the compiler: static analysis with diagnostics and a
  runnable program image.
* **0.15.0** — `assert` and `ailang test`; images link `use "*.ai"`
  libraries so an artifact is self-contained.

## Repository layout

```
ailang.py              the whole language (single file)
agents/                plugins - kind "shout" lives here
examples/              four flagship programs + their data
corpus/                one program per construct family (regression net)
plugins/               drop-in kind bundles, discovered automatically
tests/                 .ai test suites, run with `ailang test`
grammar_hash.py        prints the GRAMMAR fingerprint used in release notes
PROPOSALS.md           grammar-level design notes and decisions
PLUGINS.md             the plugin contract
CHANGELOG.md           release history
CONTRIBUTING.md        how to run, test and extend
.github/workflows/     CI: selftest, tests, fmt and audit on Ubuntu and Windows
```

```bash
py ailang.py selftest                  # 231 checks, exit 0
py ailang.py test                      # .ai suites
py ailang.py fmt corpus/operators.ai --check
```

## Known limits

These are known and deliberate; [PROPOSALS.md](PROPOSALS.md) describes the
remaining fixes that would need another grammar change.

* **`if` branches are summed.** `flow` and `payroll` add both branches of
  an `if`, so those figures are an upper bound. A loop body
  (`until … max N`) is multiplied by `N`, which is exact.
* Kind names may contain `-` in the registry and in `kind:` values, but a
  hyphenated **variable** name (`my-agent = …`) is still not a `NAME`.* `check --offline` is static: an `endpoint` assembled at run time is not
  visible, and plugin `.py` files are listed but not analysed.
* `race` breaks ties in declaration order, but wall-clock still decides
  when one branch truly finishes first — simultaneity is not observable.
* Streaming keeps usage only when the provider reports it
  (`stream_options.include_usage`); otherwise the call shows zero tokens.
* Cost accounting is journal-only: nothing is written unless `--journal`
  or `AILANG_JOURNAL` is set.
* A compiled image inlines its `.ai` libraries, so a library edited after
  `build` is not picked up until the image is rebuilt.
* `match` now owns the word: a program that used `match` as an identifier
  must rename it (the only breaking change in 0.8.0).

## License

MIT - see [LICENSE](LICENSE).

## Support the Family

Aligment Insight costs $0 to try: mock mode runs the whole language
offline, no keys, no spend. If it saved you an API bill or a week
of plumbing — send back a percent of the savings.

Bitcoin (native SegWit):
![Bitcoin](donate/donate-btc.png)
`bc1qgpa4rarq6928lypdr8mhv5nzwzqnrdpyg96w3u`

Ethereum / USDT (ERC-20):
![Ethereum](donate/donate-eth.png)
`0xa2bEf6eD2AeF47fd7D11Deb675fAEF96c2c9a18d`

Solana / USDT (SPL):
![Solana](donate/donate-sol.png)
`J9RCBpkpKAJg5zk1zLfW57JFHEwmH4HQcKXtDiYLgP8S`

These are the only official addresses. They live in this repository
and nowhere else — treat any other copy as fake.
