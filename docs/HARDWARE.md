# Hardware: Raspberry Pi ↔ radio

This interface works with any radio whose mic keys (PTT, UP, DOWN) work by pulling a line
to ground. That covers most land-mobile and UHF CB radios. It was built and documented for
an **Icom IC-F621** (see the notes at the end).

> ⚠️ **Check every pin against your radio's service manual before connecting anything.**
> Mic-jack pinouts vary between makes and models, and on many radios the mic
> **UP/DOWN keys share a single line** and are told apart by resistance to ground.
> The wiring below covers both cases. The software works the same either way.

## Parts (about AUD $60)

| Qty | Part | Notes |
|---|---|---|
| 1 | Raspberry Pi 4/5 (or Zero 2 W) | Pi OS Bookworm 64-bit |
| 1 | USB sound card, CM108/CM119 chip | Any cheap "USB audio adapter" with mic in + headphone out. CM108-based cards work well with Linux. |
| 3 | PC817 optocouplers (or a 4-channel PC817 module board) | PTT, UP, DOWN. Keeps the Pi electrically isolated from the radio. |
| 3 | 330 Ω resistors | Opto LED current limit |
| 2 | 600:600 Ω audio isolation transformers | TX and RX audio. Stops ground-loop hum and buzz. Optional but strongly recommended. |
| 1 | Mic-connector breakout (e.g. RJ45) or a spare/sacrificial mic cable | Access to the mic jack lines |
| — | Resistors/caps for audio attenuation (see below) | |

## Control lines (GPIO → opto → radio)

```
Pi GPIO17 (pin 11) ──330Ω──► PC817 #1 LED (+)   LED (–) ── Pi GND (pin 9)
                              PC817 #1 collector ── radio PTT line
                              PC817 #1 emitter   ── radio GND (mic connector ground)

Pi GPIO27 (pin 13) ──330Ω──► PC817 #2  → radio UP     (same as above)
Pi GPIO22 (pin 15) ──330Ω──► PC817 #3  → radio DOWN
```

The opto shorts the radio line to ground, which is what the mic's own button does.
These pins default to pull-down at boot, so the radio **won't key up** while the Pi boots.

### If UP and DOWN share one mic line (resistor ladder)

Measure the resistance between the UP/DN line and ground while holding each key on the
original mic (for example UP ≈ 0 Ω and DOWN ≈ 470 Ω, or similar). Then copy those values:

```
UP/DN line ──┬── R_up   ── PC817 #2 collector   (emitter → radio GND)
             └── R_down ── PC817 #3 collector   (emitter → radio GND)
```

### Radio programming

Mic UP/DOWN must be set to channel up/down in the radio's programming (the manufacturer's
programming software, "Mic/Key assignment" or similar). Make sure channel wrap-around matches
`channel.wrap` and the number of channels matches `channel.count` in `config.yaml`.

### Optional busy/squelch line

If the radio's accessory connector has a busy/squelch-open output, feed it through a
fourth opto into a GPIO (for example GPIO23) and set `gpio.busy_pin`. Without it, the
software estimates "busy" from the RX audio level.

## Audio

### TX: Pi → radio mic input
The USB card's headphone output is line level (~1 V). The mic input expects tens of mV.

```
USB L-out ──[transformer]── 10 kΩ ──┬── 1 µF ── radio MIC+
                                    100 Ω
                                     │
                               radio MIC GND
```
Start with this ~40 dB pad. Fine-tune with the **Mic gain** slider in the UI, `audio.tx_gain`,
and `alsamixer`, until deviation sounds like a normal handheld mic. The 1 µF cap blocks any
electret bias voltage the radio puts on the mic pin.

### RX: radio → Pi
Take audio from the **external speaker jack** (level follows the volume knob, so set it once
and leave it) or from a fixed-level AF output on the accessory port if there is one.

```
Radio EXT SP ──[transformer]── 47 kΩ ──┬── 1 µF ── USB MIC-in
                                       1 kΩ
                                        │
                                   USB GND
```
Speaker-level audio is far too hot for a USB mic input, so attenuate a lot. Set capture
level with `alsamixer` (F4 = capture) so normal speech peaks around −12 dBFS in the UI meter.

## Bench test checklist

1. Run with `gpio.backend: gpiozero` and **radio into a dummy load**.
2. Press ▲/▼ in the UI. The radio should step channels. Tweak `press_ms`/`gap_ms` if presses are missed.
3. Hold PTT. The radio should key. Release it and the radio should unkey within ~0.5 s.
4. Pull the Pi's network cable while transmitting. The radio must unkey within `hold_timeout_ms`.
5. Hold PTT longer than `max_tx_seconds`. The server must drop PTT.
6. Check TX audio deviation on a second radio / service monitor.

## Radio notes: Icom IC-F621

- The front-panel mic jack is an 8-pin modular (RJ45-style) connector. Get the PTT, UP/DOWN,
  MIC and ground pins from the IC-F621 service manual. Don't rely on another Icom model's pinout.
- Check whether UP and DOWN are separate lines or one resistor-ladder line by measuring the
  original mic (see above).
- Make sure the mic UP/DOWN keys are assigned to channel up/down in the radio's programming.
- Take RX audio from the external speaker jack, or from the accessory connector if your radio
  has one with fixed-level AF out and a busy signal.
