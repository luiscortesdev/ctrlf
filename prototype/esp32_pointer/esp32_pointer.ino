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
//   P<deg>   pan angle, 0-180 (90 = straight ahead)
//   T<deg>   tilt angle, clamped to TILT_MIN..TILT_MAX for eye safety
//   L1 / L0  laser on / off
//   C        center both servos, laser off
//   ?        print the current position
// The laser turns itself off if no command arrives for LASER_TIMEOUT_MS.
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
const int TILT_MIN = 20, TILT_MAX = 120;

const unsigned long LASER_TIMEOUT_MS = 15000;

// Standard hobby-servo timing: 50 Hz, 500-2400 us pulse for 0-180 degrees.
const int SERVO_HZ = 50, SERVO_BITS = 14;
const int PULSE_MIN_US = 500, PULSE_MAX_US = 2400;

int panDeg = 90;
int tiltDeg = (TILT_MIN + TILT_MAX) / 2;
bool laserOn = false;
unsigned long lastCmdMs = 0;
String line;

void writeServo(int pin, int deg) {
  int us = map(deg, 0, 180, PULSE_MIN_US, PULSE_MAX_US);
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

void report() {
  Serial.printf("OK P%d T%d L%d\n", panDeg, tiltDeg, laserOn ? 1 : 0);
}

void handle(String s) {
  s.trim();
  s.toUpperCase();
  unsigned int i = 0;
  while (i < s.length()) {
    char c = s[i++];
    if (c == 'P' || c == 'T' || c == 'L') {
      unsigned int start = i;
      while (i < s.length() && (isDigit(s[i]) || s[i] == '-')) i++;
      int v = s.substring(start, i).toInt();
      if (c == 'P') panDeg = v;
      else if (c == 'T') tiltDeg = v;
      else laserOn = (v != 0);
    } else if (c == 'C') {
      panDeg = 90;
      tiltDeg = (TILT_MIN + TILT_MAX) / 2;
      laserOn = false;
    }
  }
  lastCmdMs = millis();
  apply();
  report();
}

void wiggle() {
  int t0 = tiltDeg;
  int steps[][2] = {{70, t0}, {110, t0}, {90, t0}, {90, t0 - 15}, {90, t0 + 15}, {90, t0}};
  for (auto &s : steps) {
    panDeg = s[0];
    tiltDeg = s[1];
    apply();
    delay(350);
  }
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
  if (laserOn && millis() - lastCmdMs > LASER_TIMEOUT_MS) {
    laserOn = false;
    apply();
    Serial.println("laser auto-off");
  }
}
