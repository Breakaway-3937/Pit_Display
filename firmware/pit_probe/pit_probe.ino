/*
 * Breakaway 3937 — Pit LED PROBE
 * ------------------------------------------------------------------
 * A throwaway diagnostic sketch, NOT the pit controller. Flash it when
 * you need to answer three questions about a pit nobody has measured:
 *
 *      1. Which Arduino pin is each physical run actually plugged into?
 *      2. How many pixels long is each run?
 *      3. Is the strip's colour order really GRB?
 *
 * pit_leds.ino cannot answer any of them: its pins and counts are
 * compile-time constants, so a wrong guess just looks like "some of the
 * lights are broken". This drives EVERY usable pin from ONE shared
 * buffer, one pin at a time, so you can watch a section light up and
 * say which one it is.
 *
 * Sharing the buffer is what makes it fit. Ten separate 300-pixel
 * buffers would be 9000 bytes on a chip with 2048; one buffer driven by
 * ten controllers is 900, and since only one controller is ever shown
 * at a time they cannot contend.
 *
 * Plain-text line protocol, 115200 — no COBS. You are meant to be able
 * to drive this from the Arduino IDE's serial monitor at 2am.
 *
 *   ?                 identify + list the pins being driven
 *   i                 IDENTIFY ALL AT ONCE — every pin lights a number of
 *                     pixels equal to its own pin number, and holds it.
 *                     Count the lit pixels on a run and you have its pin,
 *                     with no stopwatch and nobody watching the terminal.
 *                     Pixel 0 is RED, so it also names the wired end.
 *   o                 blank every pin
 *   s <pin> <n> <r> <g> <b>    solid: n pixels of rgb on pin, others dark
 *   m <pin> <n>       ruler: every 10th pixel white, every 50th red
 *   c <pin> <n>       colour-order check: px0 RED, px1 GREEN, px2 BLUE
 *   w <pin> <n> <ms>  wipe one pixel at a time, to find where a run ends
 *   F <pin> <n> <r> <g> <b>
 *                     SOLO flood: blank every pin, then drive ONE pin with
 *                     n pixels of one colour. Every other pin is left idle
 *                     LOW. Use this, not 'f', when narrowing down which pin
 *                     a run is really on — twelve pins driven at once is
 *                     twelve sources of crosstalk, and on a 4-wire strip
 *                     (APA102/SK9822) a neighbouring pin waggling looks
 *                     exactly like a clock line.
 *   W <pin> <n> <r> <g> <b> [w]
 *                     RAW RGBW: hand-pack a real 4-bytes-per-pixel stream
 *                     and clock it out one pin. FastLED's own RGBW support
 *                     (setRgbw) allocates a 4/3-size buffer per show, which
 *                     an ATmega with ~350 B free cannot do — so it silently
 *                     does nothing here. This writes the bytes directly
 *                     into the CRGB array instead and costs no extra RAM.
 *                     n is RGBW pixels; 3 of them fit in 4 CRGB slots.
 *                     Optional [order]: 0=GRBW 1=RGBW 2=BGRW 3=GBRW.
 *   x <0|1>           RGBW mode off/on for every controller.
 *                     An RGBW pixel (SK6812-RGBW, WS2814) eats FOUR bytes,
 *                     not three. Drive one as RGB and your 3-byte groups
 *                     slide against its 4-byte pixels, realigning only
 *                     every 12 bytes — so a solid colour comes back as a
 *                     3-pixel repeating pattern. Black still works, because
 *                     zero bytes are zero at any alignment, which is what
 *                     makes this so confusing to diagnose.
 *   f <n> <r> <g> <b> FLOOD every pin with n pixels of one colour.
 *                     Bufferless — n is not capped by MAX_PIXELS, so this
 *                     is how you light a run longer than the buffer.
 *
 * Every command blanks all pins first, so exactly one run is ever lit.
 */

#include <FastLED.h>

// PROBE_ALL_PINS 1 — hunting: drive all twelve pins to find which one a run
//   is on. Twelve controllers cost ~1.1 KB of the 2 KB, so the buffer has to
//   stay small and long runs cannot be measured in one pass.
// PROBE_ALL_PINS 0 — measuring: once the pins are known, drive only those and
//   spend the freed memory on buffer instead. Same sketch, opposite tradeoff.
#define PROBE_ALL_PINS 0

#if PROBE_ALL_PINS
  #define MAX_PIXELS 180
#else
  #define MAX_PIXELS 420
#endif
// An RGBW pixel is 4 bytes and a CRGB slot carries 3, so the buffer holds
// three RGBW pixels for every four slots.
#define RGBW_MAX_PX ((MAX_PIXELS * 3) / 4)

#define LED_TYPE   WS2812B
#define COLOR_ORDER GRB

CRGB leds[MAX_PIXELS];

// Pins worth probing on an Uno/Nano. 0 and 1 are the USB serial pair and
// are deliberately absent — driving them would cut the link we are being
// driven over. 13 is the on-board LED but still usable for a strip.
#if PROBE_ALL_PINS
const uint8_t PINS[] = { 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13 };
#else
// The pit as measured: pin 6 feeds LEFT and RIGHT through a Y-split, pin 5
// feeds CENTRE. Everything else is unused.
const uint8_t PINS[] = { 5, 6 };
#endif
#define NUM_PINS (sizeof(PINS) / sizeof(PINS[0]))

void setup() {
  Serial.begin(115200);

  // FastLED takes the pin as a template argument, so every pin has to be
  // named literally. There is no loop that can do this.
#if PROBE_ALL_PINS
  FastLED.addLeds<LED_TYPE,  2, COLOR_ORDER>(leds, MAX_PIXELS);
  FastLED.addLeds<LED_TYPE,  3, COLOR_ORDER>(leds, MAX_PIXELS);
  FastLED.addLeds<LED_TYPE,  4, COLOR_ORDER>(leds, MAX_PIXELS);
  FastLED.addLeds<LED_TYPE,  5, COLOR_ORDER>(leds, MAX_PIXELS);
  FastLED.addLeds<LED_TYPE,  6, COLOR_ORDER>(leds, MAX_PIXELS);
  FastLED.addLeds<LED_TYPE,  7, COLOR_ORDER>(leds, MAX_PIXELS);
  FastLED.addLeds<LED_TYPE,  8, COLOR_ORDER>(leds, MAX_PIXELS);
  FastLED.addLeds<LED_TYPE,  9, COLOR_ORDER>(leds, MAX_PIXELS);
  FastLED.addLeds<LED_TYPE, 10, COLOR_ORDER>(leds, MAX_PIXELS);
  FastLED.addLeds<LED_TYPE, 11, COLOR_ORDER>(leds, MAX_PIXELS);
  FastLED.addLeds<LED_TYPE, 12, COLOR_ORDER>(leds, MAX_PIXELS);
  FastLED.addLeds<LED_TYPE, 13, COLOR_ORDER>(leds, MAX_PIXELS);
#else
  FastLED.addLeds<LED_TYPE,  5, COLOR_ORDER>(leds, MAX_PIXELS);
  FastLED.addLeds<LED_TYPE,  6, COLOR_ORDER>(leds, MAX_PIXELS);
#endif

  // No global power cap here on purpose. The controller sketch dims to
  // protect the supply; a probe that silently dims would have us chasing
  // a brightness bug that does not exist. Keep the test colours modest
  // instead — nothing here asks for full white on a long run.
  FastLED.setBrightness(150);

  blankAll();
  Serial.println(F("PROBE ready — fw probe/1.0"));
  identify();
}

int8_t controllerFor(uint8_t pin) {
  for (uint8_t i = 0; i < NUM_PINS; i++)
    if (PINS[i] == pin) return (int8_t)i;
  return -1;
}

// Push the current buffer out exactly one pin.
void showOn(uint8_t idx) {
  FastLED[idx].showLeds(FastLED.getBrightness());
}

// Every pin gets a full frame of black. A WS2812 latches its last frame,
// so a strip we stop driving keeps showing whatever it had — without this
// the previous test's pattern stays lit and every answer is a lie.
void blankAll() {
  // Bufferless, and deliberately longer than MAX_PIXELS: a run longer than
  // the buffer would otherwise keep holding its old frame past pixel 180,
  // and that stale tail reads as "the strip is broken" in every test after.
  for (uint8_t i = 0; i < NUM_PINS; i++)
    FastLED[i].showColor(CRGB::Black, 1000, 255);
}

// Pack `npx` RGBW pixels of one colour into the CRGB array and return how
// many CRGB slots that used. Three RGBW pixels occupy exactly four slots,
// so a CRGB buffer can carry an exact RGBW stream with no remainder.
//
// The controllers are GRB, so bytes leave as (struct.g, struct.r, struct.b)
// while the struct holds (r, g, b). Swapping the first two bytes of every
// triple after packing cancels that reorder exactly.
uint16_t packRgbw(uint16_t npx, uint8_t R, uint8_t G, uint8_t B, uint8_t W,
                  uint8_t ord) {
  uint8_t ch[4];
  switch (ord) {
    case 0:  ch[0]=G; ch[1]=R; ch[2]=B; ch[3]=W; break;   // GRBW
    case 2:  ch[0]=B; ch[1]=G; ch[2]=R; ch[3]=W; break;   // BGRW
    case 3:  ch[0]=G; ch[1]=B; ch[2]=R; ch[3]=W; break;   // GBRW
    default: ch[0]=R; ch[1]=G; ch[2]=B; ch[3]=W; break;   // RGBW
  }
  uint16_t ncrgb = ((uint32_t)npx * 4 + 2) / 3;
  if (ncrgb > MAX_PIXELS) ncrgb = MAX_PIXELS;
  uint16_t nbytes = ncrgb * 3;

  uint8_t *raw = (uint8_t *)leds;
  for (uint16_t i = 0; i < nbytes; i++) raw[i] = ch[i & 3];
  for (uint16_t k = 0; k + 1 < nbytes; k += 3) {
    uint8_t t = raw[k]; raw[k] = raw[k + 1]; raw[k + 1] = t;
  }
  return ncrgb;
}

void identify() {
  Serial.print(F("pins:"));
  for (uint8_t i = 0; i < NUM_PINS; i++) { Serial.print(' '); Serial.print(PINS[i]); }
  Serial.println();
  Serial.print(F("max_pixels: ")); Serial.println(MAX_PIXELS);
  Serial.print(F("color_order: ")); Serial.println(F("GRB"));
}

long args[5];
uint8_t nargs;

// Parse up to five integers out of the rest of the line.
void parseArgs(char *s) {
  nargs = 0;
  char *tok = strtok(s, " \t");
  while (tok && nargs < 5) { args[nargs++] = atol(tok); tok = strtok(NULL, " \t"); }
}

void handle(char *line) {
  while (*line == ' ') line++;
  char cmd = *line;
  if (!cmd) return;
  parseArgs(line + 1);

  if (cmd == '?') { identify(); return; }

  if (cmd == 'o') { blankAll(); Serial.println(F("ok off")); return; }

  if (cmd == 'P') {
    // Light exactly ONE pixel bright, the rest dim. Counting marks brackets
    // a length; this pins it. If the bright pixel sits at the very end of
    // the run, that index is the last pixel and the length is idx+1.
    if (nargs < 3) { Serial.println(F("err: P <pin> <n> <idx>")); return; }
    int8_t si = controllerFor((uint8_t)args[0]);
    if (si < 0) { Serial.println(F("err: bad pin")); return; }

    uint16_t npx = (uint16_t)args[1];
    uint16_t idx = (uint16_t)args[2];
    if (npx > RGBW_MAX_PX) npx = RGBW_MAX_PX;
    uint16_t ncrgb = ((uint32_t)npx * 4 + 2) / 3;
    uint16_t nbytes = ncrgb * 3;

    uint8_t *raw = (uint8_t *)leds;
    for (uint16_t i = 0; i < nbytes; i++) raw[i] = 0;
    for (uint16_t i = 0; i < npx; i++) {
      uint16_t o = (uint16_t)i * 4;
      if (i == idx) { raw[o] = 0; raw[o+1] = 0; raw[o+2] = 255; }   // blue
      else          { raw[o] = 110; raw[o+1] = 0; raw[o+2] = 0; }   // red fill
                                                                     // bright
                                                                     // enough
                                                                     // to see
                                                                     // where
                                                                     // it ends
      raw[o + 3] = 0;
    }
    for (uint16_t k = 0; k + 1 < nbytes; k += 3) {
      uint8_t t = raw[k]; raw[k] = raw[k + 1]; raw[k + 1] = t;
    }

    for (uint8_t k = 0; k < NUM_PINS; k++)
      FastLED[k].showColor(CRGB::Black, 1000, 255);
    FastLED[si].setDither(DISABLE_DITHER);
    FastLED[si].showLeds(255);

    Serial.print(F("ok mark pin=")); Serial.print(args[0]);
    Serial.print(F(" idx=")); Serial.print(idx);
    Serial.print(F(" of ")); Serial.println(npx);
    return;
  }

  if (cmd == 'M') {
    // RGBW ruler. Counting lit pixels by eye fails somewhere past thirty;
    // counting marks and a remainder does not. GREEN every 50, BLUE every
    // 10, dim red between, and the run simply stops where the strip ends.
    if (nargs < 1) { Serial.println(F("err: M <pin> [n]")); return; }
    int8_t si = controllerFor((uint8_t)args[0]);
    if (si < 0) { Serial.println(F("err: bad pin")); return; }

    uint16_t npx = (nargs >= 2) ? (uint16_t)args[1] : RGBW_MAX_PX;
    if (npx > RGBW_MAX_PX) npx = RGBW_MAX_PX;
    uint16_t ncrgb = ((uint32_t)npx * 4 + 2) / 3;
    uint16_t nbytes = ncrgb * 3;

    uint8_t *raw = (uint8_t *)leds;
    for (uint16_t i = 0; i < nbytes; i++) raw[i] = 0;
    for (uint16_t i = 0; i < npx; i++) {
      uint8_t R, G, B;
      if      (i % 50 == 0) { R = 0;  G = 255; B = 0;   }   // every 50
      else if (i % 10 == 0) { R = 0;  G = 0;   B = 255; }   // every 10
      else                  { R = 40; G = 0;   B = 0;   }   // between
      uint16_t o = (uint16_t)i * 4;                          // RGBW order
      raw[o] = R; raw[o + 1] = G; raw[o + 2] = B; raw[o + 3] = 0;
    }
    for (uint16_t k = 0; k + 1 < nbytes; k += 3) {           // undo GRB
      uint8_t t = raw[k]; raw[k] = raw[k + 1]; raw[k + 1] = t;
    }

    for (uint8_t k = 0; k < NUM_PINS; k++)
      FastLED[k].showColor(CRGB::Black, 1000, 255);
    FastLED[si].setDither(DISABLE_DITHER);
    FastLED[si].showLeds(255);

    Serial.print(F("ok rgbw-ruler pin=")); Serial.print(args[0]);
    Serial.print(F(" px=")); Serial.println(npx);
    return;
  }

  if (cmd == 'I') {
    // RGBW identify. Same latching idea as 'i', but speaking 4-byte pixels
    // so the marks are a real colour instead of misaligned garbage.
    // Blanking first is safe in any pixel width: zero bytes are zero at
    // every alignment, which is the whole reason black always worked.
    for (uint8_t k = 0; k < NUM_PINS; k++)
      FastLED[k].showColor(CRGB::Black, 1000, 255);
    for (uint8_t k = 0; k < NUM_PINS; k++) {
      uint16_t ncrgb = packRgbw(PINS[k], 255, 0, 0, 0, 1);
      FastLED[k].setDither(DISABLE_DITHER);
      FastLED[k].showLeds(255);
      (void)ncrgb;
    }
    Serial.println(F("ok rgbw-identify: lit pixels per run == its pin number"));
    return;
  }

  if (cmd == 'W') {
    // Pack an RGBW wire stream into the RGB buffer.
    //
    // The strip wants 4 bytes per pixel (GRBW). FastLED wants to send 3.
    // Twelve bytes is the common multiple: 3 RGBW pixels occupy exactly 4
    // CRGB slots, so a buffer of CRGBs can carry an exact RGBW stream with
    // no remainder and no second allocation.
    //
    // One wrinkle: the controller reorders every CRGB by COLOR_ORDER on the
    // way out. These controllers are GRB, so the bytes leaving are
    // (struct.g, struct.r, struct.b) while the struct holds (r, g, b) in
    // memory. Writing the stream straight down and then swapping the first
    // two bytes of each triple cancels that reorder exactly.
    if (nargs < 5) { Serial.println(F("err: W <pin> <n> <r> <g> <b> [w]")); return; }
    int8_t si = controllerFor((uint8_t)args[0]);
    if (si < 0) { Serial.println(F("err: bad pin")); return; }

    uint16_t npx = (uint16_t)args[1];
    uint8_t  R = (uint8_t)args[2], G = (uint8_t)args[3], B = (uint8_t)args[4];
    uint8_t  W = (nargs >= 6) ? (uint8_t)args[5] : 0;

    uint16_t ncrgb;
    if (((uint32_t)npx * 4 + 2) / 3 > MAX_PIXELS) npx = ((uint32_t)MAX_PIXELS * 3) / 4;

    uint8_t ord = (nargs >= 7) ? (uint8_t)args[6] : 1;   // default RGBW
    ncrgb = packRgbw(npx, R, G, B, W, ord);

    // Full brightness, no dither: any scaling would corrupt bytes that are
    // pixel data to the strip but arbitrary numbers to FastLED.
    for (uint8_t k = 0; k < NUM_PINS; k++)
      FastLED[k].showColor(CRGB::Black, 1000, 255);
    FastLED[si].setDither(DISABLE_DITHER);
    FastLED[si].showLeds(255);

    Serial.print(F("ok rgbw-raw pin=")); Serial.print(args[0]);
    Serial.print(F(" px=")); Serial.print(npx);
    Serial.print(F(" slots=")); Serial.print(ncrgb);
    Serial.print(F(" order=")); Serial.println(ord);
    return;
  }

  if (cmd == 'x') {
    bool on = (nargs >= 1) ? (args[0] != 0) : true;
    for (uint8_t k = 0; k < NUM_PINS; k++) {
      if (on) FastLED[k].setRgbw(RgbwDefault::value());
      else    FastLED[k].setRgbw(RgbwInvalid::value());
    }
    Serial.print(F("ok rgbw=")); Serial.println(on ? F("ON (4 bytes/px)")
                                                  : F("OFF (3 bytes/px)"));
    return;
  }

  if (cmd == 'F') {
    // Exactly one pin driven, everything else blanked and then idle low.
    if (nargs < 2) { Serial.println(F("err: F <pin> <n> [r g b]")); return; }
    int8_t si = controllerFor((uint8_t)args[0]);
    if (si < 0) { Serial.println(F("err: bad pin")); return; }
    uint16_t n = (uint16_t)args[1];
    CRGB c = (nargs >= 5) ? CRGB((uint8_t)args[2], (uint8_t)args[3], (uint8_t)args[4])
                          : CRGB(255, 0, 0);
    for (uint8_t k = 0; k < NUM_PINS; k++)
      FastLED[k].showColor(CRGB::Black, n, 255);
    FastLED[si].showColor(c, n, 255);
    Serial.print(F("ok solo pin=")); Serial.print(args[0]);
    Serial.print(F(" n=")); Serial.print(n);
    Serial.print(F(" rgb=")); Serial.print(c.r); Serial.print(',');
    Serial.print(c.g); Serial.print(','); Serial.println(c.b);
    return;
  }

  if (cmd == 'f') {
    // Solid colour needs no buffer. showColor() hands the driver ONE CRGB
    // and a repeat count, so the length it can light is bounded by time,
    // not by SRAM — 1000 px is 3000 bytes we do not have, but it is only
    // 30 ms on the wire. Anything past the physical end of a run simply
    // falls off the end of the chain and costs nothing.
    uint16_t n = (nargs >= 1) ? (uint16_t)args[0] : 1000;
    CRGB c = (nargs >= 4) ? CRGB((uint8_t)args[1], (uint8_t)args[2], (uint8_t)args[3])
                          : CRGB(255, 0, 0);
    for (uint8_t k = 0; k < NUM_PINS; k++)
      FastLED[k].showColor(c, n, 255);
    Serial.print(F("ok flood n=")); Serial.print(n);
    Serial.print(F(" rgb=")); Serial.print(c.r); Serial.print(',');
    Serial.print(c.g); Serial.print(','); Serial.print(c.b);
    Serial.println(F(" on all pins"));
    return;
  }

  if (cmd == 'i') {
    // The whole point: a WS2812 latches its last frame, so a strip we stop
    // driving keeps showing it. Walk every pin once and each run is left
    // holding its own count — all twelve visible simultaneously, forever,
    // with no timing to miss. A timed sweep asks the operator to watch a
    // terminal and a pit at the same time; this asks them to count.
    for (uint8_t k = 0; k < NUM_PINS; k++) {
      fill_solid(leds, MAX_PIXELS, CRGB::Black);
      for (uint8_t j = 0; j < PINS[k]; j++) leds[j] = CRGB(160, 160, 160);
      leds[0] = CRGB(255, 0, 0);        // marks pixel 0 — the wired end
      showOn(k);
    }
    Serial.println(F("ok identify: lit pixels per run == its pin number"));
    Serial.println(F("            (pixel 0 is RED, counts toward the total)"));
    return;
  }

  int8_t idx = (nargs >= 1) ? controllerFor((uint8_t)args[0]) : -1;
  if (idx < 0) { Serial.println(F("err: bad pin")); return; }

  uint16_t n = (nargs >= 2) ? (uint16_t)args[1] : MAX_PIXELS;
  if (n > MAX_PIXELS) n = MAX_PIXELS;

  blankAll();

  switch (cmd) {
    case 's': {
      CRGB c = (nargs >= 5) ? CRGB((uint8_t)args[2], (uint8_t)args[3], (uint8_t)args[4])
                            : CRGB(255, 255, 255);
      fill_solid(leds, n, c);
      showOn(idx);
      Serial.print(F("ok solid pin=")); Serial.print(args[0]);
      Serial.print(F(" n=")); Serial.println(n);
      break;
    }
    case 'm': {
      // A ruler you can read off the strip: white every 10, red every 50.
      // Counting lit pixels by eye fails past about thirty; counting ten
      // marks and a remainder does not.
      for (uint16_t i = 0; i < n; i++) {
        if (i % 50 == 0)      leds[i] = CRGB(255, 0, 0);
        else if (i % 10 == 0) leds[i] = CRGB(255, 255, 255);
        else                  leds[i] = CRGB(0, 0, 24);
      }
      showOn(idx);
      Serial.print(F("ok ruler pin=")); Serial.print(args[0]);
      Serial.print(F(" n=")); Serial.println(n);
      break;
    }
    case 'c': {
      // If px0 is not RED, COLOR_ORDER is wrong for this strip.
      fill_solid(leds, n, CRGB(0, 0, 16));
      if (n > 0) leds[0] = CRGB(255, 0, 0);
      if (n > 1) leds[1] = CRGB(0, 255, 0);
      if (n > 2) leds[2] = CRGB(0, 0, 255);
      showOn(idx);
      Serial.println(F("ok colorcheck: px0=RED px1=GREEN px2=BLUE"));
      break;
    }
    case 'w': {
      uint16_t ms = (nargs >= 3) ? (uint16_t)args[2] : 40;
      for (uint16_t i = 0; i < n; i++) {
        fill_solid(leds, MAX_PIXELS, CRGB::Black);
        leds[i] = CRGB(255, 255, 255);
        showOn(idx);
        // Report as we go, so the last index printed before the dot stops
        // moving is the real length of the run.
        if (i % 10 == 0) { Serial.print(F("at ")); Serial.println(i); }
        delay(ms);
      }
      Serial.println(F("ok wipe done"));
      break;
    }
    default:
      Serial.println(F("err: unknown cmd"));
  }
}

char buf[48];
uint8_t blen = 0;

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (blen) { buf[blen] = 0; handle(buf); blen = 0; }
    } else if (blen < sizeof(buf) - 1) {
      buf[blen++] = c;
    } else {
      blen = 0;
    }
  }
}
