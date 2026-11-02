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
| **Basic** (no loading fee) | 22 |
| **Extended** (≈ US$3 each, per order) | 17 |

That's about US$51 in loading fees per order, not per board. To cut it:
1. **Solder the through-hole connectors yourself:** J1 (Pi header), J2/J3 (RJ45), J4/J5 (patch
   headers), J6–J9 (screw terminals). That's 4 fewer Extended parts, and the board qualifies for
   JLC's cheaper "Economic" assembly, which only does SMD parts.
2. **Leave the power section off** (U3, L1, D1–D3, F1, C19) if the Pi has its own supply. That's
   6 fewer Extended parts. The codec rails (U4, U5) are still needed and run from the Pi's 5 V.

## Design choices

- **Basic parts wherever possible:** all resistors, nearly all capacitors, ferrites, LEDs and D4.
- **Reused values:** 33 Ω for all series resistors, 10 kΩ for the pads and pull-downs, 100 pF and a
  ferrite on every radio-side line.
- **One part doing two jobs:** SS56 is both the reverse-polarity diode and the buck catch diode.
- **TI regulators:** TLV76133 for 3.3 V. The TLV76118 (1.8 V) has too little stock, so the 1.8 V
  rail uses a TPS7A2018 instead: TI, very low noise, 11k in stock.
  Avoid the "TP"-prefixed look-alikes JLC lists (3PEAK clones, not TI).
- **TCMT4100 quad opto:** one package for all four channels. At its 50 % minimum gain, 470 Ω from
  3.3 V (≈ 4 mA) still sinks ≥ 2 mA on the radio side.
- **No trimmers:** the codec sets TX and RX levels in software.
- **Feedback from Basic values only:** 51 kΩ + 2.2 kΩ over 10 kΩ gives 5.06 V.
- **Don't substitute an RJ45 with magnetics** (for example HR911105A). Its Ethernet transformers
  block the DC on PTT and HOOK.

## Still to do before ordering

See the "Before layout" checklist at the end of the schematic page. In short:
- **Y1 pinout:** confirm it (the datasheet is encrypted).
- **U3 compensation:** R29/C28/C29 are calculated (10k / 6.8 nF / 47 pF). Confirm with a load-step test at bring-up.
- **Codec clock:** confirm the clock setup at bring-up.
- **Mic-jack numbering:** check each radio's mic-jack numbering before patching J4 → J5.
- **Isolation clearance:** keep radio-side copper ≥ 2 mm from Pi-side copper.
