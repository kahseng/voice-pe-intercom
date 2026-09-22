# Voice PE Volume Triggered Intercom

Turn two or more [Home Assistant Voice Preview Edition](https://www.home-assistant.io/voice-pe/)
devices into a household intercom. Three ways to talk, then every other device
repeats your words in its own voice:

1. **Just shout** (hands-free and wake-word-free): a loudness threshold on the
   device starts listening the instant your voice crosses it, no chime, no
   prompt.
2. Say the wake word, then speak.
3. Press the centre button once, then speak.

```
device A                             Home Assistant                       device B
shout / wake word / button   -->     Assist pipeline "Intercom"           assist_satellite.announce
mic audio streamed to HA             speech-to-text (Parakeet)            text-to-speech (Piper)
                                     catch-all sentence trigger "{message}"
                                     script.intercom_broadcast
                                     (every satellite except the sender)
```

- Nothing leaves your network: wake word and shout detection run on the
  device, transcription and speech synthesis run on the Home Assistant machine.
- The relay is text, not audio: the receiver hears Home Assistant's voice
  reading your words, two to three seconds after you stop talking.
- The wake word becomes intercom-only on these devices. The Voice PE firmware
  allows a single active wake word per device, so it cannot serve both the
  intercom and the ordinary assistant. Switching a device's **Assistant**
  selector back to a normal pipeline restores the assistant and stops the
  intercom on that device.

## Repository layout

| Path | What it is |
|---|---|
| `homeassistant/packages/voice_intercom.yaml` | The broadcast script, the catch-all automation and the helper entities. Pushed with `tools/push_config.py`; can also be installed as a package. |
| `homeassistant/pipelines.yaml` | The Assist pipelines (one per voice), as reference. |
| `homeassistant/addons/whisper.yaml` | Whisper add-on options that select the Parakeet engine. |
| `esphome/voice-pe-intercom-shout.yaml` | Firmware add-on: shout-to-talk (sound level meter, threshold slider, switch). |
| `esphome/devices/home-assistant-voice-example.yaml` | Template device config: official firmware + "Alexa" wake word + the add-on. |
| `esphome/build.sh` | Compile and flash a device config. |
| `devices.example.yaml` | Describes your devices for the dashboard renderer. Copy to `devices.yaml`. |
| `tools/` | Python helpers: push the config and dashboard, watch the intercom live, benchmark speech-to-text. |

Everything specific to one installation (addresses, device names, per-device
firmware files, `devices.yaml`) is meant to live outside this repo, for
example in a private sibling repo. `devices.yaml` and `env.sh` are git-ignored
here for that reason.

## Setup

### 1. Home Assistant side

Requirements: Home Assistant 2025.7 or newer (for the `assist_satellite`
actions), the devices already set up, and a long-lived access token (profile >
Security). The tools read the token from `HA_TOKEN` or, on a Mac, from a
Keychain item named `ha-token`:

```sh
security add-generic-password -a homeassistant -s ha-token -w "$(pbpaste)"   # token in clipboard
export HA_URL=http://homeassistant.local:8123                                # your server
pip install -r tools/requirements.txt
cp devices.example.yaml devices.yaml   # then edit
python tools/push_config.py --dashboard --devices devices.yaml
```

That creates the helpers, the script "Intercom: broadcast", the automation
"Intercom: broadcast by voice" and an "Intercom" dashboard with each device's
controls, a sound-level graph, the transcript log and tuning notes. All are
editable in the UI afterwards. (Alternative without the API: copy the package
file to `/config/packages/` and enable packages in `configuration.yaml`; then
do not also push it, or you get duplicates.)

### 2. Speech-to-text that understands free text

Speech-to-Phrase, the default local engine, only recognises a fixed list of
phrases and returns nothing for a free-form sentence. Install the **Whisper**
add-on and set its options as in `homeassistant/addons/whisper.yaml`:
`model: auto` with `stt_library: sherpa` selects NVIDIA Parakeet for English.
Measured on an ARM virtual machine with a short phrase:

| Engine | Time | Notes |
|---|---|---|
| Parakeet (sherpa) | 0.3 s | correct on every test phrase |
| Whisper `base.en` | 1.1 s | misheard "dinner is ready" 4 times out of 6 |
| Whisper `small.en` | ~3 s | accurate |

Confirm the Wyoming discovery in Settings > Devices & services; the entity
is `stt.faster_whisper` whichever engine the add-on runs.

### 3. Pipelines and per-device voice

Create a pipeline per voice as in `homeassistant/pipelines.yaml`: the add-on's
speech-to-text entity, the Home Assistant conversation agent, Piper for
text-to-speech. A device speaks announcements with **its own** pipeline's
voice, so assign pipelines per device with the **Assistant** selector on each
device page. Also on each device page: pick the wake word, and turn the
**Wake sound** off if you want no chime at all.

### 4. Firmware (optional): "Alexa" wake word and shout-to-talk

Copy `esphome/devices/home-assistant-voice-example.yaml` once per device and
replace `xxxxxx` with the last six hex digits of the device's MAC address
(the suffix of its entity ids). The file uses the official factory firmware
release as a package, plus the "Alexa" wake-word model, plus the shout add-on.
There is no Wi-Fi block and no API key on purpose: the device keeps both in
flash, so it comes back with the same Wi-Fi and the same Home Assistant
pairing after the update. Build on any computer with ESPHome (the version
must satisfy the release's `min_version`):

```sh
uv venv ~/esphome-venv && uv pip install -p ~/esphome-venv/bin/python esphome
PATH=~/esphome-venv/bin:$PATH esphome/build.sh path/to/home-assistant-voice-xxxxxx.yaml <device-ip>
```

After flashing, pick "Alexa" in the device's wake word selector. The three
new entities (switch, threshold slider, sound level) are named after the
device's Home Assistant name; put that prefix in `devices.yaml` and re-run the
dashboard push.

Once a device runs this firmware, do **not** install the stock release that
Home Assistant's `update.*` entity offers: it removes the customisations.
Update by bumping the release tag in the device file and rebuilding. The
factory firmware can always be restored over USB from the Voice PE web
installer.

## Shout-to-talk

Each device gains three entities: **Shout to talk** (switch), **Intercom shout
threshold** (dB slider; 0 is the loudest the microphone can measure, so values
are negative) and **Intercom sound level** (loudest peak of the last second).
A peak above the threshold while the device is idle (not muted, listening,
announcing or playing, no timer ringing) starts listening at once, with no
chime. The shout itself is usually caught as the first word.

Tune it on the dashboard: talk at normal volume where you use the device,
shout "hey" a few times, and set the slider about 6 dB above the talking
peaks. Typical values: silence -84, talking peaks -52, shouts -45 to -32.
Closer to 0 is harder to trigger.

### Adaptive threshold

The slider is a floor. The device also tracks an **ambient speech level**: the
second-loudest second of the last ten, so a single shout does not count but a
conversation or a conference call nearby does. Seconds while the device is
listening or replying are excluded, so the intercom never raises its own bar.
The effective threshold is the higher of the slider and the ambient level plus
the **adaptive margin** (default 4 dB), and the ambient level decays 3 dB per
minute once the room is quiet. During a call the bar rises to a few dB above
the talking, a real shout still gets through, and a few minutes after the
call ends everything is back to the slider. Both the ambient level and the
effective threshold are sensors, shown on the dashboard.

## Echo guard

When two devices hear the same shout, or one device's playback is loud enough
to trigger the other, both would relay the same sentence. The automation
drops a message that equals the previous one, within 20 s, from a different
device. Dropped ones still appear in the transcript log, marked as echoes.

## Privacy notes

The microphone is always on for wake-word detection, on the device, as with
the stock firmware. The shout meter reports one loudness number per second to
Home Assistant, never audio. What changes is consent: with shout-to-talk any
loud sound opens a listening window of up to six seconds, and speech in it is
transcribed and relayed. Transcripts are kept in the logbook (default 10 days)
and the last one in a helper entity. Controls: the per-device Shout to talk
switch, the hardware mute, a higher threshold, or an automation that turns
shout-to-talk off on a schedule.

## Tools

- `tools/push_config.py [--dashboard --devices devices.yaml]` pushes the
  package, and renders and saves the dashboard.
- `tools/monitor.py [--log FILE]` prints every pipeline run (device, text
  heard, errors), announcement and device drop-out as it happens.
- `tools/stt_benchmark.py clip.wav` times the speech-to-text stage with a
  16 kHz mono WAV; `--full` also runs the intent stage, which makes the
  devices speak.

## Debugging

- Settings > Voice assistants > pipeline > three-dot menu > Debug lists recent
  runs with what was heard and which automation answered.
- The automation's traces show the targets chosen and any announce errors.
- When two devices hear the wake word within 2 s, Home Assistant keeps the
  first to report and rejects the other as a duplicate; there is no loudness
  comparison. In testing the closer device won every time.

## License

MIT, see `LICENSE`. This repo contains only configuration and small tools of
its own; it references the official Voice PE firmware and ESPHome (ESPHome
License: MIT for YAML and Python, GPLv3 for the C++ runtime) as packages at
build time and talks to Home Assistant (Apache 2.0) over its API. None of
their code is redistributed here.
