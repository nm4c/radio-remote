# Radio Remote

A web-based remote head for a two-way radio, running on a Raspberry Pi. You can
receive audio, push-to-talk with your computer or phone microphone, and change channels
from a browser.

It works with any radio whose PTT and channel UP/DOWN can be operated by grounding a
line, which covers most land-mobile and UHF CB radios through the mic connector. It was
built for an Icom Radio. Set the model name shown in the UI with `radio.name` in
`config.yaml`.

There are two hardware modes, set with `radio.type` in `config.yaml`:

| `radio.type` | Hardware | Channels |
|---|---|---|
| `buttons` (default) | An external radio through the **[Radio Remote HAT](pihat/)** (or hand-wired optos, [docs/HARDWARE.md](docs/HARDWARE.md)) | The radio's own channels, stepped with UP/DOWN presses |
| `sa818` | The **[SA818S HAT](sa818s-hat/)**: a 1 W UHF transceiver on the HAT, no external radio | A frequency/tone list in `config.yaml`, set over the module's serial port, plus direct frequency entry. Shows signal strength (RSSI) |

```
 Browser (phone / PC)                     Raspberry Pi                      Radio  
┌───────────────────┐   WebSocket    ┌──────────────────────┐  GPIO+opto  ┌──────────┐
│ PTT / CH▲▼ / list │◄──────────────►│ server (Python)      │────────────►│ PTT UP DN│
│ mic ─► Opus       │  JSON control  │  - PTT safety timers │  USB audio  │ MIC in   │
│ speaker ◄─ Opus   │◄══════════════►│  - channel tracking  │◄───────────►│ SPK out  │
│                   │ WebRTC (UDP)   │                      │             │          │
└───────────────────┘                └──────────────────────┘             └──────────┘
```

## Features

- Hold-to-talk button (mouse, touch or **Space** key). Mic audio streams to the radio.
- Low-latency audio over WebRTC (Opus over UDP), with automatic fallback to audio over the WebSocket.
  Settings shows the round trip and receive buffer.
- Receive audio with an RX level meter and a BUSY indicator.
- Channel ▲/▼ and jump-to-channel. The Pi presses UP/DOWN the right number of times.
- Channel names are set in config.
- A radio-style control head: a desk head on wide screens and a portrait handheld on phones.
  See [Control head](#control-head).
- With the SA818S HAT: frequencies, repeater offsets and CTCSS/DCS per channel, a VFO for any
  frequency in the band, and an RSSI bar on the display.
- Several operators can listen at once. Only one can transmit, and the others see who's on air.
- Safety features:
  - Server-side transmit time-out (`max_tx_seconds`).
  - PTT drops if the browser goes quiet (network loss, closed tab, phone locked).
  - PTT drops if the browser window loses focus.
  - No channel changes while transmitting.
  - GPIO pins stay low at boot.
- Mock GPIO and mock audio, so you can develop on a laptop without the radio.

## Try it on your laptop (no hardware)

```bash
cd radio-remote
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
#  in config.yaml set: gpio.backend: mock, audio.backend: mock, password: something
python -m server -c config.yaml
```

Open <http://localhost:8080>. `localhost` counts as secure, so the mic works.
You'll hear a test tone burst every 10 s, and PTT/channel presses show in the server log.
Set `radio.type: sa818` to try the SA818S mode: the mock module answers commands and reports a wandering RSSI.
Set `audio.backend: sounddevice` to use your laptop's own mic and speakers as a pretend radio.

## Control head

The top of the page is drawn as a radio. Wider screens get a desk control head and phones
(600 px and narrower) get a handheld. Both show the channel, name, frequency, TX with the
time-out countdown, BUSY, RSSI and signal bars.

| Control | Does |
|---|---|
| Channel knob (right on the desk head, the tall one on the handheld) | Picks a channel. The channel shows straight away in amber and is sent once you stop turning. |
| D-pad up / down | One channel up or down |
| Volume knob | Speaker volume, the same as the slider |
| Soft keys | Chans (channel list), Tune (SA818) or Sync (button radios), Setup (Settings) |
| Menu (handheld) | Settings |
| Power (desk), held 1.5 s | Disconnect |
| Emergency (desk), held 1.5 s | Shows a notice only. It is not connected to anything that transmits. |

Knobs turn by dragging or with the mouse wheel. The handheld's top knobs roll with a swipe
across them. A focused knob also takes the arrow keys. PTT stays a separate button (or Space),
because neither head has one on its face.

The artwork is generated. Change `assets/control-head/build_template.py` (desk) or
`build_handheld.py` (handheld), then run them; they need `rsvg-convert` for the PNGs:

```bash
python3 assets/control-head/build_template.py && python3 assets/control-head/build_handheld.py
```

They write the app's SVGs and `radio_config.js` to `web/control-head/`, and PNG layers,
layout JSON and standalone test pages to `assets/control-head/output/`. The input handling,
`web/control-head/control_head.js`, is hand-written and shared by both templates.

## Install on the Pi

1. Build the interface: see **[docs/HARDWARE.md](docs/HARDWARE.md)**. It has generic wiring plus IC-F621 notes.
   Or use the **[Radio Remote HAT](pihat/)**, a Pi HAT with the interface, sound card and power supply on one board.
2. Copy this folder to the Pi and run:
   ```bash
   sudo ./deploy/install.sh
   sudo nano /opt/radio-remote/config.yaml
   ```
   Set `password`, `radio.type`, the channels (`channel.count` / `names` for an external radio,
   `channel.list` for the SA818S HAT; the SA818S HAT also needs the UART set up, see the `sa818:` section), and the sound card
   (`input_device` / `output_device`, for example `"USB Audio"`. List them with
   `/opt/radio-remote/.venv/bin/python -m sounddevice`).
3. `sudo systemctl start radio-remote` and watch the log with `journalctl -u radio-remote -f`.

### Remote access over HTTPS (needed for the microphone)

Browsers only allow microphone access on `https://` or `localhost`. With Tailscale:

```bash
sudo tailscale serve --bg 8080
```

Then open `https://<pi-name>.<your-tailnet>.ts.net` on any device in your tailnet.
You can also set `server.tls_cert` / `tls_key` to your own certificate.

### Using an iPhone

1. Install the Tailscale app and connect, then open the `https://…ts.net` address in Safari.
2. Tap **Share → Add to Home Screen** so it opens full-screen like an app.
3. Use headphones (wired or Bluetooth). iOS turns the loudspeaker down while a web page
   has the mic open.

Things to know:
- **Keep it on screen.** iOS pauses web audio and connections when the phone locks or you
  switch apps. The page keeps the screen awake while connected (iOS 16.4+), and drops PTT
  safely if it goes to the background.
- **After a call or Siri** the page shows "Audio paused. Tap here to resume". Any tap
  restarts audio, and if iOS released the mic, the next PTT press re-acquires it.
- **No hardware PTT.** A web page can't use the volume or headset buttons, so it's the
  on-screen button only.

## Channel tracking

Mic-line button emulation can't read the channel back from the radio, so the Pi **tracks the
channel itself** and remembers it across restarts (`state.json`). If someone changes
channel on the radio's front panel, open **Settings → Set current channel** in the UI to
re-sync. No buttons are pressed.

## Protocol (for building other clients)

WebSocket at `/ws`. The first message must be
`{"type":"auth","password":"…","name":"VK2XYZ"}`.

| Direction | Message |
|---|---|
| → | `{"type":"ptt","on":true\|false}` |
| → | `{"type":"ptt_hold"}` every `hold_interval_ms` while transmitting |
| → | `{"type":"channel","action":"up"\|"down"}` |
| → | `{"type":"channel","action":"goto"\|"sync","channel":5}` (`sync` only for `buttons`) |
| → | `{"type":"channel","action":"tune","freq":439.125,"tx_freq":null,"tone":"91.5"}` (`sa818` only: VFO, channel 0) |
| → | binary: TX audio, int16 LE mono 16 kHz, 20 ms frames. Ignored unless you hold PTT. |
| ← | `auth`, `hello` (channel list, timing, `radio_type`, `can_sync`, `can_tune`, `band`), `state` (channel, PTT owner, TOT remaining, `info` = frequency/tones or null, `error`), `meter` (5 Hz, plus `rssi` / `rssi_dbm` on `sa818`), `notice` |
| ← | binary: RX audio, same format (not sent once WebRTC audio is connected) |
| → | `{"type":"rtc_offer","sdp":"…"}` complete SDP (non-trickle): one sendrecv Opus audio track, your mic out, radio in |
| ← | `{"type":"rtc_answer","sdp":"…"}` or `{"type":"rtc_failed","text":"…"}` |
| → | `{"type":"rtc_close"}` give up on WebRTC and go back to WebSocket audio |

## Layout

```
server/   main.py (web + WebSocket + safety watchdog), radio.py (PTT, channels, button radio),
          sa818.py (SA818S module over UART), rtc.py (WebRTC audio), audio.py (sound card, resampling)
web/      index.html, app.js, worklet.js (AudioWorklet capture/playback), style.css,
          control-head/ (control head SVGs and their input handling, control_head.js)
assets/control-head/  generators for the control head artwork, plus PNGs and layout JSON
deploy/   install.sh, systemd unit
docs/     HARDWARE.md (hand-wired interface)
pihat/       Pi HAT for an external radio: KiCad project, schematic reference, JLC BoM
sa818s-hat/  Pi HAT with a built-in SA818S 1 W UHF transceiver (no external radio)
kicadgen/    shared KiCad schematic generator used by both HATs
```

## Ideas for later

- Pluggable control backends (CI-V / CAT serial) for radios that can report channel and RSSI
- Per-user accounts and audit log of who transmitted when
- VOX-style recording of RX traffic

## Licence

This project is open source and share-alike: you can use, modify, build and sell it,
but anything you distribute that's based on it must be shared under the same terms.

| Part | Licence | File |
|---|---|---|
| Software: `server/`, `web/`, `assets/control-head/`, `deploy/`, `kicadgen/`, `pihat/tools/`, `sa818s-hat/tools/`, `pihat/docs/schematic/*.py` | GNU GPL v3.0 or later | [`LICENSE`](LICENSE) |
| Hardware: the KiCad projects in `pihat/` and `sa818s-hat/` (schematics, PCB, symbols, footprints), `pihat/docs/` | CERN-OHL-S v2 | [`pihat/LICENSE`](pihat/LICENSE), [`sa818s-hat/LICENSE`](sa818s-hat/LICENSE) |
| Documentation: this README and `docs/` | CC BY-SA 4.0 | [`docs/LICENSE`](docs/LICENSE) |

Copyright © 2026 nmc. Source: <https://github.com/nm4c/radio-remote>

The HAT schematic started from KiCad's built-in Raspberry Pi HAT template, which KiCad's library
licence lets you use in any design without passing on its terms. A future custom device-tree overlay
based on the Linux kernel's would stay GPL-2.0, as the kernel requires.

"Raspberry Pi" is a trademark of Raspberry Pi Ltd. Radio makes and models are named only to describe
compatibility.
