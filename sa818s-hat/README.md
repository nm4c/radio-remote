# Radio Remote SA818S HAT

A Raspberry Pi 4 HAT with a **built-in 1 W UHF FM transceiver**: no external radio. A NiceRF SA818S-U
module (400–470 MHz) is controlled by the Pi over a serial port, with audio through the same
TLV320AIC3104 I2S codec as the [Radio Remote HAT](../pihat/). It's meant for licensed amateur or
experimental use.

| Block | What's on the board |
|---|---|
| Transceiver | SA818S-U, frequency, CTCSS/DCS and squelch set over UART (9600 8N1, GPIO14/15) |
| Harmonic filter | 7th-order Chebyshev low-pass, fc 500 MHz: −0.6 to −1.1 dB from 400–480 MHz, about −50 dB at 2nd harmonic of 476 MHz, −80 dB at the 3rd (simulated with real inductor Q) |
| Antenna | Edge-mount SMA (Samtec SMA-J-P-H-ST-EM1) |
| Control | PTT via MOSFET (boots in receive), high/low power via open-drain MOSFET, power-down on GPIO27, busy from the module's AudioON on GPIO23 |
| Audio | Codec headphone out → −30 dB → MIC_IN; AF_OUT → −6 dB → codec input. Direct, no transformers (shared ground) |
| Power | 13.8 V in, 5 V / 3.5 A buck for the Pi, **separate** 4.2 V buck + 2 A ferrite for the module, codec rails as on the pihat |
| ID EEPROM | AT24C32D, write-protected by default (R3 pull-up), JP1 to write |

## GPIO use

| Pin | GPIO | Function |
|---|---|---|
| 8 / 10 | 14 / 15 | UART TX / RX to the module |
| 11 | 17 | PTT (high = transmit) |
| 13 | 27 | Module power: high = on, low = sleep (pulled low at boot) |
| 15 | 22 | Low power: high = 0.5 W, low = 1 W |
| 16 | 23 | Busy: low = signal present (`busy_pin: 23`, `busy_pull_up: true`) |
| 12, 35, 38, 40 / 3, 5 / 37 | 18–21 / 2, 3 / 26 | I2S, I2C and reset for the codec |

The Pi 4's main UART is normally used by Bluetooth. Add `dtoverlay=disable-bt` (or `miniuart-bt`) to
`/boot/firmware/config.txt`.

## Files

| File | |
|---|---|
| `sa818s-hat.kicad_pro` / `.kicad_sch` / `.kicad_pcb` | KiCad project (from KiCad's built-in Raspberry Pi HAT template) |
| `sa818s-hat.kicad_sym`, `sa818s-hat.pretty/` | Project library: SA818S symbol and footprint, codec symbol |
| `tools/` | Generator for the sheets (`build.py`) and netlist check (`verify.py`). Uses the shared code in `../kicadgen/` and the power and codec sheets from `../pihat/tools/kicad/` |

## Before layout

- **SA818S footprint:** pad positions agree between two independent footprints (one derived from LCSC/EasyEDA)
  and the datasheet drawing. Print it 1:1 and check a module against it before ordering.
- **RF layout:** 50 Ω grounded coplanar waveguide from the module's ANT pad through the filter to the SMA,
  short and straight, with ground vias beside every filter capacitor and a solid ground plane under the module.
  Leave room for a shield can over the module and filter.
- **Supplies:** keep the 4.2 V buck and its inductor away from the module. Place C38/C39/C40 at the VBAT pad.
- **Check harmonics** on a spectrum analyser after the first build, before connecting an antenna.
- **SMA stock:** about 340 at JLC when checked (Nov 2026).
- **Frequency range:** this module revision is specified to 470 MHz. NiceRF's current page says 480 MHz.

## Software

Set `radio.type: sa818` in `config.yaml` (see `config.example.yaml`):

- `channel.list`: name, frequency, optional `tx_freq` for repeaters, `tone` (CTCSS Hz or DCS `D023N`),
  and per-channel bandwidth, squelch or low power.
- The web app shows the frequency and tones, an RSSI bar, and a box to tune any frequency in `sa818.band` (the VFO).
- At start-up the server wakes the module (PD), sets volume, audio filters and tail tone, then the channel.
  If the module stops answering, operators see the error, PTT is refused, and the server retries every 5 s.
- `gpio.busy_pin: 23` uses the module's AudioON as the BUSY indicator.
- RSSI is the module's raw 0–255 reading in 1 dB steps. Set `sa818.rssi_offset_db` after checking it against a
  known signal to show dBm.

Pi 4 set-up: `dtoverlay=disable-bt` in `/boot/firmware/config.txt`, the serial console off and the serial
hardware on (`raspi-config` → Interface Options → Serial Port), then reboot. The installer adds the service user
to `dialout` for `/dev/serial0`.

## Licence

Hardware: CERN-OHL-S v2 (see [`LICENSE`](LICENSE)). The generator scripts are GPL-3.0-or-later (see the
repository root).
