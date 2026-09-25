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
// MEASURED against the real pit on 2026-09-03 with firmware/pit_probe.
// Nothing here is a guess any more; if you change the hardware, re-measure
// with the probe rather than editing these by eye.
#define CENTER_PIN   5
#define SIDES_PIN    6

// LEFT AND RIGHT ARE ONE ELECTRICAL CHANNEL.
// A Y-split off pin 6 feeds both runs, so whatever we send appears on both
// simultaneously and they can never show different content. That is not a
// limitation to work around — it is what the centre-out animations want
// anyway, since a left pixel and its mirrored right pixel are the same
// distance from the middle of the pit and should be the same colour.
#define CENTER_COUNT 93         // measured 91-93; 93 overshoots harmlessly
#define SIDES_COUNT  76         // each side; both driven by the one channel

// ── The strips are RGBW, not RGB ────────────────────────────────────────
// Four bytes per pixel, channel order R,G,B,W. This is the single fact that
// broke everything before it was found: driving an RGBW strip with 3-byte
// pixels makes our groups slide against theirs, realigning only every 12
// bytes, so a solid colour comes back as a 3-pixel repeating pattern.
//
// Black is the one thing that still works when you get this wrong, because
// zero bytes are zero at any alignment — which is exactly why it reads as
// "the strip is dead or half-broken" instead of "wrong pixel format".
//
// FastLED's own setRgbw() is NOT usable here: it allocates a 4/3-size buffer
// on every show, and an ATmega328P has no room for that. We pack the bytes
// ourselves into `wire` below instead, which costs one fixed buffer.
// ── Channel gain (colour correction) ────────────────────────────────────
// On these RGBW strips the green and blue dies are noticeably brighter than
// the red one, so a colour with only a little green and blue in it comes out
// far more desaturated than the same numbers would on an RGB strip. The team
// red #C82027 is (200,32,39) — 13% green, 15% blue — and it rendered PINK
// while pure (200,0,0) rendered correctly. That is the whole symptom.
//
// This is what FastLED's setCorrection() does for RGB strips, and it is why
// this file cannot use it: correction rescales the frame on its way out, and
// our frame is a packed RGBW stream FastLED does not know it is carrying. So
// the gain is applied during packing instead, before the bytes are laid down.
//
// Correct the STRIP here; never fix this by editing the brand colour in the
// app. #C82027 is the team's red and has to stay that value everywhere else.
// NEUTRAL (all 255) on purpose. Gain correction was tried and abandoned:
// the app now snaps colours to saturated primaries before they reach the
// wire (see app/leds/palette.py), so almost nothing arrives as a mix that
// could wash out — and dimming green and blue would then just make a green
// or blue pit darker than a red one for no gain.
//
// Left in place, and left documented, because it is the right lever if a
// future strip batch has a genuinely skewed die. Re-tune it only against
// the real hardware, never by eye on a screen.
#define GAIN_R 255
#define GAIN_G 255
#define GAIN_B 255

// Pull min(r,g,b) into the white LED? OFF, deliberately.
//
// It gives better whites and draws less current, but it desaturates every
// mixed colour — and the brand red #C82027 is (200,32,39), so a third of its
// blue and green would move to the W LED and the pit's red reads pink. The
// one colour this installation must get right is the one it damages most.
//
// Turn it back on only if the pit starts showing mostly whites and greys.
#define RGBW_EXTRACT_WHITE 0

// Timing only; the byte layout is ours. These are SK6812 strips, whose spec
// is T0H 300 / T1H 600 / T1L 600 ns, each ±150.
//
// NEITHER BUILT-IN FASTLED TIMING IS IN SPEC ON AVR (FastLED 3.10.5, read
// from the library, fw 2.9). FastLED's AVR driver rounds its nanosecond table
// to whole 62.5 ns cycles:
//   WS2812B  250 / 625 / 375  ->  '1' = 875 high, 375 low
//   SK6812   300 / 600 / 300  ->  '1' = 937 high, 312 low   (fw 2.7, 2.8)
// Both hold a '1' high too long and low too short (spec min 450 ns). fw 2.7
// switched to FastLED's SK6812 believing it was 375 / 750 / 1250 — that is
// the unused legacy table in led_timing_legacy_avr.h — so it made the low
// shorter, not longer. `SK6812Spec` below is in spec: '0' = 375 high / 875
// low, '1' = 750 high / 500 low, 1250 ns a bit.
//
// Measured 2026-09-24 (tools/led_diag.py --flash-test): steady white, nothing
// changing, ~10 identical redraws a second flickered; the same look drawn
// once did not. Every write of a frame is a chance for the strip to misread
// it, which is why the switch positions (one write per 10 s) never showed it.
struct TimingSK6812Spec {
  enum : uint32_t { T1 = 375, T2 = 375, T3 = 500, RESET = 80 };
};
template <fl::u8 DATA_PIN, fl::EOrder RGB_ORDER>
class SK6812Spec
    : public fl::ClocklessControllerImpl<DATA_PIN, TimingSK6812Spec, RGB_ORDER> {};
#define LED_TYPE     SK6812Spec
// RGB, not GRB: the controllers emit our packed bytes verbatim and the
// channel order is applied during packing. Setting GRB here would reorder
// bytes that are pixel data to the strip but arbitrary numbers to FastLED.
#define COLOR_ORDER  RGB

// What the strips are actually plugged into, in milliamps. FastLED dims
// globally rather than browning out the supply. Three strips draw three
// times what one did — raise this to match the real PSU.
#define PSU_MILLIAMPS 2000      // informational only — see the note in setup()

// Hard ceiling on brightness, the one power lever left now that FastLED's
// own current limiter cannot be used on a packed RGBW stream. 169 pixels at
// full white is roughly 10 A; this keeps it to something a bench supply and
// the pit wiring can actually deliver.
#define MAX_BRIGHTNESS 200

// ── Centre power budget (fw 2.7) ───────────────────────────────────────
// The centre run is the longest (93 px, fed from one end), so a supply sag
// shows there first: violet at 180 drew ~1.7x the white work light, and with
// the audio amplifier's bass pulling on the same supply it flickered. Every
// centre frame is now held to the white work light's own draw — measured as
// the sum of all four channels after brightness, since current tracks that —
// and scaled down, colour kept, when it would exceed it. White at 160 sits
// exactly on the budget and is never touched. The sides are not regulated
// yet; add a SIDES budget the same way when they need one.
#define CENTER_POWER_BUDGET ((uint32_t)CENTER_COUNT * 160)
// The sides channel carries 76 px of data but feeds two strips (the Y-split),
// so its current is twice what its bytes suggest; its budget is in the same
// data units. Off (0 %) by default: sides regulation is opt-in via SET_CAP.
#define SIDES_POWER_BUDGET  ((uint32_t)SIDES_COUNT * 160)

// ── The three-way switch ──────────────────────────────────────────────
// A panel switch on the front of the pit, in reach of anyone standing at it.
// Three positions, and the middle one is the normal one:
//
//      UP      full WHITE   — work light. Somebody is under the robot.
//      CENTRE  the app      — what the pit runs on all day.
//      DOWN    full RED     — team colour with no host, no laptop, no app.
//
// Both overrides are hard: they ignore the host, the saved state and the
// current mode, and they need nothing running on the other end of the USB
// cable. That is the point of a physical switch — it is the control that
// still works when the interesting failure has already happened.
//
// There is deliberately no manual OFF. Dark is what a pit looks like when
// something is broken, and a switch position that is indistinguishable from
// a dead board costs an hour at an event. The app's kill switch is still
// there for turning the strips off on purpose.
//
// ── WHAT THE PIT ACTUALLY HAS (measured 2026-09-08, firmware/pit_switch_probe)
//
// An SPDT on-off-on, three wires, landing on an INPUT SHIELD rather than on
// the Uno's own header. Everything below is measurement, not documentation —
// the documentation was wrong about all of it.
//
//   blue   throw, closes in DOWN.  Reads on ARDUINO D2.
//   green  throw, closes in UP.    Reads on ARDUINO D7.
//   red    common.
//
// THE SHIELD'S LABELS ARE OFFSET. Blue sits in the socket marked 3 and comes
// out on Arduino D2. Never read a pin number off that shield and type it in
// here — probe it. That offset is the whole reason the original wiring notes
// looked plausible and were unusable.
//
// D7 CARRIES THE GREEN THROW, and the history matters. On 2026-09-08 this
// pin was measured stuck at 0V in every switch position — something on the
// shield beat the internal pull-up — so green was invisible all day and D7
// was written off. Re-probed after rewiring: it now floats HIGH when open
// and pulls cleanly LOW on the throw, in all three positions. The pin was
// never the fault; the connection into it was.
//
// The lesson is the probe, not the pin. A throw that does not respond is a
// measurement to take (firmware/pit_switch_probe), never a pin to condemn
// from notes — these notes were wrong about this exact pin for a session.
//
// D5 and D6 are the LED data lines and remain unavailable. D0/D1 are the
// serial port. Every other pin probed floats clean and will work.
#define HAS_MANUAL_SWITCH 1

// Which way round a closed throw reads.
//
//   1  common on GND. The internal pull-up holds the pin at 5V and a closed
//      throw drags it to 0V. CLOSED = LOW. Needs no external parts, and it
//      is what this pit measured good on.
//   0  common on +5V. The shield's pull-down resistors hold the pin at 0V
//      and a closed throw drives it to 5V. CLOSED = HIGH. The internal
//      pull-ups must stay off or they fight the shield and every pin reads
//      HIGH forever.
//
// Get this wrong and the switch does not fail loudly — it reads as stuck in
// whichever position the wrong bias implies, which looks exactly like a
// switch nobody wired.
#define SW_ACTIVE_LOW 1

// The two throws, by what they DO rather than by which terminal they are on.
// If white and red come out the wrong way round, swap these two numbers —
// which throw is physically up is a mounting fact, not an electrical one,
// and no probe can tell you.
//
// 255 means "this throw is not on a readable pin yet". That is a supported
// state, not a broken one: with one throw landed the switch still gives two
// positions (its own, and everything else = the app), and the pit is usable
// while somebody finds a socket for the other wire. Fill in the number and
// the third position appears with no other change.
#define SW_PIN_UP    7         // green — WHITE. Measured: Arduino D7.
#define SW_PIN_DOWN  2         // blue  — RED. Measured: Arduino D2.

#define SW_NO_PIN    255

// The manual positions are hard overrides, so they read nothing from the
// host or from EEPROM.
//
// Pure red, not the brand hex. On these RGBW strips (200,32,39) renders
// pink — same reason the app snaps to primaries before sending them.
#define OVERRIDE_R   255
#define OVERRIDE_G   0
#define OVERRIDE_B   0
#define OVERRIDE_BRIGHTNESS 200

// White comes from the strip's dedicated W die, not from firing R+G+B
// together. On an SK6812-RGBW the mixed white is tinted and costs three
// times the current for the same apparent brightness; the W LED is what
// the fourth wire is for. See packAndShow(), which takes the W byte.
//
// POWER. This is the brightest thing the pit can be asked to do, and the
// only one where every pixel is lit at once. 169 px at ~20 mA of W is
// roughly 3.4 A at full scale, so this figure is the throttle:
//
//      255  ≈ 3.4 A     needs a supply nobody has plugged in yet
//      200  ≈ 2.7 A     matches the red override
//      160  ≈ 2.1 A     what a 2 A brick will actually deliver
//
// PSU_MILLIAMPS above says 2000, so 160 is the honest default. Raise it
// when the real supply is known — a browning-out strip flickers and
// resets, which reads as a broken run rather than a power problem.
#define OVERRIDE_WHITE_W          255
#define OVERRIDE_WHITE_BRIGHTNESS 160

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

#define NUM_STRIPS 2
#define TOTAL_LEDS (CENTER_COUNT + SIDES_COUNT)

// Segment ids on the wire: SET_COLOR's first payload byte. 0xFF means all.
// Two, not three, because left and right are one channel — see the Y-split
// note above. INFO reports two segments and the host reads the count.
enum : uint8_t { SEG_CENTER = 0, SEG_SIDES = 1 };

// How far the innermost side pixel sits from the pit's centre point, in
// pixel-widths. Measured from the centre point itself (pixel 0 of the centre
// run), so it is a real distance now rather than an offset from a strip edge.
// Only affects how far an animation has travelled when it crosses from one
// run to the other; nothing breaks if it is a little off.
#define SIDES_GAP 20

// Which end of a side run its pixel 0 sits at. The two sides are mirrored by
// the Y-split, so index 0 lands at the same distance from the pit centre on
// both — provided they are wired as mirror images of each other.
//
// MEASURED: 0. Pixel 0 is at the INNER end, nearest the pit centre. Verified
// with a chase, which is the only effect that shows this: a breathe or a
// solid looks identical either way, so the earlier "breathe looks fine" said
// nothing about it. With this wrong, the sides ran outside-in while the
// centre ran middle-out — the two halves of the pit disagreeing is the tell.
#define SIDES_INDEX0_OUTER 0

// The sides are addressed by DISTANCE from the pit centre, not by a signed
// position: one channel drives both, so a pixel is at +d and -d at once.
// Giving the run a positive origin and dir -1 walks it inward from the far
// end, which is all axisDist() needs and keeps the existing maths unchanged.
#define SIDES_INNER_POS (SIDES_GAP)
#define SIDES_OUTER_POS (SIDES_GAP + SIDES_COUNT - 1)

const Strip strips[NUM_STRIPS] = {
  // start,          count,         origin,                dir
  // CENTRE: origin 0, not -(count/2). The pit's centre point is this run's
  // PIXEL 0 (the back end), not the middle of the strip — it runs away from
  // the centre of the pit rather than spanning it. With -(count/2) the chase
  // started halfway along and expanded both ways, which is right for a run
  // that straddles the middle and wrong for this one.
  { 0,               CENTER_COUNT,  0,                     +1 },
#if SIDES_INDEX0_OUTER
  { CENTER_COUNT,    SIDES_COUNT,   SIDES_OUTER_POS,       -1 },
#else
  { CENTER_COUNT,    SIDES_COUNT,   SIDES_INNER_POS,       +1 },
#endif
};

#define FW_MAJOR 2
#define FW_MINOR 14  // 2.14: back to 30 fps by default — 15 read as choppy
                     // 2.13: frame rate live-tunable with OP_SET_FPS; animation speed
                     //       follows the clock, not the frame count
                     // 2.12: every animation runs on the centre too — the 2.8 "centre holds
                     //       the white work light" rule is gone
                     // 2.11: ALERT flashes the sides on-board (the host no longer toggles
                     //       colours), centre steady; a mode change restarts the animation;
                     //       animated looks draw only on their frame tick
                     // 2.10: no-host look is the violet sparkle again, over the WHOLE pit,
                     //       centre included (EEPROM magic 0xC0)
                     // 2.9: in-spec SK6812 bit timing; a channel whose bytes have not
                     //      changed is not re-sent (only the 10 s refresh forces it)
                     // 2.8: in animated looks the centre holds a steady white work light;
                     //      only the sides animate (constant centre power)
                     // 2.7: redraw only when the picture changes; animations at 30 fps;
                     //      no-host look is a slow violet breathe (EEPROM magic 0xBF);
                     //      centre power budget; SK6812 bit timing (was WS2812B)
                     // 2.6: a command landing mid-write is drawn next frame, not dropped
                     // 2.5: OP_STATUS — link and timing telemetry (State unchanged, magic stays 0xBE)
                     // 2.4: no-host default is a violet sparkle (EEPROM magic 0xBE)
                     // 2.3: no-host default was a red chase (EEPROM magic 0xBD)
                     // 2.2: SET_COLOR takes an optional 5th byte, W, per segment
                     // 2.1: switch is WHITE / app / RED, no manual OFF

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
  OP_STATUS     = 0x17,   // host → device: report telemetry (fw 2.5+)
  OP_SET_CAP    = 0x18,   // centre %, sides % of the white-look draw (fw 2.7+); 0 = off
  OP_SET_FPS    = 0x19,   // animation frames per second, 1..30 (fw 2.13+); RAM only
  OP_INFO       = 0x80,
  OP_LOG        = 0x81,
  OP_STATUS_REPLY = 0x82   // device → host: the telemetry, see sendStatus()
};

enum : uint8_t {
  MODE_SOLID = 0, MODE_BREATHE, MODE_WIPE, MODE_CHASE,
  MODE_SPARKLE, MODE_RAINBOW, MODE_ALERT, MODE_OFF
};

static const uint16_t WATCHDOG_MS = 5000;
// Animation frames per second. Every frame is one write of both strip runs,
// and each write is a chance for the data line to garble it (a one-frame band
// of white / green / red — see CLAUDE.md), so fewer frames means fewer
// glitches but choppier motion. 15 was tried in 2.13 and read as choppy; 30
// is smooth. OP_SET_FPS changes it live (RAM only)
// so the effect of the write rate can be judged by eye; a reboot returns
// here. Animation SPEED does not depend on it: the phase advances by the
// time elapsed (see loop()), so a lower rate means bigger steps, not slower.
static const uint8_t  ANIM_FPS_DEFAULT = 30;
static const uint8_t  ANIM_FPS_MAX     = 30;
// A static picture is redrawn at least this often, to heal a glitched strip.
static const uint16_t REFRESH_MS    = 10000;
static const uint8_t  RX_MAX      = 200;
static const uint8_t  ALL_SEGMENTS = 0xFF;

// Bumped from 0xB9 when State grew per-strip colour. An old saved struct
// read into the new layout would garble every field after `brightness`, so
// the magic has to move whenever State's shape does.
static const uint16_t EEPROM_MAGIC_ADDR = 0;
// 0xBA -> 0xBB when NUM_STRIPS went 3 -> 2 (the Y-split); 0xBB -> 0xBC when
// the per-segment W channel was added; 0xBC -> 0xBD -> 0xBE when the no-host
// default changed (red chase, then violet sparkle), so a look somebody had
// saved as the boot default is discarded rather than kept. An old struct
// read into a new layout garbles every field after `brightness` rather than
// failing, so this has to move whenever State's shape does — and whenever
// the default must win.
static const uint8_t  EEPROM_MAGIC = 0xC0;   // 0xC0 (2.10): discard a saved breathe default

CRGB leds[TOTAL_LEDS];

// The RGBW wire buffer. An RGBW pixel is 4 bytes and a CRGB slot carries 3,
// so three pixels occupy exactly four slots and a CRGB array can hold an
// exact RGBW stream with no remainder. Sized for the LONGEST channel only —
// the two are packed and shown one at a time, so they can share it.
#define SLOTS_FOR(px)  (((uint16_t)(px) * 4 + 2) / 3)
#define CENTER_SLOTS   SLOTS_FOR(CENTER_COUNT)
#define SIDES_SLOTS    SLOTS_FOR(SIDES_COUNT)
#define WIRE_SLOTS     (CENTER_SLOTS > SIDES_SLOTS ? CENTER_SLOTS : SIDES_SLOTS)

CRGB wire[WIRE_SLOTS];

struct State {
  uint8_t mode;
  uint8_t speed;
  uint8_t brightness;
  uint8_t r[NUM_STRIPS], g[NUM_STRIPS], b[NUM_STRIPS];
  // The fourth channel, per segment: the white die, driven by the host. The
  // queue alert wants the centre run steady white while the sides carry the
  // alliance colour, and white from R+G+B is tinted and three times the
  // current. 0 for every effect and every colour that is not white.
  uint8_t w[NUM_STRIPS];
};

// What the strips show with no host: at boot, and five seconds after the
// app stops talking. A violet *sparkle*, not a solid — a moving pattern says
// "the controller is alive and waiting", and violet is a colour nothing else
// in the pit uses, so it can never be mistaken for an alliance, an alert or
// the app having crashed mid-sequence. The app's own resting look (white on
// the W die) is pushed the moment it connects.
//
// It covers every pixel, the centre too — the one animated look exempt from
// "the centre never animates" (see loop()). Restored in 2.10 once 2.9 fixed
// the flicker that rule was working around.
State state = {
  MODE_SPARKLE, 150, 180,
  { 128, 128 }, { 0, 0 }, { 255, 255 },                // violet, see palette
  { 0, 0 },
};
State fallback = state;          // what the watchdog reverts to
bool  blanked = false;
// Set whenever anything that affects the output changes. A static mode draws
// one frame, clears this, and then stops touching the strips entirely.
bool  dirty = true;
unsigned long lastRx = 0;
bool  hostSeen = false;

// ── Telemetry (fw 2.5) ────────────────────────────────────────────────────
// Counted as it happens, sent on request (OP_STATUS → sendStatus()). Nothing
// here changes behaviour; it is what the host needs to see the link's
// response rate and latency from this side.
//
// **Timed on Timer1, never millis()/micros().** A strip write holds
// interrupts off for milliseconds, and Timer0's overflow interrupt — which is
// all millis() and micros() are — is lost for the duration, so they would
// under-report exactly the windows worth measuring. Timer1 is a hardware
// counter that keeps counting with interrupts off: prescaler 64 at 16 MHz is
// 4 µs a tick, and a 16-bit difference covers 262 ms. Nothing else in this
// sketch uses Timer1 (no Servo, no PWM on 9/10, FastLED's AVR clockless
// driver is cycle-counted).
struct Telemetry {
  uint16_t framesOk;         // CRC-good frames handled
  uint16_t crcErrors;        // decoded, CRC wrong -> NAK
  uint16_t decodeErrors;     // COBS decode failed (bytes lost mid-frame)
  uint16_t overruns;         // frame longer than RX_MAX before its 0x00
  uint16_t unknownOps;       // CRC-good, op not understood -> NAK
  uint16_t shows;            // showAll() calls
  uint16_t fallbacks;        // watchdog reverted to the saved default
  uint16_t showTicksLast;    // one whole showAll(), both channels
  uint16_t showTicksMax;
  uint16_t gapTicksMax;      // longest stretch between readSerial() calls
  uint16_t cmdTicksLast;     // command received -> end of the show that drew it
  uint16_t cmdTicksMax;
  uint32_t rxBytes;
} tele = {};
uint16_t lastReadTick = 0;
// Centre power cap (fw 2.7), see capCentrePower(): the last scale applied
// (255 = untouched) and how many frames needed it.
uint8_t  capScaleLast = 255;
uint16_t cappedFrames = 0;
// Live limits, in percent of each channel's white-look budget, set by
// OP_SET_CAP so a limit can be tuned by eye without reflashing. RAM only: a
// reboot returns to the compiled defaults (centre 100 %, sides off).
uint8_t  capPctCentre = 100;
uint8_t  capPctSides  = 0;
uint16_t cmdTick = 0;
bool     cmdPending = false;

// Declared exactly as FastLED declares them (char, C linkage): a second,
// different declaration is an LTO type mismatch the compiler warns may be
// misoptimized.
extern "C" char __heap_start;
extern "C" char *__brkval;
static int freeRam() {
  char top;
  return (int)(&top - (__brkval == 0 ? &__heap_start : __brkval));
}

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

// STATUS_REPLY, big-endian like INFO. Format 2 (fw 2.7) appends the centre
// power cap: capScale(1) 255 = untouched | cappedFrames(2)   = 42 bytes.
// Format 1 (fw 2.5, 2.6):
//   format(1)=1 | uptime_ms(4) | free_ram(2) | switch(1) 0=WHITE 1=HOST 2=RED |
//   mode(1) | brightness(1) | flags(1) bit0 blanked, bit1 hostSeen, bit2 dirty |
//   framesOk crcErrors decodeErrors overruns unknownOps shows fallbacks
//   showTicksLast showTicksMax gapTicksMax cmdTicksLast cmdTicksMax (2 each) |
//   rxBytes(4)                                           = 39 bytes
// Ticks are Timer1 at 4 µs. uptime_ms is millis(), which runs slow while
// strips are being written; it is for spotting a reboot, not for timing.
// Maxima reset when read, so each reply is the worst since the last one.
uint8_t switchPosition();
extern uint8_t swPos;
void sendStatus(uint8_t seq) {
  uint8_t p[42];
  uint8_t n = 0;
  unsigned long up = millis();
  p[n++] = 2;
  p[n++] = (up >> 24) & 0xFF; p[n++] = (up >> 16) & 0xFF;
  p[n++] = (up >> 8) & 0xFF;  p[n++] = up & 0xFF;
  int ram = freeRam();
  p[n++] = (ram >> 8) & 0xFF; p[n++] = ram & 0xFF;
  p[n++] = swPos;
  p[n++] = state.mode;
  p[n++] = state.brightness;
  p[n++] = (blanked ? 1 : 0) | (hostSeen ? 2 : 0) | (dirty ? 4 : 0);
  // Straight out of the struct, in declaration order — no stack copy: this
  // can run inside showAll()'s mid-write readSerial(), the deepest stack the
  // sketch has, with ~390 bytes of RAM to spare.
  const uint16_t *v = &tele.framesOk;
  for (uint8_t i = 0; i < 12; i++) {
    p[n++] = (v[i] >> 8) & 0xFF;
    p[n++] = v[i] & 0xFF;
  }
  p[n++] = (tele.rxBytes >> 24) & 0xFF; p[n++] = (tele.rxBytes >> 16) & 0xFF;
  p[n++] = (tele.rxBytes >> 8) & 0xFF;  p[n++] = tele.rxBytes & 0xFF;
  p[n++] = capScaleLast;
  p[n++] = (cappedFrames >> 8) & 0xFF;  p[n++] = cappedFrames & 0xFF;
  sendFrame(seq, OP_STATUS_REPLY, p, n);
  tele.showTicksMax = 0;
  tele.gapTicksMax = 0;
  tele.cmdTicksMax = 0;
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
// including WHITE and RED. The manual override suppresses the *output*, not
// the conversation: flick back to the middle and the pit is already showing
// what the app has been asking for, with no round trip and no stale frame.
extern uint8_t animStep, chasePos;
extern uint16_t animFrameMs;

void handleFrame(uint8_t *body, uint8_t len) {
  if (len < 3) return;
  uint8_t expected = crc8(body, len - 1);
  uint8_t seq = body[0];
  if (body[len - 1] != expected) {
    tele.crcErrors++;
    sendFrame(seq, OP_NAK, NULL, 0);
    return;
  }
  tele.framesOk++;

  uint8_t op = body[1];
  uint8_t *p = body + 2;
  uint8_t plen = len - 3;

  lastRx = millis();
  hostSeen = true;

  // A status request changes nothing on the strips, so it must not cost a
  // redraw — measuring the link must not load the link.
  if (op == OP_STATUS) {
    sendStatus(seq);
    return;
  }

  // Only a command that can change the picture costs a redraw. A PING used
  // to set `dirty` too — one identical frame a second, and ~8 ms each time
  // with the UART deaf. HELLO returns inside the switch; SAVE changes nothing
  // on the strips.
  const bool changesOutput = (op != OP_PING && op != OP_HELLO && op != OP_SAVE);
  if (changesOutput) {
    cmdTick = TCNT1;             // time this command until it is drawn
    cmdPending = true;
  }

  switch (op) {
    case OP_HELLO:
      sendInfo(seq);
      return;

    case OP_SET_MODE:
      if (plen >= 2) {
        // A new mode starts from its first frame: an alert opens lit, a wipe
        // opens from the centre. Re-sending the same mode changes nothing.
        if (p[0] != state.mode) { animStep = 0; chasePos = 0; }
        state.mode = p[0]; state.speed = p[1]; blanked = false;
      }
      break;

    case OP_SET_COLOR: {
      // p[0] selects the segment: SEG_CENTER / SEG_SIDES, or 0xFF for both.
      // p[4], when present (fw 2.2+), is the white die for that segment; a
      // four-byte payload from an older host means W = 0, which is what
      // every colour but white wants anyway.
      if (plen >= 4) {
        const uint8_t w = (plen >= 5) ? p[4] : 0;
        if (p[0] == ALL_SEGMENTS) {
          for (uint8_t s = 0; s < NUM_STRIPS; s++) {
            state.r[s] = p[1]; state.g[s] = p[2]; state.b[s] = p[3];
            state.w[s] = w;
          }
        } else if (p[0] < NUM_STRIPS) {
          state.r[p[0]] = p[1]; state.g[p[0]] = p[2]; state.b[p[0]] = p[3];
          state.w[p[0]] = w;
        }
      }
      break;
    }

    case OP_SET_BRIGHT:
      if (plen >= 1) {
        state.brightness = (p[0] > MAX_BRIGHTNESS) ? MAX_BRIGHTNESS : p[0];
        blanked = false;
      }
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

    case OP_SET_CAP:
      // p[0] centre %, p[1] sides % of the white-look draw; 0 = no limit.
      if (plen >= 2) {
        capPctCentre = p[0] > 100 ? 100 : p[0];
        capPctSides  = p[1] > 100 ? 100 : p[1];
      }
      break;

    case OP_SET_FPS:
      if (plen >= 1) {
        uint8_t fps = p[0] < 1 ? 1 : (p[0] > ANIM_FPS_MAX ? ANIM_FPS_MAX : p[0]);
        animFrameMs = 1000 / fps;
      }
      break;

    case OP_OFF:
      blanked = true;
      break;

    case OP_PING:
      break;

    default:
      tele.unknownOps++;
      sendFrame(seq, OP_NAK, NULL, 0);
      return;
  }
  if (changesOutput) dirty = true;
  sendFrame(seq, OP_ACK, NULL, 0);
}

void readSerial() {
  // The gap since the last call is the window in which inbound bytes can only
  // queue in the UART's 2-byte FIFO (plus the ring buffer, if interrupts were
  // on). Its maximum is the single best predictor of lost frames.
  uint16_t t = TCNT1;
  uint16_t gap = t - lastReadTick;
  if (gap > tele.gapTicksMax) tele.gapTicksMax = gap;
  lastReadTick = t;

  while (Serial.available()) {
    uint8_t c = Serial.read();
    tele.rxBytes++;
    if (c == 0x00) {
      if (rxLen > 0) {
        uint8_t n = cobsDecode(rxBuf, rxLen);
        if (n) handleFrame(rxBuf, n);
        else tele.decodeErrors++;
        rxLen = 0;
      }
    } else if (rxLen < RX_MAX) {
      rxBuf[rxLen++] = c;
    } else {
      rxLen = 0;                 // overrun: drop and resync on the next 0x00
      tele.overruns++;
    }
  }
}

// ─────────────────────────────────────────────────────────────────────────
//  The three-way switch
// ─────────────────────────────────────────────────────────────────────────
// Ordered as the switch is: up, middle, down. The value is never sent
// anywhere, so the order is purely for reading the code against the panel.
enum : uint8_t { SW_WHITE = 0, SW_HOST = 1, SW_RED = 2 };

static const uint8_t SW_DEBOUNCE_MS = 25;

uint8_t swPos     = SW_HOST;     // the position we are acting on
uint8_t swPending = SW_HOST;     // the position we are waiting to trust
unsigned long swSince = 0;

// One throw, read whichever way round this pit is wired. An unfitted throw
// (255) is never closed, which is what makes a half-landed switch degrade to
// two positions instead of failing.
static inline bool throwClosed(uint8_t pin) {
  if (pin == SW_NO_PIN) return false;
#if SW_ACTIVE_LOW
  return digitalRead(pin) == LOW;      // common on GND, internal pull-up
#else
  return digitalRead(pin) == HIGH;     // common on +5V, shield pull-down
#endif
}

// The whole truth table, in one place. See the wiring comment at the top.
uint8_t switchPosition() {
#if !HAS_MANUAL_SWITCH
  return SW_HOST;                      // no switch fitted — the app drives
                                       // the pit, always.
#else
  bool up   = throwClosed(SW_PIN_UP);
  bool down = throwClosed(SW_PIN_DOWN);

  // Both closed is impossible on an SPDT, so it means a wiring fault. Fall
  // into HOST rather than latching an override nobody asked for: the pit
  // keeps running the app, which is the state somebody can actually work in.
  if (up && down) return SW_HOST;

  if (up)   return SW_WHITE;
  if (down) return SW_RED;
  return SW_HOST;                      // centre: neither throw closed
#endif
}

// Toggles bounce for a few milliseconds. Without this a flick from one throw
// to the other fires HOST in between, and the pit blinks through whatever
// the app happens to be showing on its way past the middle.
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
    dirty = true;
    sendLog(swPos == SW_WHITE ? "switch=WHITE (manual override)"
          : swPos == SW_RED   ? "switch=RED (manual override)"
                              : "switch=HOST");
  }
}

// ─────────────────────────────────────────────────────────────────────────
//  Animations — every one of them measured from the true centre
// ─────────────────────────────────────────────────────────────────────────
uint8_t animStep = 0;
uint8_t chasePos = 0;
uint16_t animFrameMs = 1000 / ANIM_FPS_DEFAULT;
// How many 60-per-second ticks the current frame covers — the unit every
// effect's speed and fade was tuned in. Set by loop() each frame.
uint8_t animTicks = 1;

// The fade that `base` applied once per 60 fps tick amounts to over `ticks`
// of them: 1 - (1 - base/256)^ticks, so a tail lasts as long at any rate.
static uint8_t fadeOver(uint8_t base, uint8_t ticks) {
  uint16_t keep = 256;
  for (uint8_t i = 0; i < ticks; i++) keep = (keep * (256 - base)) >> 8;
  return keep >= 256 ? 0 : (uint8_t)(255 - keep);
}

// How far the effect spreads across the pit, in phase counts out of 255.
// 128 is half a cycle from centre to the far ends. Lower reads as a gentle
// bloom, higher as a travelling wave.
static const uint8_t BREATHE_SPREAD = 72;
static const uint8_t RAINBOW_SPREAD = 200;
static const uint8_t CHASE_WIDTH    = 14;   // half-width of the running dot,
                                            // in normalised axis units

void render() {
  // Modes with a persistence tail fade the buffer instead of rewriting it.
  // Tuned per 60 fps tick (chase fade 40; sparkle fade 24, a 60/256 chance of
  // a twinkle) and scaled to however many ticks this frame covers, so the
  // tail and the density look the same at any frame rate.
  if (state.mode == MODE_CHASE)
    fadeToBlackBy(leds, TOTAL_LEDS, fadeOver(40, animTicks));
  if (state.mode == MODE_SPARKLE) {
    fadeToBlackBy(leds, TOTAL_LEDS, fadeOver(24, animTicks));
    // No geometry: a twinkle is a twinkle wherever it lands.
    for (uint8_t t = 0; t < animTicks; t++) {
      if (random8() >= 60) continue;
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
          // The sides flash; the centre holds its colour (and the host's W,
          // see loop()) — the queue alert's look, run entirely on-board. Lit
          // first: SET_MODE restarts animStep at 0. At speed 32..63 the
          // phase moves 4 a frame, so each half is 8 frames, ~264 ms.
          px[i] = (s == SEG_CENTER || !(animStep & 0x20)) ? base : CRGB::Black;
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
//  RGBW output
// ─────────────────────────────────────────────────────────────────────────
//
// FastLED drives 3-byte pixels; these strips want 4. Rather than fight that,
// we hand FastLED a buffer of bytes it thinks are RGB pixels and the strip
// reads as RGBW ones. Three RGBW pixels fit exactly four CRGB slots, so the
// stream is exact — no padding, no second allocation, and the whole cost is
// one WIRE_SLOTS buffer instead of a per-show heap block.
//
// Brightness is applied HERE, not by FastLED: showLeds(255) is deliberate,
// because any scaling FastLED did would land on bytes that are pixel data to
// the strip but meaningless numbers to it.

void readSerial();          // defined below; showAll drains between writes

// Hold the centre run to CENTER_POWER_BUDGET: sum every channel byte that
// will go down the wire, and if the frame would draw more than the budget,
// scale every byte by budget/sum. One extra pass over ~370 bytes, before the
// write, with interrupts still on. Reported in STATUS as the last scale
// applied (255 = untouched) and how many frames needed it.
// Returns the scale applied (255 = untouched). `pct` 0 = no limit.
uint8_t capPower(uint8_t *raw, uint16_t nbytes, uint32_t budget100, uint8_t pct) {
  if (pct == 0) return 255;
  uint32_t budget = budget100 * pct / 100;
  uint32_t sum = 0;
  for (uint16_t i = 0; i < nbytes; i++) sum += raw[i];
  if (sum <= budget) return 255;
  uint8_t scale = (uint8_t)((budget * 256UL) / sum);
  for (uint16_t i = 0; i < nbytes; i++) raw[i] = scale8(raw[i], scale);
  return scale;
}

void capCentrePower(uint8_t *raw, uint16_t nbytes) {
  capScaleLast = capPower(raw, nbytes, CENTER_POWER_BUDGET, capPctCentre);
  if (capScaleLast != 255) cappedFrames++;
}

// ── Only write what changed (fw 2.9) ────────────────────────────────────
// Re-sending an identical frame is not free on these strips: each write is a
// chance to misread a bit, and a misread shows as a flash (the flash test's
// stage 3 flickered on identical redraws alone). So each channel remembers a
// checksum of the bytes it last sent and a redraw that would send the same
// bytes again is skipped. That covers every source of a redundant redraw at
// once — a repeated command, a host resend, the steady centre of an animated
// look — without each having to know. `forceShow` (boot, the 10 s refresh)
// writes regardless, so a strip that did glitch still heals.
//
// Fletcher-16: position-sensitive, ~370 bytes in well under a millisecond,
// interrupts on. A collision skips one real change; the next change or the
// refresh draws it.
uint16_t sentSum[NUM_STRIPS];
bool     sentValid[NUM_STRIPS] = {};
bool     forceShow = true;

static uint16_t wireSum(const uint8_t *raw, uint16_t n) {
  uint8_t a = 0, b = 0;
  for (uint16_t i = 0; i < n; i++) { a += raw[i]; b += a; }
  return ((uint16_t)b << 8) | a;
}

// `wByte` is the strip's fourth channel — the dedicated white die, which
// nothing in leds[] can express because a CRGB has only three components.
// Effects and host colours pass 0 and it costs them nothing; the manual
// WHITE override passes 255 and gets a real white rather than R+G+B fired
// together, which on these strips is tinted and three times the current.
// Returns whether the channel was actually written.
bool packAndShow(uint8_t ctrl, const CRGB *src, uint16_t npx,
                 uint8_t bright, uint8_t wByte) {
  uint16_t nbytes = (uint16_t)npx * 4;
  uint16_t nslots = (nbytes + 2) / 3;
  if (nslots > WIRE_SLOTS) { nslots = WIRE_SLOTS; nbytes = nslots * 3; }

  uint8_t *raw = (uint8_t *)wire;
  for (uint16_t i = 0; i < npx; i++) {
    // Brightness first, then the strip's channel gain. Both are plain
    // multiplies, so the order only matters for rounding — but doing gain
    // last keeps it a property of the hardware rather than of the level.
    uint8_t r = scale8(scale8(src[i].r, bright), GAIN_R);
    uint8_t g = scale8(scale8(src[i].g, bright), GAIN_G);
    uint8_t b = scale8(scale8(src[i].b, bright), GAIN_B);
    uint8_t w = 0;
#if RGBW_EXTRACT_WHITE
    // Whatever all three channels share is white light, and the dedicated
    // white LED makes it better and cheaper than mixing it from RGB.
    w = r < g ? (r < b ? r : b) : (g < b ? g : b);
    r -= w; g -= w; b -= w;
#endif
    // Brightness applies to W exactly as it does to the other three — it is
    // a channel on the same die package, not a separate level.
    const uint8_t wOverride = scale8(wByte, bright);
    if (wOverride > w) w = wOverride;
    uint16_t o = (uint16_t)i * 4;
    if (o + 3 >= nbytes) break;
    raw[o] = r; raw[o + 1] = g; raw[o + 2] = b; raw[o + 3] = w;   // RGBW
  }
  // The slack at the end of the last slot is not a pixel anyone owns; leaving
  // it dirty would clock a garbage pixel onto the end of the run.
  for (uint16_t i = (uint16_t)npx * 4; i < nslots * 3; i++) raw[i] = 0;

  if (ctrl == 0) capCentrePower(raw, (uint16_t)npx * 4);
  else capPower(raw, (uint16_t)npx * 4, SIDES_POWER_BUDGET, capPctSides);

  // Skip the write when this channel would get the bytes it already has.
  const uint16_t sum = wireSum(raw, nslots * 3);
  if (!forceShow && sentValid[ctrl] && sum == sentSum[ctrl]) return false;
  sentSum[ctrl] = sum;
  sentValid[ctrl] = true;
  FastLED[ctrl].showLeds(255);
  return true;
}

// `wByte` is the override's white (the WHITE switch position); `hostW`
// adds the host's per-segment white on top of it, so an alert can light the
// centre run from the W die while the sides carry a colour.
// Returns whether either channel was actually written.
bool showAll(uint8_t bright, uint8_t wByte, bool hostW) {
  uint8_t wc = wByte, ws = wByte;
  if (hostW) {
    if (state.w[SEG_CENTER] > wc) wc = state.w[SEG_CENTER];
    if (state.w[SEG_SIDES]  > ws) ws = state.w[SEG_SIDES];
  }
  bool wrote = packAndShow(0, &leds[strips[SEG_CENTER].start], CENTER_COUNT, bright, wc);
  // Drain the port between the two channel writes. Each show holds
  // interrupts off for milliseconds and the AVR's UART keeps only two bytes
  // without its ISR, so the gap between strips is the only chance an inbound
  // frame gets. Halving the blackout roughly halves the loss.
  readSerial();
  wrote |= packAndShow(1, &leds[strips[SEG_SIDES].start], SIDES_COUNT, bright, ws);
  forceShow = false;
  return wrote;
}

// Whether this mode's output changes from frame to frame. A static mode that
// keeps re-clocking the strips buys nothing and costs the serial link: at
// ~7 ms of interrupts-off per 16 ms frame, most inbound frames land during a
// write and are lost. SOLID is what the board sits in for hours at a time,
// and the handshake happens in it, so this is the difference between a link
// that connects first time and one that connects on the third attempt.
static inline bool modeAnimates(uint8_t m) {
  return !(m == MODE_SOLID || m == MODE_OFF);
}

// ─────────────────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);

#if HAS_MANUAL_SWITCH
  // INPUT_PULLUP for a GND common; plain INPUT for a +5V common, where the
  // shield supplies the pull-downs and our pull-up would fight them.
  const uint8_t swMode = SW_ACTIVE_LOW ? INPUT_PULLUP : INPUT;
  if (SW_PIN_UP   != SW_NO_PIN) pinMode(SW_PIN_UP,   swMode);
  if (SW_PIN_DOWN != SW_NO_PIN) pinMode(SW_PIN_DOWN, swMode);
#endif
  swPos = swPending = switchPosition();     // start in the real position, no
                                            // flash of the wrong state at boot

  // Both controllers read the SAME wire buffer, with their own lengths. They
  // are only ever shown one at a time (see showAll), so they cannot contend,
  // and sharing is what keeps this inside 2 KB.
  //
  // No setCorrection(): colour correction rescales channels, and these bytes
  // are an RGBW stream FastLED does not know it is carrying.
  FastLED.addLeds<LED_TYPE, CENTER_PIN, COLOR_ORDER>(wire, CENTER_SLOTS);
  FastLED.addLeds<LED_TYPE, SIDES_PIN,  COLOR_ORDER>(wire, SIDES_SLOTS);

  // Dither would alter those same bytes between frames. Off, on both.
  FastLED[0].setDither(DISABLE_DITHER);
  FastLED[1].setDither(DISABLE_DITHER);

  // NOTE: FastLED's setMaxPowerInVoltsAndMilliamps is deliberately NOT used.
  // It works by scaling the frame it is about to send, which would corrupt a
  // packed RGBW stream. Budget power with MAX_BRIGHTNESS instead — and note
  // that white now comes from the W LED, which draws far less than firing
  // R+G+B together for the same apparent brightness.
  if (state.brightness > MAX_BRIGHTNESS) state.brightness = MAX_BRIGHTNESS;

  measureAxis();
  loadState();

  // Timer1 free-running at clk/64 = 4 µs a tick, for telemetry only. Normal
  // mode, no compare outputs, no interrupts: it just counts.
  TCCR1A = 0;
  TCCR1B = _BV(CS11) | _BV(CS10);
  lastReadTick = TCNT1;

  lastRx = millis();
}

void loop() {
  readSerial();
  pollSwitch();

  // Watchdog: if the host stops talking, fall back to the saved default
  // rather than holding whatever was on screen when it vanished.
  if (hostSeen && (millis() - lastRx > WATCHDOG_MS)) {
    tele.fallbacks++;
    state = fallback;
    blanked = false;
    hostSeen = false;
    dirty = true;
  }

  // Both channels are re-sent every REFRESH_MS regardless, so a strip that
  // glitched (a static discharge, a loose connector) recovers by itself. On
  // its own timer, not "since the last write": in an animated look the sides
  // write every frame and the unchanging centre would otherwise never refresh.
  static unsigned long lastRefresh = 0;
  if (millis() - lastRefresh >= REFRESH_MS) {
    lastRefresh = millis();
    dirty = forceShow = true;
  }

  static unsigned long lastFrame = 0;
  unsigned long now = millis();
  bool animating = (swPos == SW_HOST) && !blanked && modeAnimates(state.mode);
  // Two reasons to draw: the picture changed (draw now — no waiting for a
  // frame tick, so a command is visible within one strip write), or an
  // animation is due its next frame (every animFrameMs, 15 fps by default).
  bool animDue = animating && (now - lastFrame >= animFrameMs);
  // While animating, draw ONLY on the frame tick. A command used to force an
  // extra frame the moment it landed, which broke the rhythm and — for the
  // fading modes (chase, sparkle) — faded twice in one tick: a visible
  // stutter every time the host spoke. A change now waits at most one frame
  // (33 ms at 30 fps); `dirty` stays set until then.
  if ((dirty && !animating) || animDue) {
    if (animDue) {
      // Advance by the time that actually passed, counted in 60 fps ticks
      // (the unit the speeds were tuned in), with the remainder carried so
      // nothing drifts. Speed is then the same at any frame rate. Capped so
      // the first frame after a still spell doesn't leap. Only advanced
      // while animating: a phase that ran on behind a static frame made the
      // next mode change jump.
      static uint16_t tickAcc = 0;
      unsigned long elapsed = now - lastFrame;
      if (elapsed > 250) elapsed = 250;
      lastFrame = now;
      tickAcc += (uint16_t)elapsed * 60;
      animTicks = tickAcc / 1000;
      tickAcc %= 1000;
      if (animTicks == 0) animTicks = 1;
      animStep += animTicks * (1 + (state.speed >> 5));
      chasePos += animTicks * (1 + (state.speed >> 6));
    }

    uint8_t outBright;
    uint8_t outWhite = 0;              // the fourth channel; RGB modes send 0
    bool    hostWhite = false;         // add the host's per-segment W?
    switch (swPos) {
      case SW_WHITE:
        // The RGB channels stay dark and the white die does all of it. Mixing
        // white from R+G+B would be tinted and cost three times the current
        // for the same apparent brightness.
        fill_solid(leds, TOTAL_LEDS, CRGB::Black);
        outWhite  = OVERRIDE_WHITE_W;
        outBright = OVERRIDE_WHITE_BRIGHTNESS;
        break;

      case SW_RED:
        fill_solid(leds, TOTAL_LEDS, CRGB(OVERRIDE_R, OVERRIDE_G, OVERRIDE_B));
        outBright = OVERRIDE_BRIGHTNESS;
        break;

      default:                           // SW_HOST
        if (blanked) {
          fill_solid(leds, TOTAL_LEDS, CRGB::Black);
          outBright = 0;
        } else {
          render();
          outBright = state.brightness;
          // Only the static mode carries the host's white: an animation
          // that flashed the sides while the W die held steady would read
          // as a broken strip, and no effect asks for white anyway.
          // ALERT too: its centre is steady and may be the white die.
          hostWhite = (state.mode == MODE_SOLID || state.mode == MODE_ALERT);
          // Every animation runs on the whole pit, centre included (2.12).
          // fw 2.8 held the centre on a steady white during animations to
          // hide a flicker blamed on the amp's supply; 2.9 found the real
          // causes (re-sent frames, bit timing), and a centre that never
          // animates is a centre whose flicker can't be seen or fixed.
        }
        break;
    }
    if (outBright > MAX_BRIGHTNESS) outBright = MAX_BRIGHTNESS;
    // Clear BEFORE the write, not after. showAll() reads the serial port
    // between the centre and sides writes, and a command handled there sets
    // `dirty` for the state it just changed. Clearing afterwards threw that
    // away: the change sat undrawn until some later redraw (the next PING, up
    // to a second on) and then appeared all at once — which is what erratic
    // alert flashing and "late" commands looked like before 2.6.
    dirty = false;
    uint16_t t0 = TCNT1;
    const bool wrote = showAll(outBright, outWhite, hostWhite);
    uint16_t t1 = TCNT1;
    // `shows` counts real strip writes, so "redraws/s" in Telemetry and the
    // flash test is what reached the strips, not how often we checked.
    if (wrote) {
      uint16_t dt = t1 - t0;
      tele.shows++;
      tele.showTicksLast = dt;
      if (dt > tele.showTicksMax) tele.showTicksMax = dt;
    }
    if (cmdPending) {
      uint16_t lat = t1 - cmdTick;
      tele.cmdTicksLast = lat;
      if (lat > tele.cmdTicksMax) tele.cmdTicksMax = lat;
      cmdPending = false;
    }
  }
}
