# TR198A Ceiling-Fan Home Assistant Integration

A custom Home Assistant integration and Python toolkit for controlling TR198A ceiling fans via Broadlink RM-series RF remotes. This project provides both a Home Assistant component and a standalone Python CLI/library for advanced automation and reverse-engineered RF packet control.

---

## Features

- **Home Assistant Integration**: Adds a `fan` entity for TR198A ceiling fans, with support for speed, direction, breeze modes, light toggle, and pairing.
- **Button Entities**: Exposes pairing, light toggle, and dimming as Home Assistant button entities and services.
- **Config Flow**: Simple UI-based setup for selecting your Broadlink remote and (optionally) a smart power switch.
- **Pure Python RF Codec**: All RF packet generation is implemented in Python—no external dependencies for packet building.
- **Command-Line Tool**: `fancli.py` lets you generate handset IDs, pair fans, and send commands directly from the terminal or scripts.

---

## Installation

1. **Copy the Integration**
   - Place the `custom_components/tr198a_fan/` folder into your Home Assistant `custom_components/` directory.

2. **Restart Home Assistant**

3. **Add Integration**
   - Go to *Settings → Devices & Services → Add Integration* and search for "TR198A Ceiling-Fan".

4. **Follow the Config Flow**
   - Select your Broadlink remote (e.g., RM4 Pro).
   - Optionally select a smart power switch if your fan is powered through one.
   - Assign a friendly name.

---

## HACS Installation (Home Assistant Community Store)

You can install this integration via HACS as a custom repository:

1. In Home Assistant, go to **HACS → Integrations**.
2. Click the three dots (⋮) in the top right and select **Custom repositories**.
3. Enter the repository URL:

   ```
   https://github.com/janabimustafa/ha-tr198a-fan
   ```

   and select **Integration** as the category.
4. Click **Add**.
5. Search for "TR198A Ceiling-Fan" in HACS and install it.
6. Restart Home Assistant.
7. Add the integration via *Settings → Devices & Services → Add Integration*.

For updates, simply update via HACS when a new version is released.

---

## Usage

### Home Assistant

- **Fan Entity**: Control speed (0–9), direction, and breeze presets.
- **Light**: A light entity turns the fan's light on and off.
- **Pairing**: Use the "Pair Remote" button or service to pair a new handset ID with your fan.
- **Dimming**: Use "Dim Up" and "Dim Down" buttons/services for light dimming steps.

#### Using a smart power switch

The light can only be *toggled* over RF, so Home Assistant has to keep track of it. If the fan is powered through a smart switch, the integration follows that switch too:

- While the switch is off, the fan and light show as off. The receiver remembers what it was doing and picks it up again when power returns, and Home Assistant shows the same.
- Turning the fan or the light **on** while the switch is off turns the switch on, waits for the receiver to start up, and then switches on only what you asked for. (If the receiver restores the other one on power-up, you may see it come on for a few seconds before it is switched back off.)
- Turning the fan or the light **off** while the switch is off does nothing – it will not power the fan back up.
- **Power-on delay** (in the integration's options, default 2 s) is how long to wait after the switch turns on before sending a command. Increase it if commands sent right after power-up are missed.
- To stop using a power switch, clear the field in the integration's options.

If the light ever gets out of step anyway (for example after using the original remote), press **Sync Light** to flip the lamp without changing what Home Assistant shows.

#### Services

These do the same as the buttons and take the fan entity as their target:

- `tr198a_fan.pair`: Pair the remote to the fan.
- `tr198a_fan.dim_up` / `tr198a_fan.dim_down`: Adjust light brightness.
- `tr198a_fan.sync_light`: Flip the lamp without changing Home Assistant's state.

```yaml
action: tr198a_fan.sync_light
target:
  entity_id: fan.living_room_fan
```

### Python CLI/Library

The `fancli.py` script provides a command-line and importable interface:

```sh
# Generate a new handset ID
python fancli.py gen-id

# Build and print the pairing packet
python fancli.py pair 0x15a9

# Send a speed-5, forward-rotation command (requires --host)
python fancli.py cmd 0x15a9 --speed 5 --direction forward --host 192.168.1.42
```

Or use as a library:

```python
from fancli import build_payload, build_rf_packet, send_packet
packet = build_rf_packet(build_payload(0x15A9, speed=3, direction="forward"))
send_packet(packet, host="192.168.1.42")
```

---

## Requirements

- Home Assistant (latest recommended)
- Broadlink RM4 Pro (or compatible RF remote)
- Python 3.9+
- See `requirements.txt` for development dependencies (not all are required for integration use)

---

## Development & Reverse Engineering

- All RF protocol logic is implemented in `codec.py` and `fancli.py`.
- No proprietary libraries required for packet building.
- See `exp/` notebooks for protocol analysis and timing experiments.

---

## Compatibility with Other Remotes

This integration is designed and tested for use with the Broadlink RM4 Pro RF remote, which emulates the TR198A remote protocol. However, it may also work with other RF remotes or ceiling fan receivers that use the same or similar protocol, such as:

- Broadlink RM3 Pro (untested)
- Other Broadlink RM-series devices with 433 MHz RF (untested)
- **UC7225T** ceiling fan receivers/remotes (untested, but believed to use a compatible protocol)
- Any Home Assistant-compatible remote that can transmit raw/base64 RF codes using the TR198A or UC7225T protocol (untested)

**Note:** Functionality and reliability with remotes or receivers other than the RM4 Pro and TR198A are not guaranteed. If you successfully use this integration with another remote or receiver (such as UC7225T), please consider opening an issue or pull request to share your experience!

---

## File Overview

- `custom_components/tr198a_fan/` – Home Assistant integration
  - `fan.py` – Main fan entity logic
  - `button.py` – Button entities and service registration
  - `codec.py` – RF packet builder (pure Python)
  - `config_flow.py` – UI setup flow
  - `const.py` – Constants and service names
  - `services.yaml` – Service descriptions
  - `translations/` – UI translations
- `fancli.py` – Standalone CLI and Python library for RF control
- `exp/` – Jupyter notebooks for protocol analysis

---

## Credits

- Reverse engineering and integration by [@janabimustafa](https://github.com/janabimustafa)
- Not affiliated with the TR198A manufacturer or Broadlink.
