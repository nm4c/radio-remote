"""Sheet: Power. 13.8 V input protection, 5 V buck, TLV76133 3.3VA and TPS7A2018 1V8 rails.

Two 5 V bucks are available:
  lmr33640  TI LMR33640ADDA (Pi HAT): synchronous, internally compensated, 400 kHz. No catch
            diode, compensation network or frequency resistor; 36 V max, so a 22 V TVS.
  tps54360  TI TPS54360 (SA818S HAT): 60 V, external catch diode and type II compensation.
"""
from schbuild import Sheet
import sharedlib

R0603 = "Resistor_SMD:R_0603_1608Metric"
C0603 = "Capacitor_SMD:C_0603_1608Metric"
C0805 = "Capacitor_SMD:C_0805_2012Metric"
C1206 = "Capacitor_SMD:C_1206_3216Metric"


def chain(s, pts):
    for a, b in zip(pts, pts[1:]):
        s.wire(a, b)


def _vin_rail(s, y, shunt_x, caps_in, vin_pin, vin_global):
    """D1 -> shunts (D3, input caps) -> the buck's VIN pin."""
    chain(s, [s.p("D1.1")] + [(x, y) for x in shunt_x] + [(149.86, y), vin_pin])
    for x in shunt_x:
        s.junction((x, y))
    s.wire((81.28, y), s.p("D3.1"))
    for ref, x in caps_in + [("C19", 142.24)]:
        s.wire((x, y), s.p(f"{ref}.1"))
    s.label("VIN", (149.86, y), (1, 0), glob=vin_global)
    s.flag((127.0, y))


def _boot_cap(s, boot, sw):
    """C20 between BOOT and SW, drawn above the converter. Returns the SW node it joins."""
    s.symbol("Device:C", "C20", "100nF", (190.5, 45.72), rot=90, footprint=C0603, lcsc="C14663",
             ref_at=(190.5, 41.91), val_at=(190.5, 49.53))
    c20l, c20r = sorted([s.p("C20.1"), s.p("C20.2")])
    swn = (196.85, sw[1])
    chain(s, [boot, (184.15, boot[1]), (184.15, 45.72), c20l])
    chain(s, [c20r, (196.85, 45.72), swn])
    return swn


def _inductor(s, sw):
    # Changjiang FXL0630-6R8-M: KiCad has its exact footprint and 3D model
    s.symbol("Device:L", "L1", "6.8uH 5A", (214.63, sw[1]), rot=90, footprint="Inductor_SMD:L_Changjiang_FXL0630",
             lcsc="C167221", ref_at=(214.63, 52.07), val_at=(214.63, 59.69))
    return sorted([s.p("L1.1"), s.p("L1.2")])


def _lmr33640(s, y, shunt_x, caps_in, vin_global, G):
    """LMR33640ADDA: EN tied to VIN, PG unused, 1 uF on VCC, boot cap, SW straight to L1."""
    s.symbol("Regulator_Switching:LMR33640ADDA", "U3", "LMR33640ADDAR", (170.18, 55.88), lcsc="C2836966",
             footprint=sharedlib.fp("Package_SO:Texas_HSOP-8-1EP_3.9x4.9mm_P1.27mm_ThermalVias"),
             ref_at=(170.18, 41.91), val_at=(170.18, 74.93), conn={"4": ("nc",)})
    vin = s.p("U3.2")
    _vin_rail(s, y, shunt_x, caps_in, vin, vin_global)
    en = s.p("U3.3")
    ent = (157.48, vin[1])
    chain(s, [en, (157.48, en[1]), ent])
    s.junction(ent)
    # VCC: internal 5 V LDO, 1 uF to ground
    vcc = s.p("U3.6")
    s.symbol("Device:C", "C28", "1uF", (154.94, vcc[1] + 3.81), footprint=C0603, lcsc="C15849",
             fields_dir="left", conn={"2": G})
    chain(s, [vcc, s.p("C28.1")])
    # PGND (1) and the thermal pad (9) share a point on the symbol
    gnd = s.p("U3.1")
    chain(s, [gnd, (gnd[0], gnd[1] + 2.54)])
    s.power("GND", (gnd[0], gnd[1] + 2.54))
    s.connect((s.p("U3.5"), (1, 0)), ("label", "FB", 5.08))
    sw = s.p("U3.8")
    swn = _boot_cap(s, s.p("U3.7"), sw)
    l_in, l_out = _inductor(s, sw)
    chain(s, [sw, (186.69, sw[1]), swn, l_in])
    s.label("SW", (186.69, sw[1]), (1, 0))
    s.junction(swn)
    return sw, l_out


def _tps54360(s, y, shunt_x, caps_in, vin_global, G):
    """TPS54360: RT resistor, external compensation, catch diode D2."""
    s.symbol("Regulator_Switching:TPS54360DDA", "U3", "TPS54360BDDAR", (170.18, 55.88), lcsc="C524806",
             footprint=sharedlib.fp("Package_SO:TI_SO-PowerPAD-8_ThermalVias"),
             ref_at=(170.18, 41.91), val_at=(170.18, 80.01), conn={"3": ("nc",)})
    _vin_rail(s, y, shunt_x, caps_in, s.p("U3.2"), vin_global)
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

    sw = s.p("U3.8")
    swn = _boot_cap(s, s.p("U3.1"), sw)
    # SW node -> D2 / L1
    s.symbol("Device:D_Schottky", "D2", "SS56", (203.2, 59.69), rot=270, footprint="Diode_SMD:D_SMA",
             lcsc="C65009", ref_at=(209.55, 58.42), val_at=(209.55, 60.96), conn={"2": G})
    l_in, l_out = _inductor(s, sw)
    chain(s, [sw, (186.69, sw[1]), swn, (203.2, sw[1]), l_in])
    s.label("SW", (186.69, sw[1]), (1, 0))
    s.junction(swn); s.junction((203.2, sw[1]))
    s.wire((203.2, sw[1]), s.p("D2.1"))
    return sw, l_out


def _lmr33640_feedback(s, G):
    s.text("FEEDBACK", (226.06, 86.36), 1.5, bold=True)
    s.symbol("Device:R", "R25", "82k", (231.14, 96.52), footprint=R0603, lcsc="C23254", conn={"1": ("pwr", "+5V")})
    s.symbol("Device:R", "R26", "20k", (231.14, 106.68), footprint=R0603, lcsc="C4184", conn={"2": G})
    fbn = (231.14, 101.6)
    chain(s, [s.p("R25.2"), fbn, s.p("R26.1")])
    s.wire(fbn, (238.76, fbn[1]))
    s.junction(fbn)
    s.label("FB", (238.76, fbn[1]), (1, 0))


def _tps54360_comp_feedback(s, G):
    s.text("COMPENSATION  (TPS54360 datasheet 8.2.2.11)", (127.0, 93.98), 1.5, bold=True)
    for i, t in enumerate((
        "Cout: 3 x 22uF derated at 5 V = 42 uF",
        "fp = Iout / (2 pi Vout Cout) = 2.25 kHz",
        "fco = sqrt(fp x fsw / 2) = 26.9 kHz",
        "R29 = 2 pi fco Cout Vout / (gmps Vref gmea)",
        "    = 10.7k -> 10k   (gmps 12, gmea 350u)",
        "C28 = 1 / (2 pi R29 fp) = 7.1 nF -> 6.8 nF",
        "C29 = 1 / (pi R29 fsw) = 49 pF -> 47 pF",
    )):
        s.text(t, (127.0, 97.79 + i * 3.18))
    s.symbol("Device:R", "R29", "10k", (190.5, 99.06), footprint=R0603, lcsc="C25804", conn={"1": ("label", "COMP")})
    s.symbol("Device:C", "C28", "6.8nF", (190.5, 107.95), footprint=C0603, lcsc="C1631", conn={"2": G})
    s.wire(s.p("R29.2"), s.p("C28.1"))
    s.symbol("Device:C", "C29", "47pF C0G", (205.74, 99.06), footprint=C0603, lcsc="C1671", conn={"1": ("label", "COMP"), "2": G})

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


def build(path, page, fuse=("1.5A 33V", "C18198333"), vin_global=False, buck="tps54360",
          tvs=("SMBJ24A", "C123819"), u5_cin=True, power_in=None):
    """fuse: (value, LCSC) for F1. The SA818S board uses 2 A for a Pi 4 plus 1 W TX.
    vin_global: make VIN a global label (the SA818S board feeds its module supply from it).
    buck: "lmr33640" or "tps54360" (see the module notes). tvs: (value, LCSC) for D3; it must
    clamp below the buck's maximum input. u5_cin: a separate input cap at U5 (C26); without it
    U5 shares C25 at U4's output, so place U5 next to U4.
    power_in: (ref, value, footprint, LCSC) for the 13.8 V input connector; default J6, a 3.5 mm
    screw terminal."""
    s = Sheet("Power", path, page)
    G = ("pwr", "GND")

    s.text("13.8 V INPUT PROTECTION", (17.78, 38.1), 2.0, bold=True)
    s.text("5 V BUCK  (back-powers the Pi via header pins 2 and 4)", (152.4, 38.1), 2.0, bold=True)

    # ---- input: J6 -> F1 -> D1 -> VIN
    y = 50.8
    jref, jval, jfp, jlcsc = power_in or (
        "J6", "13.8 V in", "TerminalBlock_Phoenix:TerminalBlock_Phoenix_PT-1,5-2-3.5-H_1x02_P3.50mm_Horizontal", "C474892")
    s.symbol("Connector:Screw_Terminal_01x02", jref, jval, (25.4, y), mirror="y", footprint=jfp,
             lcsc=jlcsc, ref_at=(22.86, 45.72), val_at=(22.86, 43.18))
    s.symbol("Device:Polyfuse", "F1", fuse[0], (49.53, y), rot=90, footprint=sharedlib.fp("Fuse:Fuse_1812_4532Metric"),
             lcsc=fuse[1], ref_at=(49.53, 46.99), val_at=(49.53, 55.88))
    s.symbol("Device:D_Schottky", "D1", "SS56", (68.58, y), rot=180, footprint="Diode_SMD:D_SMA",
             lcsc="C65009", ref_at=(68.58, 46.99), val_at=(68.58, 55.88))
    chain(s, [s.p(f"{jref}.1"), (33.02, y), s.p("F1.1")])
    chain(s, [s.p("F1.2"), (55.88, y), s.p("D1.2")])
    s.label("13V8_IN", (33.02, y), (1, 0))
    s.label("VIN_F", (55.88, y), (1, 0))
    gn = (35.56, 55.88)
    chain(s, [s.p(f"{jref}.2"), (35.56, s.p(f"{jref}.2")[1]), gn])
    s.power("GND", gn)

    # shunts on VIN (15.24 mm pitch leaves room for values)
    s.symbol("Device:D_Zener", "D3", tvs[0], (81.28, 58.42), rot=270, footprint="Diode_SMD:D_SMB",
             lcsc=tvs[1], ref_at=(87.63, 57.15), val_at=(87.63, 59.69), conn={"2": G})
    caps_in = [("C16", 96.52), ("C17", 111.76), ("C18", 127.0)]
    for ref, x in caps_in:
        s.symbol("Device:C", ref, "2.2uF 50V", (x, 58.42), footprint=C1206, lcsc="C50254", conn={"2": G})
    s.symbol("Device:C_Polarized", "C19", "100uF 50V", (142.24, 58.42),
             footprint="Capacitor_SMD:CP_Elec_8x10.5", lcsc="C72480", conn={"2": G})

    shunt_x = [81.28, 96.52, 111.76, 127.0, 142.24]
    if buck == "lmr33640":
        sw, l_out = _lmr33640(s, y, shunt_x, caps_in, vin_global, G)
    else:
        sw, l_out = _tps54360(s, y, shunt_x, caps_in, vin_global, G)

    # output caps + 5V
    cx = [227.33, 242.57, 257.81]
    for x, ref in zip(cx, ("C21", "C22", "C23")):
        s.symbol("Device:C", ref, "22uF 25V", (x, sw[1] + 3.81), footprint=C1206, lcsc="C12891", conn={"2": G})
    n5 = (222.25, sw[1])
    chain(s, [l_out, n5] + [(x, sw[1]) for x in cx])
    s.power("+5V", n5)
    s.flag((cx[2], sw[1]))
    for pt in (n5, (cx[0], sw[1]), (cx[1], sw[1])):
        s.junction(pt)

    if buck == "lmr33640":
        _lmr33640_feedback(s, G)
    else:
        _tps54360_comp_feedback(s, G)

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
    if u5_cin:
        s.symbol("Device:C", "C26", "1uF", (88.9, y2 + 3.81), footprint=C0603, lcsc="C15849", conn={"2": G})
    vo, u5in, u5en = s.p("U4.2"), s.p("U5.1"), s.p("U5.3")
    chain(s, [vo, (73.66, y2), (81.28, y2), (88.9, y2), (99.06, y2), u5in])
    for x in ((73.66, 81.28, 88.9, 99.06) if u5_cin else (73.66, 81.28, 99.06)):
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
    if buck == "lmr33640":
        notes = (
            "U3 EN is tied to VIN: always on. PG is not used.",
            "LMR33640ADDA: 400 kHz, internal compensation. L1 6.8 uH and 3 x 22 uF per datasheet table 9-2.",
            "Vout = 1.0 V x (1 + R25 / R26) = 5.10 V.  C28 = VCC bypass (internal 5 V LDO).",
            "LMR33640 max input 38 V: D3 SMBJ22A clamps at about 35.5 V.",
            "+3.3VA is a quiet analog rail for the codec (AVDD, DRVDD). +1V8 feeds the codec core (DVDD).",
            "D3 is drawn with the Zener symbol: KiCad's convention for a unidirectional TVS.",
        )
    else:
        notes = (
            "U3 EN is left open: internal pull-up = always on.",
            "R28 150k sets the switching frequency to about 640 kHz.",
            "Vout = 0.8 V x (1 + (R25 + R26) / R27) = 5.06 V.",
            "TPS54360 can't sink current, so no back-feed diode is needed (HAT design guide).",
            "+3.3VA is a quiet analog rail for the codec (AVDD, DRVDD). +1V8 feeds the codec core (DVDD).",
            "D3 is drawn with the Zener symbol: KiCad's convention for a unidirectional TVS.",
        )
    for i, t in enumerate(notes):
        s.text(t, (17.78, 166.37 + i * 3.81))
    return s
