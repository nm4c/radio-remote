"""Generates the Radio Remote HAT schematic page (HTML + inline SVG) and the JLC BoM CSV.

Usage:  python3 gen.py            (writes radio-remote-hat.html here and ../jlc-bom.csv)
The parts list below (PARTS) is the single source for both outputs.
"""
import csv, html, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "radio-remote-hat.html")
CSV_OUT = os.path.join(HERE, "..", "jlc-bom.csv")
CW = 6.3  # approx mono char width at 10.5px

# ---------------------------------------------------------------- svg helpers
class Sheet:
    def __init__(self, w, h, label):
        self.w, self.h, self.label, self.e = w, h, label, []

    def add(self, s): self.e.append(s)

    def line(self, x1, y1, x2, y2, c="w"):
        self.add(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" class="{c}"/>')

    def poly(self, pts, c="w"):
        p = " ".join(f"{x},{y}" for x, y in pts)
        self.add(f'<polyline points="{p}" class="{c}"/>')

    def text(self, x, y, s, c="t-note", a="start"):
        self.add(f'<text x="{x}" y="{y}" class="{c}" text-anchor="{a}">{html.escape(s)}</text>')

    def dot(self, x, y):
        self.add(f'<circle cx="{x}" cy="{y}" r="3" class="dot"/>')

    def nc(self, x, y):
        self.add(f'<path d="M{x-5} {y-5} L{x+5} {y+5} M{x+5} {y-5} L{x-5} {y+5}" class="nc"/>')

    # ground symbols
    def gnd(self, x, y):
        self.add(f'<path d="M{x} {y} V{y+8} M{x-10} {y+8} H{x+10} M{x-6} {y+12} H{x+6} M{x-2} {y+16} H{x+2}" class="gnd"/>')

    def rgnd(self, x, y):
        self.add(f'<path d="M{x} {y} V{y+7} M{x-9} {y+7} H{x+9} L{x} {y+17} Z" class="rgnd"/>')

    def pwr(self, x, y, name, side="pi"):
        self.add(f'<path d="M{x} {y} V{y-10} M{x-10} {y-10} H{x+10}" class="pwr-{side}"/>')
        self.text(x, y - 15, name, f"t-pwr t-{side}", "middle")

    def flag(self, x, y, name, d="r", side="pi"):
        w = len(name) * CW + 12
        if d == "r":
            pts = [(x, y), (x + 8, y - 8), (x + 8 + w, y - 8), (x + 8 + w, y + 8), (x + 8, y + 8)]
            tx, a = x + 10, "start"
        else:
            pts = [(x, y), (x - 8, y - 8), (x - 8 - w, y - 8), (x - 8 - w, y + 8), (x - 8, y + 8)]
            tx, a = x - 10, "end"
        p = " ".join(f"{a_},{b}" for a_, b in pts)
        self.add(f'<polygon points="{p}" class="flag flag-{side}"/>')
        self.text(tx, y + 3.6, name, f"t-net t-{side}", a)

    # two-terminal parts, drawn along +x from (0,0) to (L,0) then rotated
    def two(self, kind, x, y, d="r", L=60, ref=None, val=None, lab="auto", side=None):
        rot = {"r": 0, "d": 90, "l": 180, "u": -90}[d]
        m = L / 2
        cls = f" part-{side}" if side else ""
        g = []
        if kind == "res":
            g += [f'<line x1="0" y1="0" x2="{m-15}" y2="0" class="w"/>',
                  f'<rect x="{m-15}" y="-6" width="30" height="12" class="b{cls}"/>',
                  f'<line x1="{m+15}" y1="0" x2="{L}" y2="0" class="w"/>']
        elif kind == "fuse":
            g += [f'<line x1="0" y1="0" x2="{m-15}" y2="0" class="w"/>',
                  f'<rect x="{m-15}" y="-6" width="30" height="12" class="b"/>',
                  f'<line x1="{m-15}" y1="0" x2="{m+15}" y2="0" class="p"/>',
                  f'<line x1="{m+15}" y1="0" x2="{L}" y2="0" class="w"/>']
        elif kind in ("cap", "ecap"):
            g += [f'<line x1="0" y1="0" x2="{m-4}" y2="0" class="w"/>',
                  f'<line x1="{m-4}" y1="-10" x2="{m-4}" y2="10" class="pl"/>',
                  f'<line x1="{m+4}" y1="-10" x2="{m+4}" y2="10" class="pl"/>',
                  f'<line x1="{m+4}" y1="0" x2="{L}" y2="0" class="w"/>']
            if kind == "ecap":
                g.append(f'<path d="M{m-14} -12 h6 M{m-11} -15 v6" class="p"/>')
        elif kind == "fb":
            g += [f'<line x1="0" y1="0" x2="{m-11}" y2="0" class="w"/>',
                  f'<rect x="{m-11}" y="-5" width="22" height="10" class="fbody"/>',
                  f'<line x1="{m+11}" y1="0" x2="{L}" y2="0" class="w"/>']
        elif kind == "ind":
            r = 5
            arcs = "".join(f" a{r},{r} 0 0 1 {2*r},0" for _ in range(4))
            g += [f'<line x1="0" y1="0" x2="{m-20}" y2="0" class="w"/>',
                  f'<path d="M{m-20} 0{arcs}" class="p"/>',
                  f'<line x1="{m+20}" y1="0" x2="{L}" y2="0" class="w"/>']
        elif kind in ("diode", "tvs", "led"):
            g += [f'<line x1="0" y1="0" x2="{m-9}" y2="0" class="w"/>',
                  f'<polygon points="{m-9},-9 {m-9},9 {m+9},0" class="b"/>',
                  f'<line x1="{m+9}" y1="0" x2="{L}" y2="0" class="w"/>']
            if kind == "tvs":
                g.append(f'<path d="M{m+5} -13 L{m+9} -9 V9 L{m+13} 13" class="pl"/>')
            else:
                g.append(f'<line x1="{m+9}" y1="-9" x2="{m+9}" y2="9" class="pl"/>')
            if kind == "led":
                g.append(f'<path d="M{m-2} -12 l7 -9 m-4 0 h4 v4 M{m+5} -12 l7 -9 m-4 0 h4 v4" class="p"/>')
        elif kind == "jumper":
            g += [f'<line x1="0" y1="0" x2="{m-9}" y2="0" class="w"/>',
                  f'<path d="M{m-9} -7 h6 a7,7 0 0 0 0,14 h-6 Z M{m+9} -7 h-6 a7,7 0 0 1 0,14 h6 Z" class="b"/>',
                  f'<line x1="{m+9}" y1="0" x2="{L}" y2="0" class="w"/>']
        self.add(f'<g transform="translate({x},{y}) rotate({rot})">{"".join(g)}</g>')
        ex, ey = {"r": (x + L, y), "d": (x, y + L), "l": (x - L, y), "u": (x, y - L)}[d]
        if ref:
            if lab == "auto":
                lab = "above" if d in "rl" else "right"
            cx, cy = (x + ex) / 2, (y + ey) / 2
            if lab == "above":
                self.text(cx, cy - 12, ref + (f" {val}" if val else ""), "t-ref", "middle")
            elif lab == "split":
                self.text(cx, cy - 12, ref, "t-ref", "middle")
                if val: self.text(cx, cy + 22, val, "t-val", "middle")
            elif lab == "right":
                self.text(cx + 14, cy - 2, ref, "t-ref")
                if val: self.text(cx + 14, cy + 11, val, "t-val")
            elif lab == "left":
                self.text(cx - 14, cy - 2, ref, "t-ref", "end")
                if val: self.text(cx - 14, cy + 11, val, "t-val", "end")
        return ex, ey

    def ic(self, x, y, w, ref, val, left, right, pitch=20, top=30, stub=30, rows=None):
        n = rows or max(len(left), len(right))
        h = (n - 1) * pitch + 2 * top
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" class="body"/>')
        self.text(x + w / 2, y - 8, ref, "t-icref", "middle")
        self.text(x + w / 2, y + h + 16, val, "t-val", "middle")
        ends = {}
        for side, pins in (("l", left), ("r", right)):
            for i, p in enumerate(pins):
                if p is None: continue
                num, name = p[0], p[1]
                net = p[2] if len(p) > 2 else None
                nside = p[3] if len(p) > 3 else "pi"
                py = y + top + i * pitch
                if side == "l":
                    x0, x1 = x - stub, x
                    self.text(x1 - 4, py - 3, str(num), "t-pn", "end")
                    self.text(x + 6, py + 3.6, name, "t-pin")
                    ex = x0
                else:
                    x0, x1 = x + w, x + w + stub
                    self.text(x0 + 4, py - 3, str(num), "t-pn")
                    self.text(x + w - 6, py + 3.6, name, "t-pin", "end")
                    ex = x1
                self.line(x0, py, x1, py, "p")
                if net == "NC":
                    self.nc(ex, py)
                elif net:
                    self.flag(ex, py, net, "l" if side == "l" else "r", nside)
                ends[str(num) if str(num) not in ends else f"{num}{name}"] = (ex, py)
                ends[name] = (ex, py)
        return ends

    def svg(self, aria):
        W, H = self.w, self.h
        z = []
        z.append(f'<rect x="4" y="4" width="{W-8}" height="{H-8}" class="frame"/>')
        cols = max(2, round(W / 180))
        for i in range(cols):
            cx = 4 + (W - 8) * (i + 0.5) / cols
            z.append(f'<text x="{cx:.0f}" y="17" class="t-zone" text-anchor="middle">{i+1}</text>')
            if i: z.append(f'<line x1="{4+(W-8)*i/cols:.0f}" y1="4" x2="{4+(W-8)*i/cols:.0f}" y2="11" class="frame"/>')
        rows = max(2, round(H / 160))
        for j in range(rows):
            cy = 4 + (H - 8) * (j + 0.5) / rows
            z.append(f'<text x="13" y="{cy+4:.0f}" class="t-zone" text-anchor="middle">{"ABCDEFG"[j]}</text>')
            if j: z.append(f'<line x1="4" y1="{4+(H-8)*j/rows:.0f}" x2="11" y2="{4+(H-8)*j/rows:.0f}" class="frame"/>')
        z.append(f'<text x="{W-14}" y="{H-12}" class="t-zone" text-anchor="end">{html.escape(self.label)}</text>')
        return (f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{html.escape(aria)}" '
                f'xmlns="http://www.w3.org/2000/svg">{"".join(z)}{"".join(self.e)}</svg>')


# ================================================================= SHEET 1: overview
def sheet_overview():
    s = Sheet(1120, 470, "RADIO REMOTE HAT · REV A · SHEET 1/7")
    def block(x, y, w, h, title, sub, side):
        s.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3" class="blk blk-{side}"/>')
        s.text(x + 12, y + 22, title, "t-blk")
        for i, line in enumerate(sub):
            s.text(x + 12, y + 41 + i * 15, line, "t-blksub")
    def arrow(x1, y1, x2, y2, label, side, ly=None, la="middle"):
        s.add(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" class="arw arw-{side}" marker-end="url(#ah-{side})"/>')
        s.text((x1 + x2) / 2, (ly if ly is not None else y1 - 7), label, f"t-arw t-{side}", la)
    s.add('<defs>'
          '<marker id="ah-pi" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 Z" class="ah-pi"/></marker>'
          '<marker id="ah-rd" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 Z" class="ah-rd"/></marker>'
          '</defs>')
    block(40, 50, 200, 330, "J1 · Pi header", ["40-pin, 2×20 female", "GPIO17/27/22 out", "GPIO23 in", "I2S: GPIO18–21", "I2C1: GPIO2/3", "ID EEPROM: GPIO0/1", "5V0 in (back-power)", "3V3_PI out"], "pi")
    block(330, 40, 230, 96, "Control · U1 TCMT4100", ["4 opto channels: PTT, UP, DN out", "BUSY in (LED on radio side)"], "pi")
    block(330, 170, 230, 96, "Codec · U2 TLV320AIC3104", ["I2S slave, I2C 0x18", "HP out diff → TX · MIC1L diff ← RX"], "pi")
    block(330, 300, 230, 80, "U6 HAT ID EEPROM", ["AT24C32D @ 0x50 on ID_SD/ID_SC", "auto-loads the codec overlay"], "pi")
    block(700, 40, 170, 96, "Filters", ["ferrite + 100 pF", "on every radio line"], "rd")
    block(700, 170, 170, 96, "Audio isolation", ["T1 TX: −40 dB pad", "T2 RX: −26 dB diff pad"], "rd")
    block(930, 50, 160, 330, "Radio connectors", ["J2 RJ45 → radio mic", "J3 RJ45 ← original mic", "J4/J5 patch headers", "J7 speaker in", "J8 busy in", "J9 accessory PTT", "", "J6 13.8 V in"], "rd")
    block(330, 410, 520, 44, "Power · J6 13.8 V → F1, D1, D3 → U3 TPS54360B → 5V0 → U4 → 3V3A → U5 → 1V8", [], "pwr")
    # barrier
    s.add('<line x1="630" y1="30" x2="630" y2="395" class="barrier"/>')
    s.text(622, 392, "GND (Pi side)", "t-pi t-small", "end")
    s.text(638, 392, "RGND (radio side)", "t-rd t-small")
    s.text(630, 26, "ISOLATION BARRIER", "t-barrier", "middle")
    arrow(240, 88, 330, 88, "GPIO17/27/22", "pi")
    arrow(330, 118, 240, 118, "GPIO23 busy", "pi", 132)
    arrow(560, 88, 700, 88, "PTT · UP · DN", "rd")
    arrow(700, 118, 560, 118, "BUSY± (LED)", "rd", 132)
    arrow(240, 208, 330, 208, "I2S + I2C1", "pi")
    arrow(560, 205, 700, 205, "HP_P/N → T1", "rd")
    arrow(700, 236, 560, 236, "T2 → RX_P/N", "rd", 250)
    arrow(870, 88, 930, 88, "", "rd")
    arrow(930, 118, 870, 118, "", "rd")
    arrow(870, 205, 930, 205, "R_MIC", "rd")
    arrow(930, 236, 870, 236, "R_SP±", "rd", 250)
    arrow(240, 340, 330, 340, "ID_SD/SC", "pi")
    arrow(1010, 380, 850, 432, "13.8 V", "rd", 418)
    arrow(330, 432, 240, 360, "5V0 → Pi pins 2/4", "pi", 418, "end")
    return s


# ================================================================= SHEET 2: power
def sheet_power():
    s = Sheet(1120, 580, "RADIO REMOTE HAT · REV A · SHEET 3/7 · POWER")
    s.text(40, 52, "13.8 V INPUT PROTECTION + 5 V BUCK", "t-sec")
    e = s.ic(60, 120, 70, "J6", "KF350-3.5-2P", [], [(1, "+13.8V"), (2, "GND")], top=30)
    s.poly([(160, 170), (180, 170), (180, 190)]); s.gnd(180, 190)
    s.two("fuse", 160, 150, "r", ref="F1", val="1.5 A", lab="above")
    s.two("diode", 220, 150, "r", ref="D1", val="SS56", lab="above")
    s.line(280, 150, 570, 150)
    s.text(500, 143, "VIN", "t-wire", "middle")
    for x in (300, 370, 440): s.dot(x, 150)
    s.two("tvs", 300, 210, "u"); s.gnd(300, 210)
    s.two("cap", 370, 150, "d"); s.gnd(370, 210)
    s.two("ecap", 440, 150, "d"); s.gnd(440, 210)
    for x, a, b in ((300, "D3", "SMBJ24A"), (370, "C16–C18", "2.2µF ×3"), (440, "C19", "100µF 50V")):
        s.text(x, 246, a, "t-ref", "middle"); s.text(x, 259, b, "t-val", "middle")
    e = s.ic(600, 110, 150, "U3", "TPS54360BDDAR",
             [(2, "VIN"), (3, "EN", "NC"), (4, "RT/CLK"), (6, "COMP")],
             [(1, "BOOT"), (8, "SW"), (5, "FB", "FB"), (7, "GND"), (9, "PAD")], top=40, rows=5)
    s.text(556, 166, "open = on", "t-note", "end")
    # RT
    s.poly([(570, 190), (520, 190), (520, 290)])
    s.two("res", 520, 290, "d", ref="R28", val="150 k", lab="left"); s.gnd(520, 350)
    s.text(512, 375, "≈ 640 kHz", "t-note", "end")
    # COMP
    s.poly([(570, 210), (560, 210), (560, 290)])
    s.add('<rect x="548" y="290" width="124" height="52" class="tbd"/>')
    s.text(610, 309, "Rc + Cc ∥ Cf", "t-ref", "middle")
    s.text(610, 324, "R29 · C28 · C29", "t-val", "middle")
    s.text(610, 337, "values TBD", "t-tbd", "middle")
    s.line(610, 342, 610, 352); s.gnd(610, 352)
    # GND / PAD
    s.poly([(780, 210), (790, 210), (790, 230), (780, 230)]); s.gnd(790, 230)
    # BOOT / SW
    s.poly([(780, 150), (795, 150), (795, 110)])
    s.two("cap", 795, 110, "r", ref="C20", val="100 nF", lab="above")
    s.poly([(855, 110), (855, 170)])
    s.line(780, 170, 900, 170)
    s.text(818, 164, "SW", "t-wire", "middle")
    s.dot(855, 170); s.dot(880, 170)
    s.two("diode", 880, 230, "u", ref="D2", val="SS56", lab="left"); s.gnd(880, 230)
    s.two("ind", 900, 170, "r", ref="L1", val="6.8 µH 5 A", lab="split")
    s.line(960, 170, 1040, 170); s.dot(990, 170)
    s.two("cap", 990, 170, "d"); s.gnd(990, 230)
    s.text(990, 266, "C21–C23", "t-ref", "middle"); s.text(990, 279, "22 µF 25 V ×3", "t-val", "middle")
    s.flag(1040, 170, "5V0", "r")
    s.text(1050, 200, "→ J1 pins 2, 4", "t-note")
    # feedback divider
    s.flag(720, 360, "5V0", "l")
    s.two("res", 720, 360, "r", ref="R25", val="51 k", lab="split")
    s.two("res", 780, 360, "r", ref="R26", val="2.2 k", lab="split")
    s.line(840, 360, 880, 360); s.dot(860, 360)
    s.poly([(860, 360), (860, 330)]); s.flag(860, 330, "FB", "r")
    s.two("res", 880, 360, "r", ref="R27", val="10 k", lab="split")
    s.poly([(940, 360), (955, 360), (955, 370)]); s.gnd(955, 370)
    s.text(720, 405, "VOUT = 0.8 V × (1 + (R25 + R26) / R27) = 5.06 V", "t-note")
    s.text(720, 420, "No back-feed diode needed: the TPS54360 can't sink current.", "t-note")
    # rails
    s.text(40, 412, "CODEC RAILS + POWER LED", "t-sec")
    s.flag(100, 450, "5V0", "l")
    s.line(100, 450, 190, 450); s.dot(140, 450)
    s.two("cap", 140, 450, "d"); s.gnd(140, 510)
    s.text(140, 545, "C24 1 µF", "t-val", "middle")
    s.ic(220, 420, 120, "U4", "TLV76133DCYR", [(3, "IN"), (1, "GND")], [(2, "OUT")], top=30, rows=2)
    s.poly([(190, 470), (180, 470), (180, 480)]); s.gnd(180, 480)
    s.line(370, 450, 490, 450)
    for x in (400, 450, 475): s.dot(x, 450)
    s.two("cap", 400, 450, "d"); s.gnd(400, 510); s.text(400, 545, "C25 10 µF", "t-val", "middle")
    s.two("cap", 450, 450, "d"); s.gnd(450, 510); s.text(450, 558, "C26 1 µF", "t-val", "middle")
    s.poly([(425, 450), (425, 420)]); s.dot(425, 450); s.flag(425, 420, "3V3A", "r")
    s.ic(520, 420, 120, "U5", "TPS7A2018PDBVR", [(1, "IN"), (3, "EN"), (2, "GND")], [(5, "OUT"), (4, "NC", "NC")], top=30, rows=3)
    s.poly([(490, 470), (475, 470), (475, 450)])
    s.gnd(490, 490)
    s.line(670, 450, 760, 450); s.dot(710, 450)
    s.two("cap", 710, 450, "d"); s.gnd(710, 510); s.text(710, 545, "C27 1 µF", "t-val", "middle")
    s.flag(760, 450, "1V8", "r")
    # LED1 moved below divider area right
    s.flag(860, 500, "5V0", "l")
    s.two("res", 860, 500, "r", ref="R7", val="1 k", lab="above")
    s.two("led", 920, 500, "r")
    s.text(950, 528, "LED1 red", "t-ref", "middle")
    s.poly([(980, 500), (995, 500), (995, 508)]); s.gnd(995, 508)
    return s


# ================================================================= SHEET 3: control
def sheet_control():
    s = Sheet(1120, 560, "RADIO REMOTE HAT · REV A · SHEET 4/7 · CONTROL")
    s.text(40, 52, "OPTO-ISOLATED PTT · UP · DOWN · BUSY", "t-sec")
    P = 40
    rows = [120 + P * i for i in range(8)]
    s.ic(420, 80, 200, "U1", "TCMT4100 (quad opto)",
         [(1, "A1"), (2, "K1"), (3, "A2"), (4, "K2"), (5, "A3"), (6, "K3"), (7, "A4"), (8, "K4")],
         [(16, "C1"), (15, "E1"), (14, "C2"), (13, "E2"), (12, "C3"), (11, "E3"), (10, "C4"), (9, "E4")],
         pitch=P, top=40)
    for i, lab in enumerate(("ch 1 · PTT", "ch 2 · UP", "ch 3 · DOWN", "ch 4 · BUSY")):
        s.text(444, 143 + 80 * i, lab, "t-chan")
    s.add('<line x1="520" y1="96" x2="520" y2="404" class="barrier"/>')
    # LED side, channels 1-3 (Pi side)
    for i, (gp, r) in enumerate((("GPIO17", "R1"), ("GPIO27", "R2"), ("GPIO22", "R3"))):
        ya, yk = rows[2 * i], rows[2 * i + 1]
        s.flag(300, ya, gp, "l")
        s.two("res", 300, ya, "r", ref=r, val="470 Ω", lab="above")
        s.line(360, ya, 390, ya)
        s.poly([(390, yk), (372, yk)]); s.gnd(372, yk)
    # BUSY LED side (radio side)
    ya, yk = rows[6], rows[7]
    s.flag(300, ya, "BUSY+", "l", "rd")
    s.two("res", 300, ya, "r", ref="R4", val="2.2 k", lab="above", side="rd")
    s.line(360, ya, 390, ya); s.dot(375, ya)
    s.line(390, yk, 300, yk); s.dot(375, yk)
    s.flag(300, yk, "BUSY−", "l", "rd")
    s.two("diode", 375, yk, "u", L=40, side="rd")
    s.text(360, ya + 24, "D4 1N4148WS", "t-ref", "end")
    # transistor side
    xs = 650
    def radio_line(y, net, rref, fb, cref):
        if rref:
            s.line(xs, y, 660, y)
            s.two("res", 660, y, "r", ref=rref, val="0 Ω", lab="above")
        else:
            s.line(xs, y, 720, y)
        s.line(720, y, 730, y)
        s.two("fb", 730, y, "r", ref=fb, lab="above")
        s.line(790, y, 840, y); s.dot(810, y)
        s.two("cap", 810, y, "d", L=50); s.rgnd(810, y + 50)
        s.text(824, y + 28, cref, "t-ref"); s.text(824, y + 40, "100 pF", "t-val")
        s.flag(840, y, net, "r", "rd")
    radio_line(rows[0], "R_PTT", None, "FB1", "C4")
    radio_line(rows[2], "R_UP", "R5", "FB2", "C5")
    radio_line(rows[4], "R_DN", "R6", "FB3", "C6")
    for y in (rows[1], rows[3], rows[5]):
        s.poly([(xs, y), (668, y)]); s.rgnd(668, y)
    s.flag(xs, rows[6], "GPIO23", "r")
    s.poly([(xs, rows[7]), (668, rows[7])]); s.gnd(668, rows[7])
    s.text(915, rows[2] - 16, "R5/R6: fit 0 Ω for separate", "t-note")
    s.text(915, rows[2] - 3, "UP/DN lines, or the measured", "t-note")
    s.text(915, rows[2] + 10, "ladder values for a shared line", "t-note")
    s.text(700, rows[6] + 28, "Pi pull-up on GPIO23: config busy_pull_up: true", "t-note")
    s.text(40, 470, "PTT INDICATOR", "t-sec")
    s.flag(140, 510, "GPIO17", "l")
    s.two("res", 140, 510, "r", ref="R8", val="1 k", lab="above")
    s.two("led", 200, 510, "r")
    s.text(230, 536, "LED2 red", "t-ref", "middle")
    s.poly([(260, 510), (275, 510), (275, 518)]); s.gnd(275, 518)
    s.text(310, 514, "LED2 ≈ 1.4 mA; GPIO17 sources ≈ 5.5 mA in total with the opto LED", "t-note")
    return s


# ================================================================= SHEET 4: radio connectors
def sheet_connectors():
    s = Sheet(1120, 630, "RADIO REMOTE HAT · REV A · SHEET 5/7 · RADIO CONNECTORS")
    s.text(40, 52, "MIC CONNECTORS (WIRED PIN-FOR-PIN) + PATCH HEADERS", "t-sec")
    rj = [(i, f"{i}", f"RJ{i}", "rd") for i in range(1, 9)]
    s.ic(80, 90, 90, "J2", "RJ45 → radio mic socket", [], rj, pitch=20, top=24)
    s.ic(80, 336, 90, "J3", "RJ45 ← original mic", [], rj, pitch=20, top=24)
    # J4 header with flags on left, pin tips on right
    s.ic(430, 90, 70, "J4", "1×8 · RJ45 pins", [(i, f"RJ{i}", f"RJ{i}", "rd") for i in range(1, 9)], [], pitch=24, top=24, rows=8)
    tips4 = [(500, 114 + 24 * i) for i in range(8)]
    for x, y in tips4: s.add(f'<circle cx="{x}" cy="{y}" r="4" class="tip"/>')
    fn = [("R_PTT"), ("R_UP"), ("R_DN"), ("R_MIC"), ("RGND"), ("RGND"), ("RGND"), ("RGND")]
    s.ic(680, 90, 70, "J5", "1×8 · functions", [], [(i + 1, n, n, "rd") for i, n in enumerate(fn)], pitch=24, top=24, rows=8)
    tips5 = [(680, 114 + 24 * i) for i in range(8)]
    for x, y in tips5: s.add(f'<circle cx="{x}" cy="{y}" r="4" class="tip"/>')
    # example patch TK-880: RJ4->PTT, RJ6->MIC, RJ5->RGND (ME), RJ3->RGND (E)
    for a, b in ((3, 0), (5, 3), (4, 4), (2, 5)):
        (x1, y1), (x2, y2) = tips4[a], tips5[b]
        s.add(f'<path d="M{x1+4} {y1} C{x1+80} {y1} {x2-80} {y2} {x2-4} {y2}" class="patch"/>')
    s.text(590, 346, "Dupont jumpers, one set per radio", "t-note", "middle")
    s.text(590, 360, "shown: Kenwood TK-880 mic jack", "t-note", "middle")
    s.text(590, 374, "4 PTT → R_PTT · 6 MIC → R_MIC", "t-note", "middle")
    s.text(590, 388, "5 ME, 3 E → RGND", "t-note", "middle")
    s.text(590, 402, "(confirm numbering with a patch lead)", "t-note", "middle")
    # terminals
    s.text(860, 52, "SCREW TERMINALS", "t-sec")
    s.ic(880, 90, 70, "J7", "speaker in", [], [(1, "SP+", "R_SPP", "rd"), (2, "SP−", "R_SPN", "rd")], top=24)
    s.ic(880, 200, 70, "J8", "busy in", [], [(1, "B+", "BUSY+", "rd"), (2, "B−", "BUSY−", "rd")], top=24)
    s.ic(880, 310, 70, "J9", "accessory", [], [(1, "PTT", "R_PTT", "rd"), (2, "GND", "RGND", "rd")], top=24)
    s.text(860, 430, "J6 (13.8 V in) is on sheet 3.", "t-note")
    s.text(860, 445, "J8 wiring: active-high busy → B+, B− to", "t-note")
    s.text(860, 460, "radio GND. Open-collector busy → B+ to", "t-note")
    s.text(860, 475, "radio +V, B− to the busy output.", "t-note")
    # JP1
    s.text(40, 570, "GROUND LINK", "t-sec")
    s.rgnd(80, 597)
    s.text(96, 614, "RGND", "t-rd t-small")
    s.poly([(80, 597), (80, 588), (150, 588)])
    s.two("jumper", 150, 588, "r", ref="JP1", val="open", lab="above")
    s.poly([(210, 588), (240, 588), (240, 597)]); s.gnd(240, 597)
    s.text(280, 592, "Solder jumper, open by default: keeps radio ground off the Pi ground.", "t-note")
    s.text(280, 607, "The radio joins the two through its own supply lead. Close only for testing.", "t-note")
    return s


# ================================================================= SHEET 5: codec
def sheet_codec():
    s = Sheet(1120, 760, "RADIO REMOTE HAT · REV A · SHEET 6/7 · CODEC")
    s.text(40, 52, "I2S CODEC · TLV320AIC3104 (RHB, QFN-32)", "t-sec")
    left = [(1, "MCLK"), (2, "BCLK"), (3, "WCLK"), (4, "DIN"), (5, "DOUT"),
            (8, "SCL", "I2C_SCL"), (9, "SDA", "I2C_SDA"), (7, "IOVDD", "3V3_PI"), (32, "DVDD", "1V8"),
            (31, "RESET"), (6, "DVSS")]
    right = [(25, "AVDD", "3V3A"), (18, "DRVDD", "3V3A"), (24, "DRVDD", "3V3A"),
             (10, "MIC1LP/LINE1LP", "RX_P"), (11, "MIC1LM/LINE1LM", "RX_N"),
             (19, "HPLOUT", "HP_P"), (20, "HPLCOM", "HP_N"),
             (15, "MICBIAS", "NC"), (12, "MIC1RP", "NC"), (13, "MIC1RM", "NC"), (14, "MIC2L", "NC"),
             (16, "MIC2R", "NC"), (23, "HPROUT", "NC"), (22, "HPRCOM", "NC"), (27, "LEFT_LOP", "NC"),
             (28, "LEFT_LOM", "NC"), (29, "RIGHT_LOP", "NC"), (30, "RIGHT_LOM", "NC"),
             (17, "AVSS1"), (26, "AVSS2"), (21, "DRVSS"), (33, "EP")]
    e = s.ic(440, 80, 240, "U2", "TLV320AIC3104IRHBR", left, right, pitch=20, top=24)
    y0 = 104
    nets = ["OSC_12M", "I2S_BCLK", "I2S_LRCLK", "I2S_DIN", "I2S_DOUT"]
    refs = ["R16", "R17", "R18", "R19", "R20"]
    for i in range(5):
        y = y0 + 20 * i
        s.line(410, y, 400, y)
        s.two("res", 340, y, "r")
        s.line(400, y, 400, y)
        s.flag(340, y, nets[i], "l")
        s.text(330 - len(nets[i]) * CW - 30, y + 4, refs[i], "t-ref", "end")
    s.text(370, y0 - 16, "33 Ω series", "t-val", "middle")
    # DVSS gnd
    yd = y0 + 20 * 10
    s.poly([(410, yd), (398, yd)]); s.gnd(398, yd)
    # RESET with pulldown
    yr = y0 + 20 * 9
    s.line(410, yr, 340, yr); s.dot(372, yr)
    s.flag(340, yr, "CODEC_RST", "l")
    s.two("res", 372, yr, "d", ref="R21", val="10 k", lab="left"); s.gnd(372, yr + 60)
    s.text(358, yr + 92, "held low until the driver releases it", "t-note", "end")
    # ground pins on right
    yg = y0 + 20 * 18
    s.poly([(710, yg), (722, yg), (722, yg + 60), (710, yg + 60)])
    for k in range(1, 3): s.poly([(710, yg + 20 * k), (722, yg + 20 * k)]); s.dot(722, yg + 20 * k)
    s.gnd(722, yg + 60)
    s.text(732, yg + 34, "pad → DRVSS / ground plane", "t-note")
    # oscillator
    s.text(40, 412, "MASTER CLOCK", "t-sec")
    s.ic(110, 450, 90, "Y1", "HSO321S 12 MHz", [(4, "VDD"), (1, "OE")], [(3, "OUT", "OSC_12M"), (2, "GND")], top=24)
    s.poly([(80, 474), (70, 474), (70, 494), (80, 494)]); s.dot(70, 474)
    s.poly([(70, 474), (70, 440)]); s.pwr(70, 440, "3V3_PI")
    s.poly([(230, 494), (240, 494), (240, 502)]); s.gnd(240, 502)
    s.text(40, 560, "Pin numbers assume the usual 3225 oscillator layout", "t-tbd")
    s.text(40, 574, "(1 OE, 2 GND, 3 OUT, 4 VDD): confirm before layout.", "t-tbd")
    # decoupling bank
    s.text(40, 618, "DECOUPLING · place each cap at its pin", "t-sec")
    caps = [("C8", "100 nF", "3V3A", "AVDD 25"), ("C9", "100 nF", "3V3A", "DRVDD 18"), ("C10", "100 nF", "3V3A", "DRVDD 24"),
            ("C13", "10 µF", "3V3A", "bulk at U2"), ("C11", "100 nF", "3V3_PI", "IOVDD 7"), ("C12", "100 nF", "1V8", "DVDD 32"),
            ("C14", "100 nF", "3V3_PI", "Y1 VDD")]
    for i, (r, v, n, pin) in enumerate(caps):
        x = 80 + i * 140
        s.pwr(x, 660, n)
        s.two("cap", x, 660, "d", L=50); s.gnd(x, 710)
        s.text(x + 12, 682, r, "t-ref"); s.text(x + 12, 694, v, "t-val")
        s.text(x, 748, pin, "t-note", "middle")
    return s


# ================================================================= SHEET 6: audio isolation + EEPROM
def xfmr(s, x, y, ref, val):
    # left winding pins 1 (top) / 3 (bottom) at x-30; right 6 / 4 at x+90
    s.add(f'<rect x="{x}" y="{y-14}" width="60" height="88" class="body"/>')
    a = "".join(" a6,6 0 0 1 0,12" for _ in range(4))
    s.add(f'<path d="M{x+16} {y+6}{a}" class="p"/>')
    b = "".join(" a6,6 0 0 0 0,12" for _ in range(4))
    s.add(f'<path d="M{x+44} {y+6}{b}" class="p"/>')
    s.add(f'<path d="M{x+27} {y+2} V{y+58} M{x+33} {y+2} V{y+58}" class="p"/>')
    s.add(f'<circle cx="{x+9}" cy="{y+4}" r="2.5" class="pdot"/><circle cx="{x+51}" cy="{y+4}" r="2.5" class="pdot"/>')
    for (px, py, n, anc) in ((x - 30, y, "1", "end"), (x - 30, y + 60, "3", "end"), (x + 90, y, "6", "start"), (x + 90, y + 60, "4", "start")):
        if anc == "end":
            s.line(px, py, x, py, "p"); s.text(x - 4, py - 4, n, "t-pn", "end")
        else:
            s.line(x + 60, py, px, py, "p"); s.text(x + 64, py - 4, n, "t-pn")
    s.poly([(x + 16, y + 6), (x + 16, y), (x, y)], "p"); s.poly([(x + 16, y + 54), (x + 16, y + 60), (x, y + 60)], "p")
    s.poly([(x + 44, y + 6), (x + 44, y), (x + 60, y)], "p"); s.poly([(x + 44, y + 54), (x + 44, y + 60), (x + 60, y + 60)], "p")
    s.text(x + 30, y - 22, ref, "t-icref", "middle")
    s.text(x + 30, y + 92, val, "t-val", "middle")
    s.text(x + 30, y + 105, "pins 2, 5 NC", "t-note", "middle")


def sheet_audio():
    s = Sheet(1120, 720, "RADIO REMOTE HAT · REV A · SHEET 7/7 · AUDIO ISOLATION + ID EEPROM")
    s.text(40, 52, "TX: CODEC → RADIO MIC", "t-sec")
    s.add('<line x1="440" y1="104" x2="440" y2="196" class="barrier"/>')
    s.add('<line x1="860" y1="314" x2="860" y2="406" class="barrier"/>')
    s.text(852, 300, "GND", "t-pi t-small", "end"); s.text(868, 300, "RGND", "t-rd t-small")
    s.text(432, 88, "GND", "t-pi t-small", "end"); s.text(448, 88, "RGND", "t-rd t-small")
    s.flag(260, 120, "HP_P", "l"); s.two("res", 260, 120, "r", ref="R14", val="33 Ω", lab="above"); s.line(320, 120, 380, 120)
    s.flag(260, 180, "HP_N", "l"); s.two("res", 260, 180, "r", ref="R15", val="33 Ω", lab="above"); s.line(320, 180, 380, 180)
    xfmr(s, 410, 120, "T1", "SM-LP-5001E")
    s.line(500, 120, 530, 120)
    s.two("res", 530, 120, "r", ref="R9", val="10 k", lab="above", side="rd")
    s.line(590, 120, 640, 120); s.dot(620, 120)
    s.two("res", 620, 120, "d", L=60, ref="R10", val="100 Ω", lab="right", side="rd")
    s.line(500, 180, 620, 180); s.dot(620, 180)
    s.poly([(620, 180), (620, 196)]); s.rgnd(620, 196)
    s.two("cap", 640, 120, "r", ref="C1", val="1 µF X7R", lab="above", side="rd")
    s.two("fb", 700, 120, "r", ref="FB4", lab="above")
    s.line(760, 120, 820, 120); s.dot(790, 120)
    s.two("cap", 790, 120, "d", L=50); s.rgnd(790, 170)
    s.text(804, 148, "C7", "t-ref"); s.text(804, 160, "100 pF", "t-val")
    s.flag(820, 120, "R_MIC", "r", "rd")
    s.text(530, 230, "Pad ≈ −40 dB (R10 / (R9 + R10)). Fine level in software: alsamixer HP volume.", "t-note")
    s.text(530, 244, "HPLCOM is set to “differential of HPLOUT”, so T1 sees no DC.", "t-note")

    s.text(40, 292, "RX: RADIO SPEAKER → CODEC", "t-sec")
    s.flag(1000, 330, "R_SPP", "r", "rd"); s.flag(1000, 390, "R_SPN", "r", "rd")
    s.two("fb", 1000, 330, "l", ref="FB5", lab="above"); s.two("fb", 1000, 390, "l", ref="FB6", lab="above")
    s.line(940, 330, 920, 330); s.line(940, 390, 920, 390)
    s.text(1090, 486, "T2 primary floats: safe on BTL speaker amps", "t-note", "end")
    xfmr(s, 830, 330, "T2", "SM-LP-5001E")
    # left: secondary pins at x=800 (1 top, 3 bottom) — mirrored placement: use T2 left side for codec
    s.line(800, 330, 760, 330); s.two("res", 760, 330, "l", ref="R11", val="10 k", lab="above")
    s.line(800, 390, 760, 390); s.two("res", 760, 390, "l", ref="R12", val="10 k", lab="above")
    s.line(700, 330, 640, 330); s.line(700, 390, 640, 390)
    s.dot(670, 330); s.dot(670, 390)
    s.two("res", 670, 330, "d", L=60, ref="R13", val="1 k", lab="right")
    s.two("cap", 640, 330, "l", ref="C2", val="1 µF", lab="above")
    s.two("cap", 640, 390, "l", ref="C3", val="1 µF", lab="above")
    s.flag(580, 330, "RX_P", "l"); s.flag(580, 390, "RX_N", "l")
    s.text(450, 470, "Pad ≈ −26 dB differential. For a line-level source (e.g. TK-880 DEO, 250 mV) raise R13 to 10 k.", "t-note")

    # EEPROM
    s.text(40, 520, "HAT ID EEPROM", "t-sec")
    s.ic(140, 540, 100, "U6", "AT24C32D-SSHM-T",
         [(8, "VCC", "3V3_PI"), (1, "A0"), (2, "A1"), (3, "A2"), (4, "GND")],
         [(5, "SDA", "ID_SD"), (6, "SCL", "ID_SC"), (7, "WP", "EE_WP")], pitch=18, top=16)
    s.poly([(110, 574), (100, 574), (100, 628), (110, 628)])
    for yy in (592, 610): s.poly([(110, yy), (100, yy)]); s.dot(100, yy)
    s.gnd(100, 628)
    for i, (r, v, n) in enumerate((("R22", "3.9 k", "ID_SD"), ("R23", "3.9 k", "ID_SC"), ("R24", "1 k", "EE_WP"))):
        x = 470 + i * 150
        s.pwr(x, 550, "3V3_PI")
        s.two("res", x, 550, "d", L=50, ref=r, val=v, lab="right")
        s.flag(x, 600, n, "r")
    s.add('<circle cx="924" cy="575" r="6" class="tip"/>')
    s.text(938, 579, "TP1", "t-ref")
    s.flag(900, 575, "EE_WP", "l")
    s.line(900, 575, 918, 575)
    s.poly([(900, 575), (900, 590)])
    s.two("jumper", 900, 590, "d", L=40, ref="JP2", val="close = write", lab="right")
    s.gnd(900, 630)
    s.text(40, 700, "C15 100 nF at VCC.  ID_SD = GPIO0 (pin 27), ID_SC = GPIO1 (pin 28): nothing else may connect to them.", "t-note")
    return s


# ================================================================= data tables
HEADER = [
    (1, "3V3", "3V3_PI", "U2 IOVDD, Y1, U6, pull-ups"), (2, "5V", "5V0", "power in (from U3)"),
    (3, "GPIO2 SDA1", "I2C_SDA", "U2 SDA"), (4, "5V", "5V0", "power in (from U3)"),
    (5, "GPIO3 SCL1", "I2C_SCL", "U2 SCL"), (6, "GND", "GND", ""),
    (9, "GND", "GND", ""), (11, "GPIO17", "GPIO17", "R1 → U1 ch 1 (PTT), R8 → LED2"),
    (12, "GPIO18 PCM_CLK", "I2S_BCLK", "R17 → U2 BCLK"), (13, "GPIO27", "GPIO27", "R2 → U1 ch 2 (UP)"),
    (14, "GND", "GND", ""), (15, "GPIO22", "GPIO22", "R3 → U1 ch 3 (DOWN)"),
    (16, "GPIO23", "GPIO23", "U1 pin 10 (BUSY in)"), (17, "3V3", "3V3_PI", ""),
    (20, "GND", "GND", ""), (25, "GND", "GND", ""),
    (27, "GPIO0 ID_SD", "ID_SD", "U6 SDA, R22"), (28, "GPIO1 ID_SC", "ID_SC", "U6 SCL, R23"),
    (30, "GND", "GND", ""), (34, "GND", "GND", ""),
    (35, "GPIO19 PCM_FS", "I2S_LRCLK", "R18 → U2 WCLK"), (37, "GPIO26", "CODEC_RST", "U2 RESET, R21"),
    (38, "GPIO20 PCM_DIN", "I2S_DOUT", "R20 ← U2 DOUT"), (39, "GND", "GND", ""),
    (40, "GPIO21 PCM_DOUT", "I2S_DIN", "R19 → U2 DIN"),
]

NETS = [
    ("pwr", "13V8_IN", "J6.1, F1.1"),
    ("pwr", "VIN_F", "F1.2, D1 A"),
    ("pwr", "VIN", "D1 K, D3 K, C16–C18, C19 +, U3.2 VIN"),
    ("pwr", "SW", "U3.8, C20.2, D2 K, L1.1"),
    ("pwr", "BOOT", "U3.1, C20.1"),
    ("pwr", "RT", "U3.4, R28.1"),
    ("pwr", "COMP", "U3.6, R29 / C29 (TBD)"),
    ("pwr", "FB", "U3.5, R26.2, R27.1"),
    ("pwr", "FB_MID", "R25.2, R26.1"),
    ("pwr", "5V0", "L1.2, C21–C23, R25.1, J1.2, J1.4, U4.3 IN, C24, R7"),
    ("pwr", "3V3A", "U4.2 OUT, C25, U5.1 IN, U5.3 EN, C26, U2.25, U2.18, U2.24, C8, C9, C10, C13"),
    ("pwr", "1V8", "U5.5 OUT, C27, U2.32 DVDD, C12"),
    ("pi", "3V3_PI", "J1.1, J1.17, U2.7 IOVDD, C11, Y1.4, Y1.1, C14, U6.8, C15, R22, R23, R24"),
    ("pi", "GND", "J1 GND pins, J6.2, C16–C27, D2 A, D3 A, R27, R28, U3.7/9, U4.1, U5.2, U1.2/4/6/9, U2.6/17/21/26/33, Y1.2, U6.1–4, R21, LED1 K, LED2 K, JP1.2, JP2.2"),
    ("pi", "GPIO17", "J1.11, R1.1, R8.1"),
    ("pi", "GPIO27", "J1.13, R2.1"),
    ("pi", "GPIO22", "J1.15, R3.1"),
    ("pi", "GPIO23", "J1.16, U1.10 (C4)"),
    ("pi", "I2C_SDA", "J1.3, U2.9"),
    ("pi", "I2C_SCL", "J1.5, U2.8"),
    ("pi", "I2S_BCLK", "J1.12, R17 → U2.2"),
    ("pi", "I2S_LRCLK", "J1.35, R18 → U2.3"),
    ("pi", "I2S_DIN", "J1.40, R19 → U2.4 (codec DIN)"),
    ("pi", "I2S_DOUT", "J1.38, R20 ← U2.5 (codec DOUT)"),
    ("pi", "OSC_12M", "Y1.3, R16 → U2.1 MCLK"),
    ("pi", "CODEC_RST", "J1.37, U2.31, R21.1"),
    ("pi", "ID_SD", "J1.27, U6.5, R22.2"),
    ("pi", "ID_SC", "J1.28, U6.6, R23.2"),
    ("pi", "EE_WP", "U6.7, R24.2, TP1, JP2.1"),
    ("pi", "HP_P / HP_N", "U2.19 / U2.20 → R14 / R15 → T1.1 / T1.3"),
    ("pi", "RX_P / RX_N", "C2 / C3 → U2.10 / U2.11"),
    ("rd", "RGND", "U1.11/13/15, C4–C7, R10.2, T1.4, J5.5–8, J9.2, JP1.1"),
    ("rd", "R_PTT", "FB1.2, C4, J5.1, J9.1"),
    ("rd", "R_UP", "FB2.2, C5, J5.2"),
    ("rd", "R_DN", "FB3.2, C6, J5.3"),
    ("rd", "R_MIC", "FB4.2, C7, J5.4"),
    ("rd", "R_SPP / R_SPN", "J7.1 / J7.2 → FB5 / FB6 → T2.6 / T2.4"),
    ("rd", "BUSY+", "J8.1, R4.1"),
    ("rd", "BUSY−", "J8.2, U1.8 (K4), D4 A"),
    ("rd", "RJ1 … RJ8", "J2.n, J3.n, J4.n (pin for pin)"),
]

PARTS = [
    ("U1", "TCMT4100 quad opto, SOP-16", "C2938669", "Ext"),
    ("U2", "TLV320AIC3104IRHBR codec, QFN-32", "C181753", "Ext"),
    ("U3", "TPS54360BDDAR buck, SOIC-8-EP", "C524806", "Ext"),
    ("U4", "TLV76133DCYR 3.3 V LDO, SOT-223", "C7527500", "Ext"),
    ("U5", "TPS7A2018PDBVR 1.8 V LDO, SOT-23-5", "C963430", "Ext"),
    ("U6", "AT24C32D-SSHM-T EEPROM, SOIC-8", "C60583", "Ext"),
    ("Y1", "HSO321S 12 MHz 3.3 V oscillator, 3225", "C160457", "Ext"),
    ("T1, T2", "SM-LP-5001E 600:600 Ω transformer", "C840532", "Ext"),
    ("L1", "FXL0630-6R8-M 6.8 µH 5 A", "C167221", "Ext"),
    ("D1, D2", "SS56 Schottky 60 V 5 A, SMA", "C65009", "Ext"),
    ("D3", "SMBJ24A TVS, SMB", "C123819", "Ext"),
    ("D4", "1N4148WS, SOD-323", "C2128", "Basic"),
    ("F1", "1812L150/33GR PTC 1.5 A 33 V", "C18198333", "Ext"),
    ("LED1, LED2", "KT-0603R red LED, 0603", "C2286", "Basic"),
    ("FB1–FB6", "GZ1608D601TF ferrite 600 Ω, 0603", "C1002", "Basic"),
    ("R1–R3", "470 Ω 0603", "C23179", "Basic"),
    ("R4, R26", "2.2 kΩ 0603", "C4190", "Basic"),
    ("R5, R6", "0 Ω 0603 (or ladder values)", "C21189", "Basic"),
    ("R7, R8, R13, R24", "1 kΩ 0603", "C21190", "Basic"),
    ("R9, R11, R12, R21, R27", "10 kΩ 0603", "C25804", "Basic"),
    ("R10", "100 Ω 0603", "C22775", "Basic"),
    ("R14–R20", "33 Ω 0603", "C23140", "Basic"),
    ("R22, R23", "3.9 kΩ 0603", "C23018", "Basic"),
    ("R25", "51 kΩ 0603", "C23196", "Basic"),
    ("R28", "150 kΩ 0603", "C22807", "Basic"),
    ("R29, C28, C29", "compensation, values TBD", "—", "Basic"),
    ("C1", "1 µF 50 V X7R 0805", "C28323", "Basic"),
    ("C2, C3, C24, C26, C27", "1 µF 50 V X5R 0603", "C15849", "Basic"),
    ("C4–C7", "100 pF C0G 0603", "C14858", "Basic"),
    ("C8–C12, C14, C15, C20", "100 nF 50 V X7R 0603", "C14663", "Basic"),
    ("C13, C25", "10 µF 25 V X5R 0805", "C15850", "Basic"),
    ("C16–C18", "2.2 µF 50 V X7R 1206", "C50254", "Basic"),
    ("C19", "100 µF 50 V electrolytic 8×10.2", "C72480", "Ext"),
    ("C21–C23", "22 µF 25 V X5R 1206", "C12891", "Basic"),
    ("J1", "2×20 female header 2.54 mm", "C2977589", "Ext · THT"),
    ("J2, J3", "RJ45 8P8C shielded, no magnetics (Amphenol RJHSE5380)", "C464586", "Ext · THT"),
    ("J4, J5", "1×8 male header 2.54 mm", "C492407", "Ext · THT"),
    ("J6–J9", "KF350-3.5-2P screw terminal", "C474892", "Ext · THT"),
    ("JP1, JP2, TP1", "PCB features only (solder jumpers, test pad)", "—", "—"),
]

FOOTPRINT = {
    "C2938669": "SOIC-16_4.4x10.3mm_P1.27mm", "C181753": "QFN-32-1EP_5x5mm_P0.5mm", "C524806": "SOIC-8-1EP_3.9x4.9mm_P1.27mm",
    "C7527500": "SOT-223-3_TabPin2", "C963430": "SOT-23-5", "C60583": "SOIC-8_3.9x4.9mm_P1.27mm",
    "C160457": "Oscillator_SMD_3225-4Pin", "C840532": "Bourns_SM-LP-5001", "C167221": "L_7.0x6.6mm",
    "C65009": "D_SMA", "C123819": "D_SMB", "C2128": "D_SOD-323", "C18198333": "Fuse_1812", "C2286": "LED_0603",
    "C1002": "L_0603", "C28323": "C_0805", "C15850": "C_0805", "C50254": "C_1206", "C12891": "C_1206",
    "C72480": "CP_Elec_8x10.2", "C2977589": "PinSocket_2x20_P2.54mm_Vertical", "C464586": "RJ45_Amphenol_RJHSE5380",
    "C492407": "PinHeader_1x08_P2.54mm_Vertical", "C474892": "TerminalBlock_P3.50mm_1x02",
}


def expand(refs):
    """'R14–R20, R22' -> ['R14', ..., 'R20', 'R22']"""
    out = []
    for part in refs.split(","):
        part = part.strip()
        m = re.fullmatch(r"([A-Z]+)(\d+)–[A-Z]*(\d+)", part)
        if m:
            out += [f"{m.group(1)}{i}" for i in range(int(m.group(2)), int(m.group(3)) + 1)]
        else:
            out.append(part)
    return out


def write_csv(path):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Comment", "Designator", "Footprint", "LCSC Part #"])
        n = 0
        for refs, desc, lcsc, _ in PARTS:
            if not lcsc.startswith("C"):
                continue  # TBD values and PCB-only features
            fp = FOOTPRINT.get(lcsc) or ("R_0603" if refs.startswith("R") else "C_0603")
            des = expand(refs)
            n += len(des)
            w.writerow([desc.split(",")[0], ",".join(des), fp, lcsc])
    print("wrote", path, n, "placements")


CHECKS = [
    ("Y1 pinout", "The HSO321S datasheet is encrypted, so pin numbers follow the usual 3225 layout. Confirm before layout; tying pin 1 high is safe either way."),
    ("U3 compensation", "R29, C28, C29 need values from TI WEBENCH or the TPS54360 datasheet design procedure (13.8 V → 5.06 V, 3 A, 640 kHz, 3 × 22 µF)."),
    ("Codec clocking", "The in-kernel dacberry400 overlay sets the codec PLL source to BCLK (clock id 2) while stating 12 MHz. Y1 feeds MCLK so a small custom overlay can select MCLK instead. First bring-up test: confirm 48 kHz playback is the right pitch."),
    ("IOVDD", "Runs at 3.3 V (3V3_PI) to match Pi logic. The dacberry400 overlay describes IOVDD as 1.8 V; that only names a regulator for the driver and doesn't change the hardware."),
    ("Mic-jack numbering", "RJ45 pin numbers versus each radio's numbering: confirm with a straight patch lead and a meter before setting the J4 → J5 jumpers."),
    ("Isolation clearance", "Keep RGND copper and every radio-side net at least 2 mm from GND-side copper, including under U1 and T1/T2."),
]

# ================================================================= page
def main():
    sheets = [
        ("overview", "Block diagram", sheet_overview(), "Block diagram: Pi-side blocks on the left of the isolation barrier, radio-side filters and connectors on the right, power along the bottom.", "Everything left of the dashed barrier is on Pi ground (GND). Everything right of it is on radio ground (RGND). Only optocouplers and transformers cross it."),
        ("power", "Power", sheet_power(), "Power sheet: 13.8 V input through fuse, reverse diode and TVS into a TPS54360 buck making 5.06 V, then a TLV76133 3.3 V LDO and TPS7A2018 1.8 V LDO for the codec.", "The 5V0 rail powers the Pi through header pins 2 and 4. 3V3A is a quiet analog rail for the codec; the Pi's own 3V3 is used only for logic."),
        ("control", "Control", sheet_control(), "Control sheet: the TCMT4100 quad optocoupler with three Pi-driven channels for PTT, UP and DOWN, and one reversed channel bringing BUSY in.", "Channels 1–3 are driven from the Pi. Channel 4 is reversed: its LED sits on the radio side, so BUSY arrives isolated on GPIO23."),
        ("connectors", "Radio connectors", sheet_connectors(), "Radio connectors sheet: two RJ45 jacks wired in parallel, a patch header pair for per-radio pin mapping, and screw terminals for speaker, busy and accessory PTT.", "J2 and J3 are joined pin for pin, so the original mic keeps working. Each radio gets its own short set of jumpers between J4 and J5."),
        ("codec", "Codec", sheet_codec(), "Codec sheet: TLV320AIC3104 with I2S and I2C to the Pi, 12 MHz master clock, reset pull-down, decoupling, and unused pins marked no-connect.", "All 33 pins are shown so the KiCad symbol can be checked pin for pin. Crossed pins are no-connect."),
        ("audio", "Audio + ID EEPROM", sheet_audio(), "Audio isolation sheet: T1 drives the radio mic through a 40 dB pad; T2 takes the radio speaker into the codec's differential input; plus the HAT ID EEPROM.", "Both transformers sit across the barrier. TX audio leaves the codec differentially; RX audio arrives differentially, so neither speaker wire is ever grounded."),
    ]
    sheet_count = 7
    nav = "".join(f'<a href="#{k}"><span class="num">{i+1 if i == 0 else i+2}</span>{t}</a>' for i, (k, t, *_ ) in enumerate(sheets[:1]))
    nav = (f'<a href="#overview"><span class="num">1</span>Block diagram</a>'
           f'<a href="#header"><span class="num">2</span>Pi header</a>'
           + "".join(f'<a href="#{k}"><span class="num">{i+3}</span>{t}</a>' for i, (k, t, *_ ) in enumerate(sheets[1:]))
           + '<a href="#nets">Netlist</a><a href="#parts">Parts</a><a href="#checks">Before layout</a>')

    def sheet_section(num, key, title, sh, aria, cap):
        return (f'<section id="{key}" class="sheet"><header class="sheet-head"><span class="sheet-no">Sheet {num}/{sheet_count}</span>'
                f'<h2>{html.escape(title)}</h2></header>'
                f'<figure><div class="scroll">{sh.svg(aria)}</div><figcaption>{html.escape(cap)}</figcaption></figure></section>')

    body = []
    k, t, sh, aria, cap = sheets[0]
    body.append(sheet_section(1, k, t, sh, aria, cap))
    # header table
    rows = "".join(
        f'<tr class="{"g" if n == "GND" else ""}"><td class="num">{p}</td><td>{html.escape(f)}</td>'
        f'<td><span class="net net-{"pwr" if n in ("5V0","3V3_PI") else "pi"}">{html.escape(n)}</span></td><td>{html.escape(d)}</td></tr>'
        for p, f, n, d in HEADER)
    body.append(f'<section id="header" class="sheet"><header class="sheet-head"><span class="sheet-no">Sheet 2/{sheet_count}</span><h2>Pi header J1</h2></header>'
                f'<p class="lede">Only these pins are used. Every pin not listed is left unconnected. Pin numbers are physical header positions.</p>'
                f'<div class="scroll"><table class="tbl"><thead><tr><th>Pin</th><th>Pi function</th><th>Net</th><th>Goes to</th></tr></thead><tbody>{rows}</tbody></table></div></section>')
    for i, (k, t, sh, aria, cap) in enumerate(sheets[1:]):
        body.append(sheet_section(i + 3, k, t, sh, aria, cap))
    nrows = "".join(f'<tr><td><span class="net net-{s}">{html.escape(n)}</span></td><td>{html.escape(m)}</td></tr>' for s, n, m in NETS)
    body.append(f'<section id="nets" class="sheet"><header class="sheet-head"><span class="sheet-no">Reference</span><h2>Netlist</h2></header>'
                f'<p class="lede">Ref.pin for every connection. Use these names as KiCad net labels: same name, same net.</p>'
                f'<div class="scroll"><table class="tbl nets"><thead><tr><th>Net</th><th>Connects</th></tr></thead><tbody>{nrows}</tbody></table></div></section>')
    prows = "".join(f'<tr><td class="ref">{html.escape(r)}</td><td>{html.escape(d)}</td><td class="mono">{html.escape(c)}</td><td><span class="chip chip-{"basic" if t.startswith("Basic") else "ext" if t.startswith("Ext") else "none"}">{html.escape(t)}</span></td></tr>' for r, d, c, t in PARTS)
    body.append(f'<section id="parts" class="sheet"><header class="sheet-head"><span class="sheet-no">Reference</span><h2>Parts</h2></header>'
                f'<p class="lede">Designators match the sheets. LCSC numbers were checked against JLCPCB stock on 2 Nov 2026; put them in each symbol’s LCSC field.</p>'
                f'<div class="scroll"><table class="tbl"><thead><tr><th>Ref</th><th>Part</th><th>LCSC</th><th>JLC type</th></tr></thead><tbody>{prows}</tbody></table></div></section>')
    crows = "".join(f'<li><strong>{html.escape(a)}.</strong> {html.escape(b)}</li>' for a, b in CHECKS)
    body.append(f'<section id="checks" class="sheet"><header class="sheet-head"><span class="sheet-no">Reference</span><h2>Before layout</h2></header><ol class="checks">{crows}</ol></section>')

    page = TEMPLATE.replace("{{NAV}}", nav).replace("{{BODY}}", "".join(body))
    open(OUT, "w").write(page)
    print("wrote", OUT, len(page), "bytes")
    write_csv(CSV_OUT)


TEMPLATE = open(os.path.join(HERE, "template.html")).read()
main()
