# Governance

Aligment Insight models an **organization of AI agents**. The metaphor is
not decoration: every concept below is enforced by the compiler or the
runtime, offline, without keys.

| Organization | Language |
|---|---|
| staff plan | the program itself |
| job description | `schema` |
| qualification / grade | `patch X { is_a: Y }` |
| wage | `budget:` |
| wage bill | `ailang payroll` |
| supervisor | `judge:` |
| quality control | gate `agent? "criterion"` |
| deadline | `timeout:` |
| personnel changes | `patch` on an artifact |
| accounting | `--journal` + `ailang stats` |
| time machine | `--replay` |

## The staff plan

A program is a staff plan: every `Agent` declared is a post that must be
filled before work starts.

```ai
researcher = Agent { model: "gpt-4o"  role: "Collect three facts." }
analyst    = Agent { model: "gpt-4o"  role: "Compress them." }
```

## Employment contract: `policy`

A `policy` block is what every agent in the file signs unless it negotiates
its own terms: the model, the wage, the deadline, the supervisor, the number
of retries.

```ai
policy { model: "gpt-4o"  salary: 0.50  budget: 0.05  judge: qa  timeout: 30 }

writer = Agent { role: "Draft a brief."  out: Brief }        # all five
critic = Agent { role: "Score it."  out: Score  timeout: 5 } # its own deadline
```

Identity fields (`kind`, `in`, `out`, `tools`, `run`) are refused with
`AIL043`: a shared default for them would be a bug factory, not a policy.

A policy belongs to its file: a library that declares one does not impose it
on the program that imports it.

## Job description: `schema`

A schema is what the post must deliver. The compiler knows it, so a
consumer's expectation is checked against a producer's promise.

```ai
schema Summary { body: str, date: str }
editor = Agent { model: "gpt-4o"  role: "Write a summary."  out: Summary }
```

## Qualification: `patch … { is_a: … }`

Grades are declared without new syntax: a `patch` whose target is a
declared schema adds a supertype instead of editing an artifact. Several
`is_a` lines mean several qualifications; diamonds are fine.

```ai
schema Text { body: str }
schema Dated { date: str }
schema Summary { body: str, date: str }
patch Summary { is_a: Text  is_a: Dated }

"seed" >> writer   # produces Summary
         >> reader # expects in: Text - accepted, Summary is a Text
```

A qualification is a promise the compiler checks: the child must supply
every required field of the parent with a compatible type (nested schemas
count, optional parent fields may be omitted). Otherwise:

```console
error[AIL036]: schema "Summary" declares is_a: Text but does not supply
field "tone": str
```

Breaking the hierarchy is a compile error, not a runtime surprise:

```console
error[AIL032]: agent "poet" produces Quote, but "analyst" expects Research:
Quote is not a subtype of Research
```

Unknown parents (`AIL031`) and cycles (`AIL030`) are rejected too. When a
schema or an expression is unknown, the compiler stays silent: a false
accusation is worse than a missed one.

## Wage: `budget` per call, `salary` per run

`budget` is a cap for **one call**, including the casts that repair a
broken contract. Exceeding it raises `AIL111` and the message names the
agent, the cap and the actual spend. `salary` caps what the **whole
run** may spend on that agent and raises `AIL113` when exhausted; the
compiler warns with `AIL041` when the salary cannot cover the
estimated number of calls.

```ai
editor = Agent { model: "gpt-4o"  role: "…"  budget: 0.05 }
critic = Agent { model: "gpt-4o"  role: "…"  salary: 0.10 }
```

## Wage bill: `ailang payroll`

Static estimate of one run, from the same numbers the runtime uses:

```console
$ ailang payroll examples/org_review.ai --budget 0.01
agent           engine                  ~in   ~out     $/call attempts    worst $
editor          mock                     24     23   0.000000        3   0.000000

total (worst case): $0.000000
budget $0.010000: OK (estimate $0.000000)
```

Prices live in one table (`PRICE_TABLE`), are marked *approx* where they
are approximate, and an unknown engine prints `?` and stays out of the
total. The language does not invent money.

If a wage cap cannot cover the estimated cost of one call, the compiler
says so before you spend anything:

```console
warning[AIL035]: agent "editor": contract cannot be satisfied: wage $0.000001 <
estimated cost $0.004965 (worst case, 3 attempts)
```

## Supervisor: `judge`

A `judge` is an independent auditor of an agent's self-review. It is a
plain field, so the grammar did not change.

```ai
editor = Agent { model: "gpt-4o"  role: "…"  judge: qa }
qa     = Agent { model: "gpt-4o"  role: "Audit the report." }
```

An unknown judge is `AIL033`.

## Quality control: gates

A gate is the inspection line: the artifact must satisfy the criterion
before it moves on. Deterministic criteria cost nothing; semantic ones are
judged by the auditor. The compiler types the criterion against the
producer's `out:` schema, so `qa? scroe >= 0` fails at `check`, not in
production.

```ai
audited = digest >> editor >> qa? verdict != "none"      # deterministic
polished = draft >> editor >> qa? "tone is professional" # semantic
```

A criterion can be declared once and reused - that is how a team gets a
standards page instead of copy-pasted strings:

```ai
gate has_topic = topic != ""
gate priced = budget >= 0
gate accepted = has_topic and priced

brief = "launch a coffee brand" >> writer >> critic? accepted
```

## Deadline: `timeout`

```ai
slow = Agent { model: "gpt-4o"  role: "…"  timeout: 30 }
```

A call that misses the deadline raises `AIL112` naming the agent and the
clause. `timeout:` must be positive and `casts:` at least 1, otherwise
`AIL034`.

## Personnel changes: `patch` on an artifact

`patch` on an *agent variable* edits a produced artifact, validated field
by field — the same keyword that declares qualifications on a schema.

```ai
report = "…" >> editor
patch report { verdict: "ship" }
```

## Accounting and the time machine

`--journal run.jsonl` records every call; `ailang stats` reports money and
latency; `--replay run.jsonl` re-runs the same program without calling a
model.

```console
$ ailang run examples/org_review.ai --journal run.jsonl
$ ailang stats run.jsonl                       # money and latency
$ ailang replay run.jsonl                      # the timeline, no model calls
$ ailang replay run.jsonl --step               # one line per step
$ ailang replay run.jsonl --program org_review.ai   # re-run on the journal
$ ailang replay run.jsonl --timeline           # just the recorded steps
```

The journal starts with a header naming the program and its hash, so
`ailang replay run.jsonl` finds the program by itself. Add `--embed-source`
to `run` and the header also carries the program text (`$journal: 3`): such
a journal replays with no files at all, which is what you archive.
`--timeline` and `--json` keep the read-only view.

`replay` never calls a model: without `--program` it reads the journal as a
timeline; with `--program` it re-runs that program and answers every call
from the recorded `(agent, input-digest)` pair.

## The report card

| command | question it answers |
|---|---|
| `ailang check FILE` | does the organization type-check? |
| `ailang flow FILE` | who gets the context, and does it fit the window? |
| `ailang payroll FILE` | what does one run cost, worst case? |
| `ailang contract FILE` | what are the terms of each post? |
| `ailang audit FILE` | is the organization ready, in one verdict? |
| `ailang review J...` | how did the gates hold over time? |
| `ailang replay J.jsonl` | what exactly happened, and can I re-run it? |

All four are static, offline and free.
