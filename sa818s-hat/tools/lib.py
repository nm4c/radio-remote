"""SA818S HAT project library: symbols and footprints KiCad doesn't ship.

  SA818S        NiceRF SA818S-U 1 W UHF transceiver module (pin table: datasheet Rev 1.3, section 9)
  TLV320AIC3104 shared with the pihat board (same codec circuit)

Footprint NiceRF_SA818S: pad positions cross-checked between the LCSC/EasyEDA-derived footprint
(easyeda2kicad "WIRELM-SMD_G-NICERF_SA818S-X", which JLC assembles from) and an independently drawn one
(PTCR XCVR_SA818S): 4.45 mm pitch on the long sides, 4.6 mm on the short side, 35.6 x 19.0 mm body.
Pads 19/20 are the castellated ground tabs on the left end (present on the LCSC footprint).
"""
import os
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "kicadgen"))
sys.path.insert(0, os.path.join(HERE, "..", "..", "pihat", "tools", "kicad"))
from sexp import Q, dump  # noqa: E402
import projlib as pihat_lib  # noqa: E402  (TLV320AIC3104 symbol, prop/pin helpers)

NS = uuid.UUID("5d1a8c3e-2b47-4f9a-a0d6-91e3c7b25f18")
LIB = "sa818s-hat"
prop, pin = pihat_lib.prop, pihat_lib.pin


def U(key):
    return Q(str(uuid.uuid5(NS, key)))


def sa818s():
    name = "SA818S"
    st = ["stroke", ["width", 0.254], ["type", "default"]]
    left = [(18, "MIC_IN", "input"), (16, "RXD", "input"), (17, "TXD", "output"), (5, "~{PTT}", "input"),
            (6, "PD", "input"), (7, "H/~{L}", "input"), (1, "~{AudioON}", "output"), (3, "AF_OUT", "output")]
    pins = [pin(t, -17.78, 17.78 - i * 5.08, 0, nm, n) for i, (n, nm, t) in enumerate(left)]   # 5.08 pitch: room for labels
    pins.append(pin("passive", 17.78, 0, 180, "ANT", 12))
    pins.append(pin("power_in", 0, 27.94, 270, "VBAT", 8))
    for x, n in ((0, 9), (2.54, 10), (5.08, 19), (7.62, 20)):
        pins.append(pin("power_in", x, -27.94, 90, "GND", n))
    for n in (2, 4, 11, 13, 14, 15):
        pins.append(pin("no_connect", 12.7, -10.16, 180, "NC", n, length=2.54, hidden=True))
    return ["symbol", Q(name), ["pin_names", ["offset", 1.016]],
            ["exclude_from_sim", "no"], ["in_bom", "yes"], ["on_board", "yes"],
            prop("Reference", "U", (0, 30.48)),
            prop("Value", name, (0, -30.48)),
            prop("Footprint", f"{LIB}:NiceRF_SA818S", (0, 0), True),
            prop("Datasheet", "https://www.nicerf.com/walkie-talkie-module/1w-embedded-walkie-talkie-module-sa818s.html", (0, 0), True),
            prop("Description", "NiceRF SA818S 1 W FM transceiver module, UHF 400-470 MHz (-U), UART control", (0, 0), True),
            ["symbol", Q(f"{name}_0_1"),
             ["rectangle", ["start", -12.7, 22.86], ["end", 12.7, -22.86], st, ["fill", ["type", "background"]]]],
            ["symbol", Q(f"{name}_1_1")] + pins,
            ["embedded_fonts", "no"]]


def sa818s_fp():
    def txt(kind, value, y, layer):
        return ["property", Q(kind), Q(value), ["at", 0, y, 0], ["layer", Q(layer)], ["uuid", U(f"fp/{kind}")],
                ["effects", ["font", ["size", 1, 1], ["thickness", 0.15]]]]

    pads = []
    xs = [-13.35, -8.9, -4.45, 0.0, 4.45, 8.9, 13.35]
    for i, x in enumerate(xs):                                   # top edge: 1..7, left to right
        pads.append((str(i + 1), x, -9.56, 1.9, 3.9))
    for n, y in zip((8, 9, 10, 11), (-6.9, -2.3, 2.3, 6.9)):     # right end: 8..11, top to bottom
        pads.append((str(n), 17.8, y, 3.9, 1.9))
    for n, x in zip(range(12, 19), reversed(xs)):                # bottom edge: 12..18, right to left
        pads.append((str(n), x, 9.56, 1.9, 3.9))
    pads += [("19", -17.8, 4.6, 3.9, 6.3), ("20", -17.8, -4.72, 3.9, 6.3)]   # left-end ground tabs

    def line(a, b, layer, w, key):
        return ["fp_line", ["start", a[0], a[1]], ["end", b[0], b[1]], ["stroke", ["width", w], ["type", "solid"]],
                ["layer", Q(layer)], ["uuid", U(f"fp/{key}")]]

    def rect(a, b, layer, w, key):
        return ["fp_rect", ["start", a[0], a[1]], ["end", b[0], b[1]], ["stroke", ["width", w], ["type", "solid"]],
                ["fill", "no"], ["layer", Q(layer)], ["uuid", U(f"fp/{key}")]]

    items = ["footprint", Q("NiceRF_SA818S"), ["version", 20260206], ["generator", Q("sa818s_hat_lib")],
             ["layer", Q("F.Cu")],
             ["descr", Q("NiceRF SA818S 1 W transceiver module, 18 castellated pads + 2 ground tabs, 35.6 x 19.0 mm")],
             ["tags", Q("SA818S SA818 NiceRF transceiver module castellated")],
             txt("Reference", "REF**", -12.6, "F.SilkS"), txt("Value", "SA818S", 12.6, "F.Fab"),
             ["attr", "smd"],
             rect((-17.8, -9.5), (17.8, 9.5), "F.Fab", 0.1, "fab"),
             line((-16.0, -9.6), (-15.0, -9.6), "F.SilkS", 0.12, "pin1a"),
             line((-15.6, -12.0), (-15.6, -11.0), "F.SilkS", 0.12, "pin1b"),
             rect((-20.0, -11.8), (20.0, 11.8), "F.CrtYd", 0.05, "crtyd")]
    for num, x, y, w, h in pads:
        items.append(["pad", Q(num), "smd", "rect", ["at", x, y], ["size", w, h],
                      ["layers", Q("F.Cu"), Q("F.Paste"), Q("F.Mask")], ["uuid", U(f"fp/pad{num}")]])
    items.append(["embedded_fonts", "no"])
    return items


def symbols():
    return {"SA818S": sa818s(), "TLV320AIC3104": pihat_lib.aic3104()}


def write_all(proj):
    lib = ["kicad_symbol_lib", ["version", 20251024], ["generator", Q("sa818s_hat_lib")],
           ["generator_version", Q("10.0")]] + list(symbols().values())
    with open(os.path.join(proj, f"{LIB}.kicad_sym"), "w") as f:
        f.write(dump(lib) + "\n")
    d = os.path.join(proj, f"{LIB}.pretty")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "NiceRF_SA818S.kicad_mod"), "w") as f:
        f.write(dump(sa818s_fp()) + "\n")
    # also ship the pihat transformer-free codec: same QFN footprint is stock, nothing else to write
    for fname, root, uri in (("sym-lib-table", "sym_lib_table", f"${{KIPRJMOD}}/{LIB}.kicad_sym"),
                             ("fp-lib-table", "fp_lib_table", f"${{KIPRJMOD}}/{LIB}.pretty")):
        path = os.path.join(proj, fname)
        text = open(path).read() if os.path.exists(path) else f"({root}\n\t(version 7)\n)\n"
        if f'(name "{LIB}")' not in text:
            entry = f'\t(lib (name "{LIB}")(type "KiCad")(uri "{uri}")(options "")(descr "Radio Remote SA818S HAT parts"))\n'
            i = text.rstrip().rindex(")")
            text = text[:i] + entry + text[i:]
            open(path, "w").write(text)
