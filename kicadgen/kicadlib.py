"""Load symbols from KiCad's stock libraries, flatten 'extends', and compute pin positions."""
import copy
import os

from sexp import Q, find, find_all, parse

STOCK = "/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols"
_cache = {}
PROJECT_LIBS = {}   # name -> callable returning {symbol_name: sexp}; registered by each board's build script


def _lib(name):
    if name in PROJECT_LIBS:                  # project library, generated in code
        return PROJECT_LIBS[name]()
    if name not in _cache:
        path = os.path.join(STOCK, name + ".kicad_sym")
        _cache[name] = {s[1]: s for s in find_all(parse(open(path).read()), "symbol")}
    return _cache[name]


def load_symbol(lib_id):
    """Return a flattened copy of lib_id ('Lib:Name') named for embedding in a schematic."""
    lib, name = lib_id.split(":", 1)
    syms = _lib(lib)
    sym = copy.deepcopy(syms[name])
    ext = find(sym, "extends")
    if ext:
        parent = copy.deepcopy(syms[ext[1]])
        # child keeps its own properties; drawing units come from the parent
        props = {p[1]: p for p in find_all(sym, "property")}
        merged = [parent[0], Q(name)]
        for x in parent[2:]:
            if isinstance(x, list) and x[0] == "property" and x[1] in props:
                merged.append(props.pop(x[1]))
            elif isinstance(x, list) and x[0] == "symbol":
                merged.append(["symbol", Q(x[1].replace(ext[1], name, 1))] + x[2:])
            else:
                merged.append(x)
        for p in props.values():
            merged.insert(2, p)
        sym = merged
    sym[1] = Q(lib_id)
    return sym


def pins(sym, unit=1):
    """{pin_number: (x, y, angle, name)} in library coordinates (Y up) for one unit plus shared unit 0."""
    out = {}
    for u in find_all(sym, "symbol"):
        parts = str(u[1]).rsplit("_", 2)
        if len(parts) == 3 and parts[1] not in ("0", str(unit)):
            continue
        for p in find_all(u, "pin"):
            at = find(p, "at")
            num = find(p, "number")[1]
            nm = find(p, "name")[1]
            out[str(num)] = (float(at[1]), float(at[2]), float(at[3]) if len(at) > 3 else 0.0, str(nm))
    return out


def place(pin, at, rot, mirror=None):
    """Schematic (x, y) of a pin endpoint and the outward direction (dx, dy) for a symbol at 'at'.

    rot is KiCad's symbol rotation in degrees (counter-clockwise on screen).
    """
    x, y, ang, _ = pin
    if mirror == "y":
        x, ang = -x, (180 - ang) % 360
    elif mirror == "x":
        y, ang = -y, (-ang) % 360
    # lib coords (Y up) rotated CCW by rot
    r = rot % 360
    rx, ry = {0: (x, y), 90: (-y, x), 180: (-x, -y), 270: (y, -x)}[r]
    a = (ang + r) % 360
    sx, sy = round(at[0] + rx, 4), round(at[1] - ry, 4)
    # pin angle points from the endpoint toward the body; outward is the reverse
    ux, uy = {0: (-1, 0), 90: (0, 1), 180: (1, 0), 270: (0, -1)}[int(a)]
    return (sx, sy), (ux, uy)
