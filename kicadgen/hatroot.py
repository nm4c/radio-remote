"""Root-sheet helpers for HATs built on KiCad's built-in Raspberry Pi HAT template.

Each board's build script passes its own names (project, designators, net mappings).
"""
import re
import uuid


def root_uuid(text):
    return re.search(r'\(kicad_sch\s+\(version \d+\)\s+\(generator "eeschema"\)\s+\(generator_version "[^"]*"\)\s+\(uuid "([^"]+)"\)', text).group(1)


def ensure_sheet(text, name, fname, at, page, project):
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
			(project "{project}"
				(path "/{ru}"
					(page "{page}")
				)
			)
		)
	)
'''
    i = text.rindex("\t(sheet_instances")
    return text[:i] + block + text[i:], u


def renumber_root(text, mapping, marker):
    """Rename template references once (skipped when `marker` is already a reference)."""
    if f'(property "Reference" "{marker}"' in text:
        return text
    for old, new in mapping.items():
        text = text.replace(f'(property "Reference" "{old}"', f'(property "Reference" "{new}"')
        text = text.replace(f'(reference "{old}")', f'(reference "{new}")')
    return text


def globalize_root(text, names, rename=None):
    """Turn the root sheet's local labels with these names into global labels, optionally renamed (idempotent).

    KiCad stores "/" inside label names as {slash}, so names/rename keys use that form.
    """
    rename = rename or {}
    out, i = [], 0
    for m in re.finditer(r'\t\(label "([^"]+)"\n\t\t\(at ([\d.]+) ([\d.]+) (\d+)\)', text):
        if m.group(1) not in names:
            continue
        end = text.index("\n\t)\n", m.end()) + 4
        u = re.search(r'\(uuid "([^"]+)"\)', text[m.start():end]).group(1)
        # a local label sits over its wire; a global label points away from it, so face the other way
        ang = (int(m.group(4)) + 180) % 360
        just = "right" if ang == 180 else "left"
        new_name = rename.get(m.group(1), m.group(1))
        block = (f'\t(global_label "{new_name}"\n\t\t(shape bidirectional)\n'
                 f'\t\t(at {m.group(2)} {m.group(3)} {ang})\n\t\t(fields_autoplaced yes)\n'
                 f'\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 1.27 1.27)\n\t\t\t)\n\t\t\t(justify {just})\n\t\t)\n'
                 f'\t\t(uuid "{u}")\n\t)\n')
        out.append(text[i:m.start()])
        out.append(block)
        i = end
    out.append(text[i:])
    return "".join(out)


def _symbol_block(text, ref):
    """(start, end) of the root symbol whose Reference is ref."""
    i = text.index(f'(property "Reference" "{ref}"')
    start = text.rindex("\n\t(symbol\n", 0, i) + 1
    end = text.index("\n\t)\n", i) + 4
    return start, end


def set_dnp(text, ref, dnp=True, in_bom=False):
    """Mark a root-sheet symbol Do Not Populate (left out of assembly) and, by default, out of the BOM."""
    st, en = _symbol_block(text, ref)
    blk = text[st:en].replace(f"(dnp {'no' if dnp else 'yes'})", f"(dnp {'yes' if dnp else 'no'})", 1)
    blk = re.sub(r"\(in_bom (yes|no)\)", f"(in_bom {'yes' if in_bom else 'no'})", blk, count=1)
    return text[:st] + blk + text[en:]


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


def root_eeprom(text, ru, fields, eeprom="U1", jumper="JP1", pullup="R3"):
    """HAT ID EEPROM to the HAT design guide on KiCad's template sheet:
    AT24C32D, A0-A2 to GND, WP pulled up by `pullup` (protected), `jumper` to GND enables writes, TP1 on WP.

    Coordinates and UUIDs are those of KiCad's built-in RaspberryPi-HAT template.
    """
    from schbuild import Sheet
    from sexp import dump
    for ref, f in fields.items():
        text = set_fields(text, ref, f)
    text = text.replace("Bridging JP1 enables write protection.",
                        f"{pullup} holds WP high: write-protected.\\nBridge {jumper} (or ground TP1) to write it.")
    if f'(reference "{pullup}")' in text:
        return text
    # A0-A2: drop the no-connect flags, bus them to the ground wire
    for u in ("00f1806c-4158-494e-882b-c5ac9b7a930a", "00f1806c-4158-494e-882b-c5ac9b7a930b",
              "00f1806c-4158-494e-882b-c5ac9b7a930c"):
        text = _remove_block(text, "no_connect", u)
    text = _remove_block(text, "wire", "d4943e77-b82c-4b31-b869-1ebef0c1006c")   # (67.31,187.96)-(81.28,187.96)
    # the jumper's far side: +3V3 -> GND
    st, en = _symbol_block(text, "#PWR0103")
    blk = text[st:en]
    blk = blk.replace('(lib_id "power:+3.3V")', '(lib_id "power:GND")').replace("(at 101.6 180.34 270)", "(at 101.6 180.34 0)")
    blk = re.sub(r'\(property "Value" "\+3V3"\n(\t+)\(at [^)]*\)', r'(property "Value" "GND"\n\1(at 101.6 184.15 0)', blk)
    blk = re.sub(r'\(property "Description" "(?:[^"\\]|\\.)*"', '(property "Description" "Power symbol creates a global label with name \\"GND\\" , ground"', blk)
    text = text[:st] + blk + text[en:]

    # the EEPROM's value text moves below its ground symbol, clear of the new WP lead
    st, en = _symbol_block(text, eeprom)
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
    # WP: label at the jumper's pin 1, pull-up and test point TP1 to the right
    s.wire((92.71, 180.34), (92.71, 184.15))
    s.junction((92.71, 180.34))
    s.label("EE_WP", (92.71, 184.15), (0, 1))
    s.symbol("Device:R_Small", pullup, "1k", (124.46, 172.72), footprint="Resistor_SMD:R_0603_1608Metric",
             lcsc="C21190", ref_at=(126.365, 171.45), val_at=(126.365, 173.99), ref_just="left", val_just="left",
             conn={"1": ("pwr", "+3V3", 2.54)})
    node = (124.46, 180.34)
    s.wire(s.p(f"{pullup}.2"), node)
    s.wire(node, (129.54, 180.34))
    s.junction(node)
    s.symbol("Connector:TestPoint", "TP1", "WP", (129.54, 180.34), footprint="TestPoint:TestPoint_Pad_D1.5mm",
             in_bom=False, ref_at=(131.445, 176.53), val_at=(131.445, 179.07), ref_just="left", val_just="left")
    s.wire(node, (124.46, 182.88))
    s.label("EE_WP", (124.46, 182.88), (0, 1))
    s.text(f"WP: {pullup} holds it high = write-protected.", (115.57, 190.5))
    s.text(f"Close {jumper} (or ground TP1) to write the EEPROM.", (115.57, 193.04))
    items = "".join(dump(x, 1) + "\n" for x in s.items)
    i = text.rindex("\t(sheet_instances")
    text = text[:i] + items + text[i:]
    # embed any library symbols the root doesn't have yet
    k = text.index("\t(lib_symbols\n") + len("\t(lib_symbols\n")
    extra = "".join(dump(sym, 2) + "\n" for lid, sym in s.libs.items() if f'(symbol "{lid}"' not in text)
    return text[:k] + extra + text[k:]


def drop_wp_jumper(text, jumper="JP1", gnd="#PWR0103", pullup="R3"):
    """Remove the EEPROM write-enable solder jumper that root_eeprom leaves on the template sheet,
    with its ground symbol and the stub between them. WP stays pulled up by `pullup`; ground TP1
    to write the EEPROM. Safe to run again."""
    if f'(property "Reference" "{jumper}"' not in text:
        return text
    for ref in (jumper, gnd):
        st, en = _symbol_block(text, ref)
        text = text[:st] + text[en:]
    m = re.search(r'\n\t\(wire\n\t\t\(pts\n\t\t\t\(xy 100\.33 180\.34\) \(xy 101\.6 180\.34\)\n.*?\n\t\)\n', text, re.S)
    if m:
        text = text[:m.start() + 1] + text[m.end():]
    text = text.replace(f"Bridge {jumper} (or ground TP1) to write it.", "Ground TP1 to write it.")
    text = text.replace(f"Close {jumper} (or ground TP1) to write the EEPROM.", "Ground TP1 to write the EEPROM.")
    return text


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


def check(cli, root, out, netname):
    """Run kicad-cli: netlist, ERC report and SVG export."""
    import os
    import subprocess
    os.makedirs(out, exist_ok=True)
    net = os.path.join(out, netname)
    r = subprocess.run([cli, "sch", "export", "netlist", "--format", "kicadsexpr", "-o", net, root], capture_output=True, text=True)
    print("netlist:", r.returncode, (r.stderr or r.stdout).strip()[-400:])
    erc = os.path.join(out, "erc.rpt")
    r = subprocess.run([cli, "sch", "erc", "--severity-all", "-o", erc, root], capture_output=True, text=True)
    print("erc:", r.returncode, (r.stderr or r.stdout).strip()[-300:])
    r = subprocess.run([cli, "sch", "export", "svg", "-o", os.path.join(out, "svg"), root], capture_output=True, text=True)
    print("svg:", r.returncode, (r.stderr or r.stdout).strip()[-200:])
