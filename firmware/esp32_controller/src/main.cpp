#include <Arduino.h>

// Ctrl+F for Your Room: pointer firmware (pan/tilt servos + laser)
//
// Board:  your working ESP32-S3 (N16R8 DevKitC). It takes commands over USB (the port
//         labeled "COM") or over Wi-Fi, so it doesn't have to be plugged into the laptop.
// Tools:  Board "ESP32S3 Dev Module", USB CDC On Boot: Disabled,
//         Flash Size: 16MB, PSRAM: OPI PSRAM. No libraries needed.
// Wi-Fi:  put your network in secrets.h (next to this file; secrets.example.h shows how).
//         Without a network there, it works over USB only. The module has an antenna
//         socket (u.FL) instead of a built-in antenna: plug the antenna in.
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
//   R...     the LED ring (see LED RING below), e.g. RW, RL, RF135
// The laser turns itself off if no P/T/L/C command arrives for LASER_TIMEOUT_MS
// ("?" doesn't count: ctrlf.py sends it every 2 s over Wi-Fi to check the link).
// The servos glide to a new angle, one degree every STEP_MS, instead of jumping:
// the laser visibly walks over, and the current draw stays steady. Every move ends going
// up in angle (moving down, they go BACKLASH_DEG past and come back), so the slack in the
// gears always sits the same way and the same command lands in the same spot.
//
// OVER WI-FI
//   It joins the first network in secrets.h it can (2.4 GHz only), then says
//   "CTRLF <name> <port>" on UDP port BEACON_PORT every 2 s so ctrlf.py can find it
//   (it's also <name>.local). ctrlf.py connects on TCP port NET_PORT and first sends
//   "KEY <LINK_KEY>"; commands from anyone without the key are ignored. One program at a
//   time (another one gets "ERR busy"). If ctrlf.py disconnects or goes quiet for
//   NET_SILENT_MS, the laser switches off.
//   The Serial Monitor shows the board's address once it has joined.
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
//                <= 20 mA: laser + -> GPIO 6, laser - -> GND (GPIO 6 is set to its
//                strongest drive, up to 40 mA; an HW-493 / KY-008 is fine on it, if dim).
//                more than that: switch it with a transistor (2N2222 or similar):
//                GPIO 6 -> 1k resistor -> base, emitter -> GND,
//                collector -> laser -, laser + -> 3.3V or 5V (whatever the laser is rated for).
//   Board power, away from the laptop: plug the COM port into a USB charger.
//   LED ring:    DI -> GPIO 7, 5V/VCC -> the board's 3V3 pin, GND -> the GND rail.
//                (Not 5V: this board's "5Vin" pin is for power coming in, and a ring on
//                5V doesn't read the ESP32's 3.3 V data reliably. It's a bit dimmer.)
//
// LED RING (16 x WS2812). The base is off (R0) or "watching" (RW: a dim slow breath, the
// privacy light: ctrlf.py sends it while the camera is on). On top of that, for a while:
//   RL listening (blue pulse)     RT thinking (a spinner)      RI done listening/thinking
//   RF<deg> found: green, brightest toward the object          RN not seen: 2 red flashes
//   RS<deg> last seen there: amber, blinking                   RA<deg> forgot it: red pulse
//   RX test: LED 0 red, LED 1 green, LED 4 blue                RB<0-255> brightness cap
// <deg> is a position around the ring, counting along the LEDs: 0 = LED 0, 22.5 = LED 1...
// (ctrlf.py works out which LED faces the object.) On boot it spins once.

#include <ESPmDNS.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include "driver/gpio.h"

#if __has_include("secrets.h")
#include "secrets.h"
#else  // no Wi-Fi set up: USB only
const char *WIFI_NETWORKS[][2] = {{"", ""}};
#define HOSTNAME "ctrlf"
#define LINK_KEY ""
#endif

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

// LED ring, powered by the board's 3.3 V regulator (with the ESP32): keep RING_MAX modest.
// The animations draw about 100 mA at most.
const int RING_PIN = 7;
const int RING_LEDS = 16;
const unsigned long RING_FRAME_MS = 20;

// Wi-Fi. Keep the ports in sync with ctrlf.py.
const uint16_t NET_PORT = 3333, BEACON_PORT = 3334;
const unsigned long NET_SILENT_MS = 6000;   // ctrlf.py says something every 2 s
const unsigned long KEY_WAIT_MS = 3000;     // a new connection must send the key by then
const unsigned long BUSY_MS = 3000;         // heard from ctrlf.py this recently: it's in use
const unsigned long WIFI_RETRY_MS = 12000;  // not on a network by then: try the next one
const unsigned long BEACON_MS = 2000;
const int WIFI_COUNT = sizeof(WIFI_NETWORKS) / sizeof(WIFI_NETWORKS[0]);

float panDeg = 90;                        // where the servos are now
float tiltDeg = (TILT_MIN + TILT_MAX) / 2;
float panTarget = panDeg;                 // where they're headed
float tiltTarget = tiltDeg;
float panVia = panDeg, tiltVia = tiltDeg; // first stop on the way (see BACKLASH_DEG)
bool laserOn = false;
unsigned long lastCmdMs = 0;
unsigned long lastStepMs = 0;
String line;

bool wifiWanted = false;  // a network and a key in secrets.h
bool wifiUp = false, serverUp = false;
int wifiIndex = -1, wifiTries = 0;
volatile uint8_t wifiReason = 0;  // why the last try to join failed (set by the Wi-Fi task)
uint8_t wifiReasonSaid = 0;
bool wifiScanned = false;
unsigned long wifiTryMs = 0, beaconMs = 0;
WiFiServer server(NET_PORT);
WiFiUDP beacon;
WiFiClient app;       // ctrlf.py, once it has sent the key
bool appOn = false;
String appLine;
unsigned long appHeardMs = 0;
WiFiClient newcomer;  // a new connection that hasn't sent the key yet
String newLine;
unsigned long newcomerMs = 0;

uint8_t ringMax = 120;    // brightness cap, of 255 (RB)
char ringBase = '0';      // '0' off or 'W' watching
char ringShow = 0;        // shown on top of the base for a while: L T F S A N X H (0 = none)
float ringPos = 0;        // where F/S/A point, in degrees along the LEDs
unsigned long ringSince = 0, ringFrameMs = 0;
float ringR[RING_LEDS], ringG[RING_LEDS], ringB[RING_LEDS];  // this frame, 0-1
rmt_data_t ringBits[RING_LEDS * 24];

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

// A message for whoever is listening: USB and, if connected, ctrlf.py over Wi-Fi.
void tell(const char *msg) {
  Serial.println(msg);
  if (appOn) app.println(msg);
}

void report(Print &out) {  // reports the targets, i.e. what was last commanded
  out.printf("OK P%.1f T%.1f L%d R1\n", panTarget, tiltTarget, laserOn ? 1 : 0);  // R1: has the ring
}

void ringCommand(String s);

void handle(String s, Print &out) {
  s.trim();
  s.toUpperCase();
  if (s.startsWith("R")) {  // the LED ring ("RT" isn't a tilt, "RL" isn't the laser)
    ringCommand(s.substring(1));
    report(out);
    return;
  }
  bool acted = false;  // anything but "?"
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
      acted = true;
    } else if (c == 'C') {
      panTarget = 90;
      tiltTarget = (TILT_MIN + TILT_MAX) / 2;
      laserOn = false;
      acted = true;
    }
  }
  if (acted) {
    panTarget = constrain(panTarget, PAN_MIN, PAN_MAX);
    tiltTarget = constrain(tiltTarget, TILT_MIN, TILT_MAX);
    panVia = panTarget < panDeg ? max(panTarget - BACKLASH_DEG, (float)PAN_MIN) : panTarget;
    tiltVia = tiltTarget < tiltDeg ? max(tiltTarget - BACKLASH_DEG, (float)TILT_MIN) : tiltTarget;
    lastCmdMs = millis();
    digitalWrite(LASER_PIN, laserOn ? HIGH : LOW);
  }
  report(out);
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

// ---------------------------------------------------------------- Wi-Fi

// ctrlf.py is gone (or replaced by a new connection from it): never leave the laser on.
void dropApp(const char *why) {
  if (!appOn) return;
  appOn = false;
  app.stop();
  ringBase = '0';  // the light says the camera is watching; ctrlf.py turns it on again
  ringShow = 0;
  Serial.printf("wifi: ctrlf.py %s\n", why);
  if (laserOn) {
    laserOn = false;
    digitalWrite(LASER_PIN, LOW);
    Serial.println("laser off");
  }
}

// For comparing network names the way people mistype them: any case, ' for the iPhone's ’.
String simplified(String name) {
  name.replace("\xE2\x80\x99", "'");
  name.toLowerCase();
  return name;
}

// What it can see, when it can't see the network it's looking for (once per boot: the
// scan pauses everything for a few seconds).
void listNetworks(const char *want) {
  WiFi.disconnect();
  int n = WiFi.scanNetworks();
  if (n <= 0) {
    Serial.println("wifi: no networks in range at all. Is the antenna plugged in?");
    return;
  }
  Serial.printf("wifi: the 2.4 GHz networks it can see:\n");
  String close;
  for (int i = 0; i < n && i < 15; i++) {
    Serial.printf("  \"%s\"  %d dBm  channel %d\n", WiFi.SSID(i).c_str(), WiFi.RSSI(i), WiFi.channel(i));
    if (simplified(WiFi.SSID(i)) == simplified(want)) close = WiFi.SSID(i);
  }
  if (close.length())
    Serial.printf("wifi: did you mean \"%s\"? The name must match exactly, capitals too.\n", close.c_str());
  else
    Serial.println("wifi: (a phone hotspot: keep its settings screen open, and turn on Maximize Compatibility)");
  WiFi.scanDelete();
}

// Why joining failed, in words; said once per reason.
void explainWifi(const char *name) {
  uint8_t r = wifiReason;
  if (!r || r == wifiReasonSaid) return;
  wifiReasonSaid = r;
  if (r == WIFI_REASON_NO_AP_FOUND) {
    Serial.printf("wifi: can't see \"%s\"\n", name);
    if (!wifiScanned) {
      wifiScanned = true;
      listNetworks(name);
    }
  } else if (r == WIFI_REASON_AUTH_FAIL || r == WIFI_REASON_4WAY_HANDSHAKE_TIMEOUT ||
             r == WIFI_REASON_HANDSHAKE_TIMEOUT) {
    Serial.printf("wifi: \"%s\" turned it down: wrong password? (%s)\n", name,
                  WiFi.disconnectReasonName((wifi_err_reason_t)r));
  } else {
    Serial.printf("wifi: couldn't join \"%s\": %s\n", name, WiFi.disconnectReasonName((wifi_err_reason_t)r));
  }
}

void joinNextNetwork() {
  do wifiIndex = (wifiIndex + 1) % WIFI_COUNT;
  while (!WIFI_NETWORKS[wifiIndex][0][0]);  // skip unnamed ones
  if (wifiTries++ < WIFI_COUNT)  // say it the first time round only, not every 12 s
    Serial.printf("wifi: joining \"%s\"...\n", WIFI_NETWORKS[wifiIndex][0]);
  WiFi.disconnect();
  WiFi.begin(WIFI_NETWORKS[wifiIndex][0], WIFI_NETWORKS[wifiIndex][1]);
  wifiTryMs = millis();
}

void startWifi() {
  for (auto &n : WIFI_NETWORKS) wifiWanted |= n[0][0] != 0;
  wifiWanted &= strlen(LINK_KEY) > 0;
  if (!wifiWanted) {
    Serial.println("wifi: off (no network in secrets.h), USB only");
    return;
  }
  WiFi.onEvent([](arduino_event_id_t, arduino_event_info_t info) {
    uint8_t r = info.wifi_sta_disconnected.reason;
    if (r != WIFI_REASON_ASSOC_LEAVE && r != WIFI_REASON_STA_LEAVING)  // (its own retries)
      wifiReason = r;
  }, ARDUINO_EVENT_WIFI_STA_DISCONNECTED);
  WiFi.setHostname(HOSTNAME);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);  // power saving delays incoming commands by up to ~100 ms
  joinNextNetwork();
}

// A new connection: it becomes the app once its first line is the key.
void checkNewcomer() {
  if (server.hasClient()) {
    newcomer.stop();  // one at a time
    newcomer = server.accept();
    newcomer.setNoDelay(true);
    newLine = "";
    newcomerMs = millis();
  }
  if (!newcomer.connected()) return;
  if (millis() - newcomerMs > KEY_WAIT_MS) {
    newcomer.stop();
    return;
  }
  while (newcomer.available()) {
    char ch = newcomer.read();
    if (ch != '\n' && ch != '\r') {
      if (newLine.length() < 80) newLine += ch;
      continue;
    }
    newLine.trim();
    if (!newLine.length()) continue;
    if (newLine != String("KEY ") + LINK_KEY) {
      newcomer.println("ERR wrong key");
    } else if (appOn && app.connected() && millis() - appHeardMs < BUSY_MS) {
      newcomer.println("ERR busy");  // one program at a time, like the USB port
    } else {
      dropApp("reconnected");  // its old connection, if that was still hanging on
      app = newcomer;
      newcomer = WiFiClient();  // (not stop(): that would close the app's connection)
      appOn = true;
      appLine = "";
      appHeardMs = millis();
      Serial.printf("wifi: ctrlf.py connected from %s\n", app.remoteIP().toString().c_str());
      report(app);
      return;
    }
    newcomer.stop();
    return;
  }
}

void checkApp() {
  if (!appOn) return;
  if (!app.connected()) {
    dropApp("disconnected");
    return;
  }
  if (millis() - appHeardMs > NET_SILENT_MS) {
    dropApp("went quiet");
    return;
  }
  while (app.available()) {
    char ch = app.read();
    appHeardMs = millis();
    if (ch == '\n' || ch == '\r') {
      if (appLine.length()) handle(appLine, app);
      appLine = "";
    } else if (appLine.length() < 64) {
      appLine += ch;
    }
  }
}

void wifiLoop() {
  if (!wifiWanted) return;
  bool up = WiFi.status() == WL_CONNECTED;
  if (up != wifiUp) {
    wifiUp = up;
    if (up) {
      wifiTries = 0;
      wifiReason = wifiReasonSaid = 0;
      Serial.printf("wifi: on \"%s\", address %s (%s.local)\n", WiFi.SSID().c_str(),
                    WiFi.localIP().toString().c_str(), HOSTNAME);
      if (!serverUp) {
        server.begin();
        server.setNoDelay(true);
        if (MDNS.begin(HOSTNAME)) MDNS.addService("ctrlf", "tcp", NET_PORT);
        serverUp = true;
      }
    } else {
      Serial.println("wifi: lost the network");
      dropApp("lost (no Wi-Fi)");
      wifiTryMs = millis();
    }
  }
  if (!up) {
    if (millis() - wifiTryMs > WIFI_RETRY_MS) {
      explainWifi(WIFI_NETWORKS[wifiIndex][0]);
      joinNextNetwork();
    }
    return;
  }
  if (millis() - beaconMs > BEACON_MS) {
    beaconMs = millis();
    beacon.beginPacket(WiFi.broadcastIP(), BEACON_PORT);
    beacon.printf("CTRLF %s %d\n", HOSTNAME, NET_PORT);
    beacon.endPacket();
  }
  checkNewcomer();
  checkApp();
}

// ---------------------------------------------------------------- LED ring

unsigned long ringShowMs(char m) {  // how long each lasts before going back to the base
  switch (m) {
    case 'L': case 'T': return 30000;  // (ctrlf.py ends these sooner, with RI)
    case 'F': case 'S': return 8000;
    case 'A': return 10000;
    case 'N': return 1000;
    case 'X': return 15000;
    case 'H': return 1300;
  }
  return 0;
}

void ringCommand(String s) {
  char m = s.length() ? s[0] : 'W';
  float v = s.substring(1).toFloat();
  if (m == '0' || m == 'W') {
    ringBase = m;
    ringShow = 0;
  } else if (m == 'I') {
    if (ringShow == 'L' || ringShow == 'T') ringShow = 0;
  } else if (m == 'B') {
    ringMax = constrain((int)v, 0, 255);
  } else if (strchr("LTNXFSA", m)) {
    ringShow = m;
    ringSince = millis();
    if (strchr("FSA", m)) ringPos = fmodf(fmodf(v, 360) + 360, 360);
  }
}

// 1 at the LED facing the object (ringPos), fading out over about 2 LEDs either side.
float toward(int i) {
  float d = fabsf(i - ringPos / 360 * RING_LEDS);
  d = min(d, RING_LEDS - d);  // the short way round
  return expf(-d * d / 1.5f);
}

uint8_t ringLevel(float v) {
  return (uint8_t)(constrain(v, 0.0f, 1.0f) * ringMax + 0.5f);
}

void ringSend() {
  int n = 0;
  for (int i = 0; i < RING_LEDS; i++) {
    uint8_t c[3] = {ringLevel(ringG[i]), ringLevel(ringR[i]), ringLevel(ringB[i])};  // GRB order
    for (int k = 0; k < 3; k++) {
      for (int bit = 7; bit >= 0; bit--) {
        bool one = (c[k] >> bit) & 1;  // at 10 MHz: a 1 is 0.8 us high + 0.4 low, a 0 the reverse
        ringBits[n].level0 = 1;
        ringBits[n].duration0 = one ? 8 : 4;
        ringBits[n].level1 = 0;
        ringBits[n].duration1 = one ? 4 : 8;
        n++;
      }
    }
  }
  rmtWrite(RING_PIN, ringBits, n, 20);
}

void drawRing() {
  unsigned long now = millis();
  if (ringShow && now - ringSince > ringShowMs(ringShow)) ringShow = 0;
  float t = (now - ringSince) / 1000.0f;  // seconds into what it's showing
  float clock = now / 1000.0f;
  char m = ringShow ? ringShow : ringBase;
  for (int i = 0; i < RING_LEDS; i++) {
    float r = 0, g = 0, b = 0, k;
    switch (m) {
      case 'W':  // watching: a dim, slow breath
        k = 0.08f + 0.04f * sinf(clock * 2 * PI / 4);
        r = 0.4f * k, g = 0.6f * k, b = k;
        break;
      case 'L':  // listening: blue, pulsing
        k = 0.55f + 0.45f * sinf(clock * 2 * PI * 1.2f);
        g = 0.15f * k, b = k;
        break;
      case 'T':  // thinking: a purple comet going round
        k = fmodf(clock * 1.5f * RING_LEDS - i + 10 * RING_LEDS, RING_LEDS);  // LEDs behind the head
        k = k < 5 ? 1 - k / 5 : 0;
        r = 0.5f * k, g = 0.1f * k, b = k;
        break;
      case 'H':  // hello (boot): one cyan sweep round
        k = t * RING_LEDS - i;
        k = k >= 0 ? max(0.0f, 1 - k / 6) : 0;
        g = 0.6f * k, b = k;
        break;
      case 'F':  // found: green toward it (fading in), dim green all round
        k = toward(i) * min(1.0f, t / 0.4f);
        r = 0.1f * k, g = 0.06f + k, b = 0.15f * k;
        break;
      case 'S':  // last seen: amber toward where it was, blinking
        k = toward(i) * (fmodf(t, 1.0f) < 0.6f ? 1 : 0.25f);
        r = k, g = 0.4f * k;
        break;
      case 'A':  // forgot it: red toward it, pulsing fast
        k = toward(i) * (0.55f + 0.45f * sinf(t * 2 * PI * 2.5f));
        r = k;
        break;
      case 'N':  // not seen / don't know: two red flashes all round
        r = (t < 0.2f || (t > 0.4f && t < 0.6f)) ? 0.6f : 0;
        break;
      case 'X':  // test: LED 0 red, LED 1 green (which way they count), LED 4 blue
        if (i == 0) r = 1;
        else if (i == 1) g = 1;
        else if (i == 4) b = 1;
        else r = g = b = 0.04f;
        break;
    }
    ringR[i] = r, ringG[i] = g, ringB[i] = b;
  }
  ringSend();
}

// ---------------------------------------------------------------- main

void setup() {
  Serial.begin(115200);
  pinMode(LASER_PIN, OUTPUT);
  gpio_set_drive_capability((gpio_num_t)LASER_PIN, GPIO_DRIVE_CAP_3);  // strongest (up to 40 mA): a brighter dot on 3.3 V
  digitalWrite(LASER_PIN, LOW);
  ledcAttach(PAN_PIN, SERVO_HZ, SERVO_BITS);
  ledcAttach(TILT_PIN, SERVO_HZ, SERVO_BITS);
  apply();
  delay(300);
  wiggle();
  rmtInit(RING_PIN, RMT_TX_MODE, RMT_MEM_NUM_BLOCKS_2, 10000000);  // 10 MHz: 0.1 us steps
  ringShow = 'H';
  ringSince = millis();
  Serial.println("Ctrl+F pointer ready. Try: P60   P120   T140   L1   L0   C   ?   RX");
  startWifi();
}

void loop() {
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '\n' || ch == '\r') {
      if (line.length()) handle(line, Serial);
      line = "";
    } else if (line.length() < 64) {
      line += ch;
    }
  }
  wifiLoop();
  if (millis() - lastStepMs >= STEP_MS) {
    lastStepMs = millis();
    step();
  }
  if (millis() - ringFrameMs >= RING_FRAME_MS) {
    ringFrameMs = millis();
    drawRing();
  }
  if (laserOn && millis() - lastCmdMs > LASER_TIMEOUT_MS) {
    laserOn = false;
    apply();
    tell("laser auto-off");
  }
}
