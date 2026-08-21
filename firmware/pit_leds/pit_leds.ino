/*
 * Breakaway 3937 — Pit LED controller
 * ------------------------------------------------------------------
 * Target : Arduino Uno / Nano (ATmega328P)
 * Strip  : WS2812B via FastLED
 * Link   : USB serial, 115200 baud, COBS-framed commands
 *
 * This board owns the animation loop. The pit display app sends short
 * commands ("be this mode, this colour, this bright") and never streams
 * pixels. That is deliberate:
 *
 *   1. FastLED.show() disables interrupts for the whole strip write —
 *      roughly 30us per pixel, so ~9ms at 300 LEDs. Incoming serial bytes
 *      are dropped during that window. Streaming frames would corrupt
 *      constantly, and worse the longer the strip. Commands are tiny and
 *      resent on demand, so a dropped one costs nothing.
 *   2. If the host crashes, closes, or gets unplugged, the strips keep
 *      running. The pit never goes dark because a Python process died.
 *
 * SRAM budget (ATmega328P has 2048 bytes, FastLED uses 3 bytes/pixel):
 *      150 px =  450 B   comfortable
 *      300 px =  900 B   practical ceiling on this chip
 *      500 px = 1500 B   too tight - move to an ESP32 instead
 *
 * Wiring: data through a 330R resistor, 1000uF across strip 5V/GND, strip
 * powered from its own 5V supply with all grounds tied together. USB alone
 * runs about eight pixels at full white.
 */

#include <FastLED.h>
#include <EEPROM.h>

// ── Configure for your strip ────────────────────────────────────────────
#define LED_PIN     6
#define NUM_LEDS    150         // keep at or below 300 on a 328P
#define LED_TYPE    WS2812B
#define COLOR_ORDER GRB

#define FW_MAJOR 1
#define FW_MINOR 0

// ── Protocol (mirrors app/leds/protocol.py) ─────────────────────────────
enum : uint8_t {
  OP_HELLO      = 0x01,
  OP_ACK        = 0x02,
  OP_NAK        = 0x03,
  OP_SET_MODE   = 0x10,
  OP_SET_COLOR  = 0x11,
  OP_SET_BRIGHT = 0x12,
  OP_SET_PIXELS = 0x13,
  OP_SAVE       = 0x14,
  OP_PING       = 0x15,
  OP_OFF        = 0x16,
  OP_INFO       = 0x80,
  OP_LOG        = 0x81
};

enum : uint8_t {
  MODE_SOLID = 0, MODE_BREATHE, MODE_WIPE, MODE_CHASE,
  MODE_SPARKLE, MODE_RAINBOW, MODE_ALERT, MODE_OFF
};

static const uint16_t WATCHDOG_MS = 5000;
static const uint8_t  RX_MAX      = 200;
static const uint8_t  ALL_SEGMENTS = 0xFF;
static const uint16_t EEPROM_MAGIC_ADDR = 0;
static const uint8_t  EEPROM_MAGIC = 0xB9;

CRGB leds[NUM_LEDS];

struct State {
  uint8_t mode;
  uint8_t speed;
  uint8_t brightness;
  uint8_t r, g, b;
};

State state   = { MODE_SOLID, 128, 180, 200, 32, 39 };  // Breakaway red
State fallback = state;          // what the watchdog reverts to
bool  blanked = false;
unsigned long lastRx = 0;
bool  hostSeen = false;

// ── COBS receive ────────────────────────────────────────────────────────
uint8_t rxBuf[RX_MAX];
uint8_t rxLen = 0;

uint8_t crc8(const uint8_t *data, uint8_t len) {
  uint8_t crc = 0;
  while (len--) {
    crc ^= *data++;
    for (uint8_t i = 0; i < 8; i++)
      crc = (crc & 0x01) ? ((crc >> 1) ^ 0x8C) : (crc >> 1);
  }
  return crc;
}

// Decode in place. Returns decoded length, or 0 if the frame is malformed.
uint8_t cobsDecode(uint8_t *buf, uint8_t len) {
  uint8_t read = 0, write = 0;
  while (read < len) {
    uint8_t code = buf[read];
    if (code == 0) return 0;
    read++;
    uint8_t end = read + code - 1;
    if (end > len) return 0;
    while (read < end) buf[write++] = buf[read++];
    if (code < 0xFF && read < len) buf[write++] = 0;
  }
  return write;
}

void sendFrame(uint8_t seq, uint8_t op, const uint8_t *payload, uint8_t plen) {
  uint8_t body[64];
  uint8_t n = 0;
  body[n++] = seq;
  body[n++] = op;
  for (uint8_t i = 0; i < plen && n < 63; i++) body[n++] = payload[i];
  body[n] = crc8(body, n);
  n++;

  // COBS-encode straight to the port so we never need a second buffer.
  uint8_t blockStart = 0;
  for (uint8_t i = 0; i <= n; i++) {
    if (i == n || body[i] == 0) {
      Serial.write((uint8_t)(i - blockStart + 1));
      for (uint8_t j = blockStart; j < i; j++) Serial.write(body[j]);
      blockStart = i + 1;
    }
  }
  Serial.write((uint8_t)0x00);
}

void sendInfo(uint8_t seq) {
  uint8_t p[9];
  p[0] = FW_MAJOR;
  p[1] = FW_MINOR;
  p[2] = (NUM_LEDS >> 8) & 0xFF;
  p[3] = NUM_LEDS & 0xFF;
  p[4] = 1;                    // one segment: the whole strip
  p[5] = 0; p[6] = 0;
  p[7] = (NUM_LEDS >> 8) & 0xFF;
  p[8] = NUM_LEDS & 0xFF;
  sendFrame(seq, OP_INFO, p, 9);
}

// ── EEPROM persistence ──────────────────────────────────────────────────
void saveState() {
  EEPROM.update(EEPROM_MAGIC_ADDR, EEPROM_MAGIC);
  EEPROM.put(EEPROM_MAGIC_ADDR + 1, state);
  fallback = state;
}

void loadState() {
  if (EEPROM.read(EEPROM_MAGIC_ADDR) == EEPROM_MAGIC) {
    EEPROM.get(EEPROM_MAGIC_ADDR + 1, state);
    if (state.mode > MODE_OFF) state.mode = MODE_SOLID;
  }
  fallback = state;
}

// ── Command dispatch ────────────────────────────────────────────────────
void handleFrame(uint8_t *body, uint8_t len) {
  if (len < 3) return;
  uint8_t expected = crc8(body, len - 1);
  uint8_t seq = body[0];
  if (body[len - 1] != expected) { sendFrame(seq, OP_NAK, NULL, 0); return; }

  uint8_t op = body[1];
  uint8_t *p = body + 2;
  uint8_t plen = len - 3;

  lastRx = millis();
  hostSeen = true;

  switch (op) {
    case OP_HELLO:
      sendInfo(seq);
      return;

    case OP_SET_MODE:
      if (plen >= 2) { state.mode = p[0]; state.speed = p[1]; blanked = false; }
      break;

    case OP_SET_COLOR:
      // p[0] is the segment; this build has one, so it is accepted and ignored.
      if (plen >= 4) { state.r = p[1]; state.g = p[2]; state.b = p[3]; }
      break;

    case OP_SET_BRIGHT:
      if (plen >= 1) { state.brightness = p[0]; blanked = false; }
      break;

    case OP_SET_PIXELS: {
      if (plen < 2) break;
      uint16_t off = ((uint16_t)p[0] << 8) | p[1];
      uint8_t n = (plen - 2) / 3;
      for (uint8_t i = 0; i < n && (off + i) < NUM_LEDS; i++)
        leds[off + i] = CRGB(p[2 + i * 3], p[3 + i * 3], p[4 + i * 3]);
      state.mode = MODE_SOLID;   // hold what was pushed
      break;
    }

    case OP_SAVE:
      saveState();
      break;

    case OP_OFF:
      blanked = true;
      break;

    case OP_PING:
      break;

    default:
      sendFrame(seq, OP_NAK, NULL, 0);
      return;
  }
  sendFrame(seq, OP_ACK, NULL, 0);
}

void readSerial() {
  while (Serial.available()) {
    uint8_t c = Serial.read();
    if (c == 0x00) {
      if (rxLen > 0) {
        uint8_t n = cobsDecode(rxBuf, rxLen);
        if (n) handleFrame(rxBuf, n);
        rxLen = 0;
      }
    } else if (rxLen < RX_MAX) {
      rxBuf[rxLen++] = c;
    } else {
      rxLen = 0;                 // overrun: drop and resync on the next 0x00
    }
  }
}

// ── Animations ──────────────────────────────────────────────────────────
uint8_t  animStep = 0;
uint16_t chasePos = 0;

void render() {
  CRGB base = CRGB(state.r, state.g, state.b);

  switch (state.mode) {
    case MODE_SOLID:
      fill_solid(leds, NUM_LEDS, base);
      break;

    case MODE_BREATHE: {
      uint8_t wave = sin8(animStep);
      // Floor at ~12% so a slow breathe never reads as a fault light.
      uint8_t level = 30 + scale8(wave, 225);
      fill_solid(leds, NUM_LEDS, base);
      nscale8(leds, NUM_LEDS, level);
      break;
    }

    case MODE_WIPE: {
      uint16_t head = map(animStep, 0, 255, 0, NUM_LEDS);
      for (uint16_t i = 0; i < NUM_LEDS; i++)
        leds[i] = (i <= head) ? base : CRGB::Black;
      break;
    }

    case MODE_CHASE:
      fadeToBlackBy(leds, NUM_LEDS, 40);
      leds[chasePos % NUM_LEDS] = base;
      leds[(chasePos + 1) % NUM_LEDS] = base;
      break;

    case MODE_SPARKLE:
      fadeToBlackBy(leds, NUM_LEDS, 24);
      if (random8() < 60) leds[random16(NUM_LEDS)] = base;
      break;

    case MODE_RAINBOW:
      fill_rainbow(leds, NUM_LEDS, animStep, 255 / max((uint16_t)1, (uint16_t)NUM_LEDS));
      break;

    case MODE_ALERT:
      fill_solid(leds, NUM_LEDS, (animStep & 0x20) ? base : CRGB::Black);
      break;

    case MODE_OFF:
    default:
      fill_solid(leds, NUM_LEDS, CRGB::Black);
      break;
  }
}

void setup() {
  Serial.begin(115200);
  FastLED.addLeds<LED_TYPE, LED_PIN, COLOR_ORDER>(leds, NUM_LEDS)
         .setCorrection(TypicalLEDStrip);
  // A ceiling the USB supply and the PSU can both live with. Raise it only
  // once you know what is actually feeding the strip.
  FastLED.setMaxPowerInVoltsAndMilliamps(5, 2000);
  loadState();
  lastRx = millis();
}

void loop() {
  readSerial();

  // Watchdog: if the host stops talking, fall back to the saved default
  // rather than holding whatever was on screen when it vanished.
  if (hostSeen && (millis() - lastRx > WATCHDOG_MS)) {
    state = fallback;
    blanked = false;
    hostSeen = false;
  }

  static unsigned long lastFrame = 0;
  unsigned long now = millis();
  if (now - lastFrame >= 16) {          // ~60fps
    lastFrame = now;
    // speed maps to how fast the animation phase advances
    animStep += 1 + (state.speed >> 5);
    chasePos += 1 + (state.speed >> 6);

    if (blanked) {
      fill_solid(leds, NUM_LEDS, CRGB::Black);
      FastLED.setBrightness(0);
    } else {
      render();
      FastLED.setBrightness(state.brightness);
    }
    FastLED.show();
  }
}
