#include <Arduino.h>

// IN1 -> GPIO3; IN2 -> GPIO4; IN3 -> GPIO5; IN4 -> GPIO6.
const uint8_t PINS[4] = {3, 4, 5, 6};
const uint8_t PHASES[8][4] = {
  {1,0,0,0}, {1,1,0,0}, {0,1,0,0}, {0,1,1,0},
  {0,0,1,0}, {0,0,1,1}, {0,0,0,1}, {1,0,0,1}
};
const unsigned long STEP_MS = 4;
const unsigned long REPORT_MS = 500;
int phaseIndex = 0;
int direction = 1;
bool running = false;
bool coilsEnergized = false; // Trang thai GPIO da ra lenh, khong phai do dong dien.
bool hasCommand = false;
uint16_t remainingSteps = 0;
unsigned long lastStepAt = 0;
unsigned long lastReportAt = 0;
uint32_t commandedSteps = 0;  // Tong buoc DA PHAT tu luc reset, ca hai chieu.
uint32_t sequence = 0;        // Thu tu ban tin Serial, khong phai thu tu buoc.
const char* reason = "boot";

// Mot dong JSON dang cho gui; khong tao hang doi log tang vo han.
char tx[320];
size_t txLength = 0;
size_t txOffset = 0;

void releaseMotor() {
  for (int i = 0; i < 4; i++) digitalWrite(PINS[i], LOW);
  coilsEnergized = false;
}

void stopMotor() {
  running = false;
  remainingSteps = 0;
  releaseMotor();
  reason = "stop_command";
}

void startMove(int newDirection) {
  direction = newDirection;
  hasCommand = true;
  remainingSteps = 512;       // Lenh moi thay the so buoc con lai cua lenh cu.
  lastStepAt = millis();
  running = true;
  reason = "start_command";
}

void handleSerial() {
  if (Serial.available() <= 0) return;
  char c = Serial.read();
  if (c == 's') stopMotor();
  else if (c == 'f') startMove(1);
  else if (c == 'r') startMove(-1);
}

void updateMotor() {
  if (!running) return;
  unsigned long now = millis();
  if (now - lastStepAt < STEP_MS) return;
  if (remainingSteps == 0) {
    running = false;
    releaseMotor();
    reason = "completed";
    return;
  }
  lastStepAt = now;
  phaseIndex = (phaseIndex + direction + 8) % 8;
  for (int i = 0; i < 4; i++) {
    digitalWrite(PINS[i], PHASES[phaseIndex][i]);
  }
  remainingSteps--;
  coilsEnergized = true;
  commandedSteps++;          // Tang SAU khi xuat mot buoc lenh, khong do truc.
}

void reportTelemetry() {
  unsigned long now = millis();
  // Chi chup trang thai moi khi dong truoc da gui het va da du 500 ms.
  if (txOffset == txLength && now - lastReportAt >= REPORT_MS && Serial) {
    lastReportAt = now;
    const char* dir = !hasCommand ? "none" : (direction == 1 ? "forward" : "reverse");
    int n = snprintf(tx, sizeof(tx),
      "{\"sequence\":%lu,\"uptime_ms\":%lu,\"controller_state\":\"%s\","
      "\"direction\":\"%s\",\"commanded_steps\":%lu,\"remaining_steps\":%u,"
      "\"coils_energized\":%s,\"reason\":\"%s\"}\n",
      (unsigned long)(sequence + 1), now, running ? "stepping" : "idle",
      dir, (unsigned long)commandedSteps, (unsigned int)remainingSteps,
      coilsEnergized ? "true" : "false", reason);
    if (n > 0 && (size_t)n < sizeof(tx)) {
      sequence++;
      txLength = (size_t)n;
      txOffset = 0;
    }
  }
  // Gui tung doan theo dung luong dem con trong. Khong cho den khi gui het.
  if (Serial && txOffset < txLength) {
    int space = Serial.availableForWrite();
    if (space > 0) {
      size_t count = txLength - txOffset;
      if (count > (size_t)space) count = (size_t)space;
      if (count > 32) count = 32;
      txOffset += Serial.write((const uint8_t*)tx + txOffset, count);
    }
  }
}

void setup() {
  for (int i = 0; i < 4; i++) pinMode(PINS[i], OUTPUT);
  releaseMotor();
  Serial.begin(115200);
}

void loop() {
  handleSerial();
  updateMotor();
  reportTelemetry();
}
