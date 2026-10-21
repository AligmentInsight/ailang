# Contributing

Thanks for considering a contribution. AI-lang is one file plus a few
directories of data, so the workflow is deliberately small.

## Ground rules

1. **Everything ships offline.** Tests must pass with no network and no API
   keys. Mock providers are the default.
2. **English only in the repository.** Code, comments, docs, examples and
   commit messages are in English; selftest check 85 enforces it.
3. **The grammar is a versioned contract: the fingerprint is locked in CI
   and in selftest; any change is a minor release with a CHANGELOG entry.**
   New behaviour arrives as field values (`kind:`, `judge:`, `endpoint:`),
   not as syntax, and a change is noted in `CHANGELOG.md` and `PROPOSALS.md`.
4. **Never weaken a check.** If a selftest check fails, fix the code it
   exercises - not the check.

## Setup

```bash
git clone <your-fork>
cd ailang
pip install lark
python ailang.py selftest      # 231/231, exit 0
```

## Everyday commands

```bash
python ailang.py selftest      # the whole suite
python ailang.py test          # .ai test suites
python ailang.py shell         # the bespoke terminal
python ailang.py fmt <file>    # canonical style (--check / --write)
python ailang.py check <file>  # static diagnostics
python ailang.py doctor        # environment report (--json)
python ailang.py build <file>  # compile to <file>.ailc
```

## Adding a selftest check

1. Write a `def t_something():` inside `selftest()` next to the others.
   Raise `AssertionError` on failure; the runner prints the name and error.
2. Register it in the `for name, fn in [...]` list at the bottom.
3. Keep it deterministic and offline: temp directories, mock providers,
   fixed sleeps only where a timeout is the subject.

## Adding an .ai test

Put a program under `tests/` and use `assert` for expectations:

```ai
schema N { v: int }
mk = Agent { model: "x"  role: "maker"  out: N }
n = "q" >> mk
patch n { v: 7 }
assert n.v == 7
```

`python ailang.py test` runs every file; a file passes when nothing raises.
Tests are formatted like everything else: `python ailang.py fmt tests --write`.

## The corpus

`corpus/` holds one program per construct family and is the regression net
for the grammar contract. Keep it at 6-8 programs.

Three files - `schemas.ai`, `race.ai` and `tools.ai` - had their originals
lost during a repository move. They were **restored by behaviour**: each was
rewritten from the features it has to exercise, and each is now the
canonical version. Their behaviour is pinned by the selftest, not by their
original text.

## Adding an agent kind

Kinds live in the registry; no grammar change is needed.

```python
class MyKind:
    def __init__(self, fields, config):
        self.name = "my-kind"
        self.last_insight = None

    def invoke(self, task, source="human"):
        self.last_insight = str(task)
        return self.last_insight


def register(api):
    api.kind("my-kind", MyKind)
```

Use it from a program with `use "agents/my_kind.py"`. See `PLUGINS.md` for
the contract, name rules and clash behaviour.

## Style

- Four-space indent, 79 columns, `snake_case`.
- Every public helper gets a one-line docstring that says *why*, not *what*.
- Errors are `AILangRuntimeError` subclasses with a message a user can act on.

## Reporting bugs

Include the program (`.ai`), the command, the version (`ailang --version`)
and the full selftest tail if it is relevant. A minimal reproduction beats a
long explanation.
