"""Generate HAT sub-sheets, link them from the root schematic, and run KiCad checks.

Usage (KiCad must not have the project open):
    python3 build.py            build + check
    python3 build.py --check    check only
"""
import os
import re
import subprocess
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.environ.get("PIHAT_PROJ") or os.path.abspath(os.path.join(HERE, "..", ".."))
ROOT = os.path.join(PROJ, "pihat.kicad_sch")
CLI = "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"
OUT = os.path.join(PROJ, "build")

sys.path.insert(0, HERE)
import sheet_power  # noqa: E402
import sheet_control  # noqa: E402
import sheet_connectors  # noqa: E402
import sheet_codec  # noqa: E402
import sheet_audio  # noqa: E402
import projlib  # noqa: E402

# (sheet name, file, builder, box position on the root sheet)
SHEETS = [
    ("Power", "power.kicad_sch", sheet_power.build, (210.82, 30.48)),
    ("Control", "control.kicad_sch", sheet_control.build, (210.82, 53.34)),
    ("Radio connectors", "connectors.kicad_sch", sheet_connectors.build, (210.82, 76.2)),
    ("Codec", "codec.kicad_sch", sheet_codec.build, (210.82, 99.06)),
    ("Audio isolation", "audio.kicad_sch", sheet_audio.build, (210.82, 121.92)),
]


def root_uuid(text):
    return re.search(r'\(kicad_sch\s+\(version \d+\)\s+\(generator "eeschema"\)\s+\(generator_version "[^"]*"\)\s+\(uuid "([^"]+)"\)', text).group(1)


def ensure_sheet(text, name, fname, at, page):
    """Return (text, sheet_uuid). Reuses an existing sheet box for fname so instance paths stay stable."""
    m = re.search(r'\(sheet\s+\(at [^)]*\)(?:(?!\n\t\(sheet\b).)*?\(property "Sheetfile" "' + re.escape(fname) + r'"', text, re.S)
    if m:
        u = re.search(r'\(uuid "([^"]+)"\)', text[m.start():m.end()]).group(1)
        return text, u
    u = str(uuid.uuid4())
    ru = root_uuid(text)
    x, y = at
    block = f'''	(sheet
		(at {x} {y})
		(size 38.1 12.7)
		(exclude_from_sim no)
		(in_bom yes)
		(on_board yes)
		(dnp no)
		(fields_autoplaced yes)
		(stroke
			(width 0.1524)
			(type solid)
		)
		(fill
			(color 0 0 0 0.0000)
		)
		(uuid "{u}")
		(property "Sheetname" "{name}"
			(at {x} {round(y - 0.7116, 4)} 0)
			(effects
				(font
					(size 1.27 1.27)
				)
				(justify left bottom)
			)
		)
		(property "Sheetfile" "{fname}"
			(at {x} {round(y + 13.2834, 4)} 0)
			(effects
				(font
					(size 1.27 1.27)
				)
				(justify left top)
			)
		)
		(instances
			(project "pihat"
				(path "/{ru}"
					(page "{page}")
				)
			)
		)
	)
'''
    i = text.rindex("\t(sheet_instances")
    return text[:i] + block + text[i:], u


# Template parts on the root sheet renamed to the HAT's designators (see docs/schematic).
ROOT_RENAME = {"U1": "U6", "R1": "R22", "R2": "R23", "C1": "C15", "JP1": "JP2"}


def renumber_root(text):
    """Rename template references once. Safe to re-run: only exact old names are touched."""
    if '(property "Reference" "U6"' in text:
        return text
    for old, new in ROOT_RENAME.items():
        text = text.replace(f'(property "Reference" "{old}"', f'(property "Reference" "{new}"')
        text = text.replace(f'(reference "{old}")', f'(reference "{new}")')
    return text


# Header nets the HAT uses: local labels on the template's header become global labels.
GLOBAL_NETS = {"GPIO17", "GPIO27", "GPIO22", "GPIO23"}
# template label -> HAT net name (made global)
# (KiCad stores "/" inside label names as {slash})
RENAME = {"GPIO18{slash}PCM.CLK": "I2S_BCLK", "GPIO19{slash}PCM.FS": "I2S_LRCLK", "GPIO20{slash}PCM.DIN": "I2S_DOUT",
          "GPIO21{slash}PCM.DOUT": "I2S_DIN", "GPIO2{slash}SDA1": "I2C_SDA", "GPIO3{slash}SCL1": "I2C_SCL",
          "GPIO26": "CODEC_RST"}


def globalize_root(text, names=GLOBAL_NETS | set(RENAME)):
    """Turn the root sheet's local labels with these names into global labels (idempotent)."""
    out, i = [], 0
    for m in re.finditer(r'\t\(label "([^"]+)"\n\t\t\(at ([\d.]+) ([\d.]+) (\d+)\)', text):
        if m.group(1) not in names:
            continue
        end = text.index("\n\t)\n", m.end()) + 4
        u = re.search(r'\(uuid "([^"]+)"\)', text[m.start():end]).group(1)
        # a local label sits over its wire; a global label points away from it, so face the other way
        ang = (int(m.group(4)) + 180) % 360
        just = "right" if ang == 180 else "left"
        new_name = RENAME.get(m.group(1), m.group(1))
        block = (f'\t(global_label "{new_name}"\n\t\t(shape bidirectional)\n'
                 f'\t\t(at {m.group(2)} {m.group(3)} {ang})\n\t\t(fields_autoplaced yes)\n'
                 f'\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 1.27 1.27)\n\t\t\t)\n\t\t\t(justify {just})\n\t\t)\n'
                 f'\t\t(uuid "{u}")\n\t)\n')
        out.append(text[i:m.start()])
        out.append(block)
        i = end
    out.append(text[i:])
    return "".join(out)


# ------------------------------------------------------------------ root EEPROM / J1 tidy
ROOT_FIELDS = {
    "U6": {"Value": "AT24C32D-SSHM-T", "Footprint": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "LCSC": "C60583",
           "Datasheet": "https://ww1.microchip.com/downloads/en/DeviceDoc/AT24C32D-AT24C64D-Data-Sheet-DS20006035A.pdf"},
    "R22": {"Footprint": "Resistor_SMD:R_0603_1608Metric", "LCSC": "C23018"},
    "R23": {"Footprint": "Resistor_SMD:R_0603_1608Metric", "LCSC": "C23018"},
    "C15": {"Value": "100nF", "Footprint": "Capacitor_SMD:C_0603_1608Metric", "LCSC": "C14663"},
    "JP2": {"Value": "WP write enable", "Footprint": "Jumper:SolderJumper-2_P1.3mm_Open_RoundedPad1.0x1.5mm"},
    "J1": {"LCSC": "C2977589"},
}


def _symbol_block(text, ref):
    """(start, end) of the root symbol whose Reference is ref."""
    i = text.index(f'(property "Reference" "{ref}"')
    start = text.rindex("\n\t(symbol\n", 0, i) + 1
    end = text.index("\n\t)\n", i) + 4
    return start, end


def set_fields(text, ref, fields):
    start, end = _symbol_block(text, ref)
    block = text[start:end]
    at = re.search(r"\(at ([\d.]+) ([\d.]+) \d+\)", block)
    for name, value in fields.items():
        pat = re.compile(r'\(property "' + re.escape(name) + r'" "[^"]*"')
        if pat.search(block):
            block = pat.sub(f'(property "{name}" "{value}"', block, count=1)
        else:
            prop = (f'\t\t(property "{name}" "{value}"\n\t\t\t(at {at.group(1)} {at.group(2)} 0)\n\t\t\t(hide yes)\n'
                    f'\t\t\t(show_name no)\n\t\t\t(do_not_autoplace no)\n\t\t\t(effects\n\t\t\t\t(font\n'
                    f'\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n')
            k = block.index("\t\t(pin ")
            block = block[:k] + prop + block[k:]
    return text[:start] + block + text[end:]


def _remove_block(text, kind, uuid_):
    """Remove a top-level (kind ...) item identified by its uuid."""
    j = text.index(f'(uuid "{uuid_}")')
    start = text.rindex(f"\n\t({kind}\n", 0, j) + 1
    end = text.index("\n\t)\n", j) + 4
    return text[:start] + text[end:]


def root_eeprom(text, ru):
    """HAT ID EEPROM to the HAT design guide: AT24C32D, A0-A2 to GND, WP pulled up (protected), JP2 to GND enables writes."""
    for ref, f in ROOT_FIELDS.items():
        text = set_fields(text, ref, f)
    text = text.replace("Bridging JP1 enables write protection.",
                        "R24 holds WP high: write-protected.\\nBridge JP2 (or ground TP1) to write it.")
    if '(reference "R24")' in text:
        return text
    from schbuild import Sheet
    from sexp import dump
    # A0-A2: drop the no-connect flags, bus them to the ground wire
    for u in ("00f1806c-4158-494e-882b-c5ac9b7a930a", "00f1806c-4158-494e-882b-c5ac9b7a930b",
              "00f1806c-4158-494e-882b-c5ac9b7a930c"):
        text = _remove_block(text, "no_connect", u)
    text = _remove_block(text, "wire", "d4943e77-b82c-4b31-b869-1ebef0c1006c")   # (67.31,187.96)-(81.28,187.96)
    # JP2's far side: +3V3 -> GND
    st, en = _symbol_block(text, "#PWR0103")
    blk = text[st:en]
    blk = blk.replace('(lib_id "power:+3.3V")', '(lib_id "power:GND")').replace("(at 101.6 180.34 270)", "(at 101.6 180.34 0)")
    blk = re.sub(r'\(property "Value" "\+3V3"\n(\t+)\(at [^)]*\)', r'(property "Value" "GND"\n\1(at 101.6 184.15 0)', blk)
    blk = re.sub(r'\(property "Description" "(?:[^"\\]|\\.)*"', '(property "Description" "Power symbol creates a global label with name \\"GND\\" , ground"', blk)
    text = text[:st] + blk + text[en:]

    # U6's value text moves below its ground symbol, clear of the new WP lead
    st, en = _symbol_block(text, "U6")
    blk = re.sub(r'(\(property "Value" "[^"]*"\n\t+)\(at [^)]*\)', r'\1(at 81.28 197 0)', text[st:en], count=1)
    text = text[:st] + blk + text[en:]

    s = Sheet("Root", f"/{ru}", "1")
    s.wire((67.31, 187.96), (69.85, 187.96))
    s.wire((69.85, 187.96), (81.28, 187.96))
    for y in (175.26, 177.8, 180.34):
        s.wire((71.12, y), (69.85, y))
    s.wire((69.85, 175.26), (69.85, 177.8))
    s.wire((69.85, 177.8), (69.85, 180.34))
    s.wire((69.85, 180.34), (69.85, 187.96))
    for pt in ((69.85, 177.8), (69.85, 180.34), (69.85, 187.96)):
        s.junction(pt)
    # WP: label at JP2 pin 1, pull-up R24 and test point TP1 to the right
    s.wire((92.71, 180.34), (92.71, 184.15))
    s.junction((92.71, 180.34))
    s.label("EE_WP", (92.71, 184.15), (0, 1))
    s.symbol("Device:R_Small", "R24", "1k", (124.46, 172.72), footprint="Resistor_SMD:R_0603_1608Metric",
             lcsc="C21190", ref_at=(126.365, 171.45), val_at=(126.365, 173.99), ref_just="left", val_just="left",
             conn={"1": ("pwr", "+3V3", 2.54)})
    node = (124.46, 180.34)
    s.wire(s.p("R24.2"), node)
    s.wire(node, (129.54, 180.34))
    s.junction(node)
    s.symbol("Connector:TestPoint", "TP1", "WP", (129.54, 180.34), footprint="TestPoint:TestPoint_Pad_D1.5mm",
             in_bom=False, ref_at=(131.445, 176.53), val_at=(131.445, 179.07), ref_just="left", val_just="left")
    s.wire(node, (124.46, 182.88))
    s.label("EE_WP", (124.46, 182.88), (0, 1))
    s.text("WP: R24 holds it high = write-protected.", (115.57, 190.5))
    s.text("Close JP2 (or ground TP1) to write the EEPROM.", (115.57, 193.04))
    items = "".join(dump(x, 1) + "\n" for x in s.items)
    i = text.rindex("\t(sheet_instances")
    text = text[:i] + items + text[i:]
    # embed any library symbols the root doesn't have yet
    k = text.index("\t(lib_symbols\n") + len("\t(lib_symbols\n")
    extra = "".join(dump(sym, 2) + "\n" for lid, sym in s.libs.items() if f'(symbol "{lid}"' not in text)
    return text[:k] + extra + text[k:]


def refresh_embedded(text, lib_ids=("Device:R_Small",)):
    """Replace the template's out-of-date embedded symbols with KiCad 10's.

    Only symbols whose pins are unchanged: KiCad 10's SolderJumper_2_Open has wider pins, so the template copy stays.
    """
    from kicadlib import load_symbol
    from sexp import dump
    for lid in lib_ids:
        key = f'\t\t(symbol "{lid}"\n'
        if key not in text:
            continue
        i = text.index(key)
        end = text.index("\n\t\t)\n", i) + 5
        text = text[:i] + dump(load_symbol(lid), 2) + "\n" + text[end:]
    return text


def build():
    projlib.write_all(PROJ)
    text = globalize_root(renumber_root(open(ROOT).read()))
    text = root_eeprom(text, root_uuid(text))
    text = refresh_embedded(text)
    ru = root_uuid(text)
    for page, (name, fname, fn, at) in enumerate(SHEETS, start=2):
        text, su = ensure_sheet(text, name, fname, at, page)
        sheet = fn(f"/{ru}/{su}", str(page))
        sheet.save(os.path.join(PROJ, fname))
        print("wrote", fname)
    open(ROOT, "w").write(text)


def check():
    os.makedirs(OUT, exist_ok=True)
    net = os.path.join(OUT, "pihat.net")
    r = subprocess.run([CLI, "sch", "export", "netlist", "--format", "kicadsexpr", "-o", net, ROOT], capture_output=True, text=True)
    print("netlist:", r.returncode, (r.stderr or r.stdout).strip()[-400:])
    erc = os.path.join(OUT, "erc.rpt")
    r = subprocess.run([CLI, "sch", "erc", "--severity-all", "-o", erc, ROOT], capture_output=True, text=True)
    print("erc:", r.returncode, (r.stderr or r.stdout).strip()[-300:])
    r = subprocess.run([CLI, "sch", "export", "svg", "-o", os.path.join(OUT, "svg"), ROOT], capture_output=True, text=True)
    print("svg:", r.returncode, (r.stderr or r.stdout).strip()[-200:])


if __name__ == "__main__":
    if "--check" not in sys.argv:
        build()
    check()
