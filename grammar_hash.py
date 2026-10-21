"""Hash the GRAMMAR constant of an ailang.py file (byte-exact)."""
import hashlib
import re
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "ailang.py"
src = open(path, encoding="utf-8").read()
m = re.search(r"GRAMMAR = r'''(.*?)'''\n", src, re.S)
if not m:
    print("GRAMMAR not found")
    sys.exit(1)
g = m.group(1)
print(f"{path}: GRAMMAR bytes={len(g.encode('utf-8'))} "
      f"sha256={hashlib.sha256(g.encode('utf-8')).hexdigest()}")
