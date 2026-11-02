"""Compare KiCad's exported netlist with the intended connections.

Expected nets are listed as sets of REF.PIN. A net matches if KiCad has a net
containing exactly those pins among the refs we check (extra pins on shared
nets like GND/+5V that belong to other sheets are ignored).
"""
import os
import sys

from sexp import find, find_all, parse

HERE = os.path.dirname(os.path.abspath(__file__))
NET = os.path.join(HERE, "..", "..", "build", "pihat.net")

POWER = {
    "13V8_IN": "J6.1 F1.1",
    "VIN_F": "F1.2 D1.2",
    "VIN": "D1.1 D3.1 C16.1 C17.1 C18.1 C19.1 U3.2",
    "RT": "U3.4 R28.1",
    "COMP": "U3.6 R29.1 C29.1",
    "COMP_MID": "R29.2 C28.1",
    "BOOT": "U3.1 C20.1",
    "SW": "U3.8 C20.2 D2.1 L1.1",
    "+5V": "L1.2 C21.1 C22.1 C23.1 R25.1 U4.3 C24.1 R7.1",
    "FB_MID": "R25.2 R26.1",
    "FB": "U3.5 R26.2 R27.1",
    "+3.3VA": "U4.2 C25.1 C26.1 U5.1 U5.3",
    "+1V8": "U5.5 C27.1",
    "LED": "R7.2 LED1.2",
    "GND": "J6.2 D3.2 C16.2 C17.2 C18.2 C19.2 U3.7 U3.9 R28.2 C28.2 C29.2 D2.2 C21.2 C22.2 C23.2 R27.2 "
           "U4.1 C24.2 C25.2 C26.2 U5.2 C27.2 LED1.1",
}
NC = {"U3.3", "U5.4"}


def load():
    tree = parse(open(NET).read())
    nets = {}
    for n in find_all(find(tree, "nets"), "net"):
        name = str(find(n, "name")[1])
        nets[name] = {f"{find(x, 'ref')[1]}.{find(x, 'pin')[1]}" for x in find_all(n, "node")}
    return nets


def main(expected=POWER, nc=NC):
    nets = load()
    refs = {p.split(".")[0] for v in expected.values() for p in v.split()} | {p.split(".")[0] for p in nc}
    where = {}
    for name, pins in nets.items():
        for p in pins:
            where[p] = name
    bad = 0
    for label, members in expected.items():
        want = set(members.split())
        got_names = {where.get(p, "<missing>") for p in want}
        if len(got_names) != 1 or "<missing>" in got_names:
            print(f"FAIL {label}: pins spread over {sorted(got_names)}")
            for p in sorted(want):
                print("      ", p, "->", where.get(p, "<missing>"))
            bad += 1
            continue
        actual = {p for p in nets[got_names.pop()] if p.split(".")[0] in refs}
        if actual != want:
            print(f"FAIL {label}: extra {sorted(actual - want)} missing {sorted(want - actual)}")
            bad += 1
        else:
            print(f"ok   {label:9s} {len(want)} pins")
    for p in nc:
        n = where.get(p)
        if n and len(nets[n]) > 1:
            print(f"FAIL {p} should be unconnected but is on {n}")
            bad += 1
    print("RESULT:", "all nets match" if not bad else f"{bad} problem(s)")
    return bad


if __name__ == "__main__":
    sys.exit(main())
