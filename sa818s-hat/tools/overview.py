"""Write docs/schematic.svg: the whole SA818 HAT circuit on one page, for the README.

Run after build.py (or after editing the schematic):  python3 tools/overview.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(os.path.dirname(PROJ), "kicadgen"))
import overview  # noqa: E402

ROWS = [
    [("", "Pi header and ID EEPROM", 165.0), ("Power", "Codec power (3.3 V / 1.8 V)", None),
     ("RF power", "Module power (4.2 V)", None)],
    [("Codec", "Codec (TLV320AIC3104)", None), ("Audio", "Audio levels", None),
     ("Transceiver", "SA818S transceiver and harmonic filter", None)],
]

if __name__ == "__main__":
    os.makedirs(os.path.join(PROJ, "docs"), exist_ok=True)
    out = os.path.join(PROJ, "docs", "schematic.svg")
    w, h = overview.build(os.path.join(PROJ, "sa818s-hat.kicad_sch"), ROWS, out,
                          "RadPi SA818 HAT",
                          "Full schematic, all sheets. Labels with the same name are connected.")
    print(f"{out}: {w:.0f} x {h:.0f} mm, {os.path.getsize(out) // 1024} kB")
