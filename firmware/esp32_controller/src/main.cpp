#include <Arduino.h>

// Ctrl+F for Your Room: pointer firmware (pan/tilt servos + laser)
//
// Board:  your working ESP32-S3 (N16R8 DevKitC), plugged into the laptop through the
//         USB port labeled "COM".
// Tools:  Board "ESP32S3 Dev Module", USB CDC On Boot: Disabled,
//         Flash Size: 16MB, PSRAM: OPI PSRAM. No libraries needed.
//
// On power-up both servos do a small wiggle, so you can see the wiring works.
//
// Commands (Serial Monitor at 115200 baud, "New Line" ending, or from ctrlf.py).
// You can combine them on one line, e.g. "P60 T80 L1":
//   P<deg>   pan angle, 0-180 (90 = straight ahead); tenths work too, e.g. P92.5
//   T<deg>   tilt angle, clamped to TILT_MIN..TILT_MAX for eye safety
//   L1 / L0  laser on / off
//   C        center both servos, laser off
//   ?        print the current position
// The laser turns itself off if no command arrives for LASER_TIMEOUT_MS.
// The servos glide to a new angle, one degree every STEP_MS, instead of jumping:
// the laser visibly walks over, and the current draw stays steady. Every move ends going
// up in angle (moving down, they go BACKLASH_DEG past and come back), so the slack in the
// gears always sits the same way and the same command lands in the same spot.
//
// WIRING (breadboard)
//   Servo power: power bank -> cut USB cable: red = +5V rail, black = GND rail.
//                1000uF capacitor across the rails (stripe side to GND).
//                Never power the servos from the ESP32's 5V/3V3 pins.
//   Each servo:  brown/black -> GND rail, red -> +5V rail, orange/yellow (signal) -> pin below
//                pan servo signal  -> GPIO 4
//                tilt servo signal -> GPIO 5
//   Common GND:  an ESP32 GND pin -> GND rail (required, or the servos jitter/ignore commands)
//   Laser:       check the laser's current in its listing.
//                <= 20 mA: laser + -> GPIO 6, laser - -> GND.
//                more than that: switch it with a transistor (2N2222 or similar):
//                GPIO 6 -> 1k resistor -> base, emitter -> GND,
//                collector -> laser -, laser + -> 3.3V or 5V (whatever the laser is rated for).

const int PAN_PIN = 4;
const int TILT_PIN = 5;
const int LASER_PIN = 6;

const int PAN_MIN = 0, PAN_MAX = 180;
// SET THESE after mounting: find the tilt where the laser is level with the table top
// and make sure TILT can never go past it toward people's eyes.
const int TILT_MIN = 90, TILT_MAX = 160;  // higher = down; below 90 the head hits the up stop

const unsigned long LASER_TIMEOUT_MS = 15000;
const unsigned long STEP_MS = 10;  // 1 degree per 10 ms = 100 degrees per second
const float BACKLASH_DEG = 2;

// Standard hobby-servo timing: 50 Hz, 500-2400 us pulse for 0-180 degrees.
const int SERVO_HZ = 50, SERVO_BITS = 14;
const int PULSE_MIN_US = 500, PULSE_MAX_US = 2400;

float panDeg = 90;                        // where the servos are now
float tiltDeg = (TILT_MIN + TILT_MAX) / 2;
float panTarget = panDeg;                 // where they're headed
float tiltTarget = tiltDeg;
float panVia = panDeg, tiltVia = tiltDeg; // first stop on the way (see BACKLASH_DEG)
bool laserOn = false;
unsigned long lastCmdMs = 0;
unsigned long lastStepMs = 0;
String line;

void writeServo(int pin, float deg) {
  float us = PULSE_MIN_US + deg * (PULSE_MAX_US - PULSE_MIN_US) / 180.0;
  uint32_t duty = (uint32_t)us * ((1UL << SERVO_BITS) - 1) / 20000UL;  // 20000 us = one 50 Hz period
  ledcWrite(pin, duty);
}

void apply() {
  panDeg = constrain(panDeg, PAN_MIN, PAN_MAX);
  tiltDeg = constrain(tiltDeg, TILT_MIN, TILT_MAX);
  writeServo(PAN_PIN, panDeg);
  writeServo(TILT_PIN, tiltDeg);
  digitalWrite(LASER_PIN, laserOn ? HIGH : LOW);
}

void report() {  // reports the targets, i.e. what was last commanded
  Serial.printf("OK P%.1f T%.1f L%d\n", panTarget, tiltTarget, laserOn ? 1 : 0);
}

void handle(String s) {
  s.trim();
  s.toUpperCase();
  unsigned int i = 0;
  while (i < s.length()) {
    char c = s[i++];
    if (c == 'P' || c == 'T' || c == 'L') {
      unsigned int start = i;
      while (i < s.length() && (isDigit(s[i]) || s[i] == '-' || s[i] == '.')) i++;
      float v = round(s.substring(start, i).toFloat() * 10) / 10.0;  // tenths of a degree
      if (c == 'P') panTarget = v;
      else if (c == 'T') tiltTarget = v;
      else laserOn = (v != 0);
    } else if (c == 'C') {
      panTarget = 90;
      tiltTarget = (TILT_MIN + TILT_MAX) / 2;
      laserOn = false;
    }
  }
  panTarget = constrain(panTarget, PAN_MIN, PAN_MAX);
  tiltTarget = constrain(tiltTarget, TILT_MIN, TILT_MAX);
  panVia = panTarget < panDeg ? max(panTarget - BACKLASH_DEG, (float)PAN_MIN) : panTarget;
  tiltVia = tiltTarget < tiltDeg ? max(tiltTarget - BACKLASH_DEG, (float)TILT_MIN) : tiltTarget;
  lastCmdMs = millis();
  digitalWrite(LASER_PIN, laserOn ? HIGH : LOW);
  report();
}

// One degree toward goal, landing exactly on it (so the == checks below can match).
float approach(float now, float goal) {
  return fabs(goal - now) <= 1.0f ? goal : now + (goal > now ? 1.0f : -1.0f);
}

// Move each servo one step closer to its next stop.
void step() {
  if (panDeg == panVia) panVia = panTarget;  // reached the overshoot: now come back up
  if (tiltDeg == tiltVia) tiltVia = tiltTarget;
  if (panDeg == panVia && tiltDeg == tiltVia) return;
  panDeg = approach(panDeg, panVia);
  tiltDeg = approach(tiltDeg, tiltVia);
  apply();
}

void wiggle() {
  float t0 = tiltDeg;
  float steps[][2] = {{70, t0}, {110, t0}, {90, t0}, {90, t0 - 15}, {90, t0 + 15}, {90, t0}};
  for (auto &s : steps) {
    panDeg = s[0];
    tiltDeg = s[1];
    apply();
    delay(350);
  }
  panTarget = panVia = panDeg;
  tiltTarget = tiltVia = tiltDeg;
}

void setup() {
  Serial.begin(115200);
  pinMode(LASER_PIN, OUTPUT);
  digitalWrite(LASER_PIN, LOW);
  ledcAttach(PAN_PIN, SERVO_HZ, SERVO_BITS);
  ledcAttach(TILT_PIN, SERVO_HZ, SERVO_BITS);
  apply();
  delay(300);
  wiggle();
  Serial.println("Ctrl+F pointer ready. Try: P60   P120   T50   L1   L0   C   ?");
}

void loop() {
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '\n' || ch == '\r') {
      if (line.length()) handle(line);
      line = "";
    } else if (line.length() < 64) {
      line += ch;
    }
  }
  if (millis() - lastStepMs >= STEP_MS) {
    lastStepMs = millis();
    step();
  }
  if (laserOn && millis() - lastCmdMs > LASER_TIMEOUT_MS) {
    laserOn = false;
    apply();
    Serial.println("laser auto-off");
  }
}
