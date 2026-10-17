#include <Arduino.h>
#include <ESP32Servo.h>
#include "config.h"

Servo panServo;
Servo tiltServo;

int currentPan = PAN_DEFAULT;
int currentTilt = TILT_DEFAULT;

void setAngles(int p, int t) {
    currentPan = constrain(p, PAN_MIN, PAN_MAX);
    currentTilt = constrain(t, TILT_MIN, TILT_MAX);
    panServo.write(currentPan);
    tiltServo.write(currentTilt);
}

void setup() {
    Serial.begin(115200);
    pinMode(PIN_LASER, OUTPUT);
    digitalWrite(PIN_LASER, LOW);

    ESP32PWM::allocateTimer(0);
    ESP32PWM::allocateTimer(1);
    panServo.setPeriodHertz(50);
    tiltServo.setPeriodHertz(50);
    panServo.attach(PIN_SERVO_PAN, 500, 2400);
    tiltServo.attach(PIN_SERVO_TILT, 500, 2400);

    setAngles(currentPan, currentTilt);
    delay(500);
    Serial.println("READY");
}

void loop() {
    if (Serial.available() > 0) {
        String line = Serial.readStringUntil('\n');
        line.trim();
        if (line.length() == 0) return;

        if (line == "PING") {
            Serial.println("PONG");
        } else if (line.startsWith("LASER:")) {
            int state = line.substring(6).toInt();
            digitalWrite(PIN_LASER, state ? HIGH : LOW);
            Serial.printf("OK:LASER=%d\n", state ? 1 : 0);
        } else if (line.startsWith("POS:")) {
            int commaIndex = line.indexOf(',');
            if (commaIndex > 4) {
                int p = line.substring(4, commaIndex).toInt();
                int t = line.substring(commaIndex + 1).toInt();
                setAngles(p, t);
                Serial.printf("OK:POS=%d,%d\n", currentPan, currentTilt);
            } else {
                Serial.println("ERR:MALFORMED_POS");
            }
        } else if (line.startsWith("STEP:")) {
            int commaIndex = line.indexOf(',');
            if (commaIndex > 5) {
                int dp = line.substring(5, commaIndex).toInt();
                int dt = line.substring(commaIndex + 1).toInt();
                setAngles(currentPan + dp, currentTilt + dt);
                Serial.printf("OK:STEP=%d,%d\n", currentPan, currentTilt);
            } else {
                Serial.println("ERR:MALFORMED_STEP");
            }
        } else if (line == "LIMITS?") {
            Serial.printf("OK:LIMITS=%d,%d,%d,%d\n", PAN_MIN, PAN_MAX, TILT_MIN, TILT_MAX);
        } else {
            Serial.println("ERR:UNKNOWN_CMD");
        }
    }
}
