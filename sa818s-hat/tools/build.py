"""Generate the SA818S HAT schematic sheets, link them from the root, and run KiCad checks.

Usage (KiCad must not have the project open):
    python3 build.py            build + check
    python3 build.py --check    check only
Set SA818S_PROJ to build into another folder (used for scratch builds).

Shared with the pihat board: the generator (kicadgen/), the power and codec sheets.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
PROJ = os.environ.get("SA818S_PROJ") or os.path.abspath(os.path.join(HERE, ".."))
ROOT = os.path.join(PROJ, "sa818s-hat.kicad_sch")
CLI = "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"
OUT = os.path.join(PROJ, "build")

sys.path.insert(0, os.path.join(REPO, "kicadgen"))
sys.path.insert(0, os.path.join(REPO, "pihat", "tools", "kicad"))   # shared power + codec sheets
sys.path.insert(0, HERE)
import kicadlib  # noqa: E402
import schbuild  # noqa: E402
import sharedlib  # noqa: E402
from hatroot import check as kicad_check, ensure_sheet, globalize_root, refresh_embedded, root_eeprom, root_uuid  # noqa: E402
import sheet_power  # noqa: E402  (pihat)
import sheet_codec  # noqa: E402  (pihat)
import sheet_rfpower  # noqa: E402
import sheet_rf  # noqa: E402
import sheet_audio  # noqa: E402
import lib  # noqa: E402

PROJECT = "sa818s-hat"
SHEETS = [
    ("Power", "power.kicad_sch", lambda p, n: sheet_power.build(p, n, fuse=("2A 33V", "C18198343"), vin_global=True), (210.82, 30.48)),
    ("RF power", "rfpower.kicad_sch", sheet_rfpower.build, (210.82, 53.34)),
    ("Transceiver", "transceiver.kicad_sch", sheet_rf.build, (210.82, 76.2)),
    ("Codec", "codec.kicad_sch", lambda p, n: sheet_codec.build(p, n, hp_n=False, lib="sa818s-hat"), (210.82, 99.06)),
    ("Audio", "audio.kicad_sch", sheet_audio.build, (210.82, 121.92)),
]

# Header nets this board uses (local labels on the template's header become global labels).
GLOBAL_NETS = {"GPIO17", "GPIO27", "GPIO22", "GPIO23"}
RENAME = {"GPIO18{slash}PCM.CLK": "I2S_BCLK", "GPIO19{slash}PCM.FS": "I2S_LRCLK", "GPIO20{slash}PCM.DIN": "I2S_DOUT",
          "GPIO21{slash}PCM.DOUT": "I2S_DIN", "GPIO2{slash}SDA1": "I2C_SDA", "GPIO3{slash}SCL1": "I2C_SCL",
          "GPIO26": "CODEC_RST", "GPIO14{slash}TXD0": "UART_TXD", "GPIO15{slash}RXD0": "UART_RXD"}

ROOT_FIELDS = {
    "U1": {"Value": "AT24C32D-SSHM-T", "Footprint": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "LCSC": "C60583",
           "Datasheet": "https://ww1.microchip.com/downloads/en/DeviceDoc/AT24C32D-AT24C64D-Data-Sheet-DS20006035A.pdf"},
    "R1": {"Footprint": "Resistor_SMD:R_0603_1608Metric", "LCSC": "C23018"},
    "R2": {"Footprint": "Resistor_SMD:R_0603_1608Metric", "LCSC": "C23018"},
    "C1": {"Value": "100nF", "Footprint": "Capacitor_SMD:C_0603_1608Metric", "LCSC": "C14663"},
    "JP1": {"Value": "WP write enable", "Footprint": "Jumper:SolderJumper-2_P1.3mm_Open_RoundedPad1.0x1.5mm"},
    "J1": {"LCSC": "C2977589"},
}


def build():
    schbuild.PROJECT = PROJECT
    kicadlib.PROJECT_LIBS["sa818s-hat"] = lib.symbols
    lib.write_all(PROJ)
    sharedlib.build(PROJ)   # shared footprints with 3D models, in ../kicad-shared
    text = globalize_root(open(ROOT).read(), GLOBAL_NETS | set(RENAME), RENAME)
    text = root_eeprom(text, root_uuid(text), ROOT_FIELDS, eeprom="U1", jumper="JP1", pullup="R3")
    text = refresh_embedded(text)
    ru = root_uuid(text)
    for page, (name, fname, fn, at) in enumerate(SHEETS, start=2):
        text, su = ensure_sheet(text, name, fname, at, page, PROJECT)
        sheet = fn(f"/{ru}/{su}", str(page))
        sheet.save(os.path.join(PROJ, fname))
        print("wrote", fname)
    open(ROOT, "w").write(text)


def check():
    kicad_check(CLI, ROOT, OUT, "sa818s-hat.net")


if __name__ == "__main__":
    if "--check" not in sys.argv:
        build()
    check()
