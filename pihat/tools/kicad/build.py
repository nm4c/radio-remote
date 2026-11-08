"""Generate HAT sub-sheets, link them from the root schematic, and run KiCad checks.

Usage (KiCad must not have the project open):
    python3 build.py            build + check
    python3 build.py --check    check only
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.environ.get("PIHAT_PROJ") or os.path.abspath(os.path.join(HERE, "..", ".."))
ROOT = os.path.join(PROJ, "pihat.kicad_sch")
CLI = "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"
OUT = os.path.join(PROJ, "build")

REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(REPO, "kicadgen"))      # shared generator (sexp, kicadlib, schbuild, hatroot)
sys.path.insert(0, HERE)
import kicadlib  # noqa: E402
import schbuild  # noqa: E402
from hatroot import check as kicad_check, ensure_sheet, globalize_root, refresh_embedded, renumber_root, root_eeprom, root_uuid  # noqa: E402
import sheet_power  # noqa: E402
import sheet_control  # noqa: E402
import sheet_connectors  # noqa: E402
import sheet_codec  # noqa: E402
import sheet_audio  # noqa: E402
import projlib  # noqa: E402

# (sheet name, file, builder, box position on the root sheet)
SHEETS = [
    ("Power", "power.kicad_sch",
     lambda p, n: sheet_power.build(p, n, buck="lmr33640", tvs=("SMBJ22A", "C224015"), u5_cin=False), (210.82, 30.48)),
    ("Control", "control.kicad_sch", sheet_control.build, (210.82, 53.34)),
    ("Radio connectors", "connectors.kicad_sch", sheet_connectors.build, (210.82, 76.2)),
    ("Codec", "codec.kicad_sch", lambda p, n: sheet_codec.build(p, n, i2s_series=False), (210.82, 99.06)),
    ("Audio isolation", "audio.kicad_sch", sheet_audio.build, (210.82, 121.92)),
]


# Template parts on the root sheet renamed to the HAT's designators (see docs/schematic).
ROOT_RENAME = {"U1": "U6", "R1": "R22", "R2": "R23", "C1": "C15", "JP1": "JP2"}

# Header nets the HAT uses: local labels on the template's header become global labels.
GLOBAL_NETS = {"GPIO17", "GPIO27", "GPIO22", "GPIO23"}
# template label -> HAT net name (made global). KiCad stores "/" inside label names as {slash}.
RENAME = {"GPIO18{slash}PCM.CLK": "I2S_BCLK", "GPIO19{slash}PCM.FS": "I2S_LRCLK", "GPIO20{slash}PCM.DIN": "I2S_DOUT",
          "GPIO21{slash}PCM.DOUT": "I2S_DIN", "GPIO2{slash}SDA1": "I2C_SDA", "GPIO3{slash}SCL1": "I2C_SCL",
          "GPIO26": "CODEC_RST"}

ROOT_FIELDS = {
    "U6": {"Value": "AT24C32D-SSHM-T", "Footprint": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "LCSC": "C60583",
           "Datasheet": "https://ww1.microchip.com/downloads/en/DeviceDoc/AT24C32D-AT24C64D-Data-Sheet-DS20006035A.pdf"},
    "R22": {"Footprint": "Resistor_SMD:R_0603_1608Metric", "LCSC": "C23018"},
    "R23": {"Footprint": "Resistor_SMD:R_0603_1608Metric", "LCSC": "C23018"},
    "C15": {"Value": "100nF", "Footprint": "Capacitor_SMD:C_0603_1608Metric", "LCSC": "C14663"},
    "JP2": {"Value": "WP write enable", "Footprint": "Jumper:SolderJumper-2_P1.3mm_Open_RoundedPad1.0x1.5mm"},
    "J1": {"LCSC": "C2977589"},
}


def build():
    schbuild.PROJECT = "pihat"
    kicadlib.PROJECT_LIBS["pihat"] = projlib.symbols
    projlib.write_all(PROJ)
    text = globalize_root(renumber_root(open(ROOT).read(), ROOT_RENAME, "U6"), GLOBAL_NETS | set(RENAME), RENAME)
    text = root_eeprom(text, root_uuid(text), ROOT_FIELDS, eeprom="U6", jumper="JP2", pullup="R24")
    text = refresh_embedded(text)
    ru = root_uuid(text)
    for page, (name, fname, fn, at) in enumerate(SHEETS, start=2):
        text, su = ensure_sheet(text, name, fname, at, page, "pihat")
        sheet = fn(f"/{ru}/{su}", str(page))
        sheet.save(os.path.join(PROJ, fname))
        print("wrote", fname)
    open(ROOT, "w").write(text)


def check():
    kicad_check(CLI, ROOT, OUT, "pihat.net")


if __name__ == "__main__":
    if "--check" not in sys.argv:
        build()
    check()
