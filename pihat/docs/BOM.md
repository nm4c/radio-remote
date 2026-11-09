# Radio Remote HAT: BoM notes (Rev A)

The full parts list, with designators, is in the schematic page
([`schematic/radio-remote-hat.html`](schematic/radio-remote-hat.html), "Parts" section) and in
[`jlc-bom.csv`](jlc-bom.csv), ready to upload to JLCPCB. Both come from the same list in
`schematic/gen.py`. Change the list there, then run `python3 gen.py` to update both.

LCSC numbers, JLC part type and stock were checked against JLCPCB on **2 Nov 2026**. Stock moves,
so re-check before ordering.

## Cost summary

| | Unique parts |
|---|---|
| **Basic** (no loading fee) | 18 |
| **Extended** (≈ US$3 each, per order) | 17 |

That's about US$51 in loading fees per order, not per board. To cut it:
1. **Solder the through-hole connectors yourself:** J1 (Pi header) and J2–J4 (pluggable
   headers). That's 4 fewer Extended parts, and the board qualifies for
   JLC's cheaper "Economic" assembly, which only does SMD parts.
2. **Leave the power section off** (U3, L1, D1, D3, F1, C19, C28) if the Pi has its own supply. That's
   6 fewer Extended parts. The codec rails (U4, U5) are still needed and run from the Pi's 5 V.

## Design choices

- **Basic parts wherever possible:** all resistors, nearly all capacitors, ferrites, LEDs and D4.
- **Reused values:** 33 Ω for all series resistors, 10 kΩ for the pads and pull-downs, 100 pF and a
  ferrite on the two RF-sensitive radio lines (PTT and MIC).
- **Synchronous buck:** the LMR33640 has its own low-side switch and internal compensation, so
  there is no catch diode, compensation network or frequency resistor.
- **TI regulators:** TLV76133 for 3.3 V. The TLV76118 (1.8 V) has too little stock, so the 1.8 V
  rail uses a TPS7A2018 instead: TI, very low noise, 11k in stock.
  Avoid the "TP"-prefixed look-alikes JLC lists (3PEAK clones, not TI).
- **TCMT4100 quad opto:** one package for all four channels. At its 50 % minimum gain, 470 Ω from
  3.3 V (≈ 4 mA) still sinks ≥ 2 mA on the radio side.
- **No trimmers or TX pad:** the codec sets the TX level in software, from line level down to mic
  level. The RX pad (R11/R12/R13, −9.5 dB) suits line-level RX audio; from a speaker output, fit
  R13 = 1 kΩ (−26 dB).
- **Feedback from Basic values only:** 82 kΩ over 20 kΩ with the LMR33640's 1.0 V reference gives 5.10 V.
- **Pluggable radio connectors:** Xinlaiya XY2500R-G, 3.5 mm pitch, right-angle with closed ends,
  8 A / 300 V. J2 13.8 V in (2 way), J3 audio (4 way: TX, TX return, RX+, RX−), J4 radio (6 way:
  ground, PTT, UP, DOWN, BUSY+, BUSY−). Each radio gets its own lead with mating 3.5 mm plugs
  (Phoenix MC 1,5/x-ST-3,5 style).

## Still to do before ordering

See the "Before layout" checklist at the end of the schematic page. In short:
- **Y1 pinout:** confirm it (the datasheet is encrypted).
- **U3 stock:** LMR33640ADDAR (C2836966) is Extended. One JLCPCB snapshot showed very few
  available to order, so check the live page; pre-order or consign if it is short.
- **U5 placement:** U5 shares C25 at U4's output instead of having its own input cap; keep them close.
- **Codec clock:** confirm the clock setup at bring-up.
- **Connector footprints:** J2–J4 use KiCad's Phoenix MC 1,5/x-G-3,5 footprints, which the
  XY2500R-G copies. Check them against the Xinlaiya drawing and keep the plug side at the board edge.
- **Isolation clearance:** keep radio-side copper ≥ 2 mm from Pi-side copper.
