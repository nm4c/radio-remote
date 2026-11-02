"""Sheet: Codec. TLV320AIC3104 I2S codec, 12 MHz MCLK oscillator, series resistors, reset pull-down, decoupling."""
from schbuild import Sheet

R0603 = "Resistor_SMD:R_0603_1608Metric"
C0603 = "Capacitor_SMD:C_0603_1608Metric"
C0805 = "Capacitor_SMD:C_0805_2012Metric"


def chain(s, pts):
    for a, b in zip(pts, pts[1:]):
        s.wire(a, b)


def build(path, page, hp_n=True, lib="pihat"):
    """hp_n=False leaves HPLCOM unconnected (single-ended TX, as on the SA818S board).
    lib: the project symbol library holding TLV320AIC3104."""
    s = Sheet("Codec", path, page)
    G = ("pwr", "GND")
    s.text("I2S CODEC  TLV320AIC3104 (QFN-32, RHB)", (17.78, 30.48), 2.0, bold=True)

    # ---- U2
    ux, uy = 139.7, 101.6
    nc = {str(n): ("nc",) for n in (12, 13, 14, 16, 15, 23, 22, 27, 28, 29, 30)}
    left = {"1": "U2_MCLK", "2": "U2_BCLK", "3": "U2_WCLK", "4": "U2_DIN", "5": "U2_DOUT"}
    conn = {**nc, **{k: ("label", v, 5.08) for k, v in left.items()},
            "8": ("glabel", "I2C_SCL", 5.08), "9": ("glabel", "I2C_SDA", 5.08),
            "10": ("glabel", "RX_P", 5.08), "11": ("glabel", "RX_N", 5.08),
            "19": ("glabel", "HP_P", 5.08), "20": ("glabel", "HP_N", 5.08) if hp_n else ("nc",)}
    s.symbol(f"{lib}:TLV320AIC3104", "U2", "TLV320AIC3104IRHBR", (ux, uy), lcsc="C181753",
             footprint="Package_DFN_QFN:QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm",
             datasheet="https://www.ti.com/lit/ds/symlink/tlv320aic3104.pdf",
             ref_at=(ux - 38.1, 125.73), val_at=(ux - 38.1, 128.27), ref_just="left", val_just="left", conn=conn)

    # reset: global label from GPIO26 + 10k pull-down (held in reset until the driver releases it)
    rst = s.p("U2.31")
    nrst = (rst[0] - 5.08, rst[1])
    chain(s, [rst, nrst, (rst[0] - 7.62, rst[1])])
    s.label("CODEC_RST", (rst[0] - 7.62, rst[1]), (-1, 0), glob=True)
    s.symbol("Device:R", "R21", "10k", (nrst[0], nrst[1] + 6.35), footprint=R0603, lcsc="C25804",
             fields_dir="right", conn={"2": G})
    s.wire(nrst, s.p("R21.1"))
    s.junction(nrst)

    # supplies (top): IOVDD +3V3, DVDD +1V8, AVDD/DRVDD/DRVDD +3.3VA
    io, dv, av, d18, d24 = (s.p(f"U2.{n}") for n in (7, 32, 25, 18, 24))
    chain(s, [io, (io[0], io[1] - 7.62)]); s.power("+3V3", (io[0], io[1] - 7.62))
    chain(s, [dv, (dv[0], dv[1] - 2.54)]); s.power("+1V8", (dv[0], dv[1] - 2.54))
    ya = av[1] - 2.54
    for p in (av, d18, d24):
        s.wire(p, (p[0], ya))
    chain(s, [(av[0], ya), (d18[0], ya), (d24[0], ya), (d24[0] + 5.08, ya)])
    s.junction((d18[0], ya)); s.junction((d24[0], ya))
    s.power("+3.3VA", (d24[0] + 5.08, ya))
    # grounds (bottom): DVSS, AVSS1, AVSS2, DRVSS, thermal pad
    gs = [s.p(f"U2.{n}") for n in (6, 17, 26, 21, 33)]
    yg = gs[0][1] + 2.54
    for p in gs:
        s.wire(p, (p[0], yg))
    chain(s, [(p[0], yg) for p in gs])
    for p in gs[1:-1]:
        s.junction((p[0], yg))
    mid = (gs[2][0], yg)
    s.wire(mid, (mid[0], yg + 2.54))
    s.power("GND", (mid[0], yg + 2.54))
    s.text("Thermal pad (33) to DRVSS / ground plane", (gs[-1][0] + 3.81, yg + 1.27))

    # ---- series resistors on MCLK and the I2S lines
    s.text("33R SERIES  (place near U2)", (17.78, 52.07), 1.5, bold=True)
    series = [("R16", "OSC_12M", False, "U2_MCLK"), ("R17", "I2S_BCLK", True, "U2_BCLK"),
              ("R18", "I2S_LRCLK", True, "U2_WCLK"), ("R19", "I2S_DIN", True, "U2_DIN"),
              ("R20", "I2S_DOUT", True, "U2_DOUT")]
    for i, (ref, top, glob, bot) in enumerate(series):
        x = 22.86 + i * 15.24
        s.symbol("Device:R", ref, "33R", (x, 76.2), footprint=R0603, lcsc="C23140",
                 conn={"1": ("glabel" if glob else "label", top, 5.08), "2": ("label", bot, 5.08)})
    s.text("I2S_DIN = Pi to codec (GPIO21).  I2S_DOUT = codec to Pi (GPIO20).", (17.78, 97.79))

    # ---- 12 MHz master clock
    s.text("MASTER CLOCK  12 MHz", (17.78, 116.84), 1.5, bold=True)
    s.symbol("Oscillator:ASE-xxxMHz", "Y1", "12MHz 3.3V", (40.64, 137.16), lcsc="C160457",
             footprint="Oscillator:Oscillator_SMD_Abracon_ASE-4Pin_3.2x2.5mm",
             ref_at=(49.53, 128.27), val_at=(49.53, 130.81), ref_just="left", val_just="left",
             conn={"2": G, "3": ("label", "OSC_12M", 5.08)})
    en, vdd = s.p("Y1.1"), s.p("Y1.4")
    top = (vdd[0], vdd[1] - 2.54)
    chain(s, [vdd, top])
    chain(s, [en, (en[0] - 2.54, en[1]), (en[0] - 2.54, top[1]), top])
    s.junction(top)
    s.power("+3V3", top)
    s.text("HSO321S (C160457): standard 3225 pinout", (17.78, 152.4))
    s.text("1 EN, 2 GND, 3 OUT, 4 VDD. Confirm before layout.", (17.78, 156.21))

    # ---- decoupling: one cap per supply pin, placed at the pin
    s.text("DECOUPLING  (one cap at each pin)", (86.36, 152.4), 1.5, bold=True)
    caps = [("C8", "100nF", "+3.3VA", "AVDD 25", "C14663", C0603),
            ("C9", "100nF", "+3.3VA", "DRVDD 18", "C14663", C0603),
            ("C10", "100nF", "+3.3VA", "DRVDD 24", "C14663", C0603),
            ("C13", "10uF", "+3.3VA", "bulk at U2", "C15850", C0805),
            ("C11", "100nF", "+3V3", "IOVDD 7", "C14663", C0603),
            ("C12", "100nF", "+1V8", "DVDD 32", "C14663", C0603),
            ("C14", "100nF", "+3V3", "Y1 VDD", "C14663", C0603)]
    for i, (ref, val, rail, note, lcsc, fp) in enumerate(caps):
        x = 91.44 + i * 12.7
        s.symbol("Device:C", ref, val, (x, 166.37), footprint=fp, lcsc=lcsc,
                 conn={"1": ("pwr", rail), "2": G})
        s.text(note, (x - 3.81, 180.34), 1.0)

    # +3V3 comes from the Pi through the header
    s.power("+3V3", (220.98, 45.72))
    s.flag((220.98, 45.72))
    s.text("+3V3 from the Pi (header pins 1, 17)", (224.79, 46.99))
    return s
