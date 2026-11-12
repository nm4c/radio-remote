"""Generate the SA818 HAT schematic sheets, link them from the root, and run KiCad checks.

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
from hatroot import check as kicad_check, drop_wp_jumper, ensure_sheet, set_dnp, globalize_root, refresh_embedded, root_eeprom, root_uuid  # noqa: E402
import sheet_power  # noqa: E402  (pihat)
import sheet_codec  # noqa: E402  (pihat)
import sheet_rfpower  # noqa: E402
import sheet_rf  # noqa: E402
import sheet_audio  # noqa: E402
import lib  # noqa: E402

PROJECT = "sa818s-hat"


def retune(sheet, parts):
    """Change value, footprint and LCSC of parts on a sheet shared with the pihat board, for this board
    only (RF/noise review, Nov 2026): {ref: (value, footprint, lcsc)}."""
    left = dict(parts)
    for node in sheet.items:
        if not (isinstance(node, list) and node and node[0] == "symbol"):
            continue
        props = {str(p[1]): p for p in node if isinstance(p, list) and p and p[0] == "property"}
        ref = str(props["Reference"][2]) if "Reference" in props else None
        if ref not in left:
            continue
        val, fp, lcsc = left.pop(ref)
        props["Value"][2] = schbuild.Q(val)
        props["Footprint"][2] = schbuild.Q(fp)
        if "LCSC" in props:
            props["LCSC"][2] = schbuild.Q(lcsc)
        else:
            node.insert(node.index(props["Footprint"]) + 1, schbuild.prop("LCSC", lcsc, (0, 0), hide=True))
    assert not left, f"retune: not found {sorted(left)}"
    return sheet
SHEETS = [
    ("Power", "power.kicad_sch", lambda p, n: retune(sheet_power.build(p, n, supply="pi", codec_ldo="tps7a2033",
                                                                         u5_from="5v"), {
        "C25": ("10uF X7R", "Capacitor_SMD:C_0805_2012Metric", "C95841"),
        **{c: ("1uF X7R", "Capacitor_SMD:C_0603_1608Metric", "C29936") for c in ("C24", "C26", "C27")}}), (210.82, 30.48)),
    ("RF power", "rfpower.kicad_sch", sheet_rfpower.build, (210.82, 53.34)),
    ("Transceiver", "transceiver.kicad_sch", sheet_rf.build, (210.82, 76.2)),
    ("Codec", "codec.kicad_sch", lambda p, n: retune(sheet_codec.build(p, n, hp_n=False, lib="sa818s-hat"), {
        "C13": ("10uF", "Capacitor_SMD:C_0805_2012Metric", "C95841"),
        "R16": ("100R", "Resistor_SMD:R_0603_1608Metric", "C22775")}), (210.82, 99.06)),
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
    "J1": {"LCSC": "C2977589"},
}


def build():
    schbuild.PROJECT = PROJECT
    kicadlib.PROJECT_LIBS["sa818s-hat"] = lib.symbols
    lib.write_all(PROJ)
    sharedlib.build(PROJ)   # shared footprints with 3D models, in ../kicad-shared
    text = globalize_root(open(ROOT).read(), GLOBAL_NETS | set(RENAME), RENAME)
    text = root_eeprom(text, root_uuid(text), ROOT_FIELDS, eeprom="U1", jumper="JP1", pullup="R3")
    text = drop_wp_jumper(text)   # no solder jumper: ground TP1 to write the EEPROM
    text = set_dnp(text, "J1")    # GPIO socket: hand-soldered, left out of JLC assembly and the BOM
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
