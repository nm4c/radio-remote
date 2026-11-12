"""One-page schematic overview: tile a project's KiCad sheets, cropped to what's drawn, into one SVG.

Exports every sheet with kicad-cli (no title block), crops each to its drawn content, lays them out in
rows under a heading, and writes a single SVG on a white background (readable in GitHub's dark mode too).
The SVG keeps KiCad's hidden text, so part references and net names are searchable in a browser.
Consecutive single-segment paths are merged and coordinates rounded to keep the file small.
"""
import os
import re
import subprocess
import tempfile

CLI = "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"
PAD = 4.0        # mm around each cropped sheet
GAP = 8.0        # mm between tiles
HEAD = 9.0       # mm for each tile's heading
MARGIN = 10.0


def _num(m):
    return f"{float(m.group(0)):.2f}".rstrip("0").rstrip(".")


def _body(svg):
    """Inner drawing of a KiCad SVG, minified."""
    body = svg[svg.index("<g "):svg.rindex("</svg>")]
    body = re.sub(r"<title>.*?</title>|<desc>Image generated.*?</desc>", "", body, flags=re.S)
    body = re.sub(r"-?\d+\.\d+", _num, body)
    body = re.sub(r"\s*\n\s*", " ", body)
    # merge runs of bare paths (they share their group's style) into one path each
    body = re.sub(r'(?:<path d="[^"]*" /> ?){2,}',
                  lambda m: '<path d="' + " ".join(re.findall(r'd="([^"]*)"', m.group(0))) + '" />', body)
    return body


def _bbox(svg, xmax=None):
    xs, ys = [], []
    for d in re.findall(r'<path[^>]* d="([^"]*)"', svg):
        for cmd, args in re.findall(r"([MLAZ])([^MLAZ]*)", d):
            n = [float(v) for v in re.findall(r"-?\d+\.?\d*", args)]
            for x, y in ([n[-2:]] if cmd == "A" and n else zip(n[::2], n[1::2])):
                if xmax is None or x < xmax:
                    xs.append(x)
                    ys.append(y)
    for cx, cy in re.findall(r'<circle cx="([\d.]+)" cy="([\d.]+)"', svg):
        if xmax is None or float(cx) < xmax:
            xs.append(float(cx))
            ys.append(float(cy))
    return min(xs) - PAD, min(ys) - PAD, max(xs) + PAD, max(ys) + PAD


def _esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build(root, rows, out, title, subtitle=""):
    """rows: list of rows, each a list of (sheet name, heading, xmax or None).

    Sheet name is the exported file's suffix ("" for the root sheet). xmax crops a sheet at that x (mm),
    e.g. to leave out the root sheet's hierarchy boxes.
    """
    proj = os.path.splitext(os.path.basename(root))[0]
    with tempfile.TemporaryDirectory() as tmp:
        r = subprocess.run([CLI, "sch", "export", "svg", "--exclude-drawing-sheet", "--no-background-color",
                            "-o", tmp, root], capture_output=True, text=True)
        if r.returncode:
            raise SystemExit(r.stderr or r.stdout)
        sheets = {}
        for f in os.listdir(tmp):
            name = f[len(proj):-4].lstrip("-")
            sheets[name] = open(os.path.join(tmp, f)).read()

    tiles, y, width = [], MARGIN + 16.0, 0.0
    for row in rows:
        x, h = MARGIN, 0.0
        for name, heading, xmax in row:
            svg = sheets[name]
            x0, y0, x1, y1 = _bbox(svg, xmax)
            w, hh = x1 - x0, y1 - y0
            tiles.append((x, y, w, hh, (x0, y0), heading, _body(svg)))
            x += w + GAP
            h = max(h, hh + HEAD)
        width = max(width, x - GAP + MARGIN)
        y += h + GAP
    height = y - GAP + MARGIN

    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.1f}mm" height="{height:.1f}mm" '
         f'viewBox="0 0 {width:.1f} {height:.1f}" font-family="Helvetica, Arial, sans-serif">',
         f"<title>{_esc(title)}</title>",
         f'<rect width="{width:.1f}" height="{height:.1f}" fill="#ffffff"/>',
         f'<text x="{MARGIN}" y="{MARGIN + 7}" font-size="9" font-weight="bold" fill="#202020">{_esc(title)}</text>']
    if subtitle:
        o.append(f'<text x="{MARGIN}" y="{MARGIN + 13}" font-size="4" fill="#606060">{_esc(subtitle)}</text>')
    for x, y, w, h, (x0, y0), heading, body in tiles:
        o.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h + HEAD:.1f}" rx="2" '
                 f'fill="none" stroke="#c8c8c8" stroke-width="0.4"/>')
        o.append(f'<text x="{x + 3:.1f}" y="{y + 6:.1f}" font-size="4.5" font-weight="bold" '
                 f'fill="#404040">{_esc(heading)}</text>')
        o.append(f'<svg x="{x:.1f}" y="{y + HEAD:.1f}" width="{w:.1f}" height="{h:.1f}" '
                 f'viewBox="{x0:.2f} {y0:.2f} {w:.2f} {h:.2f}">{body}</svg>')
    o.append("</svg>\n")
    with open(out, "w") as f:
        f.write("\n".join(o))
    return width, height
