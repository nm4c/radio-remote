"""Sheet: Control. TCMT4100 quad opto: PTT, UP, DOWN out to the radio; BUSY in from the radio."""
from schbuild import Sheet

R0603 = "Resistor_SMD:R_0603_1608Metric"
C0603 = "Capacitor_SMD:C_0603_1608Metric"
FB0603 = "Inductor_SMD:L_0603_1608Metric"


def chain(s, pts):
    for a, b in zip(pts, pts[1:]):
        s.wire(a, b)


def build(path, page):
    s = Sheet("Control", path, page)
    G, RG = ("pwr", "GND"), ("pwr", "RGND")
    XO = 127.0                     # opto column (isolation barrier)

    s.text("OPTO-ISOLATED PTT / UP / DOWN / BUSY", (17.78, 30.48), 2.0, bold=True)
    s.text("Pi side (GND)", (96.52, 38.1), 1.5, bold=True)
    s.text("Radio side (RGND)", (137.16, 38.1), 1.5, bold=True)
    s.dashed((XO, 40.64), (XO, 149.86))

    rows = [52.07, 80.01, 107.95, 135.89]
    chans = [
        # (unit, gpio, R led, net, series R, ferrite, cap, title)
        (1, "GPIO17", "R1", "R_PTT", None, "FB1", "C4", "ch 1  PTT"),
        (2, "GPIO27", "R2", "R_UP", "R5", "FB2", "C5", "ch 2  UP"),
        (3, "GPIO22", "R3", "R_DN", "R6", "FB3", "C6", "ch 3  DOWN"),
    ]
    for (unit, gpio, rled, net, rser, fb, cap, title), yc in zip(chans, rows):
        ya, yk = yc - 2.54, yc + 2.54
        s.symbol("Isolator:TCMT4100", "U1", "TCMT4100", (XO, yc), unit=unit, lcsc="C2938669",
                 ref_at=(XO - 1.27, yc - 6.35), val_at=(XO + 1.27, yc + 7.62), ref_just="right", val_just="left")
        s.text(title, (81.28, yc - 7.62), 1.27, bold=True)
        # LED side: GPIO -> 470R -> A ; K -> GND
        s.symbol("Device:R", rled, "470", (101.6, ya), rot=90, footprint=R0603, lcsc="C23179",
                 ref_at=(101.6, ya - 3.81), val_at=(101.6, ya + 3.81))
        r1, r2 = sorted([s.p(f"{rled}.1"), s.p(f"{rled}.2")])
        s.wire((88.9, ya), r1)
        s.label(gpio, (88.9, ya), (-1, 0), glob=True)
        s.wire(r2, s.p(f"U1.{2 * unit - 1}"))
        k = s.p(f"U1.{2 * unit}")
        chain(s, [k, (114.3, yk), (114.3, yk + 2.54)])
        s.power("GND", (114.3, yk + 2.54))
        # transistor side: C -> [0R] -> ferrite -> node(100p to RGND) -> global label ; E -> RGND
        c = s.p(f"U1.{18 - 2 * unit}")
        e = s.p(f"U1.{17 - 2 * unit}")
        if rser:
            s.symbol("Device:R", rser, "0R", (144.78, ya), rot=90, footprint=R0603, lcsc="C21189",
                     ref_at=(144.78, ya - 3.81), val_at=(144.78, ya + 3.81))
            a, b = sorted([s.p(f"{rser}.1"), s.p(f"{rser}.2")])
            s.wire(c, a)
            start = b
        else:
            start = c
        s.symbol("Device:FerriteBead_Small", fb, "600R@100MHz", (156.21, ya), rot=90, footprint=FB0603,
                 lcsc="C1002", ref_at=(156.21, ya - 3.81), val_at=(156.21, ya + 3.81))
        f1, f2 = sorted([s.p(f"{fb}.1"), s.p(f"{fb}.2")])
        s.wire(start, f1)
        node = (166.37, ya)
        s.symbol("Device:C", cap, "100pF", (166.37, ya + 3.81), footprint=C0603, lcsc="C14858", conn={"2": RG})
        chain(s, [f2, node, (176.53, ya)])
        s.junction(node)
        s.label(net, (176.53, ya), (1, 0), glob=True)
        chain(s, [e, (139.7, yk), (139.7, yk + 2.54)])
        s.power("RGND", (139.7, yk + 2.54))

    # channel 4: BUSY in. LED on the radio side, transistor to GPIO23
    yc = rows[3]
    ya, yk = yc - 2.54, yc + 2.54
    s.symbol("Isolator:TCMT4100", "U1", "TCMT4100", (XO, yc), unit=4, lcsc="C2938669",
             ref_at=(XO - 1.27, yc - 6.35), val_at=(XO + 1.27, yc + 12.7), ref_just="left", val_just="right", mirror="y")  # mirror flips justification
    s.text("ch 4  BUSY (reversed: LED on the radio side)", (81.28, yc - 7.62), 1.27, bold=True)
    # mirrored: LED pins on the right (radio side), transistor on the left (Pi side)
    a4, k4 = s.p("U1.7"), s.p("U1.8")
    c4, e4 = s.p("U1.10"), s.p("U1.9")
    s.symbol("Device:R", "R4", "2.2k", (152.4, a4[1]), rot=90, footprint=R0603, lcsc="C4190",
             ref_at=(152.4, a4[1] - 3.81), val_at=(152.4, a4[1] + 3.81))
    r4a, r4b = sorted([s.p("R4.1"), s.p("R4.2")])
    dn = (142.24, a4[1])
    chain(s, [a4, dn, r4a])
    s.junction(dn)
    s.wire(r4b, (166.37, a4[1]))
    s.label("BUSY+", (166.37, a4[1]), (1, 0), glob=True)
    # D4 antiparallel across the LED: cathode to A4, anode to K4
    s.symbol("Device:D", "D4", "1N4148WS", (142.24, yk + 3.81), rot=270, footprint="Diode_SMD:D_SOD-323",
             lcsc="C2128", ref_at=(146.05, yk + 2.54), val_at=(146.05, yk + 5.08), ref_just="left", val_just="left")
    s.wire(dn, s.p("D4.1"))
    kb = (137.16, yk + 7.62)
    chain(s, [k4, (137.16, k4[1]), kb])
    chain(s, [kb, s.p("D4.2")])
    s.wire(s.p("D4.2"), (166.37, s.p("D4.2")[1]))
    s.junction(s.p("D4.2"))
    s.label("BUSY-", (166.37, s.p("D4.2")[1]), (1, 0), glob=True)
    # Pi side
    s.wire(c4, (106.68, c4[1]))
    s.label("GPIO23", (106.68, c4[1]), (-1, 0), glob=True)
    chain(s, [e4, (114.3, e4[1]), (114.3, e4[1] + 2.54)])
    s.power("GND", (114.3, e4[1] + 2.54))

    # radio ground arrives through the mic / accessory connectors
    s.power("RGND", (190.5, 120.65))
    s.flag((190.5, 120.65))
    s.text("RGND enters through J2 / J3 / J9", (195.58, 121.92))

    # PTT indicator LED
    s.text("PTT INDICATOR", (17.78, 160.02), 1.5, bold=True)
    yl = 170.18
    s.symbol("Device:R", "R8", "1k", (40.64, yl), rot=90, footprint=R0603, lcsc="C21190",
             ref_at=(40.64, yl - 3.81), val_at=(40.64, yl + 3.81))
    ra, rb = sorted([s.p("R8.1"), s.p("R8.2")])
    s.wire((30.48, yl), ra)
    s.label("GPIO17", (30.48, yl), (-1, 0), glob=True)
    s.symbol("Device:LED", "LED2", "red", (53.34, yl), rot=180, footprint="LED_SMD:LED_0603_1608Metric",
             lcsc="C2286", ref_at=(53.34, yl - 3.81), val_at=(53.34, yl + 3.81))
    s.wire(rb, s.p("LED2.2"))
    chain(s, [s.p("LED2.1"), (63.5, yl)])
    s.power("GND", (63.5, yl))

    # notes
    s.text("NOTES", (17.78, 45.72), 1.5, bold=True)
    for i, t in enumerate((
        "R5 / R6: 0R for separate UP/DN",
        "lines. For a shared ladder line,",
        "fit the values measured on the mic.",
        "",
        "Opto LED: (3.3 - 1.35 V) / 470R",
        "= 4 mA; >= 2 mA sink at 50% CTR.",
        "",
        "D4: reverse protection for the",
        "BUSY LED (J8 wired backwards).",
        "",
        "GPIO23 uses the Pi's pull-up",
        "(config busy_pull_up: true).",
        "",
        "RGND = radio ground. Keep it",
        ">= 2 mm from GND copper.",
    )):
        if t:
            s.text(t, (17.78, 49.53 + i * 3.81))
    return s
