"""Sheet: Transceiver. SA818S module, 7th-order low-pass, edge SMA, and the Pi interface.

Interface choices (SA818S datasheet Rev 1.3, pin table):
  PTT is active low and the Pi holds GPIO17 low at boot -> Q1 inverts it, R40 pulls PTT up: boots in RX.
  H/L must never be driven high -> Q2 open drain: off = high power (pin left open), on = low power.
  PD low = sleep -> R43 holds it low until the server wakes the module.
  AudioON goes low while received audio is present -> busy; divided to 3.3 V for GPIO23.
  Module TXD may swing to VBAT (4.2 V) -> divided to 3.3 V for the Pi's RXD.

Low-pass: 7th-order, symmetric, tuned for a flat match up to 480 MHz (-3 dB at about 550 MHz):
5.6 pF / 18 nH / 13 pF / 18 nH / 13 pF / 18 nH / 5.6 pF. Simulated with the parts' Q, ESR and
self-resonance, pads and tolerances: 0.45-0.65 dB loss 400-480 MHz (0.62 dB at 477), return loss
better than 22 dB, about -57 dB at 954 MHz (2nd harmonic of 477) and -39 dB at 800 MHz.
"""
from schbuild import Sheet
import sharedlib

R0603 = "Resistor_SMD:R_0603_1608Metric"
C0603 = "Capacitor_SMD:C_0603_1608Metric"
C1206 = "Capacitor_SMD:C_1206_3216Metric"
L0603 = "Inductor_SMD:L_0603_1608Metric"
C0402 = "Capacitor_SMD:C_0402_1005Metric"


def chain(s, pts):
    for a, b in zip(pts, pts[1:]):
        s.wire(a, b)


def build(path, page):
    s = Sheet("Transceiver", path, page)
    G = ("pwr", "GND")
    s.text("SA818S TRANSCEIVER + HARMONIC FILTER", (17.78, 30.48), 2.0, bold=True)

    # ---------------- module
    ux, uy = 165.1, 101.6
    s.symbol("sa818s-hat:SA818S", "U6", "SA818S-U", (ux, uy), lcsc="C3001549",
             footprint="sa818s-hat:NiceRF_SA818S", ref_at=(ux - 12.7, uy - 30.48), val_at=(ux - 12.7, uy - 27.94),
             ref_just="left", val_just="left",
             conn={"18": ("glabel", "SA_MIC", 5.08), "6": ("glabel", "GPIO27", 5.08), "3": ("glabel", "SA_AF", 5.08),
                   **{str(n): ("nc",) for n in (2, 4, 11, 13, 14, 15)}})
    # supply at the VBAT pad: +4V2_RF with C40 100 nF right at the pin (C38/C39 bulk on the RF power sheet)
    vb = s.p("U6.8")
    top = (vb[0], vb[1] - 5.08)
    chain(s, [vb, top]); s.power("+4V2_RF", top)
    s.symbol("Device:C", "C40", "100nF", (vb[0] + 15.24, vb[1] - 1.27), footprint=C0603, lcsc="C14663", conn={"2": G})
    c40 = s.p("C40.1")
    mid = (vb[0], c40[1])
    s.junction(mid)
    s.wire(mid, c40)
    # C45: RF bypass. 100 nF is inductive at 450 MHz; 100 pF C0G in 0603 self-resonates near 600 MHz,
    # so it shorts the transmitter's RF on VBAT where the 100 nF can't.
    s.symbol("Device:C", "C45", "100pF C0G", (vb[0] - 27.94, vb[1] - 1.27), footprint=C0603, lcsc="C14858",
             conn={"2": G}, fields_dir="left")
    c45 = s.p("C45.1")
    s.wire((vb[0], c45[1]), c45)
    # grounds
    gs = [s.p(f"U6.{n}") for n in (9, 10, 19, 20)]
    yg = gs[0][1] + 2.54
    for p in gs:
        s.wire(p, (p[0], yg))
    chain(s, [(p[0], yg) for p in gs])
    for p in gs[1:-1]:
        s.junction((p[0], yg))
    s.wire((gs[1][0], yg), (gs[1][0], yg + 2.54)); s.power("GND", (gs[1][0], yg + 2.54))
    s.text("Pads 19/20: ground tabs. Solid ground", (ux - 12.7, yg + 10.16))
    s.text("under the module, stitching vias.", (ux - 12.7, yg + 13.97))

    # ---------------- harmonic filter -> SMA
    s.text("LOW-PASS  7th order, -3 dB at 550 MHz", (184.15, 78.74), 1.5, bold=True)
    s.text("0.6 dB loss at 477 MHz, 2nd harmonic -57 dB (simulated)", (184.15, 82.55))
    ant = s.p("U6.12")
    nodes = [ant[0] + 7.62, ant[0] + 22.86, ant[0] + 38.1, ant[0] + 53.34]
    # Filter parts (re-tuned for 477 MHz, Nov 2026). Caps: Murata GJM high-Q RF series (lowest ESR at
    # UHF; the shunt caps' ESR costs as much loss as the inductors' Q), all 0402 for low ESL:
    # GJM1555C1H5R6BB01D 5.6 pF +/-0.1 pF, GJM1555C1H130GB01D 13 pF +/-2%.
    # Inductors: Murata LQW18AN18NG80D, 18 nH 2% wirewound, lowest DCR in 0603 at LCSC (75 mOhm, 1.4 A).
    caps = [("C41", "5.6pF", "C161329", C0402), ("C42", "13pF", "C2167421", C0402),
            ("C43", "13pF", "C2167421", C0402), ("C44", "5.6pF", "C161329", C0402)]
    inds = [("L3", "18nH 2%", "C1330613"), ("L4", "18nH 2%", "C1330613"), ("L5", "18nH 2%", "C1330613")]
    for x, (ref, val, lcsc, cfp) in zip(nodes, caps):
        s.symbol("Device:C", ref, val, (x, uy + 3.81), footprint=cfp, lcsc=lcsc, conn={"2": G},
                 ref_at=(x + 2.54, uy + 5.08), val_at=(x + 2.54, uy + 7.62), ref_just="left", val_just="left")
    pts = [ant]
    for i, (ref, val, lcsc) in enumerate(inds):
        cx = (nodes[i] + nodes[i + 1]) / 2
        s.symbol("Device:L", ref, val, (cx, uy), rot=90, footprint=L0603, lcsc=lcsc,
                 ref_at=(cx, uy - 3.81), val_at=(cx, uy - 6.35))
    l_ends = [sorted([s.p(f"{r}.1"), s.p(f"{r}.2")]) for r, _, _ in inds]
    s.wire(ant, (nodes[0], uy))
    for i, (a, b) in enumerate(l_ends):
        s.wire((nodes[i], uy), a)
        s.wire(b, (nodes[i + 1], uy))
    for x in nodes:
        s.wire((x, uy), s.p(f"{caps[nodes.index(x)][0]}.1"))
    for x in nodes[:-1]:
        s.junction((x, uy))
    # J3: vertical through-hole SMA, hand-soldered (DNP for JLC). Four legs carry the antenna's leverage,
    # and it sits right at the filter output with the antenna screwed on through the top of the case.
    s.symbol("Connector:Conn_Coaxial", "J3", "SMA vertical THT", (nodes[-1] + 30.48, uy),
             footprint="Connector_Coaxial:SMA_Amphenol_132134-11_Vertical", dnp=True, in_bom=False,
             fields={"MPN": "Amphenol RF 132134-11, or any vertical THT SMA jack on a 5.08 mm leg square (e.g. LCSC C496549)"},
             ref_at=(nodes[-1] + 30.48, uy - 5.08), val_at=(nodes[-1] + 30.48, uy - 7.62))
    s.wire((nodes[-1], uy), s.p("J3.1"))
    s.junction((nodes[-1], uy))
    # L6: static bleed from the antenna to ground, NOT FITTED until the module's ANT pin is checked for DC
    # (a shunt inductor would short any bias the module puts on it). 220 nH is 620 ohm at 450 MHz: ~0.02 dB.
    lx = nodes[-1] + 12.7
    s.symbol("Device:L", "L6", "220nH DNP", (lx, uy + 6.35), footprint=L0603, dnp=True, in_bom=False,
             fields={"MPN": "Coilcraft 0603CS-R22XGLU (220 nH 2%, SRF 900 MHz)"}, conn={"2": G},
             ref_at=(lx + 2.54, uy + 7.62), val_at=(lx + 2.54, uy + 10.16), ref_just="left", val_just="left")
    s.wire((lx, uy), s.p("L6.1")); s.junction((lx, uy))
    sh = s.p("J3.2")
    s.wire(sh, (sh[0], sh[1] + 2.54)); s.power("GND", (sh[0], sh[1] + 2.54))
    for i, t in enumerate((
        "50 ohm coplanar waveguide ANT -> J3, keep it short.",
        "Filter in a straight line, ground vias at each cap.",
        "Shield can over U6 + filter if the Pi desenses RX.",
        "L6 (DNP): fit only if U6 pin 12 shows 0 V DC (RX and TX).",
        "C41/C44 5.6 pF +/-0.1 pF, C42/C43 13 pF 2%: Murata GJM high-Q 0402.",
    )):
        s.text(t, (184.15, 119.38 + i * 3.81))

    # ---------------- Pi interface (left), each circuit wired straight to its module pin
    s.text("PI INTERFACE", (17.78, 38.1), 1.5, bold=True)

    def nfet(ref, gate_net, rpd, at, pullup=None):
        """N-FET with gate pulldown; returns the drain-side terminal the module pin is wired to."""
        x, y = at
        s.symbol("Transistor_FET:2N7002", ref, "2N7002", (x, y), lcsc="C8545",
                 footprint="Package_TO_SOT_SMD:SOT-23", ref_at=(x + 3.81, y - 1.27), val_at=(x + 3.81, y + 1.27),
                 ref_just="left", val_just="left", conn={"2": G})
        g = s.p(f"{ref}.1")
        gn = (g[0] - 5.08, g[1])
        chain(s, [g, gn, (gn[0] - 7.62, g[1])])
        s.label(gate_net, (gn[0] - 7.62, g[1]), (-1, 0), glob=True)
        s.junction(gn)
        s.symbol("Device:R", rpd, "100k", (gn[0], gn[1] + 6.35), footprint=R0603, lcsc="C25803",
                 fields_dir="left", conn={"2": G})
        s.wire(gn, s.p(f"{rpd}.1"))
        d = s.p(f"{ref}.3")
        dt = (d[0], d[1] - 5.08)
        s.wire(d, dt)
        if pullup:
            s.junction(dt)
            s.symbol("Device:R", pullup, "10k", (dt[0], dt[1] - 6.35), footprint=R0603, lcsc="C25804",
                     fields_dir="left", conn={"1": ("pwr", "+3V3")})
            s.wire(s.p(f"{pullup}.2"), dt)
        return dt

    def divider(out_net, rt, rb, x, y0):
        """10k/22k divider, module output on top; returns the terminal above R(top)."""
        s.symbol("Device:R", rt, "10k", (x, y0), footprint=R0603, lcsc="C25804")
        s.symbol("Device:R", rb, "22k", (x, y0 + 10.16), footprint=R0603, lcsc="C31850", conn={"2": G})
        n = (x, y0 + 5.08)
        chain(s, [s.p(f"{rt}.2"), n, s.p(f"{rb}.1")])
        s.wire(n, (x - 7.62, n[1])); s.junction(n)
        s.label(out_net, (x - 7.62, n[1]), (-1, 0), glob=True)
        t = s.p(f"{rt}.1")
        term = (t[0], t[1] - 2.54)
        s.wire(t, term)
        return term

    # UART: Pi TX -> 1k -> module RXD
    y = 50.8
    s.text("UART 9600 8N1 (GPIO14/15)", (17.78, y - 5.08), 1.27, bold=True)
    # R48 10k: the Pi's TX idles high, and the SA818S must not have RXD held high while it sleeps
    # (datasheet pin table: "pull low RXD before sleep ... current leakage or poor reset"). 10k keeps
    # that leakage under 0.3 mA during boot; the server holds TX low (break) while the module sleeps.
    s.symbol("Device:R", "R48", "10k", (38.1, y), rot=90, footprint=R0603, lcsc="C25804", ref_at=(38.1, y - 3.81), val_at=(38.1, y + 3.81))
    a, b = sorted([s.p("R48.1"), s.p("R48.2")])
    chain(s, [(27.94, y), a]); s.label("UART_TXD", (27.94, y), (-1, 0), glob=True)

    src = {   # module pin -> terminal of the circuit that drives / reads it
        "16": b,
        "17": divider("UART_RXD", "R46", "R47", 91.44, 62.23),
        "5": nfet("Q1", "GPIO17", "R41", (45.72, 96.52), pullup="R40"),
        "7": nfet("Q2", "GPIO22", "R42", (45.72, 127.0)),
        "1": divider("GPIO23", "R44", "R45", 91.44, 128.27),
    }
    # route: across at the terminal's height, down/up a lane, across to the pin.
    # Sources above their pins take lanes right-to-left from the top, those below left-to-right.
    lanes = {"16": 124.46, "17": 119.38, "5": 114.3, "7": 104.14, "1": 109.22}
    for pin, t in src.items():
        pp = s.p(f"U6.{pin}")
        lx = lanes[pin]
        pts = [t, (lx, t[1]), (lx, pp[1]), pp]
        chain(s, [q for k, q in enumerate(pts) if k == 0 or q != pts[k - 1]])

    # PD pull-down (sleep until the server wakes the module)
    s.symbol("Device:R", "R43", "100k", (124.46, 147.32), footprint=R0603, lcsc="C25803", conn={"1": ("glabel", "GPIO27", 5.08), "2": G})

    for i, t in enumerate((
        "PTT: GPIO17 high = transmit. R40 keeps the module in receive at boot.",
        "H/L: never driven high. Q2 off = high power (1 W), on = 0.5 W.",
        "PD: GPIO27 high = on. R43 keeps the module asleep until the server starts.",
        "R46/R47, R44/R45: 10k/22k, 4.2 V -> 2.9 V for Pi inputs.",
        "BUSY: AudioON low = signal present (busy_pin 23, busy_pull_up true).",
    )):
        s.text(t, (17.78, 160.02 + i * 3.81))
    return s
