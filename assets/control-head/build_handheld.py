#!/usr/bin/env python3
"""Generate a portrait handheld radio for the remote app on phones.

The companion to build_template.py (the desk control head). It is driven by the same
control_head.js and fills the same screen fields, so the app can swap one for the other:
press targets carry data-control, knobs carry data-knob, screen text has display-* ids.

Differences from the desk head:
  - Three soft keys instead of five (display-softkey-1..3, softkey_1..3).
  - Buttons: home, menu, p1, p2 and a D-pad split over three rocker rows. No power or
    emergency button (on/off is on the volume knob of a real handheld).
  - The two top knobs are seen side-on (data-view="side"). control_head.js rolls them with a
    sideways or vertical swipe and slides their grip ridges instead of rotating the drawing.

Run it after changing anything here, in build_template.py, or in web/control-head/control_head.js:
    python3 assets/control-head/build_handheld.py      (needs rsvg-convert for the PNGs)

Outputs for the web app, written to web/control-head/:
  handheld.svg                  The live handheld: artwork, empty screen text, press targets.
Outputs for other uses, written to ./output:
  handheld.svg                  Editable vector with a blank screen
  handheld@2x.png               1120x2384 background with a blank screen
  handheld-sample@2x.png        Example showing channel info in the layout
  handheld-interactive.html     Working controls, with an event log for testing
  handheld-layout.json          Screen zones, hit areas and control behaviour

All coordinates use a 560x1192 base canvas. Multiply them by rendered_width / 560.
"""

import json
from pathlib import Path

from build_template import (  # shared drawing helpers, paths and behaviour settings
    CHANNEL_DETENT_DEG, HOLD_TO_ACTIVATE, LONG_PRESS_MS, OUT, WEB_OUT, circle, interactive_page, render, rrect,
)

W, H = 560, 1192
FONT = 'font-family="Liberation Sans, Arial, Helvetica, sans-serif"'

# ---------------------------------------------------------------------------
# Layout: the single source of truth for the artwork and handheld-layout.json.
# ---------------------------------------------------------------------------

BODY = {"x": 34, "y": 262, "w": 492, "h": 920}      # outer rubber housing
PANEL = {"x": 62, "y": 292, "w": 436, "h": 866}     # front face inside the side grips
ANTENNA = {"x": 388, "y": 10, "w": 64, "h": 300}    # its base is hidden behind the housing

# Top knobs, seen side-on: centre x, top and bottom of the grip, and width.
# sweep: degrees the volume knob turns from 0 to 100.
KNOBS = {
    "volume_knob": {"cx": 140, "top": 118, "bottom": 266, "w": 92, "sweep": 300},
    "channel_knob": {"cx": 280, "top": 40, "bottom": 266, "w": 104},
}
COLLAR_H = 26        # wider ring where each knob meets the housing
RIDGE_PERIOD = 12    # grip ridge spacing; control_head.js slides the ridges within one period

# Screen and its rows, top to bottom.
BEZEL = {"x": 124, "y": 372, "w": 312, "h": 412}
SCREEN = {"x": 136, "y": 384, "w": 288, "h": 388}
STATUS_ROW = {"y": 384, "h": 38}     # black: signal bars and status text
STRIP_ROW = {"y": 422, "h": 24}      # pale blue band under the status bar
MAIN_ROW = {"y": 446, "h": 176}      # white: zone and channel, left aligned
DETAIL_ROW = {"y": 622, "h": 90}     # pale blue frame with the detail line
SOFTKEY_ROW = {"y": 716, "h": 56}    # blue soft-key labels

ZONES = {
    "status_bar": {"x": 146, "y": 384, "w": 268, "h": 38, "align": "space-between", "suggested_font_px": 22},
    "line_1_zone": {"x": 152, "y": 456, "w": 256, "h": 42, "align": "left", "suggested_font_px": 30},
    "line_2_channel": {"x": 152, "y": 504, "w": 256, "h": 60, "align": "left", "suggested_font_px": 42},
    "line_3_detail": {"x": 146, "y": 642, "w": 268, "h": 62, "align": "center", "suggested_font_px": 26},
}

CELL_GAP = 3
CELL_W = (SCREEN["w"] - CELL_GAP * 2) / 3
SOFTKEY_LABELS = [
    {"x": round(SCREEN["x"] + i * (CELL_W + CELL_GAP), 1), "y": SOFTKEY_ROW["y"], "w": round(CELL_W, 1),
     "h": SOFTKEY_ROW["h"]}
    for i in range(3)
]

# Physical soft keys under the screen: centre x, and the shared centre y and size.
SOFTKEY_BUTTON_X = [182, 280, 378]
SOFTKEY_BUTTON_Y, SOFTKEY_BUTTON_W, SOFTKEY_BUTTON_H = 842, 90, 50

# Rocker rows: one pill each, split into the listed controls from left to right.
ROW_X, ROW_W, ROW_H = 96, 368, 62
ROWS = [
    (926, ["home", "dpad_up", "menu"]),
    (1006, ["dpad_left", "dpad_right"]),
    (1086, ["p1", "dpad_down", "p2"]),
]

ICON_BLUE = "#5aa9e6"   # home, menu and P labels, as on the reference radio
ICON_WHITE = "#e9ebee"


# ---------------------------------------------------------------------------
# Drawing.
# ---------------------------------------------------------------------------

def defs():
    clips = "".join(
        f'<clipPath id="hh-row{i}"><rect x="{ROW_X}" y="{y - ROW_H / 2}" width="{ROW_W}" height="{ROW_H}" '
        f'rx="{ROW_H / 2}"/></clipPath>'
        for i, (y, _) in enumerate(ROWS)
    )
    knob_clips = "".join(
        f'<clipPath id="hh-{name}-grip"><rect x="{k["cx"] - k["w"] / 2}" y="{k["top"]}" width="{k["w"]}" '
        f'height="{k["bottom"] - COLLAR_H - k["top"] + 4}" rx="{k["w"] * 0.22}"/></clipPath>'
        for name, k in KNOBS.items()
    )
    return f"""
<defs>
  <linearGradient id="hh-body" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0" stop-color="#0f1011"/><stop offset=".08" stop-color="#232427"/>
    <stop offset=".92" stop-color="#232427"/><stop offset="1" stop-color="#0f1011"/>
  </linearGradient>
  <linearGradient id="hh-panel" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#2a2b2e"/><stop offset="1" stop-color="#1c1d1f"/>
  </linearGradient>
  <linearGradient id="hh-button" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#34363a"/><stop offset="1" stop-color="#16171a"/>
  </linearGradient>
  <linearGradient id="hh-softcell" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#3a8fd6"/><stop offset="1" stop-color="#1f6cb0"/>
  </linearGradient>
  <linearGradient id="hh-cylinder" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0" stop-color="#000" stop-opacity=".75"/><stop offset=".35" stop-color="#fff" stop-opacity=".16"/>
    <stop offset=".55" stop-color="#fff" stop-opacity=".05"/><stop offset="1" stop-color="#000" stop-opacity=".8"/>
  </linearGradient>
  <linearGradient id="hh-antenna" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0" stop-color="#0b0c0d"/><stop offset=".4" stop-color="#2b2d30"/><stop offset="1" stop-color="#0b0c0d"/>
  </linearGradient>
  {clips}{knob_clips}
</defs>"""


def side_knob(name, k):
    """Knob seen side-on. Only the ridges (.rotor) move; the cylinder shading stays put."""
    cx, top, bottom, w = k["cx"], k["top"], k["bottom"], k["w"]
    r = w / 2
    grip_bottom = bottom - COLLAR_H + 4
    sweep = f' data-sweep="{k["sweep"]}"' if "sweep" in k else ""
    ridges = "".join(
        f'<rect x="{x}" y="{top}" width="{RIDGE_PERIOD / 2}" height="{grip_bottom - top}" fill="#050506"/>'
        for x in range(int(cx - r - 2 * RIDGE_PERIOD), int(cx + r + RIDGE_PERIOD), RIDGE_PERIOD)
    )
    return "".join([
        f'<g id="{name}" data-knob="{name}" data-view="side" data-cx="{cx}" data-cy="{(top + bottom) / 2}" '
        f'data-r="{r}" data-period="{RIDGE_PERIOD}"{sweep}>',
        # Collar at the base, then the grip body.
        rrect(cx - r - 8, bottom - COLLAR_H, w + 16, COLLAR_H + 8, 6, "#141517", 'stroke="#2c2e31" stroke-width="2"'),
        rrect(cx - r, top, w, grip_bottom - top, w * 0.22, "#26282b", 'class="focus-ring"'),
        f'<g clip-path="url(#hh-{name}-grip)"><g class="rotor">{ridges}</g></g>',
        rrect(cx - r, top, w, grip_bottom - top, w * 0.22, "url(#hh-cylinder)"),
        # Flat top face catching the light.
        f'<ellipse cx="{cx}" cy="{top + 7}" rx="{r - 8}" ry="5" fill="#3a3c40"/>',
        "</g>",
    ])


def chevron(cx, cy, direction, colour=ICON_WHITE):
    dx, dy = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}[direction]
    # Arms 14 long; the tip points in the given direction.
    tip = (cx + dx * 8, cy + dy * 8)
    a = (cx - dx * 6 + dy * 14, cy - dy * 6 + dx * 14)
    b = (cx - dx * 6 - dy * 14, cy - dy * 6 - dx * 14)
    return (f'<path d="M{a[0]} {a[1]}L{tip[0]} {tip[1]}L{b[0]} {b[1]}" fill="none" stroke="{colour}" '
            'stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>')


def icon(control, cx, cy):
    """Legend for one rocker segment."""
    if control.startswith("dpad_"):
        return chevron(cx, cy, control[5:])
    if control == "home":
        return (f'<path d="M{cx-15} {cy+1}L{cx} {cy-13}L{cx+15} {cy+1}M{cx-10} {cy-2}V{cy+13}H{cx+10}V{cy-2}" '
                f'fill="none" stroke="{ICON_BLUE}" stroke-width="5" stroke-linejoin="round" stroke-linecap="round"/>')
    if control == "menu":
        lines = "".join(
            f'<line x1="{cx-14}" y1="{cy + dy}" x2="{cx + (6 if dy < 0 else 14)}" y2="{cy + dy}"/>' for dy in (-9, 0, 9)
        )
        return (f'<g stroke="{ICON_BLUE}" stroke-width="4" stroke-linecap="round">{lines}</g>'
                + circle(cx + 13, cy - 10, 4, ICON_BLUE))
    # P1 / P2.
    return (f'<text x="{cx}" y="{cy + 9}" text-anchor="middle" fill="{ICON_BLUE}" font-size="26" font-weight="700" '
            f'{FONT}>{control.upper()}</text>')


def segments(row_controls):
    """Centre x of each control in a rocker row."""
    n = len(row_controls)
    w = ROW_W / n
    return [(c, ROW_X + w * (i + 0.5), w) for i, c in enumerate(row_controls)]


def faceplate():
    """Everything except screen content."""
    a = ANTENNA
    parts = [
        rrect(a["x"], a["y"], a["w"], a["h"], a["w"] / 2, "url(#hh-antenna)", 'stroke="#050606" stroke-width="2"'),
        *[side_knob(name, k) for name, k in KNOBS.items()],
        # Housing, then the front face between the side grips.
        rrect(BODY["x"], BODY["y"], BODY["w"], BODY["h"], 70, "url(#hh-body)", 'stroke="#050606" stroke-width="3"'),
        rrect(PANEL["x"], PANEL["y"], PANEL["w"], PANEL["h"], 50, "url(#hh-panel)", 'stroke="#111214" stroke-width="2"'),
        # Side buttons (artwork only) and the microphone hole.
        rrect(BODY["x"] - 12, 470, 16, 190, 8, "#151618", 'stroke="#050606" stroke-width="2"'),
        rrect(BODY["x"] + BODY["w"] - 4, 700, 16, 64, 8, "#151618", 'stroke="#050606" stroke-width="2"'),
        circle(90, 330, 5, "#050606"),
        # Screen bezel and the fixed parts of the screen layout.
        rrect(BEZEL["x"], BEZEL["y"], BEZEL["w"], BEZEL["h"], 14, "#050506", 'stroke="#2c2e31" stroke-width="2"'),
        f'<rect x="{SCREEN["x"]}" y="{STATUS_ROW["y"]}" width="{SCREEN["w"]}" height="{STATUS_ROW["h"]}" fill="#000"/>',
        f'<rect x="{SCREEN["x"]}" y="{STRIP_ROW["y"]}" width="{SCREEN["w"]}" height="{STRIP_ROW["h"]}" fill="#cfe3f3"/>',
        f'<rect x="{SCREEN["x"]}" y="{MAIN_ROW["y"]}" width="{SCREEN["w"]}" height="{MAIN_ROW["h"]}" fill="#fff" '
        'stroke="#cfe3f3" stroke-width="4"/>',
        f'<rect x="{SCREEN["x"]}" y="{DETAIL_ROW["y"]}" width="{SCREEN["w"]}" height="{DETAIL_ROW["h"]}" fill="#cfe3f3"/>',
        f'<rect x="{SCREEN["x"] + 4}" y="{DETAIL_ROW["y"] + 16}" width="{SCREEN["w"] - 8}" height="{DETAIL_ROW["h"] - 20}" fill="#fff"/>',
        f'<rect x="{SCREEN["x"]}" y="{SOFTKEY_ROW["y"] - 4}" width="{SCREEN["w"]}" height="{SOFTKEY_ROW["h"] + 4}" fill="#fff"/>',
        *[f'<rect x="{c["x"]}" y="{c["y"]}" width="{c["w"]}" height="{c["h"]}" fill="url(#hh-softcell)"/>'
          for c in SOFTKEY_LABELS],
    ]
    # Soft keys: rounded keys with a light dash.
    for cx in SOFTKEY_BUTTON_X:
        x, y = cx - SOFTKEY_BUTTON_W / 2, SOFTKEY_BUTTON_Y - SOFTKEY_BUTTON_H / 2
        parts += [
            rrect(x - 4, y - 4, SOFTKEY_BUTTON_W + 8, SOFTKEY_BUTTON_H + 8, 18, "#08090a"),
            rrect(x, y, SOFTKEY_BUTTON_W, SOFTKEY_BUTTON_H, 15, "url(#hh-button)", 'stroke="#3a3c3f" stroke-width="2"'),
            rrect(cx - 18, SOFTKEY_BUTTON_Y - 3, 36, 6, 3, ICON_WHITE),
        ]
    # Rocker rows with their legends.
    for y, controls in ROWS:
        parts += [
            rrect(ROW_X - 5, y - ROW_H / 2 - 5, ROW_W + 10, ROW_H + 10, ROW_H / 2 + 5, "#08090a"),
            rrect(ROW_X, y - ROW_H / 2, ROW_W, ROW_H, ROW_H / 2, "url(#hh-button)", 'stroke="#3a3c3f" stroke-width="2"'),
            *[icon(c, cx, y) for c, cx, _ in segments(controls)],
        ]
    return "\n".join(parts)


def live_screen():
    """Empty text elements that radioHead.setDisplay() fills in. Same ids as the desk head."""
    z = ZONES
    parts = ['<g id="screen-layer" pointer-events="none">']
    for i in range(4):
        parts.append(
            f'<rect class="signal-bar" x="{148 + i * 8}" y="{414 - (i + 1) * 6}" width="5" height="{(i + 1) * 6}" fill="#3ddc62"/>'
        )
    parts += [
        f'<text id="display-status" x="{z["status_bar"]["x"] + z["status_bar"]["w"]}" y="411" text-anchor="end" '
        f'fill="#fff" font-size="22" font-weight="700" {FONT}></text>',
        f'<text id="display-zone" data-max-width="{z["line_1_zone"]["w"]}" x="{z["line_1_zone"]["x"]}" y="488" '
        f'fill="#2a2a2a" font-size="30" {FONT}></text>',
        f'<text id="display-channel" data-max-width="{z["line_2_channel"]["w"]}" x="{z["line_2_channel"]["x"]}" y="550" '
        f'fill="#000" font-size="42" font-weight="700" {FONT}></text>',
        f'<text id="display-detail" data-max-width="{z["line_3_detail"]["w"]}" x="{SCREEN["x"] + SCREEN["w"] / 2}" '
        f'y="682" text-anchor="middle" fill="#111" font-size="26" font-weight="700" {FONT}></text>',
    ]
    for i, cell in enumerate(SOFTKEY_LABELS, start=1):
        parts.append(
            f'<text id="display-softkey-{i}" data-max-width="{cell["w"] - 10}" x="{cell["x"] + cell["w"] / 2}" '
            f'y="{cell["y"] + 37}" text-anchor="middle" fill="#fff" font-size="26" font-weight="700" {FONT}></text>'
        )
    parts.append("</g>")
    return "".join(parts)


def sample_overlay():
    """Placeholder content showing the zones in use. Not part of the template."""
    labels = ["Zone", "Chan", "Info"]
    parts = [
        *[f'<rect x="{148 + i * 8}" y="{414 - (i + 1) * 6}" width="5" height="{(i + 1) * 6}" fill="#3ddc62"/>'
          for i in range(4)],
        f'<text x="414" y="411" text-anchor="end" fill="#fff" font-size="22" font-weight="700" {FONT}>RX</text>',
        f'<text x="152" y="488" fill="#2a2a2a" font-size="30" {FONT}>Zone 1</text>',
        f'<text x="152" y="550" fill="#000" font-size="42" font-weight="700" {FONT}>CHANNEL 01</text>',
        f'<text x="280" y="682" text-anchor="middle" fill="#111" font-size="26" font-weight="700" {FONT}>Talkgroup 1001</text>',
    ]
    for cell, label in zip(SOFTKEY_LABELS, labels):
        parts.append(f'<text x="{cell["x"] + cell["w"] / 2}" y="{cell["y"] + 37}" text-anchor="middle" fill="#fff" '
                     f'font-size="26" font-weight="700" {FONT}>{label}</text>')
    return "\n".join(parts)


def hit_layer():
    """Invisible press targets. Rocker segments are clipped to their pill, so presses and the
    pressed highlight stop at its rounded ends."""
    parts = ['<g id="hit-layer">']
    for i, cx in enumerate(SOFTKEY_BUTTON_X, start=1):
        parts.append(rrect(cx - SOFTKEY_BUTTON_W / 2, SOFTKEY_BUTTON_Y - SOFTKEY_BUTTON_H / 2, SOFTKEY_BUTTON_W,
                           SOFTKEY_BUTTON_H, 15, "transparent", f'data-control="softkey_{i}"'))
    for row, (y, controls) in enumerate(ROWS):
        for c, cx, w in segments(controls):
            parts.append(f'<rect x="{cx - w / 2}" y="{y - ROW_H / 2}" width="{w}" height="{ROW_H}" fill="transparent" '
                         f'clip-path="url(#hh-row{row})" data-control="{c}"/>')
    parts.append("</g>")
    return "".join(parts)


def document(body):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
            f'data-template="handheld">{defs()}\n{body}\n</svg>\n')


def live_svg():
    return document(faceplate() + live_screen() + hit_layer())


def layout_json():
    hit_areas = {
        f"softkey_{i}": {"x": cx - SOFTKEY_BUTTON_W / 2, "y": SOFTKEY_BUTTON_Y - SOFTKEY_BUTTON_H / 2,
                         "w": SOFTKEY_BUTTON_W, "h": SOFTKEY_BUTTON_H}
        for i, cx in enumerate(SOFTKEY_BUTTON_X, start=1)
    }
    for y, controls in ROWS:
        for c, cx, w in segments(controls):
            hit_areas[c] = {"x": round(cx - w / 2, 1), "y": y - ROW_H / 2, "w": round(w, 1), "h": ROW_H}
    knobs = {name: {"x": k["cx"] - k["w"] / 2, "y": k["top"], "w": k["w"], "h": k["bottom"] - k["top"]}
             for name, k in KNOBS.items()}
    return {
        "base_canvas": {"w": W, "h": H},
        "scale_note": f"Multiply every value by rendered_width / {W}.",
        "screen": {"bezel": BEZEL, "screen": SCREEN},
        "text_zones": ZONES,
        "softkey_labels": SOFTKEY_LABELS,
        "hit_areas": {**hit_areas, **knobs},
        "behaviour": {
            "volume_knob": {"type": "absolute", "min": 0, "max": 100, "sweep_degrees": KNOBS["volume_knob"]["sweep"],
                            "view": "side"},
            "channel_knob": {"type": "detent", "degrees_per_step": CHANNEL_DETENT_DEG, "view": "side"},
            "side_view_note": ("Side-on knobs turn by swiping across them: right or up is clockwise, and moving "
                               "pi * width / 2 units turns 180 degrees. Their ridges slide sideways, repeating every "
                               f"{RIDGE_PERIOD} units."),
            "hold_to_activate_ms": HOLD_TO_ACTIVATE,
            "long_press_ms": LONG_PRESS_MS,
        },
    }


SAMPLE = {
    "zone": "Zone 1", "channel": "CHANNEL 01", "detail": "Talkgroup 1001", "status": "RX", "signal": 4,
    "softkeys": ["Zone", "Chan", "Info"],
}


def main():
    OUT.mkdir(exist_ok=True)
    master = OUT / "handheld.svg"
    master.write_text(document(faceplate()))
    render(master, OUT / "handheld@2x.png")
    sample = OUT / "_handheld-sample.svg"
    sample.write_text(document(faceplate() + sample_overlay()))
    render(sample, OUT / "handheld-sample@2x.png")
    sample.unlink()
    (OUT / "handheld-interactive.html").write_text(
        interactive_page(live_svg(), title="Handheld", max_width=420, sample=SAMPLE))
    (OUT / "handheld-layout.json").write_text(json.dumps(layout_json(), indent=2) + "\n")
    (WEB_OUT / "handheld.svg").write_text(live_svg())


if __name__ == "__main__":
    main()
