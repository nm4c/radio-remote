"""Sheet: Transceiver. SA818S module, 7th-order low-pass, edge SMA, and the Pi interface.

Interface choices (SA818S datasheet Rev 1.3, pin table):
  PTT is active low and the Pi holds GPIO17 low at boot -> Q1 inverts it, R40 pulls PTT up: boots in RX.
  H/L must never be driven high -> Q2 open drain: off = high power (pin left open), on = low power.
  PD low = sleep -> R43 holds it low until the server wakes the module.
  AudioON goes low while received audio is present -> busy; divided to 3.3 V for GPIO23.
  Module TXD may swing to VBAT (4.2 V) -> divided to 3.3 V for the Pi's RXD.

Low-pass: Chebyshev 0.1 dB, 7th order, fc 500 MHz, built from stock values
(6.8 pF / 22 nH / 12 pF / 24 nH). Simulated with Q=40 inductors: -0.6..-1.1 dB 400-480 MHz,
about -50 dB at 952 MHz (2nd harmonic of 476) and -80 dB at the 3rd.
"""
from schbuild import Sheet

R0603 = "Resistor_SMD:R_0603_1608Metric"
C0603 = "Capacitor_SMD:C_0603_1608Metric"
C1206 = "Capacitor_SMD:C_1206_3216Metric"
L0603 = "Inductor_SMD:L_0603_1608Metric"


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
             conn={"18": ("glabel", "SA_MIC", 5.08), "16": ("label", "SA_RXD", 5.08), "17": ("label", "SA_TXD", 5.08),
                   "5": ("label", "SA_PTT", 5.08), "6": ("glabel", "GPIO27", 5.08), "7": ("label", "SA_HL", 5.08),
                   "1": ("label", "SA_AUDIOON", 5.08), "3": ("glabel", "SA_AF", 5.08),
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
    s.text("LOW-PASS  7th-order Chebyshev, fc 500 MHz", (184.15, 78.74), 1.5, bold=True)
    s.text("2nd harmonic -50 dB, 3rd -80 dB (simulated)", (184.15, 82.55))
    ant = s.p("U6.12")
    nodes = [ant[0] + 7.62, ant[0] + 22.86, ant[0] + 38.1, ant[0] + 53.34]
    caps = [("C41", "6.8pF C0G", "C1679"), ("C42", "12pF C0G", "C38523"), ("C43", "12pF C0G", "C38523"), ("C44", "6.8pF C0G", "C1679")]
    inds = [("L3", "22nH 2%", "C162643"), ("L4", "24nH 2%", "C162644"), ("L5", "22nH 2%", "C162643")]
    for x, (ref, val, lcsc) in zip(nodes, caps):
        s.symbol("Device:C", ref, val, (x, uy + 3.81), footprint=C0603, lcsc=lcsc, conn={"2": G},
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
    s.symbol("Connector:Conn_Coaxial", "J3", "SMA edge", (nodes[-1] + 12.7, uy), lcsc="C5199907",
             footprint="Connector_Coaxial:SMA_Samtec_SMA-J-P-H-ST-EM1_EdgeMount",
             ref_at=(nodes[-1] + 12.7, uy - 5.08), val_at=(nodes[-1] + 12.7, uy - 7.62))
    s.wire((nodes[-1], uy), s.p("J3.1"))
    s.junction((nodes[-1], uy))
    sh = s.p("J3.2")
    s.wire(sh, (sh[0], sh[1] + 2.54)); s.power("GND", (sh[0], sh[1] + 2.54))
    for i, t in enumerate((
        "50 ohm coplanar waveguide ANT -> J3, keep it short.",
        "Filter in a straight line, ground vias at each cap.",
        "Shield can over U6 + filter if the Pi desenses RX.",
    )):
        s.text(t, (184.15, 119.38 + i * 3.81))

    # ---------------- Pi interface (left)
    s.text("PI INTERFACE", (17.78, 45.72), 1.5, bold=True)
    # PTT: GPIO17 -> Q1 -> SA_PTT, pulled up: boots in receive
    def nfet(ref, gate_net, drain_net, rpd, at, pullup=None):
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
        chain(s, [d, dt, (dt[0] + 7.62, dt[1])])
        s.label(drain_net, (dt[0] + 7.62, dt[1]), (1, 0))
        if pullup:
            s.junction(dt)
            s.symbol("Device:R", pullup, "10k", (dt[0], dt[1] - 6.35), footprint=R0603, lcsc="C25804",
                     fields_dir="left", conn={"1": ("pwr", "+3V3")})
            s.wire(s.p(f"{pullup}.2"), dt)

    nfet("Q1", "GPIO17", "SA_PTT", "R41", (45.72, 66.04), pullup="R40")
    nfet("Q2", "GPIO22", "SA_HL", "R42", (45.72, 101.6))

    # PD pull-down (sleep until the server wakes the module)
    s.symbol("Device:R", "R43", "100k", (99.06, 152.4), footprint=R0603, lcsc="C25803", conn={"1": ("glabel", "GPIO27", 5.08), "2": G})

    # UART: Pi TX -> 1k -> module RXD; module TXD -> 10k/22k -> Pi RX
    y = 134.62
    s.text("UART  (9600 8N1, Pi GPIO14/15)", (17.78, y - 6.35), 1.27, bold=True)
    s.symbol("Device:R", "R48", "1k", (38.1, y), rot=90, footprint=R0603, lcsc="C21190", ref_at=(38.1, y - 3.81), val_at=(38.1, y + 3.81))
    a, b = sorted([s.p("R48.1"), s.p("R48.2")])
    chain(s, [(27.94, y), a]); s.label("UART_TXD", (27.94, y), (-1, 0), glob=True)
    chain(s, [b, (48.26, y)]); s.label("SA_RXD", (48.26, y), (1, 0))

    def divider(top_net, out_net, rt, rb, at, out_glob=True):
        x, y0 = at
        s.symbol("Device:R", rt, "10k", (x, y0), footprint=R0603, lcsc="C25804", conn={"1": ("label", top_net, 5.08)})
        s.symbol("Device:R", rb, "22k", (x, y0 + 10.16), footprint=R0603, lcsc="C31850", conn={"2": G})
        n = (x, y0 + 5.08)
        chain(s, [s.p(f"{rt}.2"), n, s.p(f"{rb}.1")])
        s.wire(n, (x + 7.62, n[1])); s.junction(n)
        s.label(out_net, (x + 7.62, n[1]), (1, 0), glob=out_glob)

    divider("SA_TXD", "UART_RXD", "R46", "R47", (99.06, 58.42))
    divider("SA_AUDIOON", "GPIO23", "R44", "R45", (99.06, 104.14))
    for i, t in enumerate((
        "PTT: GPIO17 high = transmit. R40 keeps the module in receive at boot.",
        "H/L: never driven high. Q2 off = high power (1 W), on = 0.5 W.",
        "PD: GPIO27 high = on. R43 keeps the module asleep until the server starts.",
        "R46/R47, R44/R45: 10k/22k, 4.2 V -> 2.9 V for Pi inputs.",
        "BUSY: AudioON low = signal present (busy_pin 23, busy_pull_up true).",
    )):
        s.text(t, (17.78, 160.02 + i * 3.81))
    return s
