"""Footprints and 3D models shared by the Radio Remote boards (pihat, sa818s-hat).

Everything lives in kicad-shared/ at the repo root, next to both projects:
  kicad-shared/3dmodels/          STEP models KiCad's own library doesn't have. Downloaded from
                                  LCSC/EasyEDA for the exact parts (easyeda2kicad), each one
                                  checked to sit centred on its footprint's origin, at z = 0.
  kicad-shared/radio-remote.pretty  Copies of stock KiCad footprints whose 3D model reference
                                  is broken in KiCad 10 (renamed file, or no model at all),
                                  with the model path fixed. Regenerated from the installed
                                  KiCad library on every build.

Projects reach it through ${KIPRJMOD}/../kicad-shared, so the paths work from either board.
"""
import os
import re

KICAD = "/Applications/KiCad/KiCad.app/Contents/SharedSupport"
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SHARED = os.path.join(REPO, "kicad-shared")
LIB = "radio-remote"
MODELS = "${KIPRJMOD}/../kicad-shared/3dmodels"
STOCK3D = "${KICAD10_3DMODEL_DIR}"

# Our own models (kicad-shared/3dmodels), by part.
TRANSFORMER = f"{MODELS}/Bourns_SM-LP-5001E.step"     # LCSC C840532
SA818S = f"{MODELS}/NiceRF_SA818S.step"               # LCSC C3001549

# Xinlaiya XY2500V-F-3.5 vertical pluggable headers: generated here (xy2500v), pins and body
# from the LCSC footprint for the exact parts (C880554 / C880556 / C880558), model from LCSC.
XY2500V_PINS = (2, 4, 6)


def xy2500v_name(n):
    return f"Xinlaiya_XY2500V-F-3.5_1x0{n}_P3.50mm_Vertical"

# Stock footprint -> the model it should use.
FIXED = {
    # 1812 PTC: KiCad has no 1812 fuse model. LCSC C18198333 body, 4.5 x 3.2 x 1.0 mm.
    "Fuse:Fuse_1812_4532Metric": f"{MODELS}/PTC_1812_4.5x3.2x1.0mm.step",
    # 3225 oscillator: KiCad has none. HSO321S (LCSC C160457) body, 3.2 x 2.5 x 0.9 mm.
    "Oscillator:Oscillator_SMD_Abracon_ASE-4Pin_3.2x2.5mm": f"{MODELS}/Oscillator_3225_HSO321S.step",
    # TI DDA (HSOP-8 PowerPAD): the footprints name a model file that KiCad 10 renamed.
    "Package_SO:Texas_HSOP-8-1EP_3.9x4.9mm_P1.27mm_ThermalVias":
        f"{STOCK3D}/Package_SO.3dshapes/Texas_HTSOP-8-1EP_3.9x4.9mm_P1.27mm_EP2.95x4.9mm_Mask2.4x3.1mm_ThermalVias.step",
    "Package_SO:TI_SO-PowerPAD-8_ThermalVias": f"{STOCK3D}/Package_SO.3dshapes/TI_SO-PowerPAD-8.step",
}


def _line(x1, y1, x2, y2, layer, w):
    return (f'\t(fp_line\n\t\t(start {x1:g} {y1:g})\n\t\t(end {x2:g} {y2:g})\n\t\t(stroke\n\t\t\t(width {w:g})\n'
            f'\t\t\t(type solid)\n\t\t)\n\t\t(layer "{layer}")\n\t)\n')


def _rect(x1, y1, x2, y2, layer, w):
    return "".join(_line(*seg, layer, w) for seg in
                   ((x1, y1, x2, y1), (x2, y1, x2, y2), (x2, y2, x1, y2), (x1, y2, x1, y1)))


def _text(kind, value, x, y, layer, size, hide=False):
    head = f'\t(property "{kind}" "{value}"' if kind else f'\t(fp_text user "{value}"'
    return (f'{head}\n\t\t(at {x:g} {y:g} 0)\n\t\t(layer "{layer}")\n' + ('\t\t(hide yes)\n' if hide else '')
            + f'\t\t(effects\n\t\t\t(font\n\t\t\t\t(size {size:g} {size:g})\n\t\t\t\t(thickness {size * 0.15:g})\n'
            f'\t\t\t)\n\t\t)\n\t)\n')


def xy2500v(n):
    """Xinlaiya XY2500V-F-3.5-nP: vertical pluggable header, 3.5 mm pitch, closed ends.

    Origin on pin 1 (square, on the left), like KiCad's Phoenix MC footprints, so swapping these in
    for them keeps every pad where it was and the routing stays connected. Pins drilled 1.4 mm in
    2.2 mm pads; body 3.5 n + 1 mm wide, from 4 mm behind the pin row to 3 mm in front
    (the plug's wire side), as on LCSC's footprint and model for these parts."""
    pitch = 3.5
    x0 = 0.0                       # pin 1
    cx = pitch * (n - 1) / 2       # middle of the row: the body and model are centred here
    half = (pitch * n + 1) / 2
    top, bot = -4.0, 3.0
    out = [f'(footprint "{xy2500v_name(n)}"\n\t(version 20260206)\n\t(generator "radio_remote_sharedlib")\n'
           f'\t(layer "F.Cu")\n\t(descr "Xinlaiya XY2500V-F-3.5-{n}P vertical pluggable header, 3.5 mm pitch, '
           f'closed ends, 8 A 300 V (LCSC {dict(zip(XY2500V_PINS, ("C880554", "C880556", "C880558")))[n]})")\n'
           f'\t(tags "Xinlaiya XY2500V terminal pluggable vertical 3.5mm")\n',
           _text("Reference", "REF**", cx, top - 1.5, "F.SilkS", 1),
           _text("Value", xy2500v_name(n), cx, bot + 1.5, "F.Fab", 1),
           '\t(attr through_hole)\n',
           _rect(cx - half, top, cx + half, bot, "F.SilkS", 0.12),
           _line(cx - half, top + 0.6, cx + half, top + 0.6, "F.SilkS", 0.12),  # the closed back wall
           _line(x0 - 1.6, bot + 0.6, x0 + 1.6, bot + 0.6, "F.SilkS", 0.12),    # pin 1 marker
           _rect(cx - half, top, cx + half, bot, "F.Fab", 0.1),
           _rect(cx - half - 0.25, top - 0.25, cx + half + 0.25, bot + 0.25, "F.CrtYd", 0.05),
           _text(None, "${REFERENCE}", cx, (top + bot) / 2 - 1.8, "F.Fab", 1)]
    for i in range(n):
        shape = "rect" if i == 0 else "circle"
        out.append(f'\t(pad "{i + 1}" thru_hole {shape}\n\t\t(at {x0 + pitch * i:g} 0)\n\t\t(size 2.2 2.2)\n'
                   f'\t\t(drill 1.4)\n\t\t(layers "*.Cu" "*.Mask")\n\t\t(remove_unused_layers no)\n\t)\n')
    # LCSC's STEP for these sits 10.5 mm low and 7.1 mm off front-to-back (its VRML doesn't);
    # this offset puts it on the pins, checked against the VRML in edge-on renders to ~0.05 mm.
    out.append('\t(embedded_fonts no)\n'
               + _model_text(f"{MODELS}/Xinlaiya_XY2500V-F-3.5-{n}P.step", (cx, 7.1, 10.5)) + ')\n')
    return "".join(out)


def fp(stock):
    """The shared-library footprint ID to use instead of a stock one with a broken model."""
    return f"{LIB}:{stock.split(':', 1)[1]}"


def model(path):
    """A footprint (model ...) entry with no offset, scale 1, no rotation, as an s-expr list."""
    from sexp import Q
    return ["model", Q(path), ["offset", ["xyz", 0, 0, 0]], ["scale", ["xyz", 1, 1, 1]], ["rotate", ["xyz", 0, 0, 0]]]


def _model_text(path, offset=(0, 0, 0)):
    ox, oy, oz = offset
    return (f'\t(model "{path}"\n\t\t(offset\n\t\t\t(xyz {ox:g} {oy:g} {oz:g})\n\t\t)\n\t\t(scale\n\t\t\t(xyz 1 1 1)\n'
            f'\t\t)\n\t\t(rotate\n\t\t\t(xyz 0 0 0)\n\t\t)\n\t)\n')


def write_footprints():
    """Copy each stock footprint in FIXED into radio-remote.pretty with its model replaced."""
    out = os.path.join(SHARED, f"{LIB}.pretty")
    os.makedirs(out, exist_ok=True)
    for stock, path in FIXED.items():
        lib, name = stock.split(":", 1)
        text = open(os.path.join(KICAD, "footprints", f"{lib}.pretty", f"{name}.kicad_mod")).read()
        text = re.sub(r'\t\(model ".*?"\n.*?\n\t\)\n', "", text, flags=re.S)   # drop stock model(s)
        i = text.rstrip().rindex(")")
        text = text[:i] + _model_text(path) + text[i:]
        with open(os.path.join(out, f"{name}.kicad_mod"), "w") as f:
            f.write(text)
    for n in XY2500V_PINS:
        with open(os.path.join(out, f"{xy2500v_name(n)}.kicad_mod"), "w") as f:
            f.write(xy2500v(n))


def register(proj):
    """Add the shared footprint library to a project's fp-lib-table (keeps existing entries)."""
    path = os.path.join(proj, "fp-lib-table")
    text = open(path).read() if os.path.exists(path) else "(fp_lib_table\n\t(version 7)\n)\n"
    if f'(name "{LIB}")' not in text:
        entry = (f'\t(lib (name "{LIB}")(type "KiCad")(uri "${{KIPRJMOD}}/../kicad-shared/{LIB}.pretty")'
                 f'(options "")(descr "Footprints shared by the Radio Remote boards, with 3D models"))\n')
        i = text.rstrip().rindex(")")
        text = text[:i] + entry + text[i:]
        open(path, "w").write(text)


def check_models():
    """Every model the shared footprints point at must exist. Returns the missing ones."""
    missing = []
    for path in list(FIXED.values()) + [TRANSFORMER, SA818S] + [
            f"{MODELS}/Xinlaiya_XY2500V-F-3.5-{n}P.step" for n in XY2500V_PINS]:
        real = path.replace("${KIPRJMOD}/..", REPO).replace(STOCK3D, os.path.join(KICAD, "3dmodels"))
        if not os.path.exists(real):
            missing.append(path)
    return missing


def build(proj):
    write_footprints()
    register(proj)
    missing = check_models()
    if missing:
        raise SystemExit("missing 3D models: " + ", ".join(missing))
