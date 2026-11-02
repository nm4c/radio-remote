# Radio Remote HAT

A Raspberry Pi HAT that replaces the hand-wired interface in [`../docs/HARDWARE.md`](../docs/HARDWARE.md).
It has everything on one board:

- **Opto-isolated control:** PTT, channel UP/DOWN and BUSY through a TCMT4100.
- **On-board sound card:** a TLV320AIC3104 I2S codec, with isolation transformers to the radio's
  mic input and speaker.
- **Power:** a 13.8 V input with protection, and a 5 V buck that also powers the Pi.
- **HAT ID EEPROM:** tells the Pi which sound-card overlay to load at boot.

| File | What it is |
|---|---|
| `pihat.kicad_pro` / `.kicad_sch` / `.kicad_pcb` | KiCad project |
| [`docs/schematic/radio-remote-hat.html`](docs/schematic/radio-remote-hat.html) | Reference schematic: 7 sheets, netlist, parts, pre-layout checks. Open it in a browser. |
| [`docs/BOM.md`](docs/BOM.md) | BoM notes and cost summary |
| [`docs/jlc-bom.csv`](docs/jlc-bom.csv) | JLCPCB assembly BoM |
| `docs/schematic/gen.py` | Regenerates the schematic page and the CSV from one parts list |

## Software

The codec uses the `tlv320aic3x` driver already in Raspberry Pi OS. Use the `dacberry400` overlay,
or a small custom overlay if bring-up shows the codec needs to run from the 12 MHz MCLK (see the
schematic's "Before layout" notes). After that, the remote-head server sees an ordinary ALSA sound
card. Set `audio.input_device` / `output_device` in `config.yaml` to its name.

## Licence

The hardware design in this folder is licensed under the **CERN Open Hardware Licence v2, Strongly
Reciprocal (CERN-OHL-S-2.0)**. See [`LICENSE`](LICENSE). The generator scripts in `tools/` and
`docs/schematic/gen.py` are software and are GPL-3.0-or-later (see the repository root).

CERN-OHL-S asks that products made from the design say where the source can be found. Put this on
the board's silkscreen during layout, for example:

    Radio Remote HAT  CERN-OHL-S-2.0  github.com/nm4c/radio-remote
