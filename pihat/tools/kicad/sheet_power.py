"""Sheet: Power. 13.8 V input protection, TPS54360 5 V buck, TLV76133 3.3VA and TPS7A2018 1V8 rails."""
from schbuild import Sheet

R0603 = "Resistor_SMD:R_0603_1608Metric"
C0603 = "Capacitor_SMD:C_0603_1608Metric"
C0805 = "Capacitor_SMD:C_0805_2012Metric"
C1206 = "Capacitor_SMD:C_1206_3216Metric"


def chain(s, pts):
    for a, b in zip(pts, pts[1:]):
        s.wire(a, b)


def build(path, page):
    s = Sheet("Power", path, page)
    G = ("pwr", "GND")

    s.text("13.8 V INPUT PROTECTION", (17.78, 38.1), 2.0, bold=True)
    s.text("5 V BUCK  (back-powers the Pi via header pins 2 and 4)", (152.4, 38.1), 2.0, bold=True)

    # ---- input: J6 -> F1 -> D1 -> VIN
    y = 50.8
    s.symbol("Connector:Screw_Terminal_01x02", "J6", "13.8 V in", (25.4, y), mirror="y",
             footprint="TerminalBlock_Phoenix:TerminalBlock_Phoenix_PT-1,5-2-3.5-H_1x02_P3.50mm_Horizontal",
             lcsc="C474892", ref_at=(22.86, 45.72), val_at=(22.86, 43.18))
    s.symbol("Device:Polyfuse", "F1", "1.5A 33V", (49.53, y), rot=90, footprint="Fuse:Fuse_1812_4532Metric",
             lcsc="C18198333", ref_at=(49.53, 46.99), val_at=(49.53, 55.88))
    s.symbol("Device:D_Schottky", "D1", "SS56", (68.58, y), rot=180, footprint="Diode_SMD:D_SMA",
             lcsc="C65009", ref_at=(68.58, 46.99), val_at=(68.58, 55.88))
    chain(s, [s.p("J6.1"), (33.02, y), s.p("F1.1")])
    chain(s, [s.p("F1.2"), (55.88, y), s.p("D1.2")])
    s.label("13V8_IN", (33.02, y), (1, 0))
    s.label("VIN_F", (55.88, y), (1, 0))
    gn = (35.56, 55.88)
    chain(s, [s.p("J6.2"), (35.56, s.p("J6.2")[1]), gn])
    s.power("GND", gn)

    # shunts on VIN (15.24 mm pitch leaves room for values)
    s.symbol("Device:D_Zener", "D3", "SMBJ24A", (81.28, 58.42), rot=270, footprint="Diode_SMD:D_SMB",
             lcsc="C123819", ref_at=(87.63, 57.15), val_at=(87.63, 59.69), conn={"2": G})
    caps_in = [("C16", 96.52), ("C17", 111.76), ("C18", 127.0)]
    for ref, x in caps_in:
        s.symbol("Device:C", ref, "2.2uF 50V", (x, 58.42), footprint=C1206, lcsc="C50254", conn={"2": G})
    s.symbol("Device:C_Polarized", "C19", "100uF 50V", (142.24, 58.42),
             footprint="Capacitor_SMD:CP_Elec_8x10.5", lcsc="C72480", conn={"2": G})

    # ---- U3 TPS54360
    s.symbol("Regulator_Switching:TPS54360DDA", "U3", "TPS54360BDDAR", (170.18, 55.88), lcsc="C524806",
             ref_at=(170.18, 41.91), val_at=(170.18, 80.01), conn={"3": ("nc",)})
    shunt_x = [81.28, 96.52, 111.76, 127.0, 142.24]
    chain(s, [s.p("D1.1")] + [(x, y) for x in shunt_x] + [(149.86, y), s.p("U3.2")])
    for x in shunt_x:
        s.junction((x, y))
    s.wire((81.28, y), s.p("D3.1"))
    for ref, x in caps_in + [("C19", 142.24)]:
        s.wire((x, y), s.p(f"{ref}.1"))
    s.label("VIN", (149.86, y), (1, 0))
    s.flag((127.0, y))
    # U3 grounds joined to one symbol
    g7, g9 = s.p("U3.7"), s.p("U3.9")
    gj = (g9[0], g9[1] + 2.54)
    chain(s, [g7, (g7[0], gj[1]), gj])
    s.wire(g9, gj)
    s.junction(gj)
    s.power("GND", gj)

    # RT/CLK -> R28 -> GND
    rt = s.p("U3.4")
    s.symbol("Device:R", "R28", "150k", (154.94, 73.66), footprint=R0603, lcsc="C22807",
             fields_dir="left", conn={"2": G})
    chain(s, [rt, (154.94, rt[1]), s.p("R28.1")])

    # COMP / FB out to labels
    s.connect((s.p("U3.6"), (1, 0)), ("label", "COMP", 5.08))
    s.connect((s.p("U3.5"), (1, 0)), ("label", "FB", 5.08))

    # BOOT cap C20 between BOOT and SW
    boot, sw = s.p("U3.1"), s.p("U3.8")
    s.symbol("Device:C", "C20", "100nF", (190.5, 45.72), rot=90, footprint=C0603, lcsc="C14663",
             ref_at=(190.5, 41.91), val_at=(190.5, 49.53))
    c20l, c20r = sorted([s.p("C20.1"), s.p("C20.2")])
    swn = (196.85, sw[1])
    chain(s, [boot, (184.15, boot[1]), (184.15, 45.72), c20l])
    chain(s, [c20r, (196.85, 45.72), swn])
    # SW node -> D2 / L1
    s.symbol("Device:D_Schottky", "D2", "SS56", (203.2, 59.69), rot=270, footprint="Diode_SMD:D_SMA",
             lcsc="C65009", ref_at=(209.55, 58.42), val_at=(209.55, 60.96), conn={"2": G})
    s.symbol("Device:L", "L1", "6.8uH 5A", (214.63, sw[1]), rot=90, footprint="Inductor_SMD:L_Bourns_SRP7028A_7.3x6.6mm",
             lcsc="C167221", ref_at=(214.63, 52.07), val_at=(214.63, 59.69))
    l_in, l_out = sorted([s.p("L1.1"), s.p("L1.2")])
    chain(s, [sw, (186.69, sw[1]), swn, (203.2, sw[1]), l_in])
    s.label("SW", (186.69, sw[1]), (1, 0))
    s.junction(swn); s.junction((203.2, sw[1]))
    s.wire((203.2, sw[1]), s.p("D2.1"))

    # output caps + 5V
    cx = [227.33, 242.57, 257.81]
    for x, ref in zip(cx, ("C21", "C22", "C23")):
        s.symbol("Device:C", ref, "22uF 25V", (x, 59.69), footprint=C1206, lcsc="C12891", conn={"2": G})
    n5 = (222.25, sw[1])
    chain(s, [l_out, n5] + [(x, sw[1]) for x in cx])
    s.power("+5V", n5)
    s.flag((cx[2], sw[1]))
    for pt in (n5, (cx[0], sw[1]), (cx[1], sw[1])):
        s.junction(pt)

    # compensation (values TBD)
    s.text("COMPENSATION", (152.4, 93.98), 1.5, bold=True)
    s.text("values TBD: TI WEBENCH or", (152.4, 97.79))
    s.text("TPS54360 datasheet 8.2", (152.4, 100.33))
    s.symbol("Device:R", "R29", "TBD", (190.5, 99.06), footprint=R0603, conn={"1": ("label", "COMP")})
    s.symbol("Device:C", "C28", "TBD", (190.5, 107.95), footprint=C0603, conn={"2": G})
    s.wire(s.p("R29.2"), s.p("C28.1"))
    s.symbol("Device:C", "C29", "TBD", (205.74, 99.06), footprint=C0603, conn={"1": ("label", "COMP"), "2": G})

    # feedback divider
    s.text("FEEDBACK", (226.06, 86.36), 1.5, bold=True)
    s.symbol("Device:R", "R25", "51k", (231.14, 96.52), footprint=R0603, lcsc="C23196", conn={"1": ("pwr", "+5V")})
    s.symbol("Device:R", "R26", "2.2k", (231.14, 104.14), footprint=R0603, lcsc="C4190")
    s.symbol("Device:R", "R27", "10k", (231.14, 116.84), footprint=R0603, lcsc="C25804", conn={"2": G})
    fbn = (231.14, 110.49)
    chain(s, [s.p("R26.2"), fbn, s.p("R27.1")])
    s.wire(fbn, (238.76, fbn[1]))
    s.junction(fbn)
    s.label("FB", (238.76, fbn[1]), (1, 0))

    # ---- codec rails
    s.text("CODEC RAILS", (17.78, 127.0), 2.0, bold=True)
    y2 = 139.7
    s.symbol("Regulator_Linear:TLV76133DCY", "U4", "TLV76133DCYR", (60.96, y2), lcsc="C7527500",
             ref_at=(60.96, 129.54), val_at=(60.96, 132.08), conn={"1": G})
    s.symbol("Device:C", "C24", "1uF", (43.18, y2 + 3.81), footprint=C0603, lcsc="C15849", conn={"2": G})
    chain(s, [(35.56, y2), s.p("C24.1"), s.p("U4.3")])
    s.power("+5V", (35.56, y2)); s.junction(s.p("C24.1"))
    s.symbol("Regulator_Linear:TPS7A20xxxDBV", "U5", "TPS7A2018PDBVR", (111.76, y2 + 2.54), lcsc="C963430",
             ref_at=(111.76, 129.54), val_at=(111.76, 132.08), conn={"2": G, "4": ("nc",)})
    s.symbol("Device:C", "C25", "10uF", (73.66, y2 + 3.81), footprint=C0805, lcsc="C15850", conn={"2": G})
    s.symbol("Device:C", "C26", "1uF", (88.9, y2 + 3.81), footprint=C0603, lcsc="C15849", conn={"2": G})
    vo, u5in, u5en = s.p("U4.2"), s.p("U5.1"), s.p("U5.3")
    chain(s, [vo, (73.66, y2), (81.28, y2), (88.9, y2), (99.06, y2), u5in])
    for x in (73.66, 81.28, 88.9, 99.06):
        s.junction((x, y2))
    s.power("+3.3VA", (81.28, y2))
    chain(s, [u5en, (99.06, u5en[1]), (99.06, y2)])
    s.symbol("Device:C", "C27", "1uF", (129.54, y2 + 3.81), footprint=C0603, lcsc="C15849", conn={"2": G})
    chain(s, [s.p("U5.5"), (129.54, y2), (137.16, y2)])
    s.junction((129.54, y2))
    s.power("+1V8", (137.16, y2))

    # ---- power LED
    s.text("POWER LED", (172.72, 127.0), 2.0, bold=True)
    s.symbol("Device:R", "R7", "1k", (186.69, y2), rot=90, footprint=R0603, lcsc="C21190",
             ref_at=(186.69, 135.89), val_at=(186.69, 143.51))
    s.symbol("Device:LED", "LED1", "red", (199.39, y2), rot=180, footprint="LED_SMD:LED_0603_1608Metric",
             lcsc="C2286", ref_at=(199.39, 134.62), val_at=(199.39, 143.51))
    r7a, r7b = sorted([s.p("R7.1"), s.p("R7.2")])
    chain(s, [(177.8, y2), r7a]); s.power("+5V", (177.8, y2))
    s.wire(r7b, s.p("LED1.2"))
    chain(s, [s.p("LED1.1"), (208.28, y2)]); s.power("GND", (208.28, y2)); s.flag((208.28, y2))

    # ---- notes
    s.text("NOTES", (17.78, 162.56), 1.5, bold=True)
    for i, t in enumerate((
        "U3 EN is left open: internal pull-up = always on.",
        "R28 150k sets the switching frequency to about 640 kHz.",
        "Vout = 0.8 V x (1 + (R25 + R26) / R27) = 5.06 V.",
        "TPS54360 can't sink current, so no back-feed diode is needed (HAT design guide).",
        "+3.3VA is a quiet analog rail for the codec (AVDD, DRVDD). +1V8 feeds the codec core (DVDD).",
        "D3 is drawn with the Zener symbol: KiCad's convention for a unidirectional TVS.",
    )):
        s.text(t, (17.78, 166.37 + i * 3.81))
    return s
