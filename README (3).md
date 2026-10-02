# <PROJECT NAME> — Pocket Voice Agent on a Raspberry Pi Zero W

> **Hackathon Track 03 — Hardware schematic / advanced software stack**
> A self-contained, voice-driven agent that listens through a hand-built microphone amplifier, talks back through an I2S Class-D amplifier, shows live status on a small SPI LCD, takes manual input from a custom ESP32-S3 joystick-and-button controller, and polls a server for problems to act on.

<!-- Replace <PROJECT NAME>, the one-line pitch above, and every <TODO> below before submitting. -->

---

## Table of Contents

1. [What it does](#what-it-does)
2. [System architecture](#system-architecture)
3. [Hardware](#hardware)
4. [Wiring](#wiring)
5. [Custom microphone amplifier](#custom-microphone-amplifier)
6. [Software setup](#software-setup)
7. [Running it](#running-it)
8. [Real-world problems we solved](#real-world-problems-we-solved)
9. [Project structure](#project-structure)
10. [Extending the project](#extending-the-project)
11. [Troubleshooting](#troubleshooting)
12. [License](#license)

---

## What it does

End-to-end flow:

```
 Mic ──► Mic amplifier ──► ADC / sound input ──► Speech-to-Text ──┐
                                                                  ▼
 SERVER_URL ◄── polls for problems ──────────────────────►  voiceagent.py
                                                                  │
        ESP32-S3 controller ──────────────────────────────────────┤
        (USB HID: joystick + Enter/Esc/Delete/Space)                      │
                         ┌────────────────────────────────────────┤
                         ▼                                        ▼
                ST7735 LCD (status / mirror)        MAX98357A ──► Speaker
```

1. The microphone signal is boosted by a **custom-built amplifier circuit**.
2. `voiceagent.py` captures audio with PortAudio/ALSA and runs **speech-to-text**.
3. The agent **polls a server** (`SERVER_URL`) for problems/tasks and combines them with what the user says.
4. Responses are spoken through a **MAX98357A I2S Class-D amplifier** and a speaker.
5. A **128x160 ST7735 SPI LCD** mirrors the main display so the device is usable standalone.
6. A **custom ESP32-S3 controller** (joystick + 4 push buttons: Enter, Esc, Delete, Space) enumerates as a standard **USB keyboard + mouse**, so the Pi needs no drivers: navigate, confirm, correct mistakes, and quit without a physical keyboard.

<TODO: 2–3 sentences on the concrete use case, e.g. who it's for and what "problems" the server reports.>

---

## System architecture

| Layer | Component | Role |
|---|---|---|
| Input | Electret/analog mic + **DIY mic amp** | Conditions and amplifies the mic signal |
| Compute | Raspberry Pi Zero W | Runs the agent, drives display and audio |
| Software | `voiceagent.py` (Python 3.13) | STT, server polling, response logic, TTS/audio out |
| Audio out | **MAX98357A** I2S Class-D mono amp | Digital I2S in → speaker out, no DAC needed |
| Display | ST7735 128x160 SPI LCD | Status UI / mirrored display |
| Input | Custom controller on **ESP32-S3** | Reads joystick + 4 buttons, presents as USB HID keyboard + mouse |
| Backend | HTTP server at `SERVER_URL` | Source of problems/tasks for the agent |

---

## Hardware

### Bill of materials

| # | Part | Notes |
|---|---|---|
| 1 | Raspberry Pi Zero W | Main controller |
| 2 | MAX98357A I2S Class-D mono amplifier module | Speaker driver |
| 3 | Speaker (4–8 Ω, 3 W max) | <TODO: exact speaker> |
| 4 | ST7735 128x160 SPI LCD | Wired directly to GPIO (no HAT) |
| 5 | Microphone (<TODO: electret / analog MEMS>) | Feeds the DIY amplifier |
| 6 | Custom mic amplifier | <TODO: op-amp / transistor part numbers, see below> |
| 7 | Audio capture path | <TODO: USB sound card / ADC board that digitises the amplified mic signal> |
| 8 | ESP32-S3 dev board | Controller brain |
| 9 | Analog joystick module | 2-axis analog with push switch (VRX, VRY, SW) |
| 10 | 4 push buttons | Enter, Esc, Delete, Space |
| 11 | 5 V power supply, jumper wires, breadboard/perfboard | |

---

## Wiring

### ST7735 LCD → Raspberry Pi (SPI0)

| LCD pin | Pi GPIO | Function |
|---|---|---|
| LED | GPIO22 | Backlight |
| SCK | GPIO11 (SCLK) | SPI clock |
| SDA | GPIO10 (MOSI) | SPI data |
| A0 (DC) | GPIO24 | Data/command |
| RESET | GPIO25 | Reset |
| CS | GPIO8 (CE0) | Chip select |
| VCC / GND | 3.3 V / GND | Power |

### MAX98357A → Raspberry Pi (I2S)

Standard Pi I2S pins (verify against your wiring before publishing):

| MAX98357A pin | Pi pin | Function |
|---|---|---|
| VIN | 5 V | Power |
| GND | GND | Ground |
| BCLK | GPIO18 | I2S bit clock |
| LRC | GPIO19 | I2S word select |
| DIN | GPIO21 | I2S data |
| SD / GAIN | <TODO> | Shutdown / gain select (floating = 9 dB) |

The display (SPI0: GPIO 8/10/11) and I2S (GPIO 18/19/21) use different pins, so they run simultaneously without conflict.

### Joystick and buttons → ESP32-S3

| Control | ESP32-S3 pin | Notes |
|---|---|---|
| Joystick X (VRX) | GPIO1 | Analog read (12-bit, centre ≈ 2048) |
| Joystick Y (VRY) | GPIO2 | Analog read |
| Joystick press (SW) | GPIO3 | To GND, internal pull-up |
| Button 1: Enter | GPIO4 | To GND, internal pull-up |
| Button 2: Esc (exit) | GPIO5 | To GND, internal pull-up |
| Button 3: Delete | GPIO6 | To GND, internal pull-up |
| Button 4: Space | GPIO7 | To GND, internal pull-up |
| VCC / GND | 3.3 V / GND | Joystick powered at 3.3 V so the ADC is not over-driven |

> 📷 **Add here:** wiring photo + schematic image (`docs/schematic.png`). Track 03 judges look for this.

---

## Custom microphone amplifier

The Pi has no analog input, and a bare microphone signal is far too weak for reliable speech recognition. We designed and built our own amplifier stage.

- **Topology:** <TODO: e.g. single-supply op-amp non-inverting stage / transistor preamp>
- **Gain:** <TODO: e.g. ~40 dB>
- **Supply:** <TODO>
- **Bias:** <TODO: electret bias resistor, DC-blocking caps, mid-rail bias>
- **Output:** <TODO: how it connects to the capture device>

Schematic: `docs/mic-amp-schematic.png` <TODO: add file>

Design notes worth documenting: gain choice vs. noise/clipping, power-supply decoupling, and how the amp was tested (scope trace or recording samples).

---

## Custom input controller (ESP32-S3)

A handmade controller built from an analog joystick, four push buttons, and an ESP32-S3. It uses the ESP32-S3's native USB to enumerate as a **composite USB keyboard + mouse**. The Pi sees ordinary input devices, so no Pi-side driver or protocol is needed.

| Input | Output sent to the Pi |
|---|---|
| Joystick left / right | Left / Right arrow (held while deflected) |
| Joystick up / down | Up / Down arrow (held while deflected) |
| Joystick press | Right mouse click |
| Button 1 | **Enter** |
| Button 2 | **Esc** (exit) |
| Button 3 | **Delete** |
| Button 4 | **Space** |

- **Link to the Pi:** USB HID (plug the board's native USB port into the Pi).
- **Firmware:** `firmware/controller/controller.ino` (Arduino, ESP32 core, `USB.h`, `USBHIDKeyboard`, `USBHIDMouse`).
- **Joystick handling:** 12-bit ADC, centre 2048, dead zone of ±400 counts. Each axis maps to a key that is pressed and released as the stick crosses the threshold, so holding the stick gives normal key repeat.
- **Button handling:** `INPUT_PULLUP` (active low) with edge detection, so each press/release sends exactly one key-down and one key-up. The 10 ms loop delay gives basic debouncing.
- **Debug:** serial output at 115200 baud prints `USB Keyboard + Mouse ready.` on boot.

Flashing (Arduino IDE):

1. Install the **esp32** board package.
2. Select board **ESP32S3 Dev Module**.
3. Set **USB Mode: USB-OTG (TinyUSB)** so the HID classes are available.
4. Upload `controller.ino` through the board's UART/COM port, then connect the **native USB** port to the Pi.

Pin map (from the firmware):

```cpp
const int VRX_PIN = 1;
const int VRY_PIN = 2;
const int SW_PIN  = 3;
const int BTN_PINS[4] = {4, 5, 6, 7}; // Enter, Esc, Delete, Space
const int DEADZONE = 400;
```

---

## Software setup

### 1. System packages

```bash
sudo apt update
sudo apt install -y python3-pip python3-venv portaudio19-dev libasound2-dev \
                    alsa-utils python3-spidev python3-rpi.gpio
```

### 2. Enable interfaces

```bash
sudo raspi-config     # Interface Options → enable SPI
```

Enable I2S audio for the MAX98357A by adding to `/boot/firmware/config.txt` (or `/boot/config.txt` on older images):

```
dtparam=audio=off
dtoverlay=max98357a
```

Reboot, then confirm the card appears:

```bash
aplay -l
speaker-test -c1 -t wav
```

### 3. Python environment

```bash
git clone <REPO_URL>
cd <REPO_NAME>
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` should include at least: `pyaudio` (or `sounddevice`), `requests`, `st7735`, `pillow`, plus your STT/TTS libraries. <TODO: pin versions>

### 4. Display library patch

The panel we used is effectively **124x160**, not 128x160. With the stock Pimoroni `st7735` package the image is offset or clipped, so the library's resolution constants must be changed:

```python
# in the installed st7735 package (st7735/__init__.py)
ST7735_TFTWIDTH  = 124   # was 128
ST7735_TFTHEIGHT = 160
```

Verify with the Pimoroni example (`image.py`) before running the agent. <TODO: if you ship a patched copy/fork, point to it here.>

### 5. Configuration

```bash
export SERVER_URL="http://<your-server>:<port>"
```

<TODO: list any other env vars / config keys, e.g. API keys, language, wake word, input device index.>

---

## Running it

```bash
cd ~/Desktop/Agent      # or wherever the repo lives
source .venv/bin/activate
python voiceagent.py
```

Expected behaviour:

1. Display shows a boot/status screen.
2. Agent starts listening on the microphone.
3. Speak a command — transcript appears on the LCD.
4. Agent polls `SERVER_URL`, reasons about any problems, and replies through the speaker.

<TODO: add a short demo GIF/video link and a sample session transcript.>

---

## Real-world problems we solved

This is the part that goes beyond a "works on my desk" demo:

| Problem | Symptom | Fix |
|---|---|---|
| **PortAudio error -9997 "Invalid sample rate"** | STT input stream failed to open on ALSA | Query the device's supported rates (`python -m sounddevice` / `arecord --dump-hw-params`) and open the stream at a native rate (e.g. 44100/48000 Hz), then resample to the rate the STT engine needs. <TODO: confirm your final fix> |
| **LCD panel size mismatch** | Image shifted/clipped on the ST7735 | Patched library resolution constants to 124x160 |
| **No analog input on the Pi** | Mic signal unusable | Designed and built a custom mic amplifier |
| **No keyboard on a headless device** | Couldn't correct STT output or confirm actions | Built an ESP32-S3 joystick + Enter/Esc/Delete/Space controller that appears as a USB HID keyboard + mouse, so it works with zero Pi-side code |
| **No built-in amp on Pi Zero** | No loud, clean audio out | MAX98357A over I2S (digital path, no analog noise) |
| **Custom GPIO wiring instead of a HAT** | Pin conflicts, CS/DC/RESET assignment | Documented pin map above; SPI0 and I2S kept on separate pins |
| <TODO: network drops / server down> | | <TODO: retry/backoff behaviour> |

---

## Project structure

```
.
├── voiceagent.py        # Main agent: STT, server polling, audio out, display updates
├── requirements.txt
├── firmware/
│   └── controller/      # ESP32-S3 joystick + button firmware
├── docs/
│   ├── schematic.png          # Full wiring diagram
│   ├── mic-amp-schematic.png  # Custom mic amplifier
│   └── demo.gif
└── README.md
```

<TODO: adjust to match the real repo.>

---

## Extending the project

- Swap the STT/TTS engine by editing the corresponding section of `voiceagent.py`.
- Change what the agent does with server data by modifying the polling handler.
- Add display pages (battery, Wi-Fi, transcript) using Pillow and the `st7735` driver.
- Remap the controller's buttons or add more by editing `BTN_PINS` and the matching `Keyboard.press(...)` calls in the firmware. Because it is plain USB HID, no Pi-side change is needed.
- Replace the mic amp with an I2S MEMS mic (e.g. INMP441) for a fully digital input path.

---

## Troubleshooting

| Issue | Check |
|---|---|
| No sound from speaker | `aplay -l` shows the MAX98357A card; `dtoverlay=max98357a` set; SD pin not tied low |
| Mic input silent or clipping | Amp supply voltage, gain, bias; `arecord -l` and `alsamixer` capture level |
| `Invalid sample rate` | Use a rate the device supports; see table above |
| Controller does nothing | Native USB port (not the UART port) is plugged into the Pi; USB Mode set to USB-OTG (TinyUSB); `lsusb` shows the device; buttons wired to GND |
| Buttons fire twice | Add a longer debounce (increase `delay(10)` or add per-button timers) |
| Joystick drifts or keys stick | Increase `DEADZONE` or measure the real centre instead of assuming 2048 |
| Blank/white LCD | SPI enabled, CS on GPIO8, backlight on GPIO22, DC/RESET pins correct |
| Garbled or shifted LCD image | Resolution constants patched to 124x160 |

---

## License

<TODO: MIT / Apache-2.0 / etc.>

## Team

<TODO: names and roles>
