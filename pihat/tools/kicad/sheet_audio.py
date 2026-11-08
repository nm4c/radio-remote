"""Sheet: Audio isolation. T1 drives the radio's TX audio input; T2 takes the radio's RX audio into the codec differentially.

No TX pad: the codec's output volume sets the level in software, from line level (e.g. a
DM4601e rear connector) down to mic level. The RX pad suits line-level RX audio; for a speaker
output, drop R13 to 1k (see the note on the sheet)."""
from schbuild import Sheet

R0603 = "Resistor_SMD:R_0603_1608Metric"
C0603 = "Capacitor_SMD:C_0603_1608Metric"
C0805 = "Capacitor_SMD:C_0805_2012Metric"
FB0603 = "Inductor_SMD:L_0603_1608Metric"
XFMR = dict(footprint="pihat:Bourns_SM-LP-5001", lcsc="C840532")


def chain(s, pts):
    for a, b in zip(pts, pts[1:]):
        s.wire(a, b)


def hpart(s, lib, ref, val, at, **kw):
    """Horizontal two-pin part; returns (left_pin, right_pin)."""
    s.symbol(lib, ref, val, at, rot=90, ref_at=(at[0], at[1] - 3.81), val_at=(at[0], at[1] + 3.81), **kw)
    return tuple(sorted([s.p(f"{ref}.1"), s.p(f"{ref}.2")]))


def build(path, page):
    s = Sheet("Audio isolation", path, page)
    RG = ("pwr", "RGND")
    s.text("AUDIO ISOLATION", (17.78, 30.48), 2.0, bold=True)

    # ---------------- TX: codec headphone driver (differential) -> T1 -> pad -> radio MIC
    s.text("TX  codec -> radio mic", (17.78, 41.91), 1.5, bold=True)
    yt, yb = 55.88, 66.04
    s.symbol("pihat:SM-LP-5001", "T1", "SM-LP-5001E", (114.3, 60.96), ref_at=(116.84, 51.43), val_at=(116.84, 72.39), ref_just="left", val_just="left", **XFMR)
    s.dashed((114.3, 45.72), (114.3, 76.2))
    s.text("GND", (104.14, 46.99), 1.27, bold=True)
    s.text("RGND", (116.84, 46.99), 1.27, bold=True)
    for ref, net, y, pin in (("R14", "HP_P", yt, "T1.1"), ("R15", "HP_N", yb, "T1.3")):
        a, b = hpart(s, "Device:R", ref, "33R", (96.52, y), footprint=R0603, lcsc="C23140")
        s.wire(b, s.p(pin))
        s.wire(a, (a[0] - 5.08, y))
        s.label(net, (a[0] - 5.08, y), (-1, 0), glob=True)
    # secondary: 6 -> C1 -> FB4 -> R_MIC (C7 to RGND) ; 4 -> RGND
    rgn = (144.78, yb)
    chain(s, [s.p("T1.4"), rgn, (144.78, yb + 2.54)])
    s.power("RGND", (144.78, yb + 2.54))
    a1, b1 = hpart(s, "Device:C", "C1", "1uF X7R", (157.48, yt), footprint=C0805, lcsc="C28323")
    chain(s, [s.p("T1.6"), a1])
    af, bf = hpart(s, "Device:FerriteBead_Small", "FB4", "600R@100MHz", (170.18, yt), footprint=FB0603, lcsc="C1002")
    s.wire(b1, af)
    mic = (180.34, yt)
    s.symbol("Device:C", "C7", "100pF", (180.34, yt + 3.81), footprint=C0603, lcsc="C14858", conn={"2": RG})
    chain(s, [bf, mic, (190.5, yt)])
    s.junction(mic)
    s.label("R_MIC", (190.5, yt), (1, 0), glob=True)
    for i, t in enumerate((
        "No pad: set the level in software (alsamixer HP volume).",
        "Line-level inputs need about -6 to -12 dB, mic inputs about -40 dB.",
        "HPLCOM is set to 'differential of HPLOUT', so T1 sees no DC.",
        "C1 blocks the radio's electret bias on the MIC pin.",
    )):
        s.text(t, (17.78, 52.07 + i * 3.81))

    # ---------------- RX: radio speaker (floating) -> T2 -> differential pad -> codec MIC1L
    s.text("RX  radio RX audio -> codec", (17.78, 87.63), 1.5, bold=True)
    yt, yb = 101.6, 111.76
    s.symbol("pihat:SM-LP-5001", "T2", "SM-LP-5001E", (114.3, 106.68), ref_at=(116.84, 97.15), val_at=(116.84, 118.11), ref_just="left", val_just="left", **XFMR)
    s.dashed((114.3, 91.44), (114.3, 121.92))
    s.text("GND", (104.14, 92.71), 1.27, bold=True)
    s.text("RGND", (116.84, 92.71), 1.27, bold=True)
    # No RF filtering here: RX audio is ignored while the radio transmits.
    for net, y, pin in (("R_SPP", yt, "T2.6"), ("R_SPN", yb, "T2.4")):
        end = (s.p(pin)[0] + 21.59, y)
        chain(s, [s.p(pin), end])
        s.label(net, end, (1, 0), glob=True)
    nodes = {}
    for ref, cref, net, y, pin in (("R11", "C2", "RX_P", yt, "T2.1"), ("R12", "C3", "RX_N", yb, "T2.3")):
        a, b = hpart(s, "Device:R", ref, "10k", (96.52, y), footprint=R0603, lcsc="C25804")
        s.wire(b, s.p(pin))
        node = (86.36, y)
        ca, cb = hpart(s, "Device:C", cref, "1uF", (77.47, y), footprint=C0603, lcsc="C15849")
        chain(s, [cb, node, a])
        s.junction(node)
        s.wire(ca, (ca[0] - 5.08, y))
        s.label(net, (ca[0] - 5.08, y), (-1, 0), glob=True)
        nodes[net] = node
    s.symbol("Device:R", "R13", "10k", (86.36, (yt + yb) / 2), footprint=R0603, lcsc="C25804",
             ref_at=(88.9, (yt + yb) / 2 - 1.27), val_at=(88.9, (yt + yb) / 2 + 1.27), ref_just="left", val_just="left")
    s.wire(nodes["RX_P"], s.p("R13.1"))
    s.wire(s.p("R13.2"), nodes["RX_N"])
    for i, t in enumerate((
        "Pad = R13 / (R11 + R12 + R13) = about -9.5 dB, differential: for line-level RX audio",
        "(e.g. DM4601e rear connector, TK-880 DEO). Speaker output instead: R13 = 1k (-26 dB).",
        "Neither RX wire is grounded: safe on BTL speaker amps.",
    )):
        s.text(t, (17.78, 127.0 + i * 3.81))
    return s
