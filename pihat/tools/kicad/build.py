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

# (sheet name, file, builder, box position on the root sheet)
SHEETS = [
    ("Power", "power.kicad_sch", sheet_power.build, (210.82, 30.48)),
    ("Control", "control.kicad_sch", sheet_control.build, (210.82, 53.34)),
    ("Radio connectors", "connectors.kicad_sch", sheet_connectors.build, (210.82, 76.2)),
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


def globalize_root(text, names=GLOBAL_NETS):
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
        block = (f'\t(global_label "{m.group(1)}"\n\t\t(shape bidirectional)\n'
                 f'\t\t(at {m.group(2)} {m.group(3)} {ang})\n\t\t(fields_autoplaced yes)\n'
                 f'\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 1.27 1.27)\n\t\t\t)\n\t\t\t(justify {just})\n\t\t)\n'
                 f'\t\t(uuid "{u}")\n\t)\n')
        out.append(text[i:m.start()])
        out.append(block)
        i = end
    out.append(text[i:])
    return "".join(out)


def build():
    text = globalize_root(renumber_root(open(ROOT).read()))
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
