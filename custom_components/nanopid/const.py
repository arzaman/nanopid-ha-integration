"""Constants for the NanoPID integration."""

DOMAIN = "nanopid"

CONF_DEVICE_MAC = "device_mac"
CONF_DEVICE_NAME = "device_name"

DEFAULT_DEVICE_NAME = "NanoPID"
MANUFACTURER = "Arzaman SRL"
MODEL = "NanoPID v2.0"

# MQTT topic templates
TOPIC_STATUS = "nanopid/{mac}/status"
TOPIC_COMMAND = "nanopid/{mac}/command"
TOPIC_SETPOINT = "nanopid/{mac}/setpoint"
TOPIC_CONFIG = "nanopid/{mac}/config"
TOPIC_TARGET_MODE = "nanopid/{mac}/config/target_mode"

# FSM state code → human-readable string
FSM_MAP: dict[int, str] = {
    0: "Idle",
    1: "Hysteresis Run",
    2: "Hysteresis Pause",
    3: "Loop Run",
    4: "Loop Pause",
    5: "PID Run",
    6: "PID Pause",
    7: "Autotune Init",
    8: "Autotune Run",
    9: "Manual SSR",
    10: "Dimmer Run",
    11: "Dimmer Pause",
}

# Select entity options
TARGET_MODE_OPTIONS = ["PID Controller", "Hysteresis Control", "Manual Dimmer"]
CONTROL_MODE_OPTIONS = ["pwm", "zc", "phase"]
DIRECTION_OPTIONS = ["heat", "cool"]
BEHAVIOUR_OPTIONS = ["single", "profile"]
PROFILE_OPTIONS = ["dynamic", "static"]
