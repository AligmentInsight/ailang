# Changelog

All notable changes to AI-lang. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versions follow
`MAJOR.MINOR.PATCH` where a **minor** bump may change the grammar (always
noted) and a patch never does.

## [0.38.0] - 2026-10-21

A measure can now walk into a list of records, which is what real documents
are made of: invoices with lines, orders with items, reports with findings.
Grammar unchanged.

### Added
- `sum(lines.price)`, `avg`, `min`, `max` and `len` accept a path that
  crosses a list of records: the tail is mapped over the items and the
  numbers are aggregated. Works in gate criteria, `if` and `where` clauses.

### Fixed
- Such a path was rejected statically (`AIL037: Invoice has no field
  "lines.price"`); the schema walker now descends into `[Line]` and reports
  a typo at the leaf (`lines.prices` still fails, as it should).
- Two helpers (`_named_criterion`, `_gate_label`) had been lost during an
  earlier refactor and were restored; every gate label and `AIL037` message
  uses them again.

### Changed
- Selftest grew from 228 to 231 checks (a mapped sum in a gate, a mapped
  typo, a mapped invariant).

## [0.37.0] - 2026-10-21

Keys can be checked before a program spends anything, and the gate history
can be read as a trend. Grammar unchanged.

### Added
- `ailang setup --test [openai|anthropic|google|all]`: one minimal real call
  per configured key (`max_tokens: 16`), reporting latency, tokens, cost and
  the reply - or the exact HTTP error. Exit 1 when any key fails, so it can
  gate a deployment.
- `ailang review --trend`: gate pass rate grouped by day, in text and in
  `--json` (`trend` array), so a regression after a prompt change is
  visible at a glance.

### Fixed
- An HTTP failure from any provider surfaced as a raw `HTTPError` traceback.
  Both paths (the OpenAI-compatible one and `_post_json`) now raise
  `HTTP 401 from the provider: …` with the response body, which is what a
  wrong key actually produces.

### Changed
- Selftest grew from 225 to 228 checks (a live probe against a stub, a 401
  probe, and the trend view).

## [0.36.0] - 2026-10-21

Download, paste a key, run for real. Keys now have a home, and the three
named providers speak HTTP directly - `lark` is still the only dependency.
Grammar unchanged.

### Added
- `ailang setup`: stores keys and endpoints in `~/.ailang/config.json`
  (`--openai`, `--anthropic`, `--google`, `--endpoint URL [--key K]`,
  `--clear NAME`, `--json`). Keys are shown masked; `doctor` reports which
  provider is ready.
- Resolution order: a pasted key wins, the environment variable
  (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`) is the fallback.
  `AILANG_CONFIG` points the settings path somewhere else, which is how the
  tests stay isolated.

### Changed
- `OpenAIProvider` is now an OpenAI-compatible endpoint with a key: base URL
  and credentials come from the settings file, streaming included.
- `AnthropicProvider` and `GoogleProvider` no longer import vendor SDKs; they
  post JSON over `urllib` and parse `usage`/`usageMetadata`, so a fresh
  install needs nothing but `lark`.
- A missing key is a clean error that names `ailang setup`.
- Selftest grew from 221 to 225 checks (settings round-trip, config writing,
  all three dialects against local stubs, missing-key messages).

## [0.35.0] - 2026-10-21

Release candidate: the repository now checks its own consistency, and the
README says out loud what has not been verified. Grammar unchanged.

### Added
- README "Status" section: live providers, CI/installation and the real
  terminal are named as unverified, with the exact reason, so nobody is
  surprised after cloning.

### Changed
- Three release guards in the selftest:
  `pyproject.toml` and `__version__` must agree, `CHANGELOG.md` must carry a
  section for the running version, and `main(["--version"])` must answer
  through the console entry point.
- A run fuzzer: twelve generated programs must finish in mock mode within
  15 seconds, so a future change cannot introduce a hang.
- Selftest grew from 218 to 221 checks.

## [0.34.0] - 2026-10-21

The language can now explain itself: one catalogue of every diagnostic, a
command that prints it, and two fuzzers that keep the compiler and the
reports from ever crashing on odd input. Grammar unchanged.

### Added
- `ailang codes [--json]`: every `AIL` code with its severity and meaning,
  from a single table (`ERROR_CODES`) that the source and the README are
  checked against - a code cannot be invented or forgotten.
- `doctor` reports how many codes are documented.

### Changed
- Analysis fuzz: 60 generated programs (schemas with `where`, gates,
  `policy`, `until`/`if`/`match`/`fork`/`merge`/escalation) must never crash
  `analyze`, `collect_facts`, `flow_report` or `payroll_report`, and every
  diagnostic they raise must exist in the catalogue.
- README lost its last undocumented code (`AIL021`).
- Selftest grew from 216 to 218 checks.

## [0.33.1] - 2026-10-21

The HTTP path is no longer a promise: it is executed against a local stub
server on every selftest run. Grammar unchanged.

### Fixed
- An OpenAI-compatible `endpoint:` recorded `cost_usd = 0` while `payroll`
  predicted a price for the same model, so the ledger and the estimate
  disagreed. Costs now come from `PRICE_TABLE` for a priced model (and stay
  0 for an unknown one - the language still does not invent money).

### Changed
- Three integration checks talk to a real local HTTP server: a JSON call
  (headers, body, usage, `$0.00015` for 20 in / 10 out of gpt-4o), an SSE
  stream (deltas assembled, usage collected), and the price table itself.
- `fmt` is now fuzzed: a deterministic generator builds 120 mixed programs
  (schemas with `where`, gates, policy, until/if/match/fork/merge/
  escalation) and every program that parses must survive
  `parse(fmt(x)) == parse(x)`, stay idempotent and lose no comments.
- Selftest grew from 212 to 216 checks.

## [0.33.0] - 2026-10-21

First five minutes: `ailang init` writes a project that runs before you read
a manual, and a library schema can finally be used as a field type. Grammar
unchanged: 3445 bytes, `75b9a04d8fb4fa77`.

### Added
- `ailang init [PATH] [--force]`: a runnable starter project - `main.ai`
  (schema with a `where` limit, two named gates, a `policy`, two agents on a
  flow), `lib.ai` (a schema imported with `use`), `tests/smoke.ai`,
  a README and a `.gitignore`. The result is canonical (`fmt . --check` is
  clean) and `audit . --strict` says READY on the first try.
- `init` refuses a non-empty folder unless `--force` is given.

### Fixed
- A schema declared in a library could not be used as a field type:
  `use "lib.ai"` + `schema Brief { note: Note }` reported `AIL005: unknown
  schema 'Note'`. Field types now count the imported schemas too.
- The `check` cache is guarded by a test: changing the analyser revision
  always produces a different key, so a fixed analyser cannot serve an old
  diagnostic (this bit us twice).
- `salary:` is covered across two runs of one interpreter: the second paid
  call exceeds the cap and raises `AIL113` with the exact figures.

### Changed
- Selftest grew from 207 to 212 checks.

## [0.32.1] - 2026-10-21

Five defects found by an independent review of 0.32.0, plus one cache
lesson. No grammar change: still 3445 bytes, `75b9a04d8fb4fa77`.

### Fixed
- `if sum(cart.prices) <= 100 { … }` crashed the compiler with a Python
  repr leak (`unknown name 'Measure(kind='sum'…`) and exited 2. Measures are
  now resolved by `if`, including dotted paths.
- A nested invariant (`schema Outer { inner: Item where inner.score >= 0 }`)
  was rejected as `compares 'inner' (schema) with int`; the check now looks
  at the head field and lets the leaf comparison through.
- `policy { timeout: "soon" }` produced a raw `ValueError` traceback in
  `check`, `audit`, `payroll` and `contract`. Defaults are type-checked now:
  `AIL043: policy: 'timeout' needs a number`, with the caret.
- `review --until 2026-10-21` dropped that whole day (the date meant
  midnight). A bare date now covers the day, so `--min-rate` cannot be
  bypassed by a boundary.
- `review --since yesterday` raised an uncaught error; it is now a clean
  `cannot read the date` message with exit 1.
- `AIL037` silently skipped a measure whose target matched a schema or an
  agent name; a measure always points at a field, so the check is
  unconditional.

### Changed
- Analyzer revision 6: the `check` cache invalidates, so a fixed analyser
  never serves the old diagnostic.
- Selftest grew from 202 to 207 checks.

## [0.32.0] - 2026-10-21

Measurement and one contract for the whole file: criteria can size and sum
collections, invariants reach into nested records, and a `policy` block sets
the defaults every agent signs. Grammar 3297 bytes, `7d3e8f43cd83af5f`.

### Added
- `policy { … }` - file-level defaults for `model`, `endpoint`, `env`,
  `reasoning`, `budget`, `salary`, `judge`, `timeout`, `casts`, `window`,
  `price_in`, `price_out`. An agent's own field wins; identity fields
  (`kind`, `in`, `out`, `tools`, `run`) are refused with `AIL043`. The block
  is applied before anything runs, so `check`, `contract`, `payroll` and
  `audit` all see the merged view.
- Aggregates in any criterion, `if` and `where`: `len`, `sum`, `min`, `max`,
  `avg`, each type-checked (`AIL037`/`AIL042`) and compared with a number.
- Nested paths in invariants: `schema Outer { inner: Inner where
  inner.score >= 0 }`.
- `ailang review --since DATE --until DATE`: gate pass rates over a window.
- `ailang grammar --doc`: the inventory of rules, keywords and terminals.

### Fixed
- `max(...)` parses as a measure while `until … max N` keeps working.

### Changed
- Selftest grew from 192 to 202 checks.

## [0.31.0] - 2026-10-21

Standards you can measure: sizes are comparable, `until` stops paying for
models it does not need, and a journal now says how often the gates held.
Grammar moved again - 3251 bytes, fingerprint `da6788a147f1f21b`.

### Added
- `len(field)` in gate criteria, `if` conditions and `where` clauses:
  `gate has_tags = len(tags) > 0`, `schema Post { title: str where
  len(title) > 3 }`. It is type-checked (`AIL037`, `AIL042`) and only
  accepts a string or a list.
- `ailang review JOURNAL... [--json] [--min-rate R]`: gate pass rates from
  journals. Journals now record who spoke (`note`: `human`, `gate`,
  `until`, `match`, `replay:…`), so `review` counts audits only. A floor
  (`--min-rate 0.9`) turns a drifting gate into a non-zero exit for CI.
- `docs: the stated check count is real`: the selftest compares README and
  CONTRIBUTING with the actual number of checks, so the counters can no
  longer drift.

### Fixed
- `until` asked a model to judge a deterministic criterion; it now evaluates
  it locally and only audits semantic ones. A failing loop reports the
  comparison (`last: v=0 failed >= 9`), not just the attempt count.
- A failing gate names the resolved criterion (`gate 'len(tags) > 0'`) even
  when it was used through a declared name.

### Changed
- Selftest grew from 184 to 192 checks.

## [0.30.0] - 2026-10-21

Contracts with teeth: standards become reusable, salaries cover a whole run,
and a schema can state hard limits on its own data. The grammar moved for
the first time since 0.20.0 - 3164 bytes, fingerprint `b9787a4879191f4e`.

### Added
- Named gates: `gate has_topic = topic != ""` declares a reusable
  criterion, used anywhere a criterion is accepted (`qa? has_topic`,
  `until qa? has_topic max 3`). Gates compose (`gate both = priced and
  scored`), are type-checked like inline criteria (`AIL037`), and report
  `unknown gate` (`AIL038`) or a duplicate name (`AIL039`). `contract`
  prints `has_topic: topic != ""`.
- `salary:` on an agent caps what the whole run may spend on it, next to
  the per-call `budget:`. Exceeding it raises `AIL113` naming the agent, the
  salary and the spend. `AIL041` warns when the salary cannot cover the
  estimated number of calls, and `payroll`/`contract` report the figure.
- Field invariants: `schema Price { amount: float where amount >= 0 }`.
  A `where` clause is checked at every cast, patch and `in:` validation;
  violations raise an artifact error with `clause where`. Statically,
  `AIL042` catches an unknown field or a literal of the wrong type, and
  `fmt` keeps the clause.
- Signed literals: `-5` and `-0.5` are literals, so `patch x { amount: -5 }`
  and `gate ok = v > -1` parse.

### Changed
- `examples/standards.ai`: one page of standards applied twice - invariants,
  three named gates, two salaries - runs offline for $0.
- Selftest grew from 168 to 184 checks.

## [0.29.1] - 2026-10-21

Final offline patch before the repository freeze. No language change: the
grammar stays at 2967 bytes, fingerprint `2d3a42dc6bc648db`.

### Fixed
- `tests/*.ai` were not part of the formatting contract: `ailang fmt tests
  --write` reformatted both suites, and the CI step plus the selftest
  round-trip now cover `tests/` as well, so `ailang fmt . --check` is green
  for the whole repository.

### Changed
- `RELEASE.md` speaks about 0.29.1 everywhere (commit message, tag, release
  title, sdist name), lists five examples instead of four, and its
  verification block now runs `fmt . --check` and `audit . --strict` too.
- `CONTRIBUTING.md` documents that `corpus/schemas.ai`, `race.ai` and
  `tools.ai` were restored by behaviour after the originals were lost, and
  that they are now canonical.
- `.gitignore` and `.gitattributes` are ready for the first commit:
  `_archive/`, `*.bak*`, `fix*.py`, `result.txt`, caches, `dist/` and
  `build/` are ignored; text files are normalised to LF.
- `ailang fmt` now skips hidden and underscored folders when it walks a
  directory, so `fmt .` cannot reformat `_archive/` or `.zwork/`.
- `RELEASE.md` versions are synchronised with this release (commit
  message, tag, release title and sdist name all say 0.29.1).
- Selftest grew from 167 to 168 checks.

## [0.29.0] - 2026-10-29

### Fixed
- `ailang flow` crashed with `NameError: stats` whenever the text report had
  to name an attention sink (introduced when the report was refactored in
  0.22.0). `--json` was unaffected, which is why CI never saw it.

### Changed
- Hardening: empty programs, comment-only files, blank files, a lone BOM
  and CRLF line endings are covered by tests across `check`, `audit`,
  `flow`, `payroll` and `contract`; every command exits 0 without a
  traceback.
- Selftest grew from 164 to 167 checks.

## [0.28.0] - 2026-10-28

### Added
- Compiled images are first-class inputs for the governance commands:
  `ailang flow`, `payroll`, `contract` and `audit` read a `.ailc` the same
  way `run` does. `audit build.ailc` reports `syntax image 14 statements`
  and the usual sections; an image built for another grammar is `STALE`
  and blocks the verdict with `AIL120`.
- `audit --json` gained `kind` (`source` or `image`) and `built_by`.

### Changed
- Selftest grew from 161 to 164 checks.

## [0.27.0] - 2026-10-27

### Added
- `ailang audit` accepts directories: `audit examples/` walks them
  recursively for `*.ai` and skips hidden or underscored folders, so a whole
  repository is one command (13 programs, 35 agents in this repo). Missing
  paths keep their place in the report instead of being reordered, and an
  empty directory is a clear error.

### Changed
- Selftest grew from 158 to 161 checks.

## [0.26.0] - 2026-10-26

### Added
- `ailang audit FILE...` accepts several programs and audits them as one
  organization: per-program blocks, a `totals` line (programs, agents,
  errors, ~tokens, worst-case dollars) and a single verdict. A broken or
  missing program blocks the batch but does not hide the others.
- `audit --json` gained `programs[]` and `totals`; with one file it keeps
  the flat keys it always had, so existing callers do not change.

### Changed
- Selftest grew from 154 to 158 checks.

## [0.25.0] - 2026-10-25

### Fixed
- `check` ignored `use "*.ai"` libraries entirely: an error inside a
  library was never reported. It now analyses the file and every library it
  imports, each against its own source text, and names the file in the
  diagnostic (`error[AIL004]: … (lib.ai)`).
- The analysis cache was keyed by the root file only, so editing a library
  served stale results (`cache: hit` after a breaking change). The key now
  covers the whole source closure - root plus libraries.

### Changed
- `Diagnostic` carries the file it came from; `check --json` reports the
  `libraries` it analysed, on a hit as well as a miss.
- `audit` uses the same per-file analysis, so its diagnostics also point at
  the right file.
- Analyzer revision bumped to 5, so old cache entries miss.
- Selftest grew from 151 to 154 checks.

## [0.24.0] - 2026-10-24

### Added
- `ailang run FILE --journal out.jsonl --embed-source`: the journal header
  now carries the program text (`$journal: 3`), so `ailang replay out.jsonl`
  reproduces the run with no program file - useful for archives, bug
  reports and CI artefacts. Journals without the flag stay at `$journal: 2`
  and keep pointing at the file; v1 journals keep working.

### Changed
- The replay timeline reports where the program came from (`source
  embedded (N bytes)` or the path).
- Selftest grew from 148 to 151 checks.

## [0.23.0] - 2026-10-23

### Fixed
- A variable holding a produced artifact (`digest = draft >> analyst`) was
  counted as a second call of the producer. `flow` and `payroll` now treat
  it as data: the producer is called once, and the consumer's input is the
  example size of the producer's `out:` schema. The report for
  `examples/org_review.ai` dropped from ~469 to ~185 tokens and from 2 to 1
  calls per agent - the numbers are now honest.
- `until … max N` bodies were billed once. Loops now multiply the call
  count and the wage bill by `N` (nested loops multiply), so a loop is no
  longer cheap on paper and expensive in reality.

### Changed
- `payroll` prints a `calls` column next to `att`; its total is per agent
  call count.
- The README documents that `if` branches are summed (an upper bound),
  while loops are exact.
- Selftest grew from 145 to 148 checks.

## [0.22.0] - 2026-10-22

### Added
- `ailang audit FILE [--budget X] [--json] [--strict]`: one verdict for the
  whole program - syntax, types, attention, payroll and contracts in a
  single report, ending in `READY` or `BLOCKED by …`. Errors block; with
  `--strict`, warnings, attention overflow and budget overruns block too.
- Every `--json` output now carries `"schema": 1` (check, test, doctor,
  flow, payroll, contract, audit), so CI parsers can pin the shape.

### Fixed
- A gate criterion is attributed to the inspected post, not to the auditor,
  so `contract` no longer lists a supervisor's gate on the supervisor.

### Changed
- `flow` and `payroll` reports are reusable functions (`flow_report`,
  `payroll_report`), which `audit` composes.
- Selftest grew from 140 to 145 checks.

## [0.21.0] - 2026-10-21

### Added
- Structural conformance for qualifications: `patch Child { is_a: Parent }`
  now requires the child to supply every required parent field with a
  compatible type (nested schemas and optional parent fields included), or
  `AIL036` is raised. `is_a` became a promise the compiler checks instead
  of a label.
- Journal v2: every journal starts with a header (`$journal`, program path,
  source hash, statement count, grammar). `ailang replay run.jsonl` now
  finds the recorded program on its own; `--timeline` keeps the read-only
  step view, and journals without a header keep working.

- Typed gates: the criterion is checked against the producer's `out:`
  schema, including nested fields, so a misspelled field (`scroe`) or a
  nonsense comparison (`txt > 5` when `txt: str`) is `AIL037` at `check`
  time instead of a runtime surprise.

### Changed
- `stats` skips the journal header; `replay` reports a missing recorded
  program instead of failing.
- Selftest grew from 127 to 140 checks.

## [0.20.0] - 2026-10-20

### Fixed
- Identifiers that begin with a keyword now parse. Keywords carried
  priority 5 and the contextual lexer preferred priority over a longer
  match, so `used` lexed as `use` + `d`: `useful`, `printer`, `patched`,
  `iffy`, `matchx` and friends were unusable. Every keyword is now
  anchored with a word boundary (`_USE.5: /use(?!\w)/`), so `use` still
  wins over `NAME` while `useful` stays one identifier.

### Changed
- The grammar moved: 2967 bytes, fingerprint `2d3a42dc6bc648db` (was 2839
  bytes, `09c1ac9bffcb345f`). Images built by older versions are refused by
  design - rebuild them.
- Selftest grew from 125 to 127 checks (keyword prefixes, keywords still
  reserved).

## [0.19.0] - 2026-10-19

The Governance Release: the compiler now reasons about organizations of
agents - qualifications, attention, wages and contracts. The grammar is
unchanged (2839 bytes, fingerprint `09c1ac9bffcb345f`).

### Added
- Semantic types: `patch Schema { is_a: Parent }` declares a subtype at
  compile time, with multiple inheritance and diamond merging. Diagnostics:
  `AIL030` (cycle), `AIL031` (unknown parent), `AIL032` (a chain that
  breaks the hierarchy), `AIL100` (a schema patch carrying anything but
  `is_a`). The runtime accepts a qualified artifact where a supertype is
  expected and rejects strangers with the reason spelled out.
- `ailang flow FILE [--json] [--strict]`: attention report per agent with
  `~tok_in` (`max(words, chars/4)`), share of the program's input, context
  window from a registry or the agent's `window:` field, overflow warnings
  and an "attention sink" heuristic (>= 60% of the input).
- Fiscal semantics: module-level `PRICE_TABLE` (USD per 1M tokens, `approx`
  where approximate), agent-level `price_in:` / `price_out:` overrides, and
  a per-call `budget:` enforced by the runtime as `AIL111` naming the agent,
  the cap and the spend.
- `ailang payroll FILE [--budget X] [--json] [--strict]`: static wage bill
  per agent ($/call and worst case with casts), unknown engines print `?`
  and stay out of the total.
- `ailang contract FILE [--json]`: one labour-contract card per agent
  (role, engine, wage cap, deadline, supervisor, retries, gates, schemas
  and supertypes), plus `AIL033` (unknown judge) and `AIL034` (bad
  `timeout:` / `casts:`).
- `AIL035` warning: a wage cap below the estimated worst-case cost.
- Runtime messages name the agent and the clause (`gate`, `in`, `budget`,
  `timeout`) for `AIL101`-`AIL112`.
- `ailang replay journal.jsonl [--step] [--json] [--program FILE]`: the
  time machine. Without `--program` it prints the recorded timeline and its
  totals; with `--program` it re-runs that program answering every call
  from the recorded `(agent, input-digest)` pair - never calling a model.
- `flow`, `payroll` and `contract` splice `use "lib.ai"` first, so a
  report covers the whole organization and not just the importing file.
- Idle agents (declared, never called) are not billed in `payroll`, and
  a gate auditor consumes context only for semantic criteria.
- `GOVERNANCE.md`, `examples/org_review.ai`, comparison table in the
  README, CI steps for `flow`, `payroll`, `contract` and `replay`, `doctor`
  lines for subtypes, prices and windows.
- `check` cache key now includes an analyzer revision, so analysis changes
  never serve stale diagnostics.

### Changed
- Selftest grew from 92 to 125 checks.

## [0.18.0] - 2026-10-18

### Added
- Static-analysis cache: `check` stores diagnostics under
  `~/.ailang-cache/check/<key>.json`, keyed by file text + grammar
  fingerprint + version. A repeat run prints `cache: hit`; editing the file
  or changing the grammar produces a miss. `AILANG_CACHE_DIR` overrides the
  location.
- Plugin bundles: `plugins/` joins `agents/` as an auto-discovered folder.
  `run`, `check`, `build`, `test` and `kinds` load it without a `use`
  statement, and a compiled image records the plugin files that provided
  its kinds (restored on `run`).
- `--json` for `check`, `test` and `doctor`; `test --json` returns
  `{total, passed, failed, tests:[{name,status,error}]}`.
- `MANIFEST.in` so an sdist carries LICENSE, docs, examples, corpus,
  agents, plugins and tests.

### Changed
- The language is branded **Aligment Insight**; the package and command
  stay `ailang`. The shell prompt is `insight[mode]>`.
- Selftest grew to 92 checks.

## [0.17.0] - 2026-10-18

### Added
- `ailang doctor` reports the installation: ailang/python/lark versions,
  grammar fingerprint, kind and plugin counts, history file, journal env,
  mock/live mode and which data folders are present.
- `ailang grammar [--expect HASH]` prints the grammar fingerprint and fails
  with exit 1 when it does not match - a CI guard against silent drift.
- Runtime failures carry stable codes: `AIL101` assertion, `AIL102` gate,
  `AIL110` contract, `AIL111` budget, `AIL112` timeout, `AIL120` image,
  `AIL001` unknown name, `AIL020` library, `AIL100` fallback.

## [0.16.0] - 2026-10-18

### Added
- `ailang build` is incremental: the image stores a `source_hash` over the
  program, its linked libraries, the grammar and the version; an unchanged
  build prints `is up to date` and writes nothing (`--force` overrides).
- `ailang inspect FILE.ailc` prints the header (format, builder, grammar,
  source, statement count, libraries, hash) and whether the image is stale.
- `ailang run --offline` refuses to start when a static check finds an
  agent that would use the network.
- `ailang fmt` accepts directories and several paths; `--check` and
  `--write` work over a tree.

### Changed
- Every writer emits LF (`newline="\n"`), so formatting and building no
  longer introduce CRLF on Windows.
- The repository hygiene check now also rejects CRLF and blank-line runs
  outside code fences.

## [0.15.0] - 2026-10-18

### Added
- `assert <cond>` - a deterministic statement; a false condition fails with
  its source line, so programs can be tested without a framework.
- `ailang test [paths]` - runs every `.ai` file (default `tests/`) in mock
  mode, prints `ok`/`FAIL` per file and exits 1 on any failure.
- `tests/` with two suites (`language.ai`, `pipeline.ai`) covering schemas,
  patch, `if`, fork parts, gates and escalation.
- `build` links `use "*.ai"` libraries into the image, so an artifact runs
  without its sources; the image records the linked paths in `libraries`.

### Changed
- `check` now runs the static pass and returns 1 on diagnostics, in addition
  to the offline report.

## [0.14.0] - 2026-10-18

### Added
- Compiler: `ailang check` runs a static pass and prints Rust-style
  diagnostics with codes (`AIL001` unknown name, `AIL002` unknown kind,
  `AIL004` schema, `AIL005` field schema, `AIL006` tool, `AIL010` match
  branch, `AIL020` library), line, column, caret and a `help:` line.
- `ailang build FILE [-o OUT]` compiles to a JSON program image (`.ailc`)
  carrying the format tag, version, grammar fingerprint, statement lines
  and the serialised AST; `ailang run FILE.ailc` executes it directly.
- Images are stamped with the grammar fingerprint and refused on mismatch;
  foreign or malformed images produce a clear error.

## [0.13.1] - 2026-10-18

### Added
- Runtime errors carry the source line: `error: line 3: Unknown name: 'x'`
  (`parse_located` remembers where each statement starts).
- Source files saved with a UTF-8 BOM are read correctly (`_read_source`),
  so Windows editors no longer break a program with an invisible prefix.

## [0.13.0] - 2026-10-18

### Added
- Fork parts are addressable from the language: `crowd.one.v` inside gates
  and `if` (one resolver walks artifacts, nested dicts and fork parts).
- Nested field paths are no longer limited to one dot (`attr` accepts
  `NAME ("." NAME)+`).
- Shell history persists in `~/.ailang_history`; `:history` lists it.
- Emacs-style editing keys: `Ctrl-A/E`, `Ctrl-K/U`, `Ctrl-W`.

### Changed
- `KeySource` splits platform key decoding into testable class methods
  (`decode`, `ctrl`) so the mapping is covered without a real terminal.

## [0.12.0] - 2026-10-18

### Added
- `ailang shell` — the language's own terminal: a dependency-free line
  editor (arrows, Home/End, Delete, history, Tab completion, multi-line
  continuation), typed value rendering and `:commands` (`:help`, `:agents`,
  `:kinds`, `:graph`, `:stats`, `:fmt`, `:run`, `:clear`, `:quit`).
- Selftest checks 59-63 cover the editor, completion, the command layer,
  the multi-line path and the terminal loop itself (stub key source).

## [0.11.1] - 2026-10-18

### Added
- `repo: sources are English-only and BOM-free` selftest check (58 checks).
- `fmt` prints `print if … { … } else { … }` and `print match … { … }`
  blocks instead of refusing them.

### Fixed
- `build_exe.md` suffered a UTF-8/cp1251 mojibake in its em dashes;
  the file was rewritten clean and BOM-free.
- `pyproject.toml` carried a UTF-8 BOM, which trips some tooling.
- Guillemets in error and mock messages replaced with ASCII quotes.

## [0.11.0] - 2026-10-18

### Added
- `ailang fmt FILE [--check] [--write]` — AST-based canonical printer.
  `parse(fmt(x)) == parse(x)`; formatting is idempotent, which `--check`
  relies on. Leading comment blocks survive; dropped comments are reported.
- `Ref(str)` keeps the identifier/string distinction in agent and patch
  fields, so `out: Pitch` is not rewritten as `out: "Pitch"`.

## [0.10.0] - 2026-10-18

### Added (grammar)
- `if <cond> { … } else { … }` — deterministic branching as an expression;
  the value is the block's last statement. Conditions reuse the gate
  vocabulary but never call a model.
- `true` / `false` literals (usable in conditions and in `patch`).

## [0.9.0] - 2026-10-17

### Added (grammar)
- Gate comparisons and `patch` accept nested fields: `order.total >= 0`,
  `patch p { order.total: 42 }`.

### Added
- `use` resolves relative to the importing file first, then the cwd.
- `use` detects cycles and reports `use cycle: a.ai -> b.ai -> a.ai`.
- `check --offline --strict` exits 1 when networked agents are found.

## [0.8.0] - 2026-10-17

### Changed (grammar)
- `match` is an expression: `x = match r(t) { … }`, `print match …`.
  It is now a reserved word.
- Agent field values may be hyphenated: `kind: shout-loud`.

### Added
- `a || b` returns `ForkValue` — a `str` subclass carrying `.parts`.

## [0.7.3] - 2026-10-17

### Added
- `judge: NAME` agent field: an independent auditor for self-review gates.
- `stats` accepts several journals; `ask --stream`; `AILANG_JOURNAL` env.
- `run` reports language errors as `error: …` with exit code 2.
- REPL keeps reading while braces/quotes are open.

## [0.7.2] - 2026-10-16

### Added
- Strict verdict parsing (`**PASS**` tolerated, ambiguity fails closed).
- Usage accounting for streamed calls (`stream_options.include_usage`).
- `check --offline` follows `use` libraries; `stats --json`.
- Mock journals are labelled `mode: mock (dry-run)`.

## [0.7.1] - 2026-10-16

### Added
- Typed agents can audit (a verdict is text, not an artifact).
- `--stream` writes through `stream_out`; audits stay quiet.
- `race` breaks ties in declaration order.
- Conservative `check --offline` (unknown model = networked).

## [0.7.0] - 2026-10-15

### Added
- `examples/`: `pr_review.ai`, `triage.ai`, `digest.ai`, `maintainer.ai`.
- Escalation: `x >> a? "crit" ?? b` hands the ORIGINAL task to `b`.
- `stats`, `--stream`, `check --offline`.
- `pyproject.toml` with the `ailang` console script; `build_exe.md`.

## [0.6.0] - 2026-10-15

### Added (grammar unchanged)
- Kind registry: `kind: NAME`, built-in `chat` / `py` / `human` / `echo`.
- `chat` fields `endpoint:`, `env:`, `reasoning:`.
- Plugins: `use "agents/x.py"` with `register(api)`.
- `kinds` command; `corpus/` compatibility programs.

## [0.5.2] - 2026-10-14

### Fixed
- `_produce` dropped the model answer (agents echoed their input) — one line,
  six failing checks.
- File completed after truncation: kind helpers, `Interpreter`, `build_ir`,
  `DEMO`, `selftest`, CLI.

### Added
- 19-check selftest, demo, REPL, run/check/graph/ask commands.
