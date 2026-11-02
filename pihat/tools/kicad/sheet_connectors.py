"""Sheet: Radio connectors. RJ45 mic jacks (parallel), J4/J5 patch headers, screw terminals, JP1 ground link."""
from schbuild import Sheet

RJ45_FP = "Connector_RJ:RJ45_Amphenol_RJHSE5380"   # Amphenol RJHSE5380, LCSC C464586 (no magnetics)
HDR_FP = "Connector_PinHeader_2.54mm:PinHeader_1x08_P2.54mm_Vertical"
TERM_FP = "TerminalBlock_Phoenix:TerminalBlock_Phoenix_PT-1,5-2-3.5-H_1x02_P3.50mm_Horizontal"


def chain(s, pts):
    for a, b in zip(pts, pts[1:]):
        s.wire(a, b)


def build(path, page):
    s = Sheet("Radio connectors", path, page)
    s.text("MIC CONNECTORS  (wired pin for pin)", (17.78, 30.48), 2.0, bold=True)
    s.text("Shields to RGND", (17.78, 34.29))

    # J2 to the radio's mic socket, J3 for the original mic: every pin in parallel via RJ1..RJ8
    for ref, val, y in (("J2", "RJ45 to radio mic", 63.5), ("J3", "RJ45 original mic", 106.68)):
        s.symbol("Connector:8P8C_Shielded", ref, val, (33.02, y), footprint=RJ45_FP, lcsc="C464586",
                 ref_at=(33.02, y - 15.24), val_at=(45.72, y + 12.7), val_just="left",
                 conn={**{str(n): ("label", f"RJ{n}", 5.08) for n in range(1, 9)}, "SH": ("pwr", "RGND")})

    # J4: RJ45 pins on a header; J5: board functions. Patched per radio with jumper wires.
    s.text("PATCH HEADERS", (88.9, 30.48), 2.0, bold=True)
    s.symbol("Connector_Generic:Conn_01x08", "J4", "RJ45 pins", (101.6, 66.04), footprint=HDR_FP, lcsc="C492407",
             ref_at=(101.6, 53.34), val_at=(101.6, 80.01),
             conn={str(n): ("label", f"RJ{n}", 5.08) for n in range(1, 9)})
    funcs = {"1": "R_PTT", "2": "R_UP", "3": "R_DN", "4": "R_MIC"}
    s.symbol("Connector_Generic:Conn_01x08", "J5", "Functions", (139.7, 66.04), mirror="y", footprint=HDR_FP,
             lcsc="C492407", ref_at=(139.7, 53.34), val_at=(139.7, 80.01),
             conn={n: ("glabel", net, 5.08) for n, net in funcs.items()})
    # pins 5-8 all radio ground (for ME, E, HOOK ties)
    xs = s.p("J5.5")[0] + 5.08
    ys = [s.p(f"J5.{n}")[1] for n in range(5, 9)]
    for n, y in zip(range(5, 9), ys):
        s.wire(s.p(f"J5.{n}"), (xs, y))
    chain(s, [(xs, ys[0]), (xs, ys[1]), (xs, ys[2]), (xs, ys[3]), (xs, ys[3] + 2.54)])
    for y in ys[1:]:
        s.junction((xs, y))
    s.power("RGND", (xs, ys[3] + 2.54))
    for i, t in enumerate((
        "Jumper wires J4 -> J5, one set per radio.",
        "Example, Kenwood TK-880 mic jack (service manual):",
        "  J4.4 PTT -> J5.1     J4.6 MIC -> J5.4",
        "  J4.5 ME  -> J5.5     J4.3 E   -> J5.6",
        "Confirm RJ45 vs radio pin numbering with a",
        "straight patch lead and a meter first.",
    )):
        s.text(t, (88.9, 92.71 + i * 3.81))

    # screw terminals
    s.text("SCREW TERMINALS", (190.5, 30.48), 2.0, bold=True)
    terms = [
        ("J7", "Speaker in", 50.8, ("glabel", "R_SPP", 5.08), ("glabel", "R_SPN", 5.08)),
        ("J8", "Busy in", 71.12, ("glabel", "BUSY+", 5.08), ("glabel", "BUSY-", 5.08)),
        ("J9", "Accessory PTT", 91.44, ("glabel", "R_PTT", 5.08), None),
    ]
    for ref, val, y, c1, c2 in terms:
        s.symbol("Connector:Screw_Terminal_01x02", ref, val, (205.74, y), mirror="y", footprint=TERM_FP,
                 lcsc="C474892", ref_at=(203.2, y - 5.08), val_at=(203.2, y - 2.54), ref_just="right", val_just="right",
                 conn={"1": c1, **({"2": c2} if c2 else {})})
    p2 = s.p("J9.2")
    chain(s, [p2, (p2[0] + 2.54, p2[1]), (p2[0] + 2.54, p2[1] + 2.54)])
    s.power("RGND", (p2[0] + 2.54, p2[1] + 2.54))
    for i, t in enumerate((
        "J8 busy wiring:",
        "  active-high busy output -> B+, B- to radio GND",
        "  open-collector busy -> B+ to radio +V, B- to the output",
        "J9: accessory-port PTT, in parallel with J5.1",
        "J6 (13.8 V in) is on the Power sheet.",
    )):
        s.text(t, (190.5, 106.68 + i * 3.81))

    # ground link
    s.text("GROUND LINK", (17.78, 139.7), 2.0, bold=True)
    s.symbol("Jumper:SolderJumper_2_Open", "JP1", "RGND-GND", (55.88, 152.4),
             footprint="Jumper:SolderJumper-2_P1.3mm_Open_RoundedPad1.0x1.5mm",
             ref_at=(55.88, 147.32), val_at=(55.88, 157.48))
    a, b = s.p("JP1.1"), s.p("JP1.2")
    chain(s, [a, (a[0] - 5.08, a[1]), (a[0] - 5.08, a[1] + 2.54)])
    s.power("RGND", (a[0] - 5.08, a[1] + 2.54))
    chain(s, [b, (b[0] + 5.08, b[1]), (b[0] + 5.08, b[1] + 2.54)])
    s.power("GND", (b[0] + 5.08, b[1] + 2.54))
    s.text("Open by default: keeps radio ground off the Pi ground.", (73.66, 151.13))
    s.text("The radio joins the two through its own supply lead.", (73.66, 154.94))
    s.text("Close only for testing.", (73.66, 158.75))
    return s
