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

The codec uses the `tlv320aic3x` driver already in Raspberry Pi OS, with a small custom overlay
(still to write): `simple-audio-card` with the codec at I2C 0x18, `reset-gpios` on GPIO26 (active
low) and a fixed 12 MHz system clock. `simple-audio-card` always clocks the codec from MCLK, which
is where Y1's 12 MHz goes on this board. Don't use the stock `dacberry400` overlay: its driver
clocks the codec's PLL from BCLK while telling it the clock is 12 MHz, so the sample rate would be
wrong here. In ALSA, set the HPLCOM output to "differential of HPLOUT" so T1 is driven
differentially. After that the remote-head server sees an ordinary ALSA sound card: set
`audio.input_device` / `output_device` in `config.yaml` to its name, and `gpio.busy_pin: 23` with
`busy_pull_up: true`.

**Powering:** the HAT powers the Pi through header pins 2 and 4. Don't power the Pi from its USB-C
socket while the HAT is fitted and its 13.8 V input is off: the Pi's 5 V would then sit on the
buck's output with no input, which the LMR33640 datasheet says not to allow.

## Licence

The hardware design in this folder is licensed under the **CERN Open Hardware Licence v2, Strongly
Reciprocal (CERN-OHL-S-2.0)**. See [`LICENSE`](LICENSE). The generator scripts in `tools/` and
`docs/schematic/gen.py` are software and are GPL-3.0-or-later (see the repository root).

CERN-OHL-S asks that products made from the design say where the source can be found. Put this on
the board's silkscreen during layout, for example:

    Radio Remote HAT  CERN-OHL-S-2.0  github.com/nm4c/radio-remote
