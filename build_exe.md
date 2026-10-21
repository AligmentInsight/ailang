# Building a single-file `ailang` executable

Optional: only if you want an `ailang` binary with no Python installed.

## PyInstaller (Windows / macOS / Linux)

```bash
pip install pyinstaller
pyinstaller --onefile --name ailang ailang.py
dist/ailang demo
dist/ailang selftest
```

`--onefile` bundles the interpreter and `lark` into one binary; startup is
a bit slower (~1 s) because the bundle is unpacked to a temp directory.

## Keeping the examples and corpus inside the bundle

The examples read local data files (`examples/sample.diff`,
`examples/issues.txt`, `examples/long_document.md`) and the corpus imports
`corpus/lib.ai`. To run `examples/*.ai` from the built binary, ship them
next to it, or bundle them:

```bash
pyinstaller --onefile --name ailang --add-data "examples:examples" --add-data "corpus:corpus" --add-data "agents:agents" ailang.py
```

On Windows use `;` instead of `:` in `--add-data`.

## Notes

- `ailang --version` must print `ailang 0.38.0`.
- `selftest` inside the binary still runs fully offline: mock providers only.
- Plugins (`use "x.py"`) exec Python source at runtime, which PyInstaller
  does not freeze - keep plugins as plain files next to the executable.
- A compiled image (`.ailc`) is a plain JSON file and travels on its own;
  it only needs a matching `ailang` binary.
- The single-file `ailang.py` remains the reference distribution; the binary
  is a convenience wrapper, not a second implementation.
