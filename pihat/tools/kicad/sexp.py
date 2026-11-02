"""Minimal S-expression reader/writer for KiCad files.

Atoms are kept as Python strings; quoted strings are wrapped in Q so they
round-trip with their quotes.
"""
import re


class Q(str):
    """A quoted string atom."""


_tok = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))', re.S)


def parse(text):
    stack, cur = [], []
    pos = 0
    n = len(text)
    while pos < n:
        m = _tok.match(text, pos)
        if not m:
            if text[pos:].strip() == "":
                break
            raise ValueError(f"bad token at {pos}: {text[pos:pos+40]!r}")
        pos = m.end()
        if m.group(1):
            stack.append(cur)
            cur = []
        elif m.group(2):
            done = cur
            cur = stack.pop()
            cur.append(done)
        elif m.group(3) is not None:
            cur.append(Q(m.group(3).replace('\\"', '"').replace("\\\\", "\\")))
        else:
            cur.append(m.group(4))
    assert not stack, "unbalanced parentheses"
    return cur[0] if len(cur) == 1 else cur


def dump(node, indent=0):
    """Serialise in KiCad's style: one list per line, tabs for nesting, short leaf lists inline."""
    t = "\t" * indent
    if not isinstance(node, list):
        return atom(node)
    if all(not isinstance(x, list) for x in node) or _inline(node):
        return t + "(" + " ".join(dump(x) if isinstance(x, list) else atom(x) for x in node) + ")"
    head = []
    i = 0
    while i < len(node) and not isinstance(node[i], list):
        head.append(atom(node[i]))
        i += 1
    out = t + "(" + " ".join(head)
    for x in node[i:]:
        out += "\n" + (dump(x, indent + 1) if isinstance(x, list) else "\t" * (indent + 1) + atom(x))
    return out + "\n" + t + ")"


def _inline(node):
    # (xy 1 2) pairs inside pts and short property-free lists stay on one line
    return node and node[0] in ("xy", "at", "size", "color", "offset", "start", "end", "mid", "center")


def atom(x):
    if isinstance(x, Q):
        return '"' + x.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
    if isinstance(x, float):
        s = f"{x:.4f}".rstrip("0").rstrip(".")
        return "0" if s in ("-0", "") else s
    return str(x)


def find(node, key):
    for x in node:
        if isinstance(x, list) and x and x[0] == key:
            return x
    return None


def find_all(node, key):
    return [x for x in node if isinstance(x, list) and x and x[0] == key]
