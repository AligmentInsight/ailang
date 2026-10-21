# Release guide - Aligment Insight (`ailang`)

A step-by-step for publishing this repository. Everything here is done by
hand, once, when the network is available. Nothing in the project publishes
itself.

The commands are PowerShell. Adjust the folder if yours differs:

```powershell
cd "C:\path\to\ailang"
```

---

## 1. Before you start

- Install Git: <https://git-scm.com/download/win> (accept the defaults).
- Create a GitHub account and sign in.
- Have a PyPI account ready if you plan to publish the package.

Check Git:

```powershell
git --version
```

If the command is unknown, reopen PowerShell after installing.

---

## 2. Create the repository on GitHub

1. Open <https://github.com/new>.
2. Repository name: `ailang`.
3. Visibility: **Public**.
4. **Do not** add a README, .gitignore or license - the project already has
   them; adding them here causes a merge conflict on the first push.
5. Click **Create repository**.

GitHub shows a page with commands. Keep it open.

---

## 3. First commit and push

```powershell
cd "C:\path\to\ailang"

git init
git config user.name  "Your Name"
git config user.email "you@example.com"
git config core.autocrlf false       # keep the LF rules from .gitattributes

git add .
git status                            # check: no _archive/, no *.bak, no *.ailc
git commit -m "Aligment Insight 0.38.0: language, governance, tests"

git branch -M main
git remote add origin https://github.com/<your-account>/ailang.git
git push -u origin main
```

`git status` must not list `_archive/`, `ailang.py.bak*`, `result.txt`, or
any `*.ailc`. They are ignored on purpose.

---

## 4. Tag the release

```powershell
git tag -a v0.38.0 -m "Aligment Insight 0.38.0"
git push origin v0.38.0
```

Then on GitHub: **Releases -> Draft a new release**, choose tag `v0.38.0`,
title `Aligment Insight 0.38.0`, and paste the `0.38.0` section of
`CHANGELOG.md` as the description. Publish.

---

## 5. Verify the published state

In a fresh folder (to prove the repo is self-contained):

```powershell
cd $env:TEMP
git clone https://github.com/<your-account>/ailang.git ailang-check
cd ailang-check
python -m pip install lark
python ailang.py grammar --expect 75b9a04d8fb4fa77
python ailang.py selftest
python ailang.py test
python ailang.py fmt . --check
python ailang.py audit . --strict
```

All six commands must succeed. The last two are the ones that catch a
repository which was committed half-formatted or with a stale image. If `selftest` fails here but passed locally,
the cause is almost always line endings - see Troubleshooting.

The workflow runs the same commands on Windows and Linux for Python
3.10, 3.12 and 3.13 (`.github/workflows/ci.yml`). The battery has been
run locally on Windows three times; the Linux half runs for the first
time on GitHub, which is what this section verifies.

---

## 6. Publishing to PyPI

Only after the GitHub release is green.

```powershell
python -m pip install --upgrade build twine
cd "C:\path\to\ailang"

python -m build                       # writes dist/ailang-0.38.0.tar.gz and .whl
twine check dist/*                    # metadata sanity check

twine upload dist/*                   # asks for the PyPI token
```

Then verify from a clean environment:

```powershell
python -m pip install ailang
ailang --version
ailang selftest
```

**If the name `ailang` is taken on PyPI**, change `project.name` in
`pyproject.toml` to one of the fallbacks and re-run `python -m build`:

- `ai-lang`
- `ailang-lang`

The command name (`ailang`) does not have to match the package name, but
keeping them equal is simpler.

---

## 7. Troubleshooting

**`selftest` fails on another machine with CRLF complaints.**
The repository is LF-only. Run once:

```powershell
git config core.autocrlf false
git rm --cached -r .
git reset --hard
```

**`python` opens the Microsoft Store.**
Use `py` instead of `python` everywhere, or install Python from
python.org and tick "Add to PATH".

**`lark` is missing inside CI.**
The workflow installs it explicitly; locally run
`python -m pip install lark`.

**`git push` asks for a password and rejects it.**
GitHub needs a token, not a password: Settings -> Developer settings ->
Personal access tokens -> Generate new token (classic), scope `repo`, then
paste it as the password.

---

## 8. What is published

```
ailang.py          the language, one file
agents/            plugin kinds shipped with the project
plugins/           drop-in plugin bundles (auto-discovered)
examples/          six runnable programs
corpus/            compatibility programs, one per construct family
GOVERNANCE.md      the organization metaphor, concept by concept
tests/             .ai test suites
```

Compiled artefacts (`*.ailc`) are build output: they are ignored by git and
shipped separately if needed.
