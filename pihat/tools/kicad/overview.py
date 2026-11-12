"""Write docs/schematic.svg: the whole RadPi Control HAT circuit on one page, for the README.

Run after build.py (or after editing the schematic):  python3 overview.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(PROJ), "kicadgen"))
import overview  # noqa: E402

ROWS = [
    [("", "Pi header and ID EEPROM", 165.0), ("Power", "Power (13.8 V in, 5 V buck, codec rails)", None),
     ("Control", "Control (PTT, channel buttons, busy)", None)],
    [("Radio connectors", "Radio connectors", None), ("Codec", "Codec (TLV320AIC3104)", None),
     ("Audio isolation", "Audio isolation", None)],
]

if __name__ == "__main__":
    out = os.path.join(PROJ, "docs", "schematic.svg")
    w, h = overview.build(os.path.join(PROJ, "pihat.kicad_sch"), ROWS, out, "RadPi Control HAT",
                          "Full schematic, all sheets. Labels with the same name are connected.")
    print(f"{out}: {w:.0f} x {h:.0f} mm, {os.path.getsize(out) // 1024} kB")
