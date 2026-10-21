# AI-lang plugins — bring your own kind

AI-lang ships four agent kinds: `chat`, `py`, `human`, `echo`. A plugin
adds a new kind **without touching the grammar**: a kind is a field value
(`kind: shout`), and the language already parses it.

## The contract

A plugin is a plain Python file with one function:

```python
def register(api):
    api.kind("shout", ShoutAgent)
```

`api.kind(name, cls)` registers a class. Rules:

* `name` must match `[a-z][a-z0-9-]*` (compatible with the `NAME` token, so
  `kind: shout` parses without grammar changes).
* Registering a name that already exists raises `AILangRuntimeError` —
  clashes are errors, never silent overwrites.
* `cls` must expose `.invoke(task, source)` and a `.last_insight` attribute.

## The agent protocol

The class is instantiated as `cls(fields, config)`:

* `fields` — a `dict` with every field written in the `Agent { … }` block
  (`role`, `model`, `temp`, `judge`, …), already resolved: `in`/`out` arrive as
  `Schema` objects, `tools` as `Tool` objects.
* `config` — the runtime `AIlangConfig` (dry_run, thresholds, budget).

Then the runtime calls:

```python
agent.invoke(task, source)   # -> the answer (str or artifact)
agent.last_insight           # -> must hold that same answer
```

`task` is whatever the previous step produced (a string, an artifact, …).
`source` names the upstream ("human", "text", an agent name, "gate").

## Complete example

`agents/hello_kind.py`:

```python
class ShoutAgent:
    def __init__(self, fields, config):
        self.name = fields.get("model", "shout")
        self.last_insight = None

    def invoke(self, task, source="human"):
        self.last_insight = str(task).upper()
        return self.last_insight


def register(api):
    api.kind("shout", ShoutAgent)
```

Use it:

```ai
use "agents/hello_kind.py"
loud = Agent { kind: shout  role: "loud" }
print "hello world" >> loud      # HELLO WORLD
```

## Loading

* `use "agents/hello_kind.py"` — explicit import inside a program.
* `ailang kinds` — auto-loads every `*.py` in `agents/` next to
  `ailang.py` (or next to the current directory) so the listing is
  complete.

Loading is idempotent: a plugin file is executed once per process, and a
second `use` is a no-op.

## Determinism and offline

Plugins are ordinary Python: keep them deterministic and network-free if
you want the whole program to stay offline. The `py` kind is the built-in
proof that an agent can be a pure function (`run: "module.func"`).

## The promise

**The grammar never changes for a new kind.** Any agent species you can
imagine is a field value plus a class; existing programs keep parsing and
keep their behaviour. The compatibility corpus (`corpus/`) is run by the
selftest before every release to prove it.
