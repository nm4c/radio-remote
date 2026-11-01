# Radio Remote

A web-based remote head for a two-way radio, running on a Raspberry Pi. You can
receive audio, push-to-talk with your computer or phone microphone, and change channels
from a browser.

It works with any radio whose PTT and channel UP/DOWN can be operated by grounding a
line, which covers most land-mobile and UHF CB radios through the mic connector. It was
built for an Icom Radio. Set the model name shown in the UI with `radio.name` in
`config.yaml`.

```
 Browser (phone / PC)                     Raspberry Pi                      Radio  
┌───────────────────┐   WebSocket    ┌──────────────────────┐  GPIO+opto  ┌──────────┐
│ PTT / CH▲▼ / list │◄──────────────►│ server (Python)      │────────────►│ PTT UP DN│
│ mic ─► 16k PCM    │  JSON control  │  - PTT safety timers │  USB audio  │ MIC in   │
│ speaker ◄─ 16k PCM│  + binary audio│  - channel tracking  │◄───────────►│ SPK out  │
└───────────────────┘                └──────────────────────┘             └──────────┘
```

## Features

- Hold-to-talk button (mouse, touch or **Space** key). Mic audio streams to the radio.
- Receive audio with a jitter buffer, an RX level meter and a BUSY indicator.
- Channel ▲/▼ and jump-to-channel. The Pi presses UP/DOWN the right number of times.
- Channel names are set in config.
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
pip install aiohttp numpy pyyaml sounddevice
cp config.example.yaml config.yaml
#  in config.yaml set: gpio.backend: mock, audio.backend: mock, password: something
python -m server -c config.yaml
```

Open <http://localhost:8080>. `localhost` counts as secure, so the mic works.
You'll hear a test tone burst every 10 s, and PTT/channel presses show in the server log.
Set `audio.backend: sounddevice` to use your laptop's own mic and speakers as a pretend radio.

## Install on the Pi

1. Build the interface: see **[docs/HARDWARE.md](docs/HARDWARE.md)**. It has generic wiring plus IC-F621 notes.
2. Copy this folder to the Pi and run:
   ```bash
   sudo ./deploy/install.sh
   sudo nano /opt/radio-remote/config.yaml
   ```
   Set `password`, `channel.count`, `channel.names`, and the sound card
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
| → | `{"type":"channel","action":"goto"\|"sync","channel":5}` |
| → | binary: TX audio, int16 LE mono 16 kHz, 20 ms frames. Ignored unless you hold PTT. |
| ← | `auth`, `hello` (channel list, timing), `state` (channel, PTT owner, TOT remaining), `meter` (5 Hz), `notice` |
| ← | binary: RX audio, same format |

## Layout

```
server/   main.py (web + WebSocket + safety watchdog), radio.py (GPIO), audio.py (sound card, resampling)
web/      index.html, app.js, worklet.js (AudioWorklet capture/playback), style.css
deploy/   install.sh, systemd unit
docs/     HARDWARE.md (wiring)
```

## Ideas for later

- Opus compression for low-bandwidth (4G) links
- Pluggable control backends (CI-V / CAT serial) for radios that can report channel and RSSI
- Per-user accounts and audit log of who transmitted when
- VOX-style recording of RX traffic
