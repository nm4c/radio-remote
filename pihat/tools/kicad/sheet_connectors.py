"""Sheet: Radio connectors. Two pluggable terminal headers (audio, radio control) and the JP1 ground link.

All board connectors are Xinlaiya XY2500V-F, 3.5 mm pitch, vertical, closed ends, 8 A / 300 V, so
the plugs go in from above (through the HAT opening). Footprints and 3D models are in the shared
library (kicadgen/sharedlib.py). The 13.8 V input, J2, is drawn on the Power sheet. Wire each radio's accessory connector to J3 and J4 with a short lead.
"""
from schbuild import Sheet
import sharedlib

LCSC = {2: "C880554", 4: "C880556", 6: "C880558"}   # XY2500V-F-3.5-{2,4,6}P


def fp(n):
    return f"{sharedlib.LIB}:{sharedlib.xy2500v_name(n)}"


POWER_IN = ("J2", "13.8 V in", fp(2), LCSC[2])  # for sheet_power.build(power_in=...)

# Pin assignments, pin 1 first. RGND is the radio's ground.
AUDIO = ["R_MIC", "RGND", "R_SPP", "R_SPN"]
RADIO = ["RGND", "R_PTT", "R_UP", "R_DN", "BUSY+", "BUSY-"]


def chain(s, pts):
    for a, b in zip(pts, pts[1:]):
        s.wire(a, b)


def header(s, ref, value, pins, at):
    """An XY2500V-F header with global labels (or RGND symbols) on its pins."""
    n = len(pins)
    x, y = at
    s.symbol(f"Connector_Generic:Conn_01x0{n}", ref, value, at, mirror="y", footprint=fp(n),
             lcsc=LCSC[n], ref_at=(x, y - 3.81 - 1.27 * n), val_at=(x, y + 3.81 + 1.27 * n),
             conn={str(i + 1): ("pwr", "RGND", 5.08) if net == "RGND" else ("glabel", net, 5.08)
                   for i, net in enumerate(pins)})


def build(path, page):
    s = Sheet("Radio connectors", path, page)
    s.text("RADIO CONNECTORS  (XY2500V-F, 3.5 mm pluggable, vertical)", (17.78, 30.48), 2.0, bold=True)

    s.text("J3  AUDIO", (40.64, 45.72), 1.5, bold=True)
    header(s, "J3", "Audio", AUDIO, (50.8, 58.42))
    for i, t in enumerate((
        "1  TX audio to the radio (mic or line input)",
        "2  TX audio return (radio ground)",
        "3  RX audio + from the radio",
        "4  RX audio -",
        "RX + / - float on T2: either polarity, and safe",
        "on a bridged (BTL) speaker output.",
    )):
        s.text(t, (78.74, 52.07 + i * 3.81))

    s.text("J4  RADIO", (40.64, 91.44), 1.5, bold=True)
    header(s, "J4", "Radio", RADIO, (50.8, 106.68))
    for i, t in enumerate((
        "1  Radio ground",
        "2  PTT (the opto pulls it to ground)",
        "3  Channel UP",
        "4  Channel DOWN",
        "5  BUSY +",
        "6  BUSY -",
        "BUSY wiring: active-high busy output -> 5, and 6 to radio ground.",
        "Open-collector busy: 5 to radio +V, 6 to the busy output.",
    )):
        s.text(t, (78.74, 97.79 + i * 3.81))

    s.text("Mating plugs: 3.5 mm pluggable terminal plugs (Xinlaiya XY2500F / Phoenix MC 1,5/x-ST-3,5 style), 2, 4, 6 way.",
           (17.78, 140.97))
    s.text("The 13.8 V input J2 is on the Power sheet.", (17.78, 144.78))

    # ground link
    s.text("GROUND LINK", (17.78, 157.48), 2.0, bold=True)
    s.symbol("Jumper:SolderJumper_2_Open", "JP1", "RGND-GND", (55.88, 170.18),
             footprint="Jumper:SolderJumper-2_P1.3mm_Open_RoundedPad1.0x1.5mm",
             ref_at=(55.88, 165.1), val_at=(55.88, 175.26))
    a, b = s.p("JP1.1"), s.p("JP1.2")
    chain(s, [a, (a[0] - 5.08, a[1]), (a[0] - 5.08, a[1] + 2.54)])
    s.power("RGND", (a[0] - 5.08, a[1] + 2.54))
    chain(s, [b, (b[0] + 5.08, b[1]), (b[0] + 5.08, b[1] + 2.54)])
    s.power("GND", (b[0] + 5.08, b[1] + 2.54))
    s.text("Open by default: keeps radio ground off the Pi ground.", (73.66, 168.91))
    s.text("The radio joins the two through its own supply lead.", (73.66, 172.72))
    s.text("Close only for testing.", (73.66, 176.53))
    return s
