/*
  One overhead screen, drawn from state that arrives on a websocket.

  The contract is Cheesy Arena's: every message is {type, data}; the full
  current state arrives the moment the socket opens, and deltas after that.
  Every `screen` message is a *complete* state, so a message missed during a
  reconnect repairs itself on the next one — there is nothing to sequence and
  nothing to re-request.

  Two things are deliberately NOT sent by the server:

  * **The dwell rail.** The payload carries the dwell's deadline; this file
    animates against it with requestAnimationFrame. That is the whole reason
    the pit machine is idle between slides — a rail at 60fps costs it nothing.
  * **The clock.** `server_now_ms` arrives with every state and is differenced
    against this machine's clock once, so a Pi whose time is wrong (no RTC, no
    internet at an event — the normal case) still counts down correctly.
*/

(function () {
  "use strict";

  var body = document.body;
  var SCREEN = body.dataset.screen;
  var WS_PORT = body.dataset.wsPort;

  var el = {
    root: document.documentElement,
    plate: document.getElementById("plate"),
    stage: document.getElementById("stage"),
    teamName: document.getElementById("teamName"),
    teamNumber: document.getElementById("teamNumber"),
    headerRight: document.getElementById("headerRight"),
    ledgerLeft: document.getElementById("ledgerLeft"),
    ledgerRight: document.getElementById("ledgerRight"),
    railFill: document.getElementById("railFill"),
    offline: document.getElementById("offline"),
    offlineTitle: document.getElementById("offlineTitle"),
    offlineBody: document.getElementById("offlineBody")
  };

  var state = null;
  var clockSkew = 0;        // serverNow - clientNow, in ms
  var backoff = 500;

  // ── Helpers ────────────────────────────────────────────────────────────

  function h(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }

  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  // **Every string below is read by a stranger standing in the pit.** None of
  // them may name a port, a socket, a setting or the control panel: a visitor
  // cannot act on any of that, and a panel that asks them to is worse than a
  // panel that simply says the team is on it. Operator-facing wording lives on
  // the picker page, which only the crew ever opens.
  var WELCOME = "Breakaway welcomes you to our pit";
  var TROUBLE = "We\u2019re having technical difficulties \u2014 back shortly.";

  var TRACE =
    '<svg class="trace" viewBox="0 0 300 62" aria-hidden="true">' +
    '<path d="M10,54 L52,14 L252,14"/><circle cx="264" cy="14" r="9"/></svg>';

  // ── Faces ──────────────────────────────────────────────────────────────

  function faceStatement(slide) {
    var box = h("div", "face stack");
    if (slide.eyebrow) box.appendChild(h("div", "eyebrow", slide.eyebrow));
    // The Trace is the statement archetype's one red thing, and only when
    // there is a single focal element for it to land on.
    var trace = document.createElement("div");
    trace.innerHTML = TRACE;
    box.appendChild(trace.firstChild);
    box.appendChild(h("div", "headline", slide.title));
    if (slide.body) box.appendChild(h("div", "subline", slide.body));
    return box;
  }

  function faceFigure(slide) {
    var box = h("div", "face stack");
    if (slide.eyebrow) box.appendChild(h("div", "eyebrow", slide.eyebrow));
    var row = h("div", "figure-row");
    var fig = h("div", "figure", slide.figure || slide.title);
    // 260px is the design's *maximum*, not a fixed size: a real log yields
    // figures from "14.2" to "62,118,775", and a ten-glyph numeral at 260
    // runs straight out of the plate. Scale the numeral and its unit together
    // so their relationship survives — the same rule as _fit_figure().
    var glyphs = String(slide.figure || slide.title).length;
    var k = glyphs > 5 ? Math.max(0.42, 5 / glyphs) : 1;
    fig.style.fontSize = "calc(" + Math.round(260 * k) + " * var(--u))";
    row.appendChild(fig);
    if (slide.unit) {
      var unit = h("div", "figure-unit", slide.unit);
      unit.style.fontSize = "calc(" + Math.round(64 * k) + " * var(--u))";
      row.appendChild(unit);
    }
    box.appendChild(row);
    if (slide.body) box.appendChild(h("div", "subline", slide.body));
    if (slide.from_log) box.appendChild(h("div", "seal", "FROM LOG"));
    return box;
  }

  function faceRoster(slide) {
    var box = h("div", "face stack");
    if (slide.eyebrow) box.appendChild(h("div", "eyebrow", slide.eyebrow));
    if (slide.title) box.appendChild(h("div", "check-title", slide.title));
    var grid = h("div", "roster");
    (slide.items || []).forEach(function (item) {
      grid.appendChild(h("div", null, item));
    });
    box.appendChild(grid);
    return box;                 // no red at all: a sponsor mark brings its own
  }

  function faceLunch(data) {
    var box = h("div", "face stack");
    box.appendChild(h("div", "eyebrow", data.eyebrow || "Back shortly"));
    var trace = document.createElement("div");
    trace.innerHTML = TRACE;
    box.appendChild(trace.firstChild);
    var head = h("div", "headline", data.headline || "");
    head.style.fontSize = "calc(200 * var(--u))";
    box.appendChild(head);
    if (data.body) box.appendChild(h("div", "subline", data.body));
    return box;
  }

  function faceChecklist(data) {
    var box = h("div", "face");
    var head = h("div", "check-head");
    head.appendChild(h("div", "check-title", data.name || "Pit checklist"));
    head.appendChild(h("div", "check-count", data.done + " / " + data.total));
    box.appendChild(head);
    var bar = h("div", "check-bar");
    var fill = h("i");
    fill.style.width = (data.total ? (100 * data.done / data.total) : 0) + "%";
    bar.appendChild(fill);
    box.appendChild(bar);
    if (!data.items.length) {
      box.appendChild(h("div", "subline", "Nothing on the list right now."));
      return box;
    }
    var rows = h("div", "check-rows");
    data.items.forEach(function (item) {
      var row = h("div", "check-row" + (item.done ? " done" : ""));
      row.appendChild(h("span", "tick"));
      row.appendChild(h("span", null, item.text));
      rows.appendChild(row);
    });
    box.appendChild(rows);
    return box;
  }

  function sparkline(shape, w, hgt) {
    if (!shape || shape.length < 2) return null;
    var lo = Math.min.apply(null, shape), hi = Math.max.apply(null, shape);
    var span = (hi - lo) || 1;
    var d = shape.map(function (v, i) {
      var x = (i / (shape.length - 1)) * w;
      var y = hgt - ((v - lo) / span) * hgt;
      return (i ? "L" : "M") + x.toFixed(1) + "," + y.toFixed(1);
    }).join(" ");
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 " + w + " " + hgt);
    svg.setAttribute("preserveAspectRatio", "none");
    var path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", d);
    svg.appendChild(path);
    return svg;
  }

  function faceBoard(data) {
    var box = h("div", "face");
    if (data.empty) {
      // A board with nothing behind it is not an error and must not read like
      // one to a visitor; it just has not been given this match's data yet.
      box.appendChild(h("div", "check-title", WELCOME));
      box.appendChild(h("div", "subline",
        "Robot data for this match is not loaded yet."));
      return box;
    }
    if (data.which === "robot_info") {
      var table = h("table", "motors");
      var thead = h("thead");
      var hr = h("tr");
      ["Motor", "CAN", "Type", "Temp", "Stator", "Faults"].forEach(function (t) {
        hr.appendChild(h("th", null, t));
      });
      thead.appendChild(hr); table.appendChild(thead);
      var tb = h("tbody");
      (data.motors || []).slice(0, 12).forEach(function (m) {
        var tr = h("tr", m.status === "fault" ? "fault" : null);
        [m.label || "—", m.can_id, m.device_type,
         m.temp_c == null ? "—" : m.temp_c.toFixed(0) + "C",
         m.stator_a == null ? "—" : m.stator_a.toFixed(1) + "A",
         (m.faults || []).length ? m.faults.join(", ") : "—"
        ].forEach(function (v) { tr.appendChild(h("td", null, String(v))); });
        tb.appendChild(tr);
      });
      table.appendChild(tb);
      box.appendChild(table);
      return box;
    }
    box.appendChild(h("div", "eyebrow", data.source || "Latest log"));
    box.appendChild(h("div", "board-status",
      data.worst === "fault" ? "Fault latched"
        : data.worst === "warn" ? "Check before the next match"
        : "Nothing to report"));
    var grid = h("div", "vitals");
    (data.vitals || []).slice(0, 4).forEach(function (v) {
      // Red marks the fault and its origin only — never a second region.
      var tile = h("div", "vital" + (v.status === "fault" ? " fault" : ""));
      tile.appendChild(h("div", "label", v.label));
      var val = h("div", "value", v.value);
      if (v.unit) val.appendChild(h("span", "unit", " " + v.unit));
      tile.appendChild(val);
      var spark = sparkline(v.shape, 240, 52);
      if (spark) tile.appendChild(spark);
      grid.appendChild(tile);
    });
    box.appendChild(grid);
    return box;
  }

  function faceNextMatch(data) {
    var box = h("div", "face stack");
    if (data.empty) {
      box.appendChild(h("div", "check-title", WELCOME));
      box.appendChild(h("div", "subline",
        "Our next match has not been posted yet."));
      return box;
    }
    box.appendChild(h("div", "eyebrow", "Next match"));
    box.appendChild(h("div", "headline", data.label || ""));
    box.appendChild(h("div", "subline", data.status || ""));
    return box;
  }

  function faceJudges(data) {
    var box = h("div", "face");
    var src = data.images && data.images[data.index];
    if (!src) {
      box.appendChild(h("div", "check-title", WELCOME));
      return box;
    }
    var img = document.createElement("img");
    img.className = "judges-img";
    img.src = src;
    box.appendChild(img);
    return box;
  }

  function faceUnknown() {
    // Reached only if a future face ships without a drawing routine here.
    // The visitor gets the welcome card; the console line is for whoever is
    // debugging it, and never reaches the panel.
    var box = h("div", "face stack");
    box.appendChild(h("div", "check-title", WELCOME));
    box.appendChild(h("div", "subline", "Thanks for stopping by."));
    return box;
  }

  // ── Drawing ────────────────────────────────────────────────────────────

  function applyPalette(p) {
    var r = el.root.style;
    r.setProperty("--ground", p.ground);
    r.setProperty("--plate", p.plate);
    r.setProperty("--ink", p.ink);
    r.setProperty("--body", p.body);
    r.setProperty("--muted", p.muted);
    r.setProperty("--faint", p.faint);
    r.setProperty("--rule", p.rule);
    r.setProperty("--red", p.red);
    el.root.dataset.theme = p.theme;
  }

  function render(s) {
    state = s;
    applyPalette(s.palette);
    el.root.style.setProperty("--accent", s.team.primary);
    body.dataset.face = s.face;
    body.dataset.side = s.side;

    el.teamName.textContent = (s.team.name || "").toUpperCase();
    el.teamNumber.textContent = s.team.number;
    var letter = s.screen.slice(-1).toUpperCase();
    el.headerRight.textContent =
      "SCREEN " + letter + "  /  " + String(s.mode).toUpperCase();

    var node;
    if (s.on === false) {
      // **The screen's own switch is off, and that looks exactly like the pit
      // machine being off.** One overlay covers both, because from where a
      // visitor is standing they are the same event: this screen is not
      // showing them anything, and the team is aware. The last picture stays
      // underneath, dimmed, precisely as it does on a dropped connection —
      // the page is never simply blank, and never passes stale content off as
      // live either.
      showTrouble();
      el.railFill.style.width = "0%";
      return;
    }
    if (s.face === "rotation" && s.rotation) {
      if (s.rotation.board) {
        node = faceBoard(s.board || { which: s.rotation.board, empty: true });
      } else {
        var slide = s.rotation.slide || {};
        node = slide.kind === "figure" ? faceFigure(slide)
             : slide.kind === "roster" ? faceRoster(slide)
             : faceStatement(slide);
      }
      el.ledgerLeft.textContent =
        String(s.rotation.index + 1).padStart(2, "0") + " / " +
        String(s.rotation.count).padStart(2, "0");
    } else if (s.face === "lunch") {
      node = faceLunch(s.lunch || {});
      el.ledgerLeft.textContent = "LUNCH";
    } else if (s.face === "checklist") {
      node = faceChecklist(s.checklist || { items: [], done: 0, total: 0 });
      el.ledgerLeft.textContent = "CHECKLIST";
    } else if (s.face === "judges") {
      node = faceJudges(s.judges || {});
      el.ledgerLeft.textContent = "JUDGES";
    } else if (s.face === "diagnostics" || s.face === "robot_info") {
      node = faceBoard(s.board || { which: s.face, empty: true });
      el.ledgerLeft.textContent = "LIVE BOARD";
    } else if (s.face === "next_match") {
      node = faceNextMatch(s.next_match || { empty: true });
      el.ledgerLeft.textContent = "NEXT MATCH";
    } else {
      node = faceUnknown();
      el.ledgerLeft.textContent = "";
    }

    el.ledgerRight.textContent = s.ledger_right || "";
    clear(el.stage);
    el.stage.appendChild(node);
  }

  // ── The dwell rail, animated here rather than sent ─────────────────────

  function tickRail() {
    requestAnimationFrame(tickRail);
    if (!state || state.on === false || !state.dwell || !state.dwell.running) {
      el.railFill.style.width = "0%";
      return;
    }
    var now = Date.now() + clockSkew;
    var d = state.dwell;
    var left = d.deadline_ms - now;
    var progress = 1 - (left / d.duration_ms);
    el.railFill.style.width =
      (Math.max(0, Math.min(1, progress)) * 100).toFixed(2) + "%";
  }
  requestAnimationFrame(tickRail);

  // ── The socket ─────────────────────────────────────────────────────────

  function showTrouble(note) {
    // One message for every failure, deliberately. A visitor cannot tell a
    // dropped socket from an unpublished screen from a restarted pit machine,
    // and does not need to: all three mean "the team is dealing with it".
    el.offlineTitle.textContent = WELCOME;
    el.offlineBody.textContent = note || TROUBLE;
    el.offline.hidden = false;
  }

  function connect() {
    var url = "ws://" + location.hostname + ":" + WS_PORT + "/" + SCREEN;
    var ws;
    try {
      ws = new WebSocket(url);
    } catch (e) {
      return setTimeout(connect, backoff);
    }

    ws.onopen = function () {
      backoff = 500;
      el.offline.hidden = true;
    };

    ws.onmessage = function (event) {
      var msg;
      try { msg = JSON.parse(event.data); } catch (e) { return; }
      if (msg.type === "screen") {
        // One skew measurement per message is free and keeps a Pi with no RTC
        // counting down correctly even if its clock drifts during an event.
        if (msg.data && msg.data.server_now_ms) {
          clockSkew = msg.data.server_now_ms - Date.now();
        }
        el.offline.hidden = true;
        render(msg.data);
      } else if (msg.type === "refused") {
        // The reason is an operator's sentence about configuration. It goes
        // to the console for whoever is setting the display up, never onto
        // the glass.
        if (msg.data && msg.data.reason) console.info(msg.data.reason);
        showTrouble();
      }
    };

    ws.onclose = function () {
      // The last good picture stays on screen underneath, dimmed, so the
      // panel is never simply blank — but it is plainly marked as stale
      // rather than passing for live.
      showTrouble();
      setTimeout(connect, backoff);
      backoff = Math.min(backoff * 2, 5000);
    };

    ws.onerror = function () { try { ws.close(); } catch (e) {} };
  }

  connect();
})();
