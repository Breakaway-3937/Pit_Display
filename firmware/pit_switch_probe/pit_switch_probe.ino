/*
 * Breakaway 3937 — Pit SWITCH PROBE
 * ------------------------------------------------------------------
 * A throwaway diagnostic sketch, NOT the pit controller. Flash it when
 * you need to answer the one question the documentation got wrong:
 *
 *      WHICH PINS IS THE THREE-WAY SWITCH ACTUALLY PLUGGED INTO?
 *
 * pit_leds.ino cannot answer it. Its switch pins are compile-time
 * constants and both inputs sit HIGH on their pull-ups when nothing is
 * attached — which is electrically indistinguishable from the centre
 * detent. A wrong pin therefore does not look like a wrong pin. It looks
 * like a switch that is stuck in the middle, forever, silently.
 *
 * So this sketch drives nothing and reads everything: every pin that is
 * safe to touch is put on its internal pull-up and watched. You flick the
 * switch, it tells you which pins moved.
 *
 * Plain-text line protocol, 115200 — no COBS. You are meant to be able to
 * drive this from the Arduino IDE's serial monitor with one hand on the
 * switch.
 *
 *   ?        help, and the list of pins being watched
 *   p        print a snapshot of every watched pin right now
 *   u        record this position as UP      (throw one)
 *   c        record this position as CENTRE  (both throws open)
 *   d        record this position as DOWN    (throw two)
 *   r        REPORT — compare the three recordings, work out the wiring,
 *            and print the #define block to paste into pit_leds.ino
 *   v        common-rail check: is the switch common wired to GND or 5V?
 *
 * Live changes are printed as they happen, so in practice you flash this,
 * flick the switch three times, and read the answer off the terminal
 * before touching a key. The u/c/d/r sequence exists to turn that into a
 * config block you can paste rather than a wiring diagram you interpret.
 *
 * WHY THESE PINS AND NOT ALL OF THEM
 * ------------------------------------------------------------------
 *   D0, D1   the USB serial port. Pulling these up fights the USB-serial
 *            chip and takes the terminal down with it — you would lose the
 *            output that is the entire point of this sketch.
 *   D5, D6   the LED data lines (CENTER_PIN and SIDES_PIN in pit_leds).
 *            The switch cannot be on them, because the strips are.
 *
 * Everything else — D2, D3, D4, D7..D13, A0..A5 — is fair game. The analog
 * pins are ordinary digital inputs when you read them as one, and a switch
 * plugged into A-something is exactly the kind of thing wrong documentation
 * hides.
 *
 * D13 has the on-board LED and its resistor hanging off it. It still reads
 * a closed switch as LOW; it is just the one pin whose OPEN state is a
 * little less crisp than the others. It is reported like any other, and it
 * is worth a second look if it is the only pin that moves.
 */

// Watch the LED DATA pins as well? Off by default, because the strips own
// D5 and D6 and a switch cannot be on a pin a strip is already using.
//
// Turn it on when the switch is one pin short. If the throws are supposed to
// be on two pins and only one of them ever moves, the missing one is either
// unconnected or on a pin this sketch is not looking at — and after D0/D1
// (which are the serial port, and would break the terminal and the upload if
// anything were pulling on them) the only pins left are these two.
//
// This sketch drives nothing, so reading them is safe. The strips may twitch
// while their data lines float, which is noise on an undriven input and not
// a fault. Flash pit_leds back and it stops.
#define PROBE_LED_PINS 0

// ── Watched pins ────────────────────────────────────────────────────────
// A0..A5 are 14..19 on this chip, so a plain pin number covers them and the
// digital pins in one table. The names are only for printing.
const uint8_t PINS[] = {
#if PROBE_LED_PINS
  5, 6,
#endif
  2, 3, 4, 7, 8, 9, 10, 11, 12, 13,
  A0, A1, A2, A3, A4, A5
};
#define NPINS (sizeof(PINS) / sizeof(PINS[0]))

// ── Sense mode ──────────────────────────────────────────────────────────
//
// Two ways a switch can be wired, and they are mirror images:
//
//   PULLUP  common to GND. The MCU's internal pull-up holds the pin at 5V
//           and a closed throw drags it to 0V. CLOSED = LOW. This is what a
//           bare Arduino wants, and it needs no external parts.
//
//   PULLDN  common to +5V. Something on the board — an input shield, almost
//           always — holds the pin at 0V through a resistor, and a closed
//           throw drives it to 5V. CLOSED = HIGH. The internal pull-up must
//           be OFF, or it fights the shield's resistor and the pin reads
//           HIGH no matter what the switch does.
//
// Reading a pull-down board in PULLUP mode is the failure this sketch was
// written to catch and then very nearly missed: the pull-up wins on the pins
// with a weak pull-down (so they read HIGH always, and look unconnected) and
// loses on the pins with a strong one (so they read LOW always, and look
// grounded). Neither reading moves when the switch does, and both are wrong.
bool senseDown = false;                 // false = PULLUP, true = PULLDN

void applyMode() {
  for (uint8_t i = 0; i < NPINS; i++)
    pinMode(PINS[i], senseDown ? INPUT : INPUT_PULLUP);
  delay(20);
}

// True when this pin reads as a CLOSED throw, whichever way round it is.
static inline bool closedNow(uint8_t i) {
  bool level = digitalRead(PINS[i]);
  return senseDown ? level : !level;
}

void printPin(uint8_t i) {
  uint8_t p = PINS[i];
  if (p >= A0) { Serial.print('A'); Serial.print(p - A0); }
  else         { Serial.print('D'); Serial.print(p);      }
}

// ── Debounced read of the whole port ────────────────────────────────────
// A mechanical throw bounces for a few milliseconds. Everything here is a
// human-speed observation, so we can simply insist on a reading that holds
// still rather than filtering it: sample until two passes 5 ms apart agree,
// and give up after ~120 ms so a genuinely noisy pin cannot hang the sketch.
void sampleAll(bool *out) {
  bool prev[NPINS];
  for (uint8_t i = 0; i < NPINS; i++) prev[i] = digitalRead(PINS[i]);
  for (uint8_t attempt = 0; attempt < 24; attempt++) {
    delay(5);
    bool same = true;
    for (uint8_t i = 0; i < NPINS; i++) {
      bool now = digitalRead(PINS[i]);
      if (now != prev[i]) same = false;
      prev[i] = now;
    }
    if (same) break;
  }
  for (uint8_t i = 0; i < NPINS; i++) out[i] = prev[i];
}

// LOW means "this pin is connected to the switch common, and the common is
// closed through this throw" — assuming the common goes to GND, which is
// what the pull-ups are for. See the 'v' command if it does not.
void printSnapshot(const bool *s) {
  Serial.print(senseDown ? F("[PULLDN] ") : F("[PULLUP] "));
  bool anyLow = false;
  for (uint8_t i = 0; i < NPINS; i++) {
    printPin(i);
    Serial.print(s[i] ? F("=H ") : F("=L "));
    if (!s[i]) anyLow = true;
  }
  Serial.println();
  if (!anyLow && !senseDown)
    Serial.println(F("  (every pin high — nothing closed in this position)"));
  if (senseDown) {
    Serial.print(F("  closed (H) here:"));
    bool any = false;
    for (uint8_t i = 0; i < NPINS; i++)
      if (s[i]) { Serial.print(' '); printPin(i); any = true; }
    if (!any) Serial.print(F(" none"));
    Serial.println();
  }
}

// ── Recordings ──────────────────────────────────────────────────────────
bool live[NPINS];
bool rec[3][NPINS];
bool have[3] = { false, false, false };
const char *POSNAME[3] = { "UP", "CENTRE", "DOWN" };

void record(uint8_t slot) {
  sampleAll(live);
  for (uint8_t i = 0; i < NPINS; i++) rec[slot][i] = live[i];
  have[slot] = true;
  Serial.print(F("recorded ")); Serial.print(POSNAME[slot]); Serial.println(':');
  printSnapshot(rec[slot]);
}

// ── The report ──────────────────────────────────────────────────────────
//
// The interesting pins are the ones that are not the same in all three
// positions. A pin that never moves is not wired to this switch, however
// convincingly it sits LOW — a pin held LOW in every position is a pin
// tied to ground, not a throw.
void report() {
  for (uint8_t k = 0; k < 3; k++) {
    if (!have[k]) {
      Serial.print(F("! no recording for "));
      Serial.print(POSNAME[k]);
      Serial.println(F(" — flick the switch there and press u / c / d"));
      return;
    }
  }

  Serial.println();
  Serial.println(F("── pins that move ─────────────────────────────"));
  uint8_t movers[NPINS];
  uint8_t nmov = 0;
  for (uint8_t i = 0; i < NPINS; i++) {
    if (rec[0][i] == rec[1][i] && rec[1][i] == rec[2][i]) continue;
    movers[nmov++] = i;
    Serial.print(F("  "));
    printPin(i);
    Serial.print(F("   UP="));     Serial.print(rec[0][i] ? 'H' : 'L');
    Serial.print(F("  CENTRE=")); Serial.print(rec[1][i] ? 'H' : 'L');
    Serial.print(F("  DOWN="));   Serial.println(rec[2][i] ? 'H' : 'L');
  }

  if (nmov == 0) {
    Serial.println(F("  none."));
    Serial.println(F("  The switch is not on any watched pin, or its common"));
    Serial.println(F("  is not on GND. Run 'v', and check D0/D1/D5/D6 by eye —"));
    Serial.println(F("  those four are excluded on purpose (serial and LED data)."));
    return;
  }

  Serial.println();
  Serial.println(F("── wiring ─────────────────────────────────────"));

  // The shape we expect: SPDT on-off-on, common to GND, one pin per throw,
  // neither closed in the centre. Anything else is reported as itself rather
  // than forced into that mould — a three-terminal rotary switch with a pin
  // per position is a real thing and reads as three movers, not two.
  int8_t up = -1, down = -1, centre = -1;
  for (uint8_t m = 0; m < nmov; m++) {
    uint8_t i = movers[m];
    if (!rec[0][i] && rec[1][i] && rec[2][i]) up = i;
    if (rec[0][i] && rec[1][i] && !rec[2][i]) down = i;
    if (rec[0][i] && !rec[1][i] && rec[2][i]) centre = i;
  }

  if (up >= 0 && down >= 0) {
    Serial.println(F("  SPDT on-off-on, centre closes neither throw."));
    Serial.println(F("  Paste this into pit_leds.ino:"));
    Serial.println();
    Serial.println(F("      #define HAS_MANUAL_SWITCH 1"));
    Serial.print  (F("      #define SW_ACTIVE_LOW "));
    Serial.println(senseDown ? F("0   // common on +5V") : F("1   // common on GND"));
    Serial.print  (F("      #define SW_PIN_UP    ")); Serial.print(PINS[up]);
    Serial.println(F("     // white"));
    Serial.print  (F("      #define SW_PIN_DOWN  ")); Serial.print(PINS[down]);
    Serial.println(F("     // red"));
    Serial.println();
    Serial.println(F("  Those are ARDUINO pin numbers, which are not"));
    Serial.println(F("  necessarily the numbers printed on a shield."));
  } else if (up >= 0 || down >= 0) {
    int8_t got = (up >= 0) ? up : down;
    Serial.println(F("  Only ONE throw is readable. That is a supported"));
    Serial.println(F("  build — you get two positions, that throw and"));
    Serial.println(F("  everything else — so the pit stays usable:"));
    Serial.println();
    Serial.println(F("      #define HAS_MANUAL_SWITCH 1"));
    Serial.print  (F("      #define SW_ACTIVE_LOW "));
    Serial.println(senseDown ? F("0") : F("1"));
    Serial.print  (F("      #define SW_PIN_"));
    Serial.print  ((up >= 0) ? F("UP    ") : F("DOWN  "));
    Serial.println(PINS[got]);
    Serial.print  (F("      #define SW_PIN_"));
    Serial.print  ((up >= 0) ? F("DOWN  ") : F("UP    "));
    Serial.println(F("255   // not on a readable pin yet"));
  } else {
    Serial.println(F("  Not a shape this sketch recognises. The table above"));
    Serial.println(F("  is the truth; switchPosition() in pit_leds.ino is"));
    Serial.println(F("  the one place that has to agree with it."));
  }

  Serial.println();
  Serial.println(F("  UP and DOWN are however the switch is MOUNTED, which no"));
  Serial.println(F("  measurement can tell you. If white and red come out the"));
  Serial.println(F("  wrong way round, set SW_INVERT to 1 — do not re-pin it."));
}

// ── Wiring check ────────────────────────────────────────────────────────
//
// Everything above assumes the switch common goes to GND, so a closed throw
// pulls its pin LOW against the internal pull-up. A LOW is therefore real
// evidence: something is connecting that pin to ground.
//
// A HIGH proves nothing, and this command exists to say so. An unconnected
// pin, an open throw, and a pin wired to 5V all read HIGH, and the AVR has
// no internal pull-DOWN to tell them apart.
//
// An earlier version of this command tried: drop the pull-ups, sample for a
// couple of hundred milliseconds, and call anything still HIGH "driven".
// That is wrong, and confidently wrong, which is worse. A floating CMOS
// input is a tiny capacitor with nothing to discharge it — the pull-up that
// was on a moment ago leaves it sitting at 5V, and it holds there for
// seconds. The test reported every unconnected pin on the board as driven.
// It has been replaced with the truth and a way to settle it.
void wiringCheck() {
  sampleAll(live);

  Serial.println();
  Serial.println(F("── wiring check ───────────────────────────────"));
  Serial.println(F("hold the switch in ONE position while reading this."));

  bool anyLow = false;
  for (uint8_t i = 0; i < NPINS; i++) {
    if (live[i]) continue;
    anyLow = true;
    Serial.print(F("  "));
    printPin(i);
    Serial.println(F("  LOW — really connected to ground through the switch"));
  }
  if (!anyLow)
    Serial.println(F("  no pin is LOW: nothing is closed in this position."));

  Serial.println();
  Serial.println(F("  Every other pin is HIGH, which means one of three"));
  Serial.println(F("  things and this board cannot tell you which:"));
  Serial.println(F("    - nothing is wired to it"));
  Serial.println(F("    - a throw is wired to it but open in this position"));
  Serial.println(F("    - it is wired to 5V (common on the wrong rail)"));
  Serial.println(F("  A floating input holds its last charge for seconds, so"));
  Serial.println(F("  no amount of sampling separates those. To settle it,"));
  Serial.println(F("  put a 10k resistor from the suspect pin to GND: wired"));
  Serial.println(F("  to 5V it stays HIGH, otherwise it goes LOW and stays."));
  Serial.println();
  Serial.println(F("  If a throw never reads LOW in ANY position, its wire"));
  Serial.println(F("  does not reach this board. That is a wiring job, not a"));
  Serial.println(F("  firmware setting."));
}

// ── Float test ──────────────────────────────────────────────────────────
//
// Tells a pin DRIVEN to 5V apart from a pin sitting HIGH because nothing is
// touching it. The wiring check above says those two are indistinguishable,
// and with pull-ups they are — this is how you break the tie.
//
// A switch whose common is on 5V instead of GND is invisible to a pull-up:
// closing a throw drives the pin to 5V, which is exactly where the pull-up
// already had it. The information is there, it just cannot be read the
// ordinary way.
//
// The method: charge every pin through its pull-up, then let go of all of
// them at once and watch which ones fall. An input with nothing attached is
// a few picofarads with no path to ground, so it drifts down over tens of
// milliseconds to seconds — leakage is all it has. A pin held at 5V by a
// wire never falls at all.
//
// SAFE ON PURPOSE. The obvious way to do this is to drive the pin LOW as an
// output and see how fast it snaps back. Do not: if the pin really is wired
// to 5V, that is the 5V rail shorted to ground through the MCU's output
// driver — ~200 mA through a pin rated 40 mA, on hardware that is up a
// ladder and works. Everything here is an input. The worst case is an
// inconclusive answer, which is the correct worst case for a diagnostic.
// How long to wait for a released pin to fall. An unconnected input has only
// leakage to discharge it and can easily hold 5V for many seconds, so a short
// window reports every pin as HELD and tells you nothing. Long enough that
// bare pins DO fall is what makes a pin that does not fall meaningful.
#ifndef FLOAT_LIMIT_MS
#define FLOAT_LIMIT_MS 3000
#endif

void floatTest() {
  const uint16_t LIMIT_MS = FLOAT_LIMIT_MS;
  uint16_t fell[NPINS];

  for (uint8_t i = 0; i < NPINS; i++) { pinMode(PINS[i], INPUT_PULLUP); fell[i] = 0xFFFF; }
  delay(20);                                   // charge every pin to 5V
  for (uint8_t i = 0; i < NPINS; i++) pinMode(PINS[i], INPUT);   // let go

  unsigned long t0 = millis();
  uint16_t elapsed = 0;
  uint8_t remaining = NPINS;
  while (elapsed < LIMIT_MS && remaining) {
    elapsed = (uint16_t)(millis() - t0);
    for (uint8_t i = 0; i < NPINS; i++) {
      if (fell[i] != 0xFFFF) continue;
      if (digitalRead(PINS[i]) == LOW) { fell[i] = elapsed; remaining--; }
    }
  }

  applyMode();

  Serial.print(F("float:"));
  for (uint8_t i = 0; i < NPINS; i++) {
    Serial.print(' ');
    printPin(i);
    Serial.print('=');
    if (fell[i] == 0xFFFF) Serial.print(F("HELD"));   // never fell: driven, or grounded-open
    else                   Serial.print(fell[i]);     // ms until it fell: floating
  }
  Serial.println();
  // A pin that reads LOW *with* the pull-up on is grounded, and shows here as
  // 0 — it never had to fall. That is not the same as HELD, and the snapshot
  // in 'p' is what separates them.
}

// ── Help ────────────────────────────────────────────────────────────────
void help() {
  Serial.println();
  Serial.println(F("Breakaway pit switch probe"));
  Serial.println(F("  ?  help   p  snapshot   v  wiring check"));
  Serial.println(F("  f  float test — driven-to-5V vs just unconnected"));
  Serial.println(F("  n  PULLDN mode (shield, common on +5V, closed=HIGH)"));
  Serial.println(F("  P  PULLUP mode (bare board, common on GND, closed=LOW)"));
  Serial.println(F("  u / c / d   record UP / CENTRE / DOWN"));
  Serial.println(F("  r  report + the #define block for pit_leds.ino"));
  Serial.print  (F("watching: "));
  for (uint8_t i = 0; i < NPINS; i++) { printPin(i); Serial.print(' '); }
  Serial.println();
#if PROBE_LED_PINS
  Serial.println(F("not watched: D0 D1 (serial).  D5 D6 INCLUDED (LED data)"));
#else
  Serial.println(F("not watched: D0 D1 (serial)  D5 D6 (LED data)"));
#endif
  Serial.println(F("flick the switch — changes print as they happen."));
  Serial.println();
}

void setup() {
  Serial.begin(115200);
  applyMode();
  delay(30);
  sampleAll(live);
  help();
  Serial.println(F("current state:"));
  printSnapshot(live);
}

void loop() {
  if (Serial.available()) {
    switch (Serial.read()) {
      case '?': help();                                  break;
      case 'p': sampleAll(live); printSnapshot(live);    break;
      case 'u': record(0);                               break;
      case 'c': record(1);                               break;
      case 'd': record(2);                               break;
      case 'r': report();                                break;
      case 'v': wiringCheck();                           break;
      case 'f': floatTest();                             break;
      case 'n': senseDown = true;  applyMode();
                Serial.println(F("mode=PULLDN (shield pull-downs; closed reads HIGH)"));
                sampleAll(live); printSnapshot(live);    break;
      case 'P': senseDown = false; applyMode();
                Serial.println(F("mode=PULLUP (internal pull-ups; closed reads LOW)"));
                sampleAll(live); printSnapshot(live);    break;
      default:  break;                                   // newlines, mostly
    }
  }

  // Live change reporting. Anything that moves is worth seeing without
  // having to remember to press a key first.
  bool now[NPINS];
  for (uint8_t i = 0; i < NPINS; i++) now[i] = digitalRead(PINS[i]);
  bool changed = false;
  for (uint8_t i = 0; i < NPINS; i++) if (now[i] != live[i]) changed = true;
  if (changed) {
    sampleAll(live);                       // re-read debounced before printing
    Serial.println(F("change:"));
    printSnapshot(live);
  }
  delay(10);
}
