"""Sheet: RF power. Second TPS54360 makes 4.2 V for the SA818S (datasheet typical supply), from VIN.

Separate from the Pi's 5 V so TX current steps (60 mA RX -> 750 mA TX) don't disturb the Pi or the codec,
and a 2 A ferrite + local caps keep switching ripple off the module (the datasheet's FAQ blames ripple
for short range).
"""
from schbuild import Sheet

R0603 = "Resistor_SMD:R_0603_1608Metric"
C0603 = "Capacitor_SMD:C_0603_1608Metric"
C1206 = "Capacitor_SMD:C_1206_3216Metric"


def chain(s, pts):
    for a, b in zip(pts, pts[1:]):
        s.wire(a, b)


def build(path, page):
    s = Sheet("RF power", path, page)
    G = ("pwr", "GND")
    s.text("4.2 V MODULE SUPPLY  (from VIN, separate from the Pi's 5 V)", (17.78, 38.1), 2.0, bold=True)
    y = 50.8
    # VIN in with local input caps
    xs = [55.88, 71.12]
    for ref, x in zip(("C31", "C32"), xs):
        s.symbol("Device:C", ref, "2.2uF 50V", (x, 58.42), footprint=C1206, lcsc="C50254", conn={"2": G})
    s.symbol("Regulator_Switching:TPS54360DDA", "U7", "TPS54360BDDAR", (101.6, 55.88), lcsc="C524806",
             ref_at=(101.6, 41.91), val_at=(101.6, 80.01), conn={"3": ("nc",)})
    chain(s, [(40.64, y)] + [(x, y) for x in xs] + [s.p("U7.2")])
    s.label("VIN", (40.64, y), (-1, 0), glob=True)
    for x in xs:
        s.junction((x, y))
        s.wire((x, y), s.p(f"{'C31' if x == xs[0] else 'C32'}.1"))
    g7, g9 = s.p("U7.7"), s.p("U7.9")
    gj = (g9[0], g9[1] + 2.54)
    chain(s, [g7, (g7[0], gj[1]), gj]); s.wire(g9, gj); s.junction(gj); s.power("GND", gj)
    rt = s.p("U7.4")
    s.symbol("Device:R", "R30", "150k", (86.36, 73.66), footprint=R0603, lcsc="C22807", fields_dir="left", conn={"2": G})
    chain(s, [rt, (86.36, rt[1]), s.p("R30.1")])
    s.connect((s.p("U7.6"), (1, 0)), ("label", "COMP_RF", 5.08))
    s.connect((s.p("U7.5"), (1, 0)), ("label", "FB_RF", 5.08))
    boot, sw = s.p("U7.1"), s.p("U7.8")
    s.symbol("Device:C", "C30", "100nF", (121.92, 45.72), rot=90, footprint=C0603, lcsc="C14663",
             ref_at=(121.92, 41.91), val_at=(121.92, 49.53))
    c30l, c30r = sorted([s.p("C30.1"), s.p("C30.2")])
    swn = (128.27, sw[1])
    chain(s, [boot, (115.57, boot[1]), (115.57, 45.72), c30l])
    chain(s, [c30r, (128.27, 45.72), swn])
    s.symbol("Device:D_Schottky", "D4", "SS56", (134.62, 59.69), rot=270, footprint="Diode_SMD:D_SMA",
             lcsc="C65009", ref_at=(137.795, 58.42), val_at=(137.795, 60.96), conn={"2": G})
    s.symbol("Device:L", "L2", "6.8uH 5A", (146.05, sw[1]), rot=90, footprint="Inductor_SMD:L_Bourns_SRP7028A_7.3x6.6mm",
             lcsc="C167221", ref_at=(146.05, 51.43), val_at=(146.05, 60.33))
    l_in, l_out = sorted([s.p("L2.1"), s.p("L2.2")])
    chain(s, [sw, (118.11, sw[1]), swn, (134.62, sw[1]), l_in])
    s.label("SW_RF", (118.11, sw[1]), (1, 0))
    s.junction(swn); s.junction((134.62, sw[1]))
    s.wire((134.62, sw[1]), s.p("D4.1"))
    cx = [157.48, 172.72, 187.96]
    for x, ref in zip(cx, ("C33", "C34", "C35")):
        s.symbol("Device:C", ref, "22uF 25V", (x, 59.69), footprint=C1206, lcsc="C12891", conn={"2": G})
    chain(s, [l_out] + [(x, sw[1]) for x in cx] + [(196.85, sw[1])])
    for x in cx[:-1]:
        s.junction((x, sw[1]))
    s.junction((cx[-1], sw[1]))
    # FB_TOP: the converter output, before the ferrite
    top = (cx[1], sw[1] - 5.08)
    s.wire((cx[1], sw[1]), top)
    s.label("FB_TOP", top, (0, -1))
    # ferrite into the module rail, local bulk after it
    s.symbol("Device:FerriteBead", "FB1", "220R 2A", (203.2, sw[1]), rot=90,
             footprint="Inductor_SMD:L_0805_2012Metric", lcsc="C85840", ref_at=(203.2, 51.43), val_at=(203.2, 49.53))
    f1, f2 = sorted([s.p("FB1.1"), s.p("FB1.2")])
    s.wire((196.85, sw[1]), f1)
    for x, ref, val, lcsc, fp in ((215.9, "C38", "22uF 25V", "C12891", C1206), (231.14, "C39", "22uF 25V", "C12891", C1206)):
        s.symbol("Device:C", ref, val, (x, 59.69), footprint=fp, lcsc=lcsc, conn={"2": G})
    chain(s, [f2, (215.9, sw[1]), (231.14, sw[1]), (243.84, sw[1])])
    s.junction((215.9, sw[1])); s.junction((231.14, sw[1]))
    s.power("+4V2_RF", (243.84, sw[1]))
    s.flag((231.14, sw[1]))
    s.text("FB1: BLM21PG221SN1D, 220 ohm @ 100 MHz, 2 A.", (196.85, 76.2))
    s.text("Place C38/C39 at the module's VBAT pad.", (196.85, 80.01))

    # feedback (before the ferrite) and compensation
    s.text("FEEDBACK  Vout = 0.8 V x (1 + R31/R32) = 4.20 V", (17.78, 93.98), 1.5, bold=True)
    s.symbol("Device:R", "R31", "51k", (30.48, 106.68), footprint=R0603, lcsc="C23196", conn={"1": ("label", "FB_TOP")})
    s.symbol("Device:R", "R32", "12k", (30.48, 119.38), footprint=R0603, lcsc="C22790", conn={"2": G})
    fbn = (30.48, 113.03)
    chain(s, [s.p("R31.2"), fbn, s.p("R32.1")])
    s.wire(fbn, (38.1, fbn[1])); s.junction(fbn)
    s.label("FB_RF", (38.1, fbn[1]), (1, 0))
    s.text("FB_TOP is the 4.2 V node before FB1 (regulate the converter, not the filtered rail).", (17.78, 132.08))

    s.text("COMPENSATION  (datasheet 8.2.2.11)", (93.98, 93.98), 1.5, bold=True)
    for i, t in enumerate((
        "Cout 3 x 22uF at 4.2 V = 45 uF, Iout 1 A",
        "fp = 1 A / (2 pi 4.2 V 45 uF) = 842 Hz",
        "fco = sqrt(fp x fsw / 2) = 16.5 kHz",
        "R33 = 2 pi fco Cout Vout / (gmps Vref gmea) = 5.8k -> 5.6k",
        "C36 = 1 / (2 pi R33 fp) = 34 nF -> 33 nF",
        "C37 = 1 / (pi R33 fsw) = 88 pF -> 100 pF",
    )):
        s.text(t, (93.98, 97.79 + i * 3.18))
    s.symbol("Device:R", "R33", "5.6k", (175.26, 101.6), footprint=R0603, lcsc="C23189", conn={"1": ("label", "COMP_RF")})
    s.symbol("Device:C", "C36", "33nF", (175.26, 110.49), footprint=C0603, lcsc="C21117", conn={"2": G})
    s.wire(s.p("R33.2"), s.p("C36.1"))
    s.symbol("Device:C", "C37", "100pF", (190.5, 101.6), footprint=C0603, lcsc="C14858", conn={"1": ("label", "COMP_RF"), "2": G})
    return s
