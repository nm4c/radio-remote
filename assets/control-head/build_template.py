#!/usr/bin/env python3
"""Generate an E5-style control head background for a remote radio app.

Run it after changing anything here or in web/control-head/control_head.js:
    python3 assets/control-head/build_template.py      (needs rsvg-convert for the PNGs)

Outputs for the web app, written to web/control-head/ (served as /static/control-head/):
  control-head.svg              The live head: artwork, empty screen text and press targets.
                                app.js inserts it into the page, then loads control_head.js.
  radio_config.js               RADIO_CONFIG (detent, long press, hold-to-activate times) for
                                control_head.js, from the same constants as layout.json.

Outputs for other uses, written to ./output next to this script:
  control-head.svg              Editable vector with a blank black screen
  control-head@2x.png           3200x920 background with a blank black screen
  control-head-cutout@2x.png    Same, with a transparent hole in the main screen
  control-head-sample@2x.png    Example showing channel info in the layout
  control-head-base@2x.png      Background without knobs, for separate knob layers
  knob-volume@2x.png            Rotatable volume knob layer
  knob-channel@2x.png           Rotatable channel knob layer
  control-head-interactive.html Working controls, with an event log for testing
  layout.json                   Screen zones, hit areas and control behaviour

control_head.js itself is hand-written, not generated. It lives in web/control-head/ so the
app serves the same file this script inlines into the test page.

All coordinates use a 1600x460 base canvas. Multiply them by your rendered
width / 1600 to place text or click targets in the app.
"""

import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "output"
WEB_OUT = HERE.parent.parent / "web" / "control-head"
CONTROL_JS = WEB_OUT / "control_head.js"
W, H = 1600, 460

# ---------------------------------------------------------------------------
# Layout: the single source of truth for the artwork and layout.json.
# ---------------------------------------------------------------------------

# Black bezel around the LCD.
BEZEL = {"x": 420, "y": 40, "w": 755, "h": 281}
# Whole LCD: the main content window plus the soft-key label row.
LCD = {"x": 435, "y": 64, "w": 732, "h": 250}
MAIN = {"x": 435, "y": 64, "w": 732, "h": 200}
SOFTKEY_ROW_Y, SOFTKEY_ROW_H = 266, 48

# Stacked text zones in the main window, matching the photo's three lines.
ZONES = {
    "status_bar": {"x": 447, "y": 68, "w": 708, "h": 40, "align": "space-between", "suggested_font_px": 24},
    "line_1_zone": {"x": 447, "y": 110, "w": 708, "h": 40, "align": "center", "suggested_font_px": 32},
    "line_2_channel": {"x": 447, "y": 150, "w": 708, "h": 68, "align": "center", "suggested_font_px": 58},
    "line_3_detail": {"x": 447, "y": 220, "w": 708, "h": 40, "align": "center", "suggested_font_px": 30},
}

# Five equal soft-key label cells, with a 3 px gap between neighbours.
CELL_GAP = 3
CELL_W = (LCD["w"] - CELL_GAP * 4) / 5
SOFTKEY_LABELS = [
    {"x": round(LCD["x"] + i * (CELL_W + CELL_GAP), 1), "y": SOFTKEY_ROW_Y, "w": round(CELL_W, 1), "h": SOFTKEY_ROW_H}
    for i in range(5)
]

# Physical soft-key buttons beneath the LCD (centre x positions from the photo).
SOFTKEY_BUTTON_X = [483, 640, 800, 958, 1116]
SOFTKEY_BUTTON_Y, SOFTKEY_BUTTON_W, SOFTKEY_BUTTON_H = 392, 130, 62

# Round controls: centre x, centre y, radius.
CONTROLS = {
    "power": (87, 85, 42),
    "volume_knob": (287, 117, 95),
    "channel_knob": (1310, 119, 95),
    "emergency": (1512, 89, 42),
    "brightness": (315, 265, 34),
    "programmable_p": (330, 371, 40),
    "home": (1512, 375, 42),
}
DPAD = (1342, 329, 92)  # Centre x, centre y, radius.

# Control behaviour, shared by layout.json and the interactive page.
CHANNEL_DETENT_DEG = 15          # 24 clicks per full turn of the channel knob.
LONG_PRESS_MS = 600              # Ordinary buttons also report a long press.
HOLD_TO_ACTIVATE = {"emergency": 1500, "power": 1500}  # Guards against stray clicks.
KNOB_LAYER_SIZE = 200            # Knob layer size in base-canvas units.

# Compact view for narrow (phone) screens: crop to the bezel, channel knob and D-pad, so the
# screen text is about 40% larger than in the full-width head. The left end (power, volume
# knob, mic socket) and the emergency button fall outside it. Stored on the app SVG as
# data-compact-viewbox; app.js switches to it.
COMPACT_X0 = BEZEL["x"] - 5
COMPACT_X1 = max(DPAD[0] + DPAD[2], CONTROLS["channel_knob"][0] + CONTROLS["channel_knob"][2]) + 6
COMPACT_VIEWBOX = (COMPACT_X0, 0, COMPACT_X1 - COMPACT_X0, H)


# ---------------------------------------------------------------------------
# Small drawing helpers. Each returns an SVG fragment as a string.
# ---------------------------------------------------------------------------

def circle(cx, cy, r, fill, extra=""):
    return f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{fill}" {extra}/>'


def rrect(x, y, w, h, r, fill, extra=""):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" {extra}/>'


def screw(cx, cy):
    """Torx-style faceplate screw."""
    return "".join([
        circle(cx, cy, 24, "#0c0d0e"),
        circle(cx, cy, 20, "url(#metal)"),
        circle(cx, cy, 8, "#26282a"),
        circle(cx, cy, 3, "#6b6e71"),
    ])


def round_button(cx, cy, r, inner):
    """Raised round button with a recessed outer ring and an icon on top."""
    return "".join([
        circle(cx, cy, r + 4, "#0a0b0c"),
        circle(cx, cy, r, "url(#button)", 'stroke="#3a3c3f" stroke-width="2"'),
        inner,
    ])


def knob(cx, cy, r, pointer=False, name=""):
    """Rotary knob. Only the .rotor parts turn, so the dome lighting stays fixed."""
    parts = [
        f'<g id="{name}" data-knob="{name}" data-cx="{cx}" data-cy="{cy}">',
        circle(cx, cy, r + 5, "#090a0b"),
        circle(cx, cy, r, "url(#knobBase)"),
        '<g class="rotor">',
        # Dashes create the segmented bright ring seen on the real knobs.
        f'<circle cx="{cx}" cy="{cy}" r="{r - 3}" fill="none" stroke="#c9ccd0" '
        f'stroke-width="5" stroke-dasharray="72 12 96 14"/>',
        # Ring of grip notches inside the bright band.
        f'<circle cx="{cx}" cy="{cy}" r="{r - 13}" fill="none" stroke="#050606" '
        f'stroke-width="12" stroke-dasharray="7 9"/>',
        "</g>",
        circle(cx, cy, r - 22, "url(#knobDome)", 'stroke="#2d2f32" stroke-width="2"'),
    ]
    if pointer:
        # Volume indicator. This lower-left angle is the zero-volume position.
        parts.append(
            f'<g class="rotor"><line x1="{cx - 48}" y1="{cy + 48}" x2="{cx - 20}" y2="{cy + 20}" '
            'stroke="#d8dadd" stroke-width="13" stroke-linecap="round"/></g>'
        )
    parts.append("</g>")
    return "".join(parts)


def softkey_button(cx):
    x = cx - SOFTKEY_BUTTON_W / 2
    y = SOFTKEY_BUTTON_Y - SOFTKEY_BUTTON_H / 2
    return "".join([
        rrect(x - 4, y - 4, SOFTKEY_BUTTON_W + 8, SOFTKEY_BUTTON_H + 8, 12, "#08090a"),
        rrect(x, y, SOFTKEY_BUTTON_W, SOFTKEY_BUTTON_H, 9, "url(#button)", 'stroke="#3a3c3f" stroke-width="2"'),
        rrect(cx - 26, SOFTKEY_BUTTON_Y - 6, 52, 12, 6, "#b9bcc0"),
    ])


def dpad(cx, cy, r):
    """Four-way pad split by an X, with a light tick on each direction."""
    d = r * 0.68  # Divider length along each diagonal, from the centre.
    parts = [
        circle(cx, cy, r + 5, "#08090a"),
        circle(cx, cy, r, "url(#button)", 'stroke="#3a3c3f" stroke-width="2"'),
        f'<path d="M{cx-d} {cy-d}L{cx+d} {cy+d}M{cx+d} {cy-d}L{cx-d} {cy+d}" '
        'stroke="#050606" stroke-width="9"/>',
        rrect(cx - 4, cy - r + 16, 8, 30, 4, "#b9bcc0"),
        rrect(cx - 4, cy + r - 46, 8, 30, 4, "#b9bcc0"),
        rrect(cx - r + 16, cy - 4, 30, 8, 4, "#b9bcc0"),
        rrect(cx + r - 46, cy - 4, 30, 8, 4, "#b9bcc0"),
    ]
    return "".join(parts)


def mic_connector(cx, cy):
    """Circular front accessory connector with its pin field."""
    parts = [
        circle(cx, cy, 100, "#17181a"),
        circle(cx, cy, 88, "#050606", 'stroke="#2c2e31" stroke-width="3"'),
        circle(cx, cy, 52, "#17181a", 'stroke="#000" stroke-width="3"'),
    ]
    # Pin layout: one centre pin, an inner ring and an outer ring.
    from math import cos, sin, tau
    pins = [(cx, cy)]
    for count, radius in ((5, 18), (10, 36)):
        pins += [(cx + radius * cos(i / count * tau), cy + radius * sin(i / count * tau)) for i in range(count)]
    parts += [circle(round(x, 1), round(y, 1), 4, "#050606") for x, y in pins]
    return "".join(parts)


# ---------------------------------------------------------------------------
# SVG composition.
# ---------------------------------------------------------------------------

DEFS = f"""
<defs>
  <linearGradient id="body" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#2d2f32"/><stop offset=".12" stop-color="#202224"/>
    <stop offset=".9" stop-color="#191a1c"/><stop offset="1" stop-color="#0d0e0f"/>
  </linearGradient>
  <linearGradient id="button" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#303235"/><stop offset="1" stop-color="#141517"/>
  </linearGradient>
  <linearGradient id="softcell" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#6c6e71"/><stop offset="1" stop-color="#55575a"/>
  </linearGradient>
  <linearGradient id="metal" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#b6b8bb"/><stop offset="1" stop-color="#5a5c5f"/>
  </linearGradient>
  <radialGradient id="knobBase" cx=".5" cy=".4" r=".7">
    <stop offset="0" stop-color="#34363a"/><stop offset="1" stop-color="#101113"/>
  </radialGradient>
  <radialGradient id="knobDome" cx=".42" cy=".32" r=".8">
    <stop offset="0" stop-color="#37393c"/><stop offset="1" stop-color="#17181a"/>
  </radialGradient>
  <radialGradient id="orange" cx=".42" cy=".35" r=".75">
    <stop offset="0" stop-color="#f26a2a"/><stop offset="1" stop-color="#c63f0c"/>
  </radialGradient>
  <!-- White keeps the artwork. Black punches the transparent screen window. -->
  <mask id="screenHole" maskUnits="userSpaceOnUse" x="0" y="0" width="{W}" height="{H}">
    <rect width="{W}" height="{H}" fill="#fff"/>
    <rect x="{MAIN['x']}" y="{MAIN['y']}" width="{MAIN['w']}" height="{MAIN['h']}" fill="#000"/>
  </mask>
</defs>"""


def faceplate(screen_fill, knobs=True):
    """Draw everything except optional sample text. knobs=False leaves them out."""
    power_x, power_y, power_r = CONTROLS["power"]
    emerg_x, emerg_y, emerg_r = CONTROLS["emergency"]
    home_x, home_y, home_r = CONTROLS["home"]
    bright_x, bright_y, _ = CONTROLS["brightness"]
    p_x, p_y, p_r = CONTROLS["programmable_p"]

    parts = [
        # Housing, recessed centre panel and end-cap seams.
        rrect(5, 2, 1590, 457, 36, "url(#body)", 'stroke="#050606" stroke-width="3"'),
        rrect(258, 22, 1195, 420, 14, "#1b1c1e", 'stroke="#111214" stroke-width="3"'),
        '<path d="M150 18V124M1450 22V440" stroke="#0b0c0d" stroke-width="3"/>',

        # Left side controls.
        round_button(
            power_x, power_y, power_r,
            f'<path d="M{power_x-15} {power_y-12}A21 21 0 1 0 {power_x+15} {power_y-12}" fill="none" '
            f'stroke="#27b34b" stroke-width="6" stroke-linecap="round"/>'
            f'<line x1="{power_x}" y1="{power_y-25}" x2="{power_x}" y2="{power_y}" stroke="#27b34b" '
            'stroke-width="6" stroke-linecap="round"/>'
            + circle(power_x, power_y, power_r - 3, "none", 'stroke="#1c6b34" stroke-width="3"'),
        ),
        # Speaker grille ridges beside the volume knob.
        rrect(132, 132, 54, 16, 8, "#111214", 'stroke="#34363a" stroke-width="2"'),
        rrect(132, 162, 54, 16, 8, "#111214", 'stroke="#34363a" stroke-width="2"'),
        rrect(132, 192, 54, 16, 8, "#111214", 'stroke="#34363a" stroke-width="2"'),
        knob(*CONTROLS["volume_knob"], pointer=True, name="volume_knob") if knobs else "",
        screw(78, 230),
        mic_connector(142, 344),

        # Brightness pill with a small sun icon.
        rrect(bright_x - 55, bright_y - 31, 110, 62, 31, "url(#button)", 'stroke="#3a3c3f" stroke-width="2"'),
        circle(bright_x, bright_y, 7, "#b9bcc0"),
        f'<g stroke="#b9bcc0" stroke-width="3" stroke-linecap="round">'
        + "".join(
            f'<line x1="{bright_x}" y1="{bright_y-11}" x2="{bright_x}" y2="{bright_y-18}" '
            f'transform="rotate({angle} {bright_x} {bright_y})"/>'
            for angle in range(0, 360, 45)
        )
        + "</g>",

        # P button. The letter is a path, so the SVG needs no font.
        round_button(
            p_x, p_y, p_r,
            f'<path d="M{p_x-11} {p_y+19}V{p_y-19}H{p_x+3}A11 11 0 0 1 {p_x+3} {p_y+3}H{p_x-11}" '
            'fill="none" stroke="#b9bcc0" stroke-width="7" stroke-linejoin="round"/>',
        ),

        # LCD bezel, then the screen and soft-key label cells.
        rrect(BEZEL["x"], BEZEL["y"], BEZEL["w"], BEZEL["h"], 6, "#0a0b0c", 'stroke="#2c2e31" stroke-width="2"'),
    ]
    if screen_fill:
        parts.append(
            f'<rect id="main-screen" x="{MAIN["x"]}" y="{MAIN["y"]}" width="{MAIN["w"]}" '
            f'height="{MAIN["h"]}" fill="{screen_fill}"/>'
        )
    parts += [
        f'<rect x="{c["x"]}" y="{c["y"]}" width="{c["w"]}" height="{c["h"]}" fill="url(#softcell)"/>'
        for c in SOFTKEY_LABELS
    ]
    parts += [softkey_button(x) for x in SOFTKEY_BUTTON_X]
    parts += [
        # Right side controls.
        knob(*CONTROLS["channel_knob"], name="channel_knob") if knobs else "",
        circle(emerg_x, emerg_y, emerg_r + 28, "#0b0c0d", 'stroke="#2a2c2f" stroke-width="2"'),
        circle(emerg_x, emerg_y, emerg_r, "url(#orange)"),
        circle(1482, 189, 13, "#2d5fc4", 'stroke="#0d1b3a" stroke-width="3"'),
        screw(1522, 230),
        dpad(*DPAD),
        round_button(
            home_x, home_y, home_r,
            f'<path d="M{home_x-18} {home_y-1}L{home_x} {home_y-17}L{home_x+18} {home_y-1}M{home_x-12} {home_y-2}'
            f'V{home_y+16}H{home_x-4}V{home_y+5}H{home_x+4}V{home_y+16}H{home_x+12}V{home_y-2}" fill="none" '
            'stroke="#d8c33a" stroke-width="5" stroke-linejoin="round" stroke-linecap="round"/>',
        ),
    ]
    return "\n".join(parts)


def sample_overlay():
    """Placeholder content that shows the zones in use. Not part of the template."""
    font = 'font-family="Liberation Sans, Arial, Helvetica, sans-serif"'
    cx = MAIN["x"] + MAIN["w"] / 2
    labels = ["Call", "RSSI", "Zone", "Chan", "Scan"]
    parts = [
        # Signal bars in the status row.
        *[
            f'<rect x="{456 + i * 9}" y="{94 - (i + 1) * 6}" width="6" height="{(i + 1) * 6}" fill="#3ddc62"/>'
            for i in range(4)
        ],
        f'<text x="1149" y="97" text-anchor="end" fill="#3ddc62" font-size="24" font-weight="700" {font}>RX</text>',
        f'<text x="{cx}" y="141" text-anchor="middle" fill="#e8e8e8" font-size="32" {font}>Zone 1</text>',
        f'<text x="{cx}" y="205" text-anchor="middle" fill="#fff" font-size="58" font-weight="700" {font}>CHANNEL 01</text>',
        f'<text x="{cx}" y="251" text-anchor="middle" fill="#e8e8e8" font-size="30" {font}>Talkgroup 1001</text>',
    ]
    for cell, label in zip(SOFTKEY_LABELS, labels):
        parts.append(
            f'<text x="{cell["x"] + cell["w"] / 2}" y="{cell["y"] + 33}" text-anchor="middle" fill="#f2f2f2" '
            f'font-size="27" font-weight="700" {font}>{label}</text>'
        )
    return "\n".join(parts)


def document(body, box=(0, 0, W, H), attrs=""):
    """Wrap a fragment in an SVG. box crops the output, as used for knob layers."""
    x, y, w, h = box
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x} {y} {w} {h}" width="{w}" height="{h}"{attrs}>'
        f"{DEFS}\n{body}\n</svg>\n"
    )


def layout_json():
    """Describe zones and hit areas in base-canvas coordinates."""
    hit_areas = {name: {"cx": c[0], "cy": c[1], "r": c[2]} for name, c in CONTROLS.items()}
    hit_areas["brightness"] = {"x": 260, "y": 234, "w": 110, "h": 62}
    # The D-pad is split into four centred boxes, one per direction.
    x, y, r = DPAD
    third = round(r * 2 / 3)
    hit_areas["dpad_up"] = {"x": x - third // 2, "y": y - r, "w": third, "h": third}
    hit_areas["dpad_down"] = {"x": x - third // 2, "y": y + r - third, "w": third, "h": third}
    hit_areas["dpad_left"] = {"x": x - r, "y": y - third // 2, "w": third, "h": third}
    hit_areas["dpad_right"] = {"x": x + r - third, "y": y - third // 2, "w": third, "h": third}
    for i, cx in enumerate(SOFTKEY_BUTTON_X, start=1):
        hit_areas[f"softkey_{i}"] = {
            "x": cx - SOFTKEY_BUTTON_W // 2,
            "y": SOFTKEY_BUTTON_Y - SOFTKEY_BUTTON_H // 2,
            "w": SOFTKEY_BUTTON_W,
            "h": SOFTKEY_BUTTON_H,
        }
    return {
        "base_canvas": {"w": W, "h": H},
        "scale_note": "Multiply every value by rendered_width / 1600.",
        "screen": {"full_lcd": LCD, "main_window": MAIN},
        "text_zones": ZONES,
        "softkey_labels": SOFTKEY_LABELS,
        "hit_areas": hit_areas,
        # Same behaviour as control_head.js, for apps that handle input themselves.
        "behaviour": {
            "volume_knob": {"type": "absolute", "min": 0, "max": 100, "sweep_degrees": 270,
                            "zero_angle_note": "Artwork is drawn at value 0; rotate clockwise by value * 2.7."},
            "channel_knob": {"type": "detent", "degrees_per_step": CHANNEL_DETENT_DEG},
            "hold_to_activate_ms": HOLD_TO_ACTIVATE,
            "long_press_ms": LONG_PRESS_MS,
        },
        "knob_layers": {
            "volume_knob": {"file": "knob-volume@2x.png", "centre": {"x": 287, "y": 117}, "size": KNOB_LAYER_SIZE},
            "channel_knob": {"file": "knob-channel@2x.png", "centre": {"x": 1310, "y": 119}, "size": KNOB_LAYER_SIZE},
        },
    }


def hit_layer():
    """Invisible press targets. They only become visible while pressed."""
    from math import cos, radians, sin

    parts = ['<g id="hit-layer">']
    for name, (cx, cy, r) in CONTROLS.items():
        if name.endswith("_knob"):
            continue  # Knobs handle their own pointer input.
        if name == "brightness":
            parts.append(rrect(260, 234, 110, 62, 31, "transparent", f'data-control="{name}"'))
        else:
            parts.append(circle(cx, cy, r, "transparent", f'data-control="{name}"'))
    for i, cx in enumerate(SOFTKEY_BUTTON_X, start=1):
        parts.append(rrect(
            cx - SOFTKEY_BUTTON_W / 2, SOFTKEY_BUTTON_Y - SOFTKEY_BUTTON_H / 2,
            SOFTKEY_BUTTON_W, SOFTKEY_BUTTON_H, 9, "transparent", f'data-control="softkey_{i}"',
        ))
    # D-pad wedges meet along the X dividers. Screen angles run clockwise from east.
    x, y, r = DPAD
    for name, mid in (("dpad_right", 0), ("dpad_down", 90), ("dpad_left", 180), ("dpad_up", 270)):
        x1, y1 = x + r * cos(radians(mid - 45)), y + r * sin(radians(mid - 45))
        x2, y2 = x + r * cos(radians(mid + 45)), y + r * sin(radians(mid + 45))
        parts.append(
            f'<path d="M{x} {y}L{x1:.1f} {y1:.1f}A{r} {r} 0 0 1 {x2:.1f} {y2:.1f}Z" '
            f'fill="transparent" data-control="{name}"/>'
        )
    parts.append("</g>")
    return "".join(parts)


def live_screen():
    """Empty text elements that radioHead.setDisplay() fills in."""
    font = 'font-family="Liberation Sans, Arial, Helvetica, sans-serif"'
    cx = MAIN["x"] + MAIN["w"] / 2
    parts = ['<g id="screen-layer" pointer-events="none">']
    for i in range(4):
        parts.append(
            f'<rect class="signal-bar" x="{456 + i * 9}" y="{94 - (i + 1) * 6}" width="6" '
            f'height="{(i + 1) * 6}" fill="#3ddc62"/>'
        )
    parts += [
        f'<text id="display-status" x="1149" y="97" text-anchor="end" fill="#3ddc62" font-size="24" font-weight="700" {font}></text>',
        f'<text id="display-zone" data-max-width="708" x="{cx}" y="141" text-anchor="middle" fill="#e8e8e8" font-size="32" {font}></text>',
        f'<text id="display-channel" data-max-width="708" x="{cx}" y="205" text-anchor="middle" fill="#fff" font-size="58" font-weight="700" {font}></text>',
        f'<text id="display-detail" data-max-width="708" x="{cx}" y="251" text-anchor="middle" fill="#e8e8e8" font-size="30" {font}></text>',
    ]
    for i, cell in enumerate(SOFTKEY_LABELS, start=1):
        parts.append(
            f'<text id="display-softkey-{i}" data-max-width="{cell["w"] - 10}" x="{cell["x"] + cell["w"] / 2}" '
            f'y="{cell["y"] + 33}" text-anchor="middle" fill="#f2f2f2" font-size="27" font-weight="700" {font}></text>'
        )
    parts.append("</g>")
    return "".join(parts)


def live_svg():
    """The working head: artwork, empty screen text and press targets.

    Used by the web app (web/control-head/control-head.svg) and the test page.
    """
    compact = " ".join(str(v) for v in COMPACT_VIEWBOX)
    return document(
        faceplate("#000") + live_screen() + hit_layer(),
        attrs=f' data-compact-viewbox="{compact}"',
    )


def radio_config():
    """Behaviour settings control_head.js reads from the global RADIO_CONFIG."""
    return {"detent": CHANNEL_DETENT_DEG, "longPressMs": LONG_PRESS_MS, "holdMs": HOLD_TO_ACTIVATE}


def radio_config_js():
    """RADIO_CONFIG as a script the app loads before control_head.js."""
    return (
        "// Generated by assets/control-head/build_template.py. Do not edit by hand:\n"
        "// change the constants there and re-run it, so layout.json stays in step.\n"
        "//   detent       degrees of channel-knob rotation per channel step\n"
        "//   longPressMs  hold time before ordinary buttons also report long_press\n"
        "//   holdMs       guarded buttons: hold time before they report activate\n"
        f"const RADIO_CONFIG = Object.freeze({json.dumps(radio_config())});\n"
    )


def interactive_page():
    """Single self-contained page: live SVG, control script and test log."""
    svg = live_svg()
    # Inlined so this page stays a single file. The app loads the same text from radio_config.js.
    config = radio_config_js()
    script = CONTROL_JS.read_text()
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Control Head</title>
<style>
  body {{ margin: 0; padding: 16px; background: #111214; color: #d8dadd; font: 14px/1.4 system-ui, sans-serif; }}
  #radio {{ max-width: 1100px; margin: auto; }}
  #radio svg {{ display: block; width: 100%; height: auto; touch-action: none; user-select: none; }}
  [data-control], [data-knob] {{ cursor: pointer; outline: none; }}
  [data-control].pressed {{ fill: rgba(255, 255, 255, .22); }}
  [data-control]:focus-visible {{ stroke: #6aa8ff; stroke-width: 4; }}
  [data-knob]:focus-visible > circle:first-child {{ stroke: #6aa8ff; stroke-width: 4; }}
  #log {{ max-width: 1100px; margin: 16px auto 0; height: 180px; overflow: auto; padding: 8px;
         background: #000; font: 13px/1.5 ui-monospace, monospace; white-space: pre-wrap; }}
</style></head><body>
<div id="radio">{svg}</div>
<div id="log" role="log" aria-label="Control events"></div>
<script>
{config}{script}
// Demo wiring. Replace this block with calls to your radio backend.
radioHead.setDisplay({{
  zone: "Zone 1", channel: "CHANNEL 01", detail: "Talkgroup 1001", status: "RX", signal: 4,
  softkeys: ["Call", "RSSI", "Zone", "Chan", "Scan"],
}});
const log = document.getElementById("log");
radioHead.on(e => {{
  log.textContent = JSON.stringify(e) + "\\n" + log.textContent;
}});
</script></body></html>
"""


def render(svg_path, png_path):
    """Rasterise at 2x. rsvg-convert keeps the transparency."""
    subprocess.run(["rsvg-convert", "-z", "2", "-o", str(png_path), str(svg_path)], check=True)


def main():
    OUT.mkdir(exist_ok=True)

    # Master template with a blank black screen.
    master = OUT / "control-head.svg"
    master.write_text(document(faceplate("#000")))
    render(master, OUT / "control-head@2x.png")

    # Masked copy, for an app that draws its own screen behind the artwork.
    cutout = OUT / "_cutout.svg"
    cutout.write_text(document(f'<g mask="url(#screenHole)">{faceplate(None)}</g>'))
    render(cutout, OUT / "control-head-cutout@2x.png")
    cutout.unlink()

    # Preview with placeholder channel information.
    sample = OUT / "_sample.svg"
    sample.write_text(document(faceplate("#000") + sample_overlay()))
    render(sample, OUT / "control-head-sample@2x.png")
    sample.unlink()

    # Knob-free background and separately rotatable knob layers.
    base = OUT / "_base.svg"
    base.write_text(document(faceplate("#000", knobs=False)))
    render(base, OUT / "control-head-base@2x.png")
    base.unlink()
    half = KNOB_LAYER_SIZE // 2
    for name, file_stem, pointer in (("volume_knob", "knob-volume", True), ("channel_knob", "knob-channel", False)):
        cx, cy, r = CONTROLS[name]
        layer = OUT / f"_{file_stem}.svg"
        layer.write_text(document(knob(cx, cy, r, pointer, name), (cx - half, cy - half, KNOB_LAYER_SIZE, KNOB_LAYER_SIZE)))
        render(layer, OUT / f"{file_stem}@2x.png")
        layer.unlink()

    (OUT / "control-head-interactive.html").write_text(interactive_page())
    (OUT / "layout.json").write_text(json.dumps(layout_json(), indent=2) + "\n")

    # Web app files, next to the hand-written control_head.js.
    (WEB_OUT / "control-head.svg").write_text(live_svg())
    (WEB_OUT / "radio_config.js").write_text(radio_config_js())


if __name__ == "__main__":
    main()
