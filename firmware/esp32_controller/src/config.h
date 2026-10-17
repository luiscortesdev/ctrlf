#pragma once

// Pin Definitions
const int PIN_SERVO_PAN  = 18;
const int PIN_SERVO_TILT = 19;
const int PIN_LASER      = 23;

// Angle Constraints (Firmware hard safety limits)
const int PAN_MIN = 10;
const int PAN_MAX = 170;
const int PAN_DEFAULT = 90;

const int TILT_MIN = 0;       // Level with or pointing down at table
const int TILT_MAX = 45;      // STRICT SAFETY LIMIT: NEVER exceed 45 degrees
const int TILT_DEFAULT = 20;
