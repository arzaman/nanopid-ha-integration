# MQTT Reference — NanoPID Controller (v2.0)

## 1. Overview

The NanoPID communicates with Home Assistant and any standard MQTT broker over TCP port 1883 (no TLS/SSL).

- **Broker:** Configurable via WiFi Provisioning (stored in NVS). No hardcoded credentials.
- **Authentication:** Optional username/password, also provisioned via NVS.
- **Device ID:** Derived from the WiFi MAC address: `nanopid_<12-char-hex>` (e.g. `nanopid_a1b2c3d4e5f6`).
- **HA Auto-Discovery prefix:** `homeassistant/` (standard HA MQTT discovery).

---

## 2. Topic Map

All operational topics follow the pattern `nanopid/<mac>/<function>`.
Discovery topics follow the HA standard pattern `homeassistant/<component>/nanopid_<mac>/<object>/config`.

### 2.1 Published by the device (Output)

| Topic | QoS | Retain | Rate | Description |
|-------|-----|--------|------|-------------|
| `nanopid/<mac>/status` | 0 | No | 5 s | Full device state — JSON (see §3.1) |
| `homeassistant/<component>/nanopid_<mac>/<obj>/config` | 1 | Yes | Once at boot | HA auto-discovery config for each entity (see §5) |

> **Note:** There is no LWT / availability topic implemented in the current firmware. The broker considers the device offline after the MQTT keep-alive expires.

### 2.2 Subscribed by the device (Input / Control)

| Topic | QoS | Description | Handler |
|-------|-----|-------------|---------|
| `nanopid/<mac>/command` | 1 | Process commands: start, stop, pause, resume — plain string or bundled JSON (see §3.2) | `handle_mqtt_command` |
| `nanopid/<mac>/setpoint` | 1 | Update active setpoint or dimmer power (see §3.3) | `handle_mqtt_setpoint` |
| `nanopid/<mac>/config` | 1 | Update process configuration: output mode, direction, behaviour, profile type, alarm thresholds (see §3.4) | `handle_mqtt_config` |
| `nanopid/<mac>/config/target_mode` | 1 | Select the control algorithm for the next start (see §3.5) | `handle_mqtt_target_mode` |

---

## 3. Payload Reference

### 3.1 Status payload — `nanopid/<mac>/status`

Published every 5 seconds. All values are read atomically under `fsm_mutex`.

```json
{
  "temp": 25.40,
  "sp":   65.00,
  "pwr":  78.50,
  "fsm":  5,
  "heap": 24512,
  "zc":   1,
  "th_l": 10.0,
  "th_h": 90.0,
  "alarm": "OFF",
  "ctrl": "zc",
  "dir":  "heat",
  "beh":  "single",
  "prof": "dynamic",
  "tgt":  "PID Controller"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `temp` | float | Current process temperature (°C) |
| `sp` | float | Active setpoint: temperature °C for PID/Hysteresis, power % (0-100) for Dimmer |
| `pwr` | float | Output power (0-100 %). PID: computed output. Hysteresis: 0 or 100. Dimmer: set power. |
| `fsm` | int | FSM state code (see §4) |
| `heap` | int | Free heap memory in bytes (diagnostic) |
| `zc` | int | AC Zero-Crossing signal active: `1` = detected, `0` = absent |
| `th_l` | float | Alarm low-threshold (°C) |
| `th_h` | float | Alarm high-threshold (°C) |
| `alarm` | string | Current alarm: `"OFF"` \| `"Over Temp"` \| `"Under Temp"` \| `"Start Event"` \| `"Stop Event"` |
| `ctrl` | string | Active output control mode: `"pwm"` \| `"zc"` \| `"phase"` |
| `dir` | string | Control direction: `"heat"` \| `"cool"` |
| `beh` | string | Run behaviour: `"single"` (fixed setpoint) \| `"profile"` (ramp/soak profile) |
| `prof` | string | Profile advance mode: `"dynamic"` (temp-triggered) \| `"static"` (time-triggered) |
| `tgt` | string | Selected target mode for next start: `"PID Controller"` \| `"Hysteresis Control"` \| `"Manual Dimmer"` |

---

### 3.2 Command — `nanopid/<mac>/command`

Accepts two formats on the same topic.

#### Format A — Plain string (existing, unchanged)

| Payload | FSM precondition | Action |
|---------|-----------------|--------|
| `start` | `IDLE_MODE` | Start in the mode previously set via target_mode topic |
| `stop` | Any | Stop process, return to IDLE |
| `pause` | `*_RUN_MODE` | Pause the running process |
| `resume` | `*_PAUSE_MODE` | Resume the paused process |

#### Format B — Bundled START (JSON)

Applies configuration and starts the process atomically in a single message.
All fields except `cmd` are **optional** — omitted fields leave the current controller value unchanged.

```json
{
  "cmd":  "start",
  "mode": "PID Controller",
  "sp":   65.0,
  "ctrl": "zc",
  "dir":  "heat",
  "beh":  "single",
  "prof": "dynamic"
}
```

| Field | Values | Description |
|-------|--------|-------------|
| `cmd` | `"start"` | **Required.** Identifies this as a bundled start. |
| `mode` | `"PID Controller"` \| `"Hysteresis Control"` \| `"Manual Dimmer"` | Target control algorithm |
| `sp` | float | Setpoint (°C for PID/Hys, 0-100 % for Dimmer) |
| `ctrl` | `"pwm"` \| `"zc"` \| `"phase"` | Output control mode |
| `dir` | `"heat"` \| `"cool"` | Control direction |
| `beh` | `"single"` \| `"profile"` | Run behaviour |
| `prof` | `"dynamic"` \| `"static"` | Profile advance mode |

**Behaviour:**
- All provided fields are applied atomically under `fsm_mutex` before the FSM transition.
- The FSM transitions to RUN only if the controller is currently in `IDLE_MODE`.
- Sending `{"cmd":"start"}` with no other fields is equivalent to the plain `"start"` string.

---

### 3.3 Setpoint — `nanopid/<mac>/setpoint`

Updates the active setpoint while running or from idle. Accepts plain float or JSON.

```
65.5
```
or
```json
{"val": 65.5}
```

**Mode-dependent clamping:**

| Mode | Valid range | Variable updated |
|------|-------------|-----------------|
| PID Controller | -55.0 … 125.0 °C | `PIDController.setPoint` + `on_off_temp_cntrl.set_point` |
| Hysteresis Control | -55.0 … 125.0 °C | `on_off_temp_cntrl.set_point` + `PIDController.setPoint` |
| Manual Dimmer | 0 … 100 % | `dimmer_power_out` |

The mode is determined by the current FSM state. If the FSM is `IDLE_MODE`, the selected `target_mode` is used to infer the correct variable.

---

### 3.4 Configuration — `nanopid/<mac>/config`

Updates process configuration parameters. All fields are optional; only present fields are applied.

```json
{
  "mode":    "zc",
  "dir":     "heat",
  "start":   "single",
  "prof":    "dynamic",
  "th_low":  10.0,
  "th_high": 90.0
}
```

| Field | Values | Description |
|-------|--------|-------------|
| `mode` | `"pwm"` \| `"zc"` \| `"phase"` | Output control mode (PWM, Zero-Crossing, Phase Angle) |
| `dir` | `"heat"` \| `"cool"` | Control direction for PID and Hysteresis |
| `start` | `"single"` \| `"profile"` | Run behaviour: fixed setpoint or ramp/soak profile |
| `prof` | `"dynamic"` \| `"static"` | Profile advance: temperature-triggered or time-triggered |
| `th_low` | float (°C) | Low alarm threshold |
| `th_high` | float (°C) | High alarm threshold |

Changes take effect immediately. These are **not** saved to NVS by this command alone — they must be saved via the display/encoder Save menu or will be lost at next power cycle.

---

### 3.5 Target Mode — `nanopid/<mac>/config/target_mode`

Selects the control algorithm that will be used on the next `start` command.
Has no effect if the controller is already running.

| Payload | Mode selected |
|---------|--------------|
| `PID Controller` | PID closed-loop temperature control |
| `Hysteresis Control` | On/off hysteresis temperature control |
| `Manual Dimmer` | Open-loop manual power output (0-100 %) |

---

## 4. FSM State Codes

| Code | Name | Description |
|------|------|-------------|
| 0 | `IDLE_MODE` | Stopped, output off |
| 1 | `ON_OFF_RUN_MODE` | Hysteresis control running |
| 2 | `ON_OFF_PAUSE_MODE` | Hysteresis control paused, output off |
| 3 | `LOOP_RUN_MODE` | Time-loop control running |
| 4 | `LOOP_PAUSE_MODE` | Time-loop control paused |
| 5 | `PID_RUN_MODE` | PID control running |
| 6 | `PID_PAUSE_MODE` | PID control paused, output off |
| 7 | `AUTOTUNER_MODE_INIT` | PID auto-tuner initialising |
| 8 | `AUTOTUNER_MODE_RUN` | PID auto-tuner running |
| 9 | `SSR_MANUAL_MODE` | Manual SSR direct control |
| 10 | `DIMMER_RUN_MODE` | Manual dimmer running |
| 11 | `DIMMER_PAUSE_MODE` | Manual dimmer paused, output off |

---

## 5. HA Auto-Discovery Entities

Sent once at boot (QoS 1, retained). Device info embedded in each discovery message:

```json
{
  "identifiers": ["nanopid_<mac>"],
  "name": "NanoPID Controller",
  "model": "ESP32-S3",
  "manufacturer": "SmartPID"
}
```

### Sensors (read-only)

| HA Entity | Discovery topic | `val_tpl` field | Unit |
|-----------|----------------|-----------------|------|
| Temperature | `homeassistant/sensor/nanopid_<mac>/temp/config` | `value_json.temp` | °C |
| Power Output | `homeassistant/sensor/nanopid_<mac>/power/config` | `value_json.pwr` | % |
| Process Status | `homeassistant/sensor/nanopid_<mac>/status_text/config` | FSM → human string | — |
| Free Heap | `homeassistant/sensor/nanopid_<mac>/heap/config` | `value_json.heap` | B |
| Alarm Status | `homeassistant/sensor/nanopid_<mac>/alarm/config` | `value_json.alarm` | — |
| AC Detected (ZC) | `homeassistant/binary_sensor/nanopid_<mac>/ac_detect/config` | `value_json.zc` → ON/OFF | — |

Process Status text mapping (via Jinja2 in discovery `val_tpl`):

| `fsm` value | Displayed text |
|-------------|---------------|
| 0 | Idle |
| 1 | Hysteresis Run |
| 2 | Hysteresis Pause |
| 5 | PID Run |
| 6 | PID Pause |
| 10 | Dimmer Run |
| 11 | Dimmer Pause |

### Controls (read/write)

| HA Entity | Discovery topic | State topic | Command topic | Notes |
|-----------|----------------|-------------|---------------|-------|
| Main Setpoint | `homeassistant/number/nanopid_<mac>/setpoint/config` | `status` → `sp` | `nanopid/<mac>/setpoint` | Range -55…125, step 0.1 |
| Alarm TH Low | `homeassistant/number/nanopid_<mac>/th_low/config` | `status` → `th_l` | `nanopid/<mac>/config` | Range -55…125, step 1 |
| Alarm TH High | `homeassistant/number/nanopid_<mac>/th_high/config` | `status` → `th_h` | `nanopid/<mac>/config` | Range -55…125, step 1 |
| Target Mode | `homeassistant/select/nanopid_<mac>/mode/config` | — | `nanopid/<mac>/config/target_mode` | Options: PID Controller, Hysteresis Control, Manual Dimmer |
| Control Mode | `homeassistant/select/nanopid_<mac>/conf_mode/config` | — | `nanopid/<mac>/config` | Options: pwm, zc, phase |
| Direction | `homeassistant/select/nanopid_<mac>/conf_dir/config` | — | `nanopid/<mac>/config` | Options: heat, cool |
| Start Behaviour | `homeassistant/select/nanopid_<mac>/conf_start/config` | — | `nanopid/<mac>/config` | Options: single, profile |
| Profile Type | `homeassistant/select/nanopid_<mac>/conf_prof/config` | — | `nanopid/<mac>/config` | Options: dynamic, static |

### Action Buttons

| HA Entity | Discovery topic | Payload sent | Destination topic |
|-----------|----------------|--------------|------------------|
| Start Process | `homeassistant/button/nanopid_<mac>/start/config` | `start` | `nanopid/<mac>/command` |
| Stop Process | `homeassistant/button/nanopid_<mac>/stop/config` | `stop` | `nanopid/<mac>/command` |
| Pause Process | `homeassistant/button/nanopid_<mac>/pause/config` | `pause` | `nanopid/<mac>/command` |
| Resume Process | `homeassistant/button/nanopid_<mac>/resume/config` | `resume` | `nanopid/<mac>/command` |

> The **Start** button sends plain `"start"` (uses current NVS config). For a synchronised start from HA dashboard values, use the Bundled START script (see §6).

---

## 6. Home Assistant Integration Patterns

### 6.1 Simple Start (plain button)
Uses whatever configuration is currently stored in the device NVS.
The HA Start button sends `"start"` to `nanopid/<mac>/command`. No script needed.

### 6.2 Bundled Start (script — dashboard-synchronised)
Sends all current HA entity values together with the start command in a single atomic message.
Create an HA script/automation and call it from the dashboard:

```yaml
alias: NanoPID — Bundled Start
sequence:
  - service: mqtt.publish
    data:
      topic: "nanopid/<mac>/command"
      payload: >-
        {"cmd":"start",
         "mode":"{{ states('select.nanopid_target_mode') }}",
         "sp":{{ states('number.nanopid_main_setpoint') | float }},
         "ctrl":"{{ states('select.nanopid_control_mode') }}",
         "dir":"{{ states('select.nanopid_direction') }}",
         "beh":"{{ states('select.nanopid_start_behaviour') }}",
         "prof":"{{ states('select.nanopid_profile_type') }}"}
```

Replace `<mac>` with the device MAC hex string (e.g. `a1b2c3d4e5f6`).
Replace entity IDs with the actual HA entity IDs assigned at discovery.

### 6.3 Update single parameter while running
Send to the appropriate topic at any time — changes take effect immediately:

```
# Change setpoint while PID is running
nanopid/<mac>/setpoint  →  {"val": 70.0}

# Change alarm thresholds
nanopid/<mac>/config    →  {"th_low": 5.0, "th_high": 95.0}
```

---

## 7. Internal Architecture

### 7.1 Tasks

| Task | Stack | Core | Description |
|------|-------|------|-------------|
| `discovery_task` | 2400 B | 1 | One-shot at connect: publishes all HA discovery messages sequentially (800 ms gap between each) then self-deletes |
| `publish_status_task` | 3072 B | 1 | Periodic 5 s publish of `/status`. Skipped if MQTT outbox > 512 B or free heap < 8 KB |

### 7.2 Shared Buffer and Mutex

Both tasks share a single 1024-byte static buffer `g_shared_payload` protected by `mqtt_buffer_mutex`.
- Timeout on `xSemaphoreTake`: 1000 ms for discovery, skipped for periodic status if busy.
- `fsm_mutex` is used for all reads/writes of `node_c3` shared state; timeout 100 ms for status publish, 500 ms for command handlers.

### 7.3 Outbox protection

`publish_status_task` checks `esp_mqtt_client_get_outbox_size()` before each publish.
If the outbox exceeds 512 bytes, the publish cycle is deferred 5 s. This prevents heap exhaustion on broker reconnect storms.
