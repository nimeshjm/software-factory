"""Glob matching with ``**`` semantics (zero or more path segments)."""

from __future__ import annotations

import re
from functools import lru_cache


@lru_cache(maxsize=256)
def glob_to_regex(glob: str) -> re.Pattern[str]:
    g = glob[2:] if glob.startswith("./") else glob
    i, n, out = 0, len(g), ["^"]
    while i < n:
        c = g[i]
        if c == "*":
            if g[i : i + 2] == "**":
                i += 2
                if i < n and g[i] == "/":
                    i += 1
                    out.append("(?:.*/)?")
                else:
                    out.append(".*")
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(c))
        i += 1
    out.append("$")
    return re.compile("".join(out))


def match_glob(path: str, glob: str) -> bool:
    return bool(glob_to_regex(glob).match(path[2:] if path.startswith("./") else path))
