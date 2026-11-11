"""Build KiCad 10 schematic sheets programmatically.

Symbols come from KiCad's stock libraries (flattened and embedded). Every pin
connection is placed at the pin's computed endpoint, so connectivity can be
checked with `kicad-cli sch export netlist`.
"""
import uuid

from kicadlib import load_symbol, pins, place
from sexp import Q, dump, find, find_all

FONT = ["effects", ["font", ["size", 1.27, 1.27]]]
PROJECT = "pihat"   # set by the board build script (schbuild.PROJECT = ...)


NS = uuid.UUID("6f1c2a4e-3d5b-4c8a-9e7f-2b1d0c9a8e7d")   # fixed namespace: stable UUIDs across rebuilds


def U(key=None):
    """Deterministic UUID from a key, so regenerating a sheet keeps schematic-to-PCB links intact."""
    return Q(str(uuid.uuid5(NS, key)) if key else str(uuid.uuid4()))


def _fx(x):
    return round(float(x), 4)


def prop(name, value, at, hide=False, justify=None, angle=0):
    eff = ["effects", ["font", ["size", 1.27, 1.27]]]
    if justify:
        eff.append(["justify", *justify.split()])
    p = ["property", Q(name), Q(value), ["at", _fx(at[0]), _fx(at[1]), angle]]
    if hide:
        p.append(["hide", "yes"])
    p += [["show_name", "no"], ["do_not_autoplace", "no"], eff]
    return p


class Sheet:
    def __init__(self, title, path, page, file_uuid=None):
        self.title = title
        self.path = path            # instance path, e.g. "/<root-uuid>/<sheet-uuid>"
        self.page = page
        self.uuid = file_uuid or U(f"sheet/{title}")
        self._seq = {}
        self.libs = {}
        self.items = []
        self.pinpos = {}            # "REF.pin" -> (x, y)
        self.pwr_n = 0
        self.wires = []

    def _k(self, *parts):
        """Key for an item; repeats of the same key get a running suffix."""
        base = "/".join([self.title] + [str(p) for p in parts])
        n = self._seq.get(base, 0)
        self._seq[base] = n + 1
        return f"{base}#{n}"

    # ------------------------------------------------------------ symbols
    def symbol(self, lib_id, ref, value, at, rot=0, mirror=None, footprint="", lcsc="",
               datasheet="", conn=None, ref_at=None, val_at=None, fields_dir="right", dnp=False, in_bom=True, unit=1, ref_just=None, val_just=None,
               fields=None):
        """fields: extra hidden properties, e.g. {"MPN": "..."} for a part chosen by manufacturer number."""
        if lib_id not in self.libs:
            self.libs[lib_id] = load_symbol(lib_id)
        sym = self.libs[lib_id]
        pdefs = pins(sym, unit)
        x, y = at
        desc = next((str(p[2]) for p in find_all(sym, "property") if p[1] == "Description"), "")
        if not footprint:
            footprint = next((str(p[2]) for p in find_all(sym, "property") if p[1] == "Footprint"), "")
        is_pwr = lib_id.startswith("power:")
        # field placement
        if ref_at is None or val_at is None:
            if fields_dir == "right":
                ra, va, j = (x + 2.54, y - 1.27), (x + 2.54, y + 1.27), "left"
            elif fields_dir == "left":
                ra, va, j = (x - 2.54, y - 1.27), (x - 2.54, y + 1.27), "right"
            elif fields_dir == "above":
                ra, va, j = (x, y - 5.08), (x, y - 2.79), None
            else:  # below
                ra, va, j = (x, y + 3.3), (x, y + 5.6), None
            ref_at = ref_at or ra
            val_at = val_at or va
        else:
            j = None
        node = ["symbol", ["lib_id", Q(lib_id)], ["at", _fx(x), _fx(y), rot]]
        if mirror:
            node.append(["mirror", mirror])
        node += [["unit", unit], ["body_style", 1], ["exclude_from_sim", "no"],
                 ["in_bom", "yes" if in_bom and not is_pwr else "no"], ["on_board", "no" if is_pwr else "yes"],
                 ["in_pos_files", "no" if is_pwr else "yes"], ["dnp", "yes" if dnp else "no"],
                 ["uuid", U(self._k("sym", ref, unit))]]
        fa = 90 if rot % 180 == 90 else 0
        node.append(prop("Reference", ref, ref_at, hide=is_pwr, justify=ref_just or j, angle=fa))
        node.append(prop("Value", value, val_at, justify=val_just or j, angle=fa))
        node.append(prop("Footprint", footprint, (x, y), hide=True))
        node.append(prop("Datasheet", datasheet, (x, y), hide=True))
        node.append(prop("Description", desc, (x, y), hide=True))
        if lcsc:
            node.append(prop("LCSC", lcsc, (x, y), hide=True))
        for name, val in (fields or {}).items():
            node.append(prop(name, val, (x, y), hide=True))
        for num in pdefs:
            node.append(["pin", Q(num), ["uuid", U(self._k("pin", ref, unit, num))]])
        node.append(["instances", ["project", Q(PROJECT), ["path", Q(self.path), ["reference", Q(ref)], ["unit", unit]]]])
        self.items.append(node)
        ends = {}
        for num, pd in pdefs.items():
            (px, py), d = place(pd, at, rot, mirror)
            ends[num] = ((px, py), d)
            self.pinpos[f"{ref}.{num}"] = (px, py)
        for num, c in (conn or {}).items():
            self.connect(ends[str(num)], c)
        return ends

    def flag(self, at):
        """PWR_FLAG: tells ERC this net is driven (power arrives through a connector or passive part)."""
        self.pwr_n += 1
        self.symbol("power:PWR_FLAG", f"#FLG{self.page}{self.pwr_n:02d}", "PWR_FLAG", at,
                    ref_at=at, val_at=(at[0], at[1] - 3.81))

    def power(self, net, at, body_dir=None):
        """Power symbol whose pin sits at 'at'. body_dir: where the symbol body points (default: GND down, rails up)."""
        self.pwr_n += 1
        lib = "power:GND" if net in ("RGND",) else "power:" + net
        try:
            load_symbol(lib)
        except KeyError:  # custom rail name: draw it with a stock symbol, the Value names the net
            lib = "power:GND" if net.endswith("GND") else "power:+5V"
        default = "down" if net.endswith("GND") else "up"
        bd = body_dir or default
        order = ["down", "right", "up", "left"] if default == "down" else ["up", "left", "down", "right"]
        rot = order.index(bd) * 90
        self.symbol(lib, f"#PWR{self.page}{self.pwr_n:02d}", net, at, rot=rot,
                    ref_at=(at[0], at[1]), val_at=self._pwr_text(at, bd))

    @staticmethod
    def _pwr_text(at, bd):
        dx, dy = {"up": (0, -3.81), "down": (0, 5.08), "left": (-6.35, 0), "right": (6.35, 0)}[bd]
        return (at[0] + dx, at[1] + dy)

    def connect(self, end, c, stub=2.54):
        (px, py), (ux, uy) = end
        kind = c[0]
        if kind == "nc":
            self.items.append(["no_connect", ["at", _fx(px), _fx(py)], ["uuid", U(self._k("nc", px, py))]])
            return
        if kind == "pwr":
            s = c[2] if len(c) > 2 else 0
            tx, ty = px + ux * s, py + uy * s
            if s:
                self.wire((px, py), (tx, ty))
            body = {(0, -1): "up", (0, 1): "down", (-1, 0): "left", (1, 0): "right"}[(ux, uy)]
            self.power(c[1], (tx, ty), body_dir=body)
            return
        if kind == "glabel":
            s_ = c[2] if len(c) > 2 else stub
            tx, ty = px + ux * s_, py + uy * s_
            if s_:
                self.wire((px, py), (tx, ty))
            self.label(c[1], (tx, ty), (ux, uy), glob=True)
            return
        if kind == "label":
            s = c[2] if len(c) > 2 else stub
            tx, ty = px + ux * s, py + uy * s
            if s:
                self.wire((px, py), (tx, ty))
            self.label(c[1], (tx, ty), (ux, uy))
            return
        raise ValueError(c)

    # ------------------------------------------------------------ primitives
    def wire(self, *pts):
        for a, b in zip(pts, pts[1:]):
            self.items.append(["wire", ["pts", ["xy", _fx(a[0]), _fx(a[1])], ["xy", _fx(b[0]), _fx(b[1])]],
                               ["stroke", ["width", 0], ["type", "solid"]], ["uuid", U(self._k("w", a, b))]])
            self.wires.append((a, b))

    def junction(self, at):
        self.items.append(["junction", ["at", _fx(at[0]), _fx(at[1])], ["diameter", 0], ["color", 0, 0, 0, 0], ["uuid", U(self._k("j", at))]])

    def label(self, name, at, direction=(1, 0), glob=False):
        ang, just = {(1, 0): (0, "left bottom"), (-1, 0): (180, "right bottom"),
                     (0, -1): (90, "left bottom"), (0, 1): (270, "right bottom")}[tuple(direction)]
        eff = ["effects", ["font", ["size", 1.27, 1.27]], ["justify", *just.split()]]
        if glob:
            gj = {0: "left", 180: "right", 90: "left", 270: "right"}[ang]
            self.items.append(["global_label", Q(name), ["shape", "bidirectional"], ["at", _fx(at[0]), _fx(at[1]), ang],
                               ["fields_autoplaced", "yes"],
                               ["effects", ["font", ["size", 1.27, 1.27]], ["justify", gj]], ["uuid", U(self._k("gl", name, at))]])
        else:
            self.items.append(["label", Q(name), ["at", _fx(at[0]), _fx(at[1]), ang], eff, ["uuid", U(self._k("l", name, at))]])

    def dashed(self, a, b):
        self.items.append(["polyline", ["pts", ["xy", _fx(a[0]), _fx(a[1])], ["xy", _fx(b[0]), _fx(b[1])]],
                           ["stroke", ["width", 0], ["type", "dash"]], ["uuid", U(self._k("pl", a, b))]])

    def text(self, s, at, size=1.27, bold=False):
        font = ["font", ["size", size, size]]
        if bold:
            font.append(["bold", "yes"])
        self.items.append(["text", Q(s), ["exclude_from_sim", "no"], ["at", _fx(at[0]), _fx(at[1]), 0],
                           ["effects", font, ["justify", "left", "bottom"]], ["uuid", U(self._k("t", s))]])

    def p(self, refpin):
        return self.pinpos[refpin]

    # ------------------------------------------------------------ output
    def save(self, path, title_extra=None):
        root = ["kicad_sch", ["version", 20260306], ["generator", Q("eeschema")], ["generator_version", Q("10.0")],
                ["uuid", self.uuid], ["paper", Q("A4")],
                ["title_block", ["title", Q(self.title)], ["date", Q("2026-11-02")], ["rev", Q("A")],
                 ["company", Q("Radio Remote HAT")]] + ([["comment", 1, Q(title_extra)]] if title_extra else []),
                ["lib_symbols"] + list(self.libs.values())]
        root += self.items
        root.append(["embedded_fonts", "no"])
        with open(path, "w") as f:
            f.write(dump(root) + "\n")
