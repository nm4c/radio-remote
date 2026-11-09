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


def fp(stock):
    """The shared-library footprint ID to use instead of a stock one with a broken model."""
    return f"{LIB}:{stock.split(':', 1)[1]}"


def model(path):
    """A footprint (model ...) entry with no offset, scale 1, no rotation, as an s-expr list."""
    from sexp import Q
    return ["model", Q(path), ["offset", ["xyz", 0, 0, 0]], ["scale", ["xyz", 1, 1, 1]], ["rotate", ["xyz", 0, 0, 0]]]


def _model_text(path):
    return (f'\t(model "{path}"\n\t\t(offset\n\t\t\t(xyz 0 0 0)\n\t\t)\n\t\t(scale\n\t\t\t(xyz 1 1 1)\n'
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
    for path in list(FIXED.values()) + [TRANSFORMER, SA818S]:
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
