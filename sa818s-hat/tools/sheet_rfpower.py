"""Sheet: RF power. A TPS7A94 ultra-low-noise LDO makes 4.2 V for the SA818S from the Pi's 5 V.

The board has no power input of its own: the Pi is powered from its USB-C socket and the HAT takes
5 V from header pins 2 and 4. The module (3.3-5.5 V, 4.2 V typical; 60 mA RX, 0.65-0.75 A TX) gets
its own rail, chosen for noise and ripple rejection over cost: 0.46 uVrms noise, 110 dB PSRR at
1 kHz and 50 dB at 1 MHz, 150 mV typical / 240 mV max dropout at 1 A (datasheet SBVS336C).

Following the datasheet's typical application (8.2): 10 uF in, 10 uF + 1 uF out (the 1 uF adds
5-7 dB of PSRR above 200 kHz, 8.1.9), 10 uF on NR/SS (lowest noise, 8.1.9 table 8-2), then a
ferrite + capacitor pi-filter before the load, which TI uses to clean up the peak near 1 MHz.
"""
from schbuild import Sheet
import sharedlib

R0603 = "Resistor_SMD:R_0603_1608Metric"
R1206 = "Resistor_SMD:R_1206_3216Metric"
C0603 = "Capacitor_SMD:C_0603_1608Metric"
C0805 = "Capacitor_SMD:C_0805_2012Metric"
C1206 = "Capacitor_SMD:C_1206_3216Metric"
C10U = ("10uF X7R 16V", C0805, "C95841")       # Samsung CL21B106KOQNNNE
C1U = ("1uF X7R 25V", C0603, "C29936")         # Samsung CL10B105KA8NNNC


def chain(s, pts):
    for a, b in zip(pts, pts[1:]):
        s.wire(a, b)


def cap(s, ref, part, at):
    val, fp, lcsc = part
    s.symbol("Device:C", ref, val, at, footprint=fp, lcsc=lcsc, conn={"2": ("pwr", "GND")})


def build(path, page):
    s = Sheet("RF power", path, page)
    G = ("pwr", "GND")
    s.text("4.2 V MODULE SUPPLY  (TPS7A94 ultra-low-noise LDO from the Pi's 5 V)", (17.78, 38.1), 2.0, bold=True)
    y = 55.88

    # +5V in, input caps
    xin = [48.26, 68.58]
    for ref, x in zip(("C31", "C32"), xin):
        cap(s, ref, C10U, (x, y + 7.62))
    x0 = 116.84
    s.symbol("sa818s-hat:TPS7A94", "U7", "TPS7A9401DSCR", (x0, y + 5.08), lcsc="C5220127",
             footprint=sharedlib.fp("Package_SON:Texas_DSC0010J_ThermalVias"),
             ref_at=(x0, y - 10.16), val_at=(x0, y - 7.62), conn={"4": ("nc",)})
    vin = s.p("U7.1")
    chain(s, [(38.1, y)] + [(x, y) for x in xin] + [vin])
    s.power("+5V", (38.1, y))
    for ref, x in zip(("C31", "C32"), xin):
        s.junction((x, y))
        s.wire((x, y), s.p(f"{ref}.1"))
    s.connect((s.p("U7.3"), (-1, 0)), ("label", "EN_RF", 5.08))
    s.connect((s.p("U7.5"), (-1, 0)), ("label", "PG_SET", 5.08))
    s.connect((s.p("U7.7"), (1, 0)), ("label", "NR_SS", 5.08))
    g6, g11 = s.p("U7.6"), s.p("U7.11")
    gj = (g6[0], g6[1] + 2.54)
    chain(s, [g6, gj]); chain(s, [g11, (g11[0], gj[1]), gj]); s.junction(gj); s.power("GND", gj)

    # output: 10 uF + 1 uF at the pins, SNS Kelvin-connected at them, then the ferrite pi-filter
    out, sns = s.p("U7.9"), s.p("U7.8")
    xs, xo1, xo2 = 137.16, 144.78, 165.1
    cap(s, "C33", C10U, (xo1, y + 7.62))
    cap(s, "C35", C1U, (xo2, y + 7.62))
    chain(s, [out, (xs, y), (xo1, y), (xo2, y), (182.88, y), (190.5, y)])
    chain(s, [sns, (xs, sns[1]), (xs, y)])
    for x in (xs, xo1, xo2, 182.88):
        s.junction((x, y))
    s.wire((xo1, y), s.p("C33.1")); s.wire((xo2, y), s.p("C35.1"))
    s.label("VREG", (182.88, y), (0, -1))

    # R34: 0 ohm bypass, not fitted. Fit it (and remove U7) to run the module straight from 5 V.
    s.symbol("Device:R", "R34", "0R DNP", (116.84, 27.94), rot=90, footprint=R1206, dnp=True, in_bom=False,
             ref_at=(116.84, 24.13), val_at=(116.84, 31.75))
    ra, rb = sorted([s.p("R34.1"), s.p("R34.2")])
    chain(s, [(68.58, y), (68.58, ra[1]), ra])
    chain(s, [rb, (xo1, rb[1]), (xo1, y)])

    s.symbol("Device:FerriteBead", "FB1", "220R 2A", (198.12, y), rot=90,
             footprint="Inductor_SMD:L_0805_2012Metric", lcsc="C85840", ref_at=(198.12, y - 6.35), val_at=(198.12, y - 8.89))
    f1, f2 = sorted([s.p("FB1.1"), s.p("FB1.2")])
    s.wire((190.5, y), f1)
    # C38 ceramic for high frequencies; C39 polymer tantalum: bulk for the TX key-up step, and its
    # ~35 mOhm ESR damps the ferrite + ceramic resonance that all-ceramic caps would leave ringing.
    cap(s, "C38", C10U, (210.82, y + 7.62))
    s.symbol("Device:C_Polarized", "C39", "100uF 6.3V polymer", (228.6, y + 7.62),
             footprint="Capacitor_Tantalum_SMD:CP_EIA-3528-21_Kemet-B", lcsc="C79109",
             fields={"MPN": "Panasonic 6TPE100MAZB (POSCAP, 35 mOhm)"}, conn={"2": G})
    chain(s, [f2, (210.82, y), (228.6, y), (243.84, y)])
    s.junction((210.82, y)); s.junction((228.6, y))
    s.wire((210.82, y), s.p("C38.1")); s.wire((228.6, y), s.p("C39.1"))
    s.power("+4V2_RF", (243.84, y))
    s.flag((228.6, y))

    # ---- set-up pins, drawn below and joined by labels
    y2 = 99.06
    s.text("OUTPUT SET  Vout = 150 uA x R31 = 4.20 V", (17.78, y2 - 5.08), 1.5, bold=True)
    s.symbol("Device:R", "R31", "28.0k 0.1%", (40.64, y2 + 7.62), footprint=R0603, lcsc="C667083",
             fields={"MPN": "FH TD03G2802BT (thin film, 0.1%, 25 ppm/C); alt. Vishay TNPW060328K0BHEA C1714791"},
             conn={"1": ("label", "NR_SS"), "2": G})
    s.symbol("Device:C", "C34", "10uF X7R 16V", (55.88, y2 + 7.62), footprint=C0805, lcsc="C95841",
             conn={"1": ("label", "NR_SS"), "2": G})

    s.text("ENABLE: on at 1.22 V x (1 + R32/R35) = 3.9 V", (88.9, y2 - 5.08), 1.5, bold=True)
    s.symbol("Device:R", "R32", "22k", (101.6, y2 + 3.81), footprint=R0603, lcsc="C31850",
             conn={"1": ("pwr", "+5V")})
    s.symbol("Device:R", "R35", "10k", (101.6, y2 + 13.97), footprint=R0603, lcsc="C25804", conn={"2": G})
    en = (101.6, y2 + 8.89)
    chain(s, [s.p("R32.2"), en, s.p("R35.1")]); s.junction(en)
    s.wire(en, (109.22, en[1])); s.label("EN_RF", (109.22, en[1]), (1, 0))

    s.text("CURRENT LIMIT: R36 || R37 < 12.5k = 1.3 A", (170.18, y2 - 5.08), 1.5, bold=True)
    s.symbol("Device:R", "R36", "150k", (182.88, y2 + 3.81), footprint=R0603, lcsc="C22807",
             conn={"1": ("label", "VREG")})
    s.symbol("Device:R", "R37", "10k", (182.88, y2 + 13.97), footprint=R0603, lcsc="C25804", conn={"2": G})
    pg = (182.88, y2 + 8.89)
    chain(s, [s.p("R36.2"), pg, s.p("R37.1")]); s.junction(pg)
    s.wire(pg, (190.5, pg[1])); s.label("PG_SET", (190.5, pg[1]), (1, 0))

    s.text("NOTES", (17.78, 129.54), 1.5, bold=True)
    for i, t in enumerate((
            "U7 TPS7A9401DSCR (TI): 1 A, 0.46 uVrms, PSRR 110 dB @ 1 kHz / 50 dB @ 1 MHz, dropout 240 mV max at 1 A.",
            "Headroom: 5.0 V - 4.2 V = 0.8 V; still 0.55 V if the Pi's 5 V sags to 4.75 V on TX.",
            "R31 sets Vout with the internal 150 uA reference: thin film 0.1% for accuracy and low excess noise.",
            "C34 on NR/SS filters the reference (start-up about 28 ms). C31-C35: X7R ceramics only - the output",
            "  needs 1-20 mOhm ESR and under 2 nH (datasheet 6.3), which rules out tantalum here.",
            "Layout: C31/C32 at IN, C33/C35 at OUT, grounds together at U7's GND with no vias in between.",
            "  SNS (pin 8) Kelvin to C33. Thermal pad to a large ground area: 0.6 W on TX, RthJA 46 C/W (JEDEC).",
            "R36 || R37 = 9.4k sets the full 1.3 A current limit. PG is not used (left open, as the datasheet allows).",
            "FB1 BLM21PG221SN1D (220 ohm @ 100 MHz, 2 A, 45 mOhm) + C38/C39: pi-filter at the module's VBAT pad.",
            "C39 polymer tantalum (35 mOhm) damps that pi-filter and holds up the 0.75 A step at key-up.",
            "R34 (not fitted): 0 ohm bypass. Fit it AND remove U7 to run the module straight from +5V.",
    )):
        s.text(t, (17.78, 133.35 + i * 3.81))
    return s
