# NanoPID Controller — Home Assistant Integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![HA Version](https://img.shields.io/badge/HA-2023.6%2B-blue.svg)](https://www.home-assistant.io)

Custom integration for the **SmartPID Nano** thermal process controller by [Arzaman SRL](https://arzaman.com).

---

## Features

- Full MQTT local-push integration — no cloud dependency
- 16 entities per device: sensors, selects, numbers, buttons
- Multi-device support (one config entry per MAC)
- Config flow with MAC address validation
- Bundled-Start script: sends all current HA entity values atomically before starting the process
- Lovelace dashboard included (requires layout-card, apexcharts-card, lovelace-mushroom, stack-in-card)

---

## Prerequisites

- Home Assistant 2023.6 or later
- MQTT integration configured and connected to the same broker as the NanoPID
- HACS installed

---

## Installation via HACS

1. In HACS → Integrations → ⋮ → Custom repositories
2. Add URL `https://github.com/arzaman/nanopid-ha-integration` — category **Integration**
3. Find **NanoPID Controller** and click Install
4. Restart Home Assistant

---

## Configuration

Go to **Settings → Devices & Services → Add Integration → NanoPID Controller**.

| Field | Required | Description |
|-------|----------|-------------|
| Device MAC Address | Yes | 12 hex characters from the device label or boot log (e.g. `24587c5cd104`) |
| Device Name | No | Friendly name (default: `NanoPID`). Used as prefix for all entity IDs. |

After setup, all entities are available immediately. No YAML configuration required.

---

## Entity IDs (default device name "NanoPID")

| Entity | ID |
|--------|----|
| Temperature | `sensor.nanopid_temperature` |
| Power Output | `sensor.nanopid_power_output` |
| Process Status | `sensor.nanopid_process_status` |
| Alarm Status | `sensor.nanopid_alarm_status` |
| Free Heap | `sensor.nanopid_free_heap` |
| AC Detected | `binary_sensor.nanopid_ac_detected` |
| Target Mode | `select.nanopid_target_mode` |
| Control Mode | `select.nanopid_control_mode` |
| Direction | `select.nanopid_direction` |
| Start Behaviour | `select.nanopid_start_behaviour` |
| Profile Type | `select.nanopid_profile_type` |
| Main Setpoint | `number.nanopid_main_setpoint` |
| Alarm TH Low | `number.nanopid_alarm_th_low` |
| Alarm TH High | `number.nanopid_alarm_th_high` |
| Start | `button.nanopid_start` |
| Stop | `button.nanopid_stop` |
| Pause | `button.nanopid_pause` |
| Resume | `button.nanopid_resume` |

> If you set a custom device name (e.g. "Forno"), entity IDs will be prefixed accordingly (`sensor.forno_temperature`, etc.).

---

## Dashboard

Copy `lovelace/nanopid_dashboard.yaml` content into a new Lovelace raw YAML view.

Required HACS frontend cards:
- [layout-card](https://github.com/thomasloven/lovelace-layout-card)
- [apexcharts-card](https://github.com/RomRider/apexcharts-card)
- [lovelace-mushroom](https://github.com/piitaya/lovelace-mushroom)
- [stack-in-card](https://github.com/custom-cards/stack-in-card)

---

## Bundled Start Script

The `packages/nanopid_bundle_start.yaml` file provides an HA script that reads the current values of all select/number entities and sends them atomically with the start command, ensuring the device starts with exactly the configuration shown on the dashboard.

**Setup:**
1. Copy the file to `config/packages/nanopid_bundle_start.yaml`
2. Ensure your `configuration.yaml` includes:
   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```
3. Replace `YOUR_DEVICE_MAC` in the file with your device MAC (e.g. `24587c5cd104`)
4. Restart HA

The dashboard Start button calls `script.nanopid_bundled_start` automatically.

---

## MQTT Topics Reference

| Direction | Topic | Description |
|-----------|-------|-------------|
| Device → HA | `nanopid/<mac>/status` | JSON state, every 5 s |
| HA → Device | `nanopid/<mac>/command` | `start`/`stop`/`pause`/`resume` or bundled JSON |
| HA → Device | `nanopid/<mac>/setpoint` | Float setpoint value |
| HA → Device | `nanopid/<mac>/config` | JSON config update |
| HA → Device | `nanopid/<mac>/config/target_mode` | Plain string mode selection |

See `HA/MQTT_REFERENCE.md` in the NanoPID firmware repository for full payload documentation.

---

## Device Information

| | |
|-|---|
| Manufacturer | Arzaman SRL |
| Model | NanoPID v2.0 |
| MCU | ESP32-S3 |
| IoT Class | local_push |

---

## License

MIT — © Arzaman SRL
