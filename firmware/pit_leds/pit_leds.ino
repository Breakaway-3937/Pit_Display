/*
 * Breakaway 3937 — Pit LED controller
 * ------------------------------------------------------------------
 * Target : Arduino Uno / Nano (ATmega328P)
 * Strips : three WS2812B runs — LEFT, CENTRE, RIGHT pit units
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
 * SRAM budget (ATmega328P has 2048 bytes, FastLED uses 3 bytes/pixel).
 * The number that matters is the TOTAL across all three strips:
 *      150 px total =  450 B   comfortable
 *      300 px total =  900 B   practical ceiling on this chip
 *      500 px total = 1500 B   too tight - move to an ESP32 instead
 *
 * Wiring: data through a 330R resistor per strip, 1000uF across each
 * strip's 5V/GND, strips powered from their own 5V supply with all grounds
 * tied back to the Arduino. USB alone runs about eight pixels at full white.
 *
 *
 * THE ONE IDEA IN THIS FILE: A SINGLE AXIS THROUGH THE TRUE CENTRE
 * ------------------------------------------------------------------
 * Three physical strips, one virtual strip. Every animation is a function
 * of a pixel's distance from the TRUE CENTRE of the pit — not from its own
 * strip's pixel 0. A breathe blooms outward from the middle of the centre
 * unit and reaches both far ends at the same instant; a wipe opens from the
 * middle in both directions at once.
 *
 * That is what makes three separate runs read as one installation. Without
 * it each strip animates about its own middle and the pit looks like three
 * unrelated light boxes.
 *
 * Each strip declares where it sits on that axis (see `strips[]` below), so
 * gaps between units, unequal lengths, and strips wired right-to-left are
 * all just numbers in a table. Nothing else in the file needs to know.
 */

#include <FastLED.h>
#include <EEPROM.h>

// ─────────────────────────────────────────────────────────────────────────
//  CONFIGURE FOR YOUR PIT  — everything marked FILLER is a placeholder
// ─────────────────────────────────────────────────────────────────────────

// Data pins. These must be literal numbers: FastLED takes the pin as a
// template argument, so it cannot come from a variable or a loop.
#define LEFT_PIN     6          // FILLER
#define CENTER_PIN   5          // FILLER
#define RIGHT_PIN    6          // FILLER

// Pixel counts per unit.
#define LEFT_COUNT   60         // FILLER
#define CENTER_COUNT 60         // FILLER
#define RIGHT_COUNT  60         // FILLER

#define LED_TYPE     WS2812B
#define COLOR_ORDER  GRB

// What the strips are actually plugged into, in milliamps. FastLED dims
// globally rather than browning out the supply. Three strips draw three
// times what one did — raise this to match the real PSU.
#define PSU_MILLIAMPS 2000      // FILLER

// ── The three-way switch ────────────────────────────────────────────────
// An SPDT on-off-on: common to GND, each throw to one input pin, both pins
// held high by the internal pull-ups. The centre position closes neither
// throw, which is why it reads as "both high".
//
//      A     B     position        meaning
//    ─────────────────────────────────────────────────────────────
//     LOW   HIGH   throw A         RED     — all strips solid red
//     HIGH  HIGH   centre (open)   OFF     — dark, host ignored
//     HIGH  LOW    throw B         HOST    — the app drives the pit
//     LOW   LOW    (impossible)    HOST    — miswired; fail into host control
//
// To reorder the positions, edit switchPosition() — it is the only place
// the truth table lives.
#define SW_PIN_A     7         // FILLER
#define SW_PIN_B     2         // FILLER

// The manual RED position is a hard override, so it does not read state
// from the host or from EEPROM. Breakaway Red at a brightness that survives
// pit lighting.
#define OVERRIDE_R   200
#define OVERRIDE_G   32
#define OVERRIDE_B   39
#define OVERRIDE_BRIGHTNESS 200 // FILLER

// ── Strip placement on the shared axis ──────────────────────────────────
// `origin` is the position of that strip's PIXEL 0 measured in pixel-widths
// from the true centre of the pit: negative is left, positive is right.
// `dir` is +1 if rising pixel index travels rightward, -1 if the strip is
// wired the other way (data injected at its right-hand end).
//
// Worked example — the defaults below. Three 60-px units, each outer unit
// sitting a 20-px-wide gap away from the centre unit, all wired left-to-right:
//
//        LEFT unit            CENTRE unit           RIGHT unit
//   [0 ············ 59]  [0 ············ 59]  [0 ············ 59]
//  -110            -51   -30            +29   +50            +109
//                                  ^
//                          true centre = 0
//
// Centre unit: 60 px straddling zero, so pixel 0 sits at -30.
// Left unit:   its LAST pixel must land at -30 - 20 - 1 = -51, so its pixel
//              0 is 59 further left: -110.
// Right unit:  its FIRST pixel lands at +29 + 20 + 1 = +50.
//
// A strip wired backwards changes only two numbers: put `origin` at the
// physical position of pixel 0 (now the far end) and set `dir` to -1.

struct Strip {
  uint16_t start;    // offset into leds[] — array order, not spatial order
  uint16_t count;
  int16_t  origin;   // pixel-widths from true centre, of this strip's pixel 0
  int8_t   dir;      // +1 rising index goes right, -1 goes left
};

#define NUM_STRIPS 3
#define TOTAL_LEDS (LEFT_COUNT + CENTER_COUNT + RIGHT_COUNT)

// Segment ids on the wire: SET_COLOR's first payload byte. 0xFF means all.
enum : uint8_t { SEG_LEFT = 0, SEG_CENTER = 1, SEG_RIGHT = 2 };

const Strip strips[NUM_STRIPS] = {
  // start,                        count,        origin, dir
  { 0,                             LEFT_COUNT,   -110,   +1 },  // FILLER
  { LEFT_COUNT,                    CENTER_COUNT,  -30,   +1 },  // FILLER
  { LEFT_COUNT + CENTER_COUNT,     RIGHT_COUNT,   +50,   +1 },  // FILLER
};

#define FW_MAJOR 2
#define FW_MINOR 0

// ─────────────────────────────────────────────────────────────────────────
//  Protocol (mirrors app/leds/protocol.py — change both sides together)
// ─────────────────────────────────────────────────────────────────────────
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

// Bumped from 0xB9 when State grew per-strip colour. An old saved struct
// read into the new layout would garble every field after `brightness`, so
// the magic has to move whenever State's shape does.
static const uint16_t EEPROM_MAGIC_ADDR = 0;
static const uint8_t  EEPROM_MAGIC = 0xBA;

CRGB leds[TOTAL_LEDS];

struct State {
  uint8_t mode;
  uint8_t speed;
  uint8_t brightness;
  uint8_t r[NUM_STRIPS], g[NUM_STRIPS], b[NUM_STRIPS];
};

State state = {
  MODE_SOLID, 128, 180,
  { 200, 200, 200 }, { 32, 32, 32 }, { 39, 39, 39 },   // Breakaway red
};
State fallback = state;          // what the watchdog reverts to
bool  blanked = false;
unsigned long lastRx = 0;
bool  hostSeen = false;

// ─────────────────────────────────────────────────────────────────────────
//  The shared axis
// ─────────────────────────────────────────────────────────────────────────
//
// Positions are held in HALF-pixel units, because a strip with an even pixel
// count has no pixel sitting exactly on its middle — the centre falls in the
// seam between two of them. Doubling the resolution and taking each pixel's
// centre (2*pos + 1) makes the mirror exact: with the defaults above, LEFT
// pixel 0 lands at -219 and RIGHT pixel 59 at +219. Work in whole pixels and
// every "symmetric" effect is off by half a pixel on one side.
//
// The span is measured once at boot rather than assumed, so changing a count
// or nudging an origin needs no other edit.

int16_t  axisSpanH = 1;          // largest |half-pixel position| on the pit
uint32_t axisInv   = 0;          // (255 << 16) / axisSpanH, for the divide-free normalise

static inline int16_t halfPos(const Strip &s, uint16_t i) {
  return (int16_t)(2 * (s.origin + (int16_t)s.dir * (int16_t)i) + 1);
}

void measureAxis() {
  int16_t span = 1;
  for (uint8_t s = 0; s < NUM_STRIPS; s++) {
    if (strips[s].count == 0) continue;
    // Monotonic along the strip, so only the two ends can be the maximum.
    int16_t a = halfPos(strips[s], 0);
    int16_t z = halfPos(strips[s], strips[s].count - 1);
    if (a < 0) a = -a;
    if (z < 0) z = -z;
    if (a > span) span = a;
    if (z > span) span = z;
  }
  axisSpanH = span;
  axisInv = ((uint32_t)255 << 16) / (uint32_t)span;
}

// 0 at the true centre, 255 at whichever end of the pit is furthest out.
// A 32-bit multiply-and-shift instead of a divide: this runs once per pixel
// per frame, and the 328P has no divider.
static inline uint8_t axisDist(int16_t h) {
  uint16_t d = (h < 0) ? (uint16_t)(-h) : (uint16_t)h;
  uint32_t v = ((uint32_t)d * axisInv) >> 16;
  return (v > 255) ? 255 : (uint8_t)v;
}

// Which strip owns a flat leds[] index — only needed by SPARKLE, which picks
// a pixel at random and then wants that unit's colour.
static inline uint8_t stripOf(uint16_t index) {
  for (uint8_t s = NUM_STRIPS - 1; s > 0; s--)
    if (index >= strips[s].start) return s;
  return 0;
}

// ─────────────────────────────────────────────────────────────────────────
//  COBS receive
// ─────────────────────────────────────────────────────────────────────────
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

// Unsolicited diagnostic line. The host surfaces these as SerialLink.frame,
// and a plain serial monitor shows them legibly enough to debug the switch.
void sendLog(const char *msg) {
  uint8_t buf[48];
  uint8_t n = 0;
  while (msg[n] && n < sizeof(buf)) { buf[n] = (uint8_t)msg[n]; n++; }
  sendFrame(0, OP_LOG, buf, n);
}

// INFO: major | minor | count_hi | count_lo | n_seg | (start,len) * n_seg.
// The host never hardcodes geometry — it asks, and now gets three segments
// back, one per pit unit, in leds[] order.
void sendInfo(uint8_t seq) {
  uint8_t p[5 + 4 * NUM_STRIPS];
  uint8_t n = 0;
  p[n++] = FW_MAJOR;
  p[n++] = FW_MINOR;
  p[n++] = (TOTAL_LEDS >> 8) & 0xFF;
  p[n++] = TOTAL_LEDS & 0xFF;
  p[n++] = NUM_STRIPS;
  for (uint8_t s = 0; s < NUM_STRIPS; s++) {
    p[n++] = (strips[s].start >> 8) & 0xFF;
    p[n++] = strips[s].start & 0xFF;
    p[n++] = (strips[s].count >> 8) & 0xFF;
    p[n++] = strips[s].count & 0xFF;
  }
  sendFrame(seq, OP_INFO, p, n);
}

// ─────────────────────────────────────────────────────────────────────────
//  EEPROM persistence
// ─────────────────────────────────────────────────────────────────────────
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

// ─────────────────────────────────────────────────────────────────────────
//  Command dispatch
// ─────────────────────────────────────────────────────────────────────────
//
// Commands are accepted and applied to `state` in every switch position,
// including RED and OFF. The manual override suppresses the *output*, not
// the conversation: flick back to HOST and the pit is already showing what
// the app has been asking for, with no round trip and no stale frame.
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
      // p[0] selects the pit unit: SEG_LEFT / SEG_CENTER / SEG_RIGHT, or
      // 0xFF for all three. The app currently only ever sends 0xFF; the
      // per-unit path exists so it can stop doing that without a reflash.
      if (plen >= 4) {
        if (p[0] == ALL_SEGMENTS) {
          for (uint8_t s = 0; s < NUM_STRIPS; s++) {
            state.r[s] = p[1]; state.g[s] = p[2]; state.b[s] = p[3];
          }
        } else if (p[0] < NUM_STRIPS) {
          state.r[p[0]] = p[1]; state.g[p[0]] = p[2]; state.b[p[0]] = p[3];
        }
      }
      break;

    case OP_SET_BRIGHT:
      if (plen >= 1) { state.brightness = p[0]; blanked = false; }
      break;

    case OP_SET_PIXELS: {
      // Offset indexes leds[] directly: LEFT, then CENTRE, then RIGHT, in
      // wiring order. This is the one command that is NOT centre-relative.
      if (plen < 2) break;
      uint16_t off = ((uint16_t)p[0] << 8) | p[1];
      uint8_t n = (plen - 2) / 3;
      for (uint8_t i = 0; i < n && (off + i) < TOTAL_LEDS; i++)
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

// ─────────────────────────────────────────────────────────────────────────
//  The three-way switch
// ─────────────────────────────────────────────────────────────────────────
enum : uint8_t { SW_RED = 0, SW_OFF = 1, SW_HOST = 2 };

static const uint8_t SW_DEBOUNCE_MS = 25;

uint8_t swPos     = SW_HOST;     // the position we are acting on
uint8_t swPending = SW_HOST;     // the position we are waiting to trust
unsigned long swSince = 0;

// The whole truth table, in one place. See the wiring comment at the top.
uint8_t switchPosition() {
  bool a = (digitalRead(SW_PIN_A) == LOW);   // pull-ups: closed reads LOW
  bool b = (digitalRead(SW_PIN_B) == LOW);
  if (a && !b) return SW_RED;
  if (b && !a) return SW_HOST;
  if (!a && !b) return SW_OFF;               // centre position, both open
  return SW_HOST;                            // both closed: impossible on an
                                             // SPDT, so it means a wiring
                                             // fault — leave the pit usable
                                             // rather than dark.
}

// Toggles bounce for a few milliseconds. Without this a flick through the
// centre detent fires OFF, then the destination, then OFF again.
void pollSwitch() {
  uint8_t now_pos = switchPosition();
  unsigned long now = millis();
  if (now_pos != swPending) {
    swPending = now_pos;
    swSince = now;
    return;
  }
  if (swPending != swPos && (now - swSince) >= SW_DEBOUNCE_MS) {
    swPos = swPending;
    sendLog(swPos == SW_RED  ? "switch=RED (manual override)"
          : swPos == SW_OFF  ? "switch=OFF (manual override)"
                             : "switch=HOST");
  }
}

// ─────────────────────────────────────────────────────────────────────────
//  Animations — every one of them measured from the true centre
// ─────────────────────────────────────────────────────────────────────────
uint8_t animStep = 0;
uint8_t chasePos = 0;

// How far the effect spreads across the pit, in phase counts out of 255.
// 128 is half a cycle from centre to the far ends. Lower reads as a gentle
// bloom, higher as a travelling wave.
static const uint8_t BREATHE_SPREAD = 72;
static const uint8_t RAINBOW_SPREAD = 200;
static const uint8_t CHASE_WIDTH    = 14;   // half-width of the running dot,
                                            // in normalised axis units

void render() {
  // Modes with a persistence tail fade the buffer instead of rewriting it.
  if (state.mode == MODE_CHASE)   fadeToBlackBy(leds, TOTAL_LEDS, 40);
  if (state.mode == MODE_SPARKLE) {
    fadeToBlackBy(leds, TOTAL_LEDS, 24);
    // No geometry: a twinkle is a twinkle wherever it lands.
    if (random8() < 60) {
      uint16_t i = random16(TOTAL_LEDS);
      uint8_t s = stripOf(i);
      leds[i] = CRGB(state.r[s], state.g[s], state.b[s]);
    }
    return;
  }

  for (uint8_t s = 0; s < NUM_STRIPS; s++) {
    const Strip &st = strips[s];
    const CRGB base(state.r[s], state.g[s], state.b[s]);
    CRGB *px = &leds[st.start];

    // Walk the half-pixel position forward by a constant instead of
    // recomputing halfPos() per pixel — this is the inner loop of the frame.
    int16_t h = halfPos(st, 0);
    const int16_t step = 2 * (int16_t)st.dir;

    for (uint16_t i = 0; i < st.count; i++, h += step) {
      const uint8_t d = axisDist(h);      // 0 = true centre, 255 = far end

      switch (state.mode) {
        case MODE_SOLID:
          px[i] = base;
          break;

        case MODE_BREATHE: {
          // Phase lags with distance, so the pulse opens at the centre of
          // the centre unit and rolls out to both far ends together.
          uint8_t wave = sin8((uint8_t)(animStep - scale8(d, BREATHE_SPREAD)));
          // Floor at ~12% so a slow breathe never reads as a fault light.
          uint8_t level = 30 + scale8(wave, 225);
          px[i] = base;
          px[i].nscale8(level);
          break;
        }

        case MODE_WIPE:
          // Opens outward from the centre in both directions at once.
          px[i] = (d <= animStep) ? base : CRGB::Black;
          break;

        case MODE_CHASE: {
          // Two dots leaving the centre together, one per side. They are
          // mirrored for free: `d` cannot tell left from right.
          uint8_t delta = (d > chasePos) ? (d - chasePos) : (chasePos - d);
          if (delta <= CHASE_WIDTH) px[i] = base;
          break;
        }

        case MODE_RAINBOW:
          // Spectrum blooms from the centre, mirrored left and right.
          px[i] = CHSV((uint8_t)(animStep + scale8(d, RAINBOW_SPREAD)), 255, 255);
          break;

        case MODE_ALERT:
          px[i] = (animStep & 0x20) ? base : CRGB::Black;
          break;

        case MODE_OFF:
        default:
          px[i] = CRGB::Black;
          break;
      }
    }
  }
}

// ─────────────────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);

  pinMode(SW_PIN_A, INPUT_PULLUP);
  pinMode(SW_PIN_B, INPUT_PULLUP);
  swPos = swPending = switchPosition();     // start in the real position, no
                                            // flash of the wrong state at boot

  FastLED.addLeds<LED_TYPE, LEFT_PIN,   COLOR_ORDER>(leds, strips[SEG_LEFT].start,   LEFT_COUNT)
         .setCorrection(TypicalLEDStrip);
  FastLED.addLeds<LED_TYPE, CENTER_PIN, COLOR_ORDER>(leds, strips[SEG_CENTER].start, CENTER_COUNT)
         .setCorrection(TypicalLEDStrip);
  FastLED.addLeds<LED_TYPE, RIGHT_PIN,  COLOR_ORDER>(leds, strips[SEG_RIGHT].start,  RIGHT_COUNT)
         .setCorrection(TypicalLEDStrip);

  // A ceiling the supply can live with. FastLED dims globally to stay under
  // it rather than letting the rail sag.
  FastLED.setMaxPowerInVoltsAndMilliamps(5, PSU_MILLIAMPS);

  measureAxis();
  loadState();
  lastRx = millis();
}

void loop() {
  readSerial();
  pollSwitch();

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
    // speed maps to how fast the animation phase advances. Kept running in
    // every switch position so returning to HOST resumes mid-stride instead
    // of snapping back to phase zero.
    animStep += 1 + (state.speed >> 5);
    chasePos += 1 + (state.speed >> 6);

    switch (swPos) {
      case SW_OFF:
        fill_solid(leds, TOTAL_LEDS, CRGB::Black);
        FastLED.setBrightness(0);
        break;

      case SW_RED:
        fill_solid(leds, TOTAL_LEDS, CRGB(OVERRIDE_R, OVERRIDE_G, OVERRIDE_B));
        FastLED.setBrightness(OVERRIDE_BRIGHTNESS);
        break;

      default:                           // SW_HOST
        if (blanked) {
          fill_solid(leds, TOTAL_LEDS, CRGB::Black);
          FastLED.setBrightness(0);
        } else {
          render();
          FastLED.setBrightness(state.brightness);
        }
        break;
    }
    FastLED.show();
  }
}
