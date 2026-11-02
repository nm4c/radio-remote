"""Sheet: Audio. Codec <-> SA818S, direct (same ground, no transformers).

TX: codec HPLOUT (0.707 Vrms full scale) -> 1 uF -> 10k/330R pad (-30 dB, ~22 mVrms max) -> 1 uF -> MIC_IN.
    The SA818S wants ~10 mV for standard deviation (datasheet); fine level in software.
RX: AF_OUT (700 mV, 200 ohm) -> 1 uF -> 10k/10k (-6 dB) -> codec MIC1LP. MIC1LM to ground through 1 uF
    (pseudo-differential: the codec measures against the same ground point).
"""
from schbuild import Sheet

R0603 = "Resistor_SMD:R_0603_1608Metric"
C0603 = "Capacitor_SMD:C_0603_1608Metric"


def chain(s, pts):
    for a, b in zip(pts, pts[1:]):
        s.wire(a, b)


def hpart(s, lib, ref, val, at, **kw):
    s.symbol(lib, ref, val, at, rot=90, ref_at=(at[0], at[1] - 3.81), val_at=(at[0], at[1] + 3.81), **kw)
    return tuple(sorted([s.p(f"{ref}.1"), s.p(f"{ref}.2")]))


def build(path, page):
    s = Sheet("Audio", path, page)
    G = ("pwr", "GND")
    s.text("AUDIO  codec <-> SA818S  (direct, shared ground)", (17.78, 30.48), 2.0, bold=True)

    # TX
    y = 55.88
    s.text("TX  codec HPLOUT -> module MIC_IN, -30 dB", (17.78, 43.18), 1.5, bold=True)
    a, b = hpart(s, "Device:C", "C50", "1uF", (55.88, y), footprint=C0603, lcsc="C15849")
    chain(s, [(45.72, y), a]); s.label("HP_P", (45.72, y), (-1, 0), glob=True)
    ra, rb = hpart(s, "Device:R", "R50", "10k", (71.12, y), footprint=R0603, lcsc="C25804")
    s.wire(b, ra)
    node = (83.82, y)
    s.symbol("Device:R", "R51", "330R", (83.82, y + 6.35), footprint=R0603, lcsc="C23138", conn={"2": G})
    ca, cb = hpart(s, "Device:C", "C51", "1uF", (96.52, y), footprint=C0603, lcsc="C15849")
    chain(s, [rb, node, ca]); s.junction(node)
    s.wire(node, s.p("R51.1"))
    chain(s, [cb, (109.22, y)]); s.label("SA_MIC", (109.22, y), (1, 0), glob=True)
    s.text("C51 isolates the pad from any bias on MIC_IN.", (17.78, 72.39))

    # RX
    y = 101.6
    s.text("RX  module AF_OUT -> codec MIC1LP, -6 dB", (17.78, 88.9), 1.5, bold=True)
    a, b = hpart(s, "Device:C", "C52", "1uF", (55.88, y), footprint=C0603, lcsc="C15849")
    chain(s, [(45.72, y), a]); s.label("SA_AF", (45.72, y), (-1, 0), glob=True)
    ra, rb = hpart(s, "Device:R", "R52", "10k", (71.12, y), footprint=R0603, lcsc="C25804")
    s.wire(b, ra)
    node = (83.82, y)
    s.symbol("Device:R", "R53", "10k", (83.82, y + 6.35), footprint=R0603, lcsc="C25804", conn={"2": G})
    chain(s, [rb, node, (96.52, y)]); s.junction(node)
    s.wire(node, s.p("R53.1"))
    s.label("RX_P", (96.52, y), (1, 0), glob=True)
    # MIC1LM reference
    y2 = 127.0
    s.symbol("Device:C", "C53", "1uF", (55.88, y2 + 3.81), footprint=C0603, lcsc="C15849", conn={"2": G})
    chain(s, [(45.72, y2), s.p("C53.1")]); s.label("RX_N", (45.72, y2), (-1, 0), glob=True)
    s.text("MIC1LM to ground through C53: pseudo-differential input", (17.78, 142.24))
    return s
