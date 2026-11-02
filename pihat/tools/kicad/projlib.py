"""Project library: symbols and footprints KiCad doesn't ship.

Writes <project>/pihat.kicad_sym, <project>/pihat.pretty/*.kicad_mod and registers both
in the project's sym-lib-table / fp-lib-table.

Pin data comes from the datasheets:
  TLV320AIC3104 (TI SLAS510F, Pin Functions table), RHB QFN-32, pad 33 = thermal pad
  SM-LP-5001 (Bourns): windings 1-3 and 4-6, pins 2 and 5 not connected, dots on 1 and 6
"""
import os
import uuid

from sexp import Q, dump

NS = uuid.UUID("0b6c5f1e-8f0a-4a3e-9c51-7d2e4b3a1f60")


def U(key):
    return Q(str(uuid.uuid5(NS, key)))


FONT = ["effects", ["font", ["size", 1.27, 1.27]]]


def prop(name, value, at, hide=False):
    p = ["property", Q(name), Q(value), ["at", at[0], at[1], 0]]
    if hide:
        p.append(["hide", "yes"])
    p.append(FONT)
    return p


def pin(kind, x, y, ang, name, num, length=5.08, hidden=False):
    p = ["pin", kind, "line", ["at", x, y, ang], ["length", length]]
    if hidden:
        p.append(["hide", "yes"])
    p += [["name", Q(name), FONT], ["number", Q(str(num)), FONT]]
    return p


# --------------------------------------------------------------------- TLV320AIC3104
def aic3104():
    name = "TLV320AIC3104"
    left = [  # (num, name, type) top to bottom
        (1, "MCLK", "input"), (2, "BCLK", "bidirectional"), (3, "WCLK", "bidirectional"),
        (4, "DIN", "input"), (5, "DOUT", "output"), (8, "SCL", "bidirectional"),
        (9, "SDA", "bidirectional"), (31, "~{RESET}", "input"),
    ]
    right = [
        (10, "MIC1LP/LINE1LP", "input"), (11, "MIC1LM/LINE1LM", "input"),
        (12, "MIC1RP/LINE1RP", "input"), (13, "MIC1RM/LINE1RM", "input"),
        (14, "MIC2L/LINE2L", "input"), (16, "MIC2R/LINE2R", "input"), (15, "MICBIAS", "output"),
        (19, "HPLOUT", "output"), (20, "HPLCOM", "output"), (23, "HPROUT", "output"), (22, "HPRCOM", "output"),
        (27, "LEFT_LOP", "output"), (28, "LEFT_LOM", "output"), (29, "RIGHT_LOP", "output"), (30, "RIGHT_LOM", "output"),
    ]
    top = [(7, "IOVDD"), (32, "DVDD"), (25, "AVDD"), (18, "DRVDD"), (24, "DRVDD")]
    bottom = [(6, "DVSS"), (17, "AVSS1"), (26, "AVSS2"), (21, "DRVSS"), (33, "EP")]
    W, H = 15.24, 27.94  # half-width / half-height of the body
    pins = []
    for i, (n, nm, t) in enumerate(left):
        pins.append(pin(t, -W - 5.08, 17.78 - i * 2.54, 0, nm, n))
    for i, (n, nm, t) in enumerate(right):
        pins.append(pin(t, W + 5.08, 17.78 - i * 2.54, 180, nm, n))
    xs = [-10.16, -5.08, 0, 5.08, 10.16]
    for x, (n, nm) in zip(xs, top):
        pins.append(pin("power_in", x, H + 5.08, 270, nm, n))
    for x, (n, nm) in zip(xs, bottom):
        pins.append(pin("power_in" if n != 33 else "passive", x, -H - 5.08, 90, nm, n))
    return ["symbol", Q(name), ["pin_names", ["offset", 1.016]],
            ["exclude_from_sim", "no"], ["in_bom", "yes"], ["on_board", "yes"],
            prop("Reference", "U", (0, H + 7.62)),
            prop("Value", name, (0, -H - 7.62)),
            prop("Footprint", "Package_DFN_QFN:QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm", (0, 0), True),
            prop("Datasheet", "https://www.ti.com/lit/ds/symlink/tlv320aic3104.pdf", (0, 0), True),
            prop("Description", "Low-power stereo audio codec, I2S/I2C, QFN-32 (RHB)", (0, 0), True),
            ["symbol", Q(f"{name}_0_1"),
             ["rectangle", ["start", -W, H], ["end", W, -H], ["stroke", ["width", 0.254], ["type", "default"]],
              ["fill", ["type", "background"]]]],
            ["symbol", Q(f"{name}_1_1")] + pins,
            ["embedded_fonts", "no"]]


# --------------------------------------------------------------------- SM-LP-5001
def smlp5001():
    name = "SM-LP-5001"
    st = ["stroke", ["width", 0.254], ["type", "default"]]
    g = []
    for side, x, mx in (("L", -2.54, -1.27), ("R", 2.54, 1.27)):
        for k in range(4):
            y0 = 5.08 - k * 2.54
            g.append(["arc", ["start", x, y0], ["mid", mx, y0 - 1.27], ["end", x, y0 - 2.54], st, ["fill", ["type", "none"]]])
    g.append(["polyline", ["pts", ["xy", -0.635, 5.08], ["xy", -0.635, -5.08]], st, ["fill", ["type", "none"]]])
    g.append(["polyline", ["pts", ["xy", 0.635, 5.08], ["xy", 0.635, -5.08]], st, ["fill", ["type", "none"]]])
    for cx in (-3.81, 3.81):
        g.append(["circle", ["center", cx, 4.445], ["radius", 0.381], ["stroke", ["width", 0], ["type", "default"]],
                  ["fill", ["type", "outline"]]])
    pins = [pin("passive", -7.62, 5.08, 0, "~", 1), pin("passive", -7.62, -5.08, 0, "~", 3),
            pin("passive", 7.62, 5.08, 180, "~", 6), pin("passive", 7.62, -5.08, 180, "~", 4),
            pin("no_connect", -7.62, 0, 0, "NC", 2, hidden=True), pin("no_connect", 7.62, 0, 180, "NC", 5, hidden=True)]
    return ["symbol", Q(name), ["pin_numbers", ["hide", "no"]], ["pin_names", ["offset", 0], ["hide", "yes"]],
            ["exclude_from_sim", "no"], ["in_bom", "yes"], ["on_board", "yes"],
            prop("Reference", "T", (0, 8.89)),
            prop("Value", name, (0, -8.89)),
            prop("Footprint", "pihat:Bourns_SM-LP-5001", (0, 0), True),
            prop("Datasheet", "https://www.bourns.com/docs/Product-Datasheets/SM-LP-5001.pdf", (0, 0), True),
            prop("Description", "600:600 ohm audio line-matching transformer, SMD-6", (0, 0), True),
            ["symbol", Q(f"{name}_0_1")] + g,
            ["symbol", Q(f"{name}_1_1")] + pins,
            ["embedded_fonts", "no"]]


def symbols():
    return {"TLV320AIC3104": aic3104(), "SM-LP-5001": smlp5001()}


def write_symlib(path):
    lib = ["kicad_symbol_lib", ["version", 20251024], ["generator", Q("pihat_projlib")],
           ["generator_version", Q("10.0")]] + list(symbols().values())
    with open(path, "w") as f:
        f.write(dump(lib) + "\n")


# --------------------------------------------------------------------- footprint
def smlp5001_fp():
    """Bourns SM-LP-5001 suggested PCB layout: pads 3.0 x 1.5 mm, 2.54 mm pitch, 15.0 mm across outer edges."""
    def txt(kind, value, y, layer):
        return ["property", Q(kind), Q(value), ["at", 0, y, 0], ["layer", Q(layer)], ["uuid", U(f"fp/{kind}")],
                ["effects", ["font", ["size", 1, 1], ["thickness", 0.15]]]]

    pads = []
    for num, x, y in ((1, -6, -2.54), (2, -6, 0), (3, -6, 2.54), (6, 6, -2.54), (5, 6, 0), (4, 6, 2.54)):
        pads.append(["pad", Q(str(num)), "smd", "rect", ["at", x, y], ["size", 3, 1.5],
                     ["layers", Q("F.Cu"), Q("F.Paste"), Q("F.Mask")], ["uuid", U(f"fp/pad{num}")]])

    def line(a, b, layer, w, key):
        return ["fp_line", ["start", a[0], a[1]], ["end", b[0], b[1]], ["stroke", ["width", w], ["type", "solid"]],
                ["layer", Q(layer)], ["uuid", U(f"fp/{key}")]]

    def rect(a, b, layer, w, key):
        return ["fp_rect", ["start", a[0], a[1]], ["end", b[0], b[1]], ["stroke", ["width", w], ["type", "solid"]],
                ["fill", "no"], ["layer", Q(layer)], ["uuid", U(f"fp/{key}")]]

    return ["footprint", Q("Bourns_SM-LP-5001"), ["version", 20260206], ["generator", Q("pihat_projlib")],
            ["layer", Q("F.Cu")],
            ["descr", Q("Bourns SM-LP-5001 line-matching transformer, SMD-6, 12.8x9.0 mm body, suggested PCB layout from the datasheet")],
            ["tags", Q("transformer audio 600 ohm SM-LP-5001")],
            txt("Reference", "REF**", -5.9, "F.SilkS"), txt("Value", "SM-LP-5001", 5.9, "F.Fab"),
            ["attr", "smd"],
            rect((-6.4, -4.5), (6.4, 4.5), "F.Fab", 0.1, "fab"),
            line((-4.3, -4.6), (4.3, -4.6), "F.SilkS", 0.12, "silk_top"),
            line((-4.3, 4.6), (4.3, 4.6), "F.SilkS", 0.12, "silk_bot"),
            line((-7.7, -3.6), (-7.7, -3.1), "F.SilkS", 0.12, "pin1"),
            rect((-7.75, -4.75), (7.75, 4.75), "F.CrtYd", 0.05, "crtyd")] + pads + [["embedded_fonts", "no"]]


def write_fp(dirpath):
    os.makedirs(dirpath, exist_ok=True)
    with open(os.path.join(dirpath, "Bourns_SM-LP-5001.kicad_mod"), "w") as f:
        f.write(dump(smlp5001_fp()) + "\n")


def register(proj):
    """Add the pihat libraries to the project's library tables (keeps any existing entries)."""
    for fname, kind, uri, root in (("sym-lib-table", "KiCad", "${KIPRJMOD}/pihat.kicad_sym", "sym_lib_table"),
                                   ("fp-lib-table", "KiCad", "${KIPRJMOD}/pihat.pretty", "fp_lib_table")):
        path = os.path.join(proj, fname)
        text = open(path).read() if os.path.exists(path) else f"({root}\n\t(version 7)\n)\n"
        if '(name "pihat")' not in text:
            entry = f'\t(lib (name "pihat")(type "{kind}")(uri "{uri}")(options "")(descr "Radio Remote HAT parts"))\n'
            i = text.rstrip().rindex(")")
            text = text[:i] + entry + text[i:]
            open(path, "w").write(text)


def write_all(proj):
    write_symlib(os.path.join(proj, "pihat.kicad_sym"))
    write_fp(os.path.join(proj, "pihat.pretty"))
    register(proj)
