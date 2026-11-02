"""Compare a KiCad netlist export with intended connections (shared by both boards).

expected: {label: "REF.PIN REF.PIN ..."} (labels are for reporting; nets are matched by membership).
A net matches when KiCad has one net holding exactly those pins among the refs being checked.
"""
from sexp import find, find_all, parse


def load(path):
    tree = parse(open(path).read())
    nets = {}
    for n in find_all(find(tree, "nets"), "net"):
        name = str(find(n, "name")[1])
        nets[name] = {f"{find(x, 'ref')[1]}.{find(x, 'pin')[1]}" for x in find_all(n, "node")}
    return nets


def merge(*dicts):
    out = {}
    for d in dicts:
        for k, v in d.items():
            out[k] = (out[k] + " " + v) if k in out else v
    return out


def check(path, expected, nc=(), named=None, separate=()):
    """named: {net_name: pin} that must carry that exact name; separate: [(pin_a, pin_b)] that must be on different nets."""
    nets = load(path)
    refs = {p.split(".")[0] for v in expected.values() for p in v.split()} | {p.split(".")[0] for p in nc}
    where = {p: name for name, pins in nets.items() for p in pins}
    bad = 0
    for label, members in expected.items():
        want = set(members.split())
        got = {where.get(p, "<missing>") for p in want}
        if len(got) != 1 or "<missing>" in got:
            print(f"FAIL {label}: pins spread over {sorted(got)}")
            for p in sorted(want):
                print("      ", p, "->", where.get(p, "<missing>"))
            bad += 1
            continue
        actual = {p for p in nets[got.pop()] if p.split(".")[0] in refs}
        if actual != want:
            print(f"FAIL {label}: extra {sorted(actual - want)} missing {sorted(want - actual)}")
            bad += 1
        else:
            print(f"ok   {label:12s} {len(want)} pins")
    for p in nc:
        n = where.get(p)
        if n and len(nets[n]) > 1:
            print(f"FAIL {p} should be unconnected but is on {n}")
            bad += 1
    for name, pin in (named or {}).items():
        if where.get(pin) not in (name, "/" + name):
            print(f"FAIL net carrying {pin} is named {where.get(pin)!r}, expected {name!r}")
            bad += 1
    for a, b in separate:
        if where.get(a) and where.get(a) == where.get(b):
            print(f"FAIL {a} and {b} are on the same net ({where.get(a)})")
            bad += 1
    print("RESULT:", "all nets match" if not bad else f"{bad} problem(s)")
    return bad
