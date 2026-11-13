# An Australian voice for the AI agent

Piper's ready-made English voices are American or British. This page makes your own: record
someone Australian reading prompts, fine-tune an existing Piper voice on the recordings, and
put the result on the Pi. The voice runs offline, faster than real time on a Pi 5.

Until it's ready, use a cloud voice with an Australian accent (`bot.tts` in `config.yaml`:
Azure `en-AU-WilliamNeural` or `en-AU-NatashaNeural`, Google `en-AU-Neural2-B`, or an
ElevenLabs voice).

**Whose voice:** yours, or someone who has agreed to it. A good clone of a person who hasn't
could be mistaken for them on air.

## 1. Record (an evening or two)

You need a quiet room (soft furnishings, no fridge hum), a decent USB microphone about 20 cm
away, and a reader with a natural, steady delivery. Aim for **1 to 2 hours** of speech (about
800–1500 sentences). 30 minutes works but sounds rougher.

[Piper Recording Studio](https://github.com/rhasspy/piper-recording-studio) shows one prompt at
a time in the browser and records it:

```bash
git clone https://github.com/rhasspy/piper-recording-studio.git
cd piper-recording-studio
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# Add the Australian prompts beside the standard English set:
cp /path/to/radpi/docs/voice/prompts-en-AU.txt "prompts/English (United States)_en-US/"
python3 -m piper_recording_studio
```

Open http://localhost:8000. Read the standard English prompts (they cover the language's
sounds evenly) and `prompts-en-AU.txt` (local words, place names, numbers, radio phrases and the
rhythm of how people actually talk on a channel). Read in the voice you want the agent to have:
the model copies tone and energy as well as accent.

Then export a training set (the studio's README has the details):

```bash
sudo apt-get install ffmpeg
pip install -r requirements_export.txt
python3 -m export_dataset "output/English (United States)_en-US/" ~/aussie-dataset
```

## 2. Fine-tune (a few hours on an NVIDIA GPU)

Use a PC with an NVIDIA graphics card, or rent a cloud GPU for a few dollars. Training
starts from an existing medium-quality Piper checkpoint, which is far quicker than from
scratch. Following [Piper's training guide](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/TRAINING.md):

```bash
git clone https://github.com/OHF-voice/piper1-gpl.git && cd piper1-gpl
python3 -m venv .venv && source .venv/bin/activate
python3 -m pip install -e '.[train]'
./build_monotonic_align.sh
```

Download a **medium** checkpoint to start from, from
[rhasspy/piper-checkpoints](https://huggingface.co/datasets/rhasspy/piper-checkpoints). A British
one (for example `en_GB` alan or northern_english_male) is closer to Australian than an
American one. Then:

```bash
python3 -m piper.train fit \
  --data.voice_name "aussie" \
  --data.csv_path ~/aussie-dataset/metadata.csv \
  --data.audio_dir ~/aussie-dataset/wav/ \
  --model.sample_rate 22050 \
  --data.espeak_voice "en-gb" \
  --data.cache_dir ~/aussie-cache/ \
  --data.config_path ~/aussie-cache/config.json \
  --data.batch_size 32 \
  --ckpt_path /path/to/the-medium-checkpoint.ckpt
```

- `espeak_voice` only sets how text is turned into sounds. `en-gb` suits Australian English
  (non-rhotic, similar vowels); the accent itself is learned from the recordings.
- Piper's guide suggests about 2,000 epochs in total for a fine-tune on under 5 hours of audio,
  counting the checkpoint's own epochs (`--trainer.max_epochs`). Listen to samples as it goes:
  stop when it sounds right, not at a number.
- Check `metadata.csv` and the audio folder path against what the export actually wrote.

Export the voice:

```bash
python3 -m piper.train.export_onnx --checkpoint lightning_logs/version_0/checkpoints/<last>.ckpt \
  --output-file en_AU-aussie-medium.onnx
cp ~/aussie-cache/config.json en_AU-aussie-medium.onnx.json
```

## 3. Use it

Copy both files to the Pi, for example into `/opt/radpi/voices/`, then in `config.yaml`:

```yaml
bot:
  tts:
    backend: piper
    voice: voices/en_AU-aussie-medium.onnx   # relative to config.yaml
```

Restart (`sudo systemctl restart radpi`) and try it offline first:

```bash
cd /opt/radpi && sudo -u radio .venv/bin/python -m server.bot -c config.yaml replay some-over.wav
```

If it mispronounces a word, add it to `bot.pronounce` spelled the way it should sound:
`{"Wagga": "Wogga", "RadPi": "Rad Pie"}`.

Over a 12.5 kHz FM channel the audio is band-limited (about 300–3000 Hz), which hides most
synthesis artefacts. A voice that's only okay on headphones can sound convincing on air, so judge
it over the radio.
