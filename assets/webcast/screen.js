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

  // ── "Did you know?": home's facts, ours left, the league's right ────────
  // A port of facts_overlay.py. A column that can't show every fact pages:
  // `factsStart` remembers where each column's page begins between renders,
  // and the pager moves it on by however many fitted.
  var factsStart = { ours: 0, league: 0 };
  var factsPager = null;

  function factsColumn(title, key, items) {
    var col = h("div", "facts-col");
    col.appendChild(h("div", "facts-title", title));
    var list = h("div", "facts-list");
    var start = items.length ? factsStart[key] % items.length : 0;
    items.slice(start).concat(items.slice(0, start)).forEach(function (text) {
      list.appendChild(h("div", "fact", text));
    });
    col.appendChild(list);
    col.dataset.key = key;
    col.dataset.count = items.length;
    return col;
  }

  function factsPage() {
    // Count what fitted in each column, then start the next page there.
    var cols = el.stage.querySelectorAll(".facts-col");
    var moved = false;
    cols.forEach(function (col) {
      var list = col.querySelector(".facts-list");
      var shown = 0;
      list.childNodes.forEach(function (f) {
        if (f.offsetTop + f.offsetHeight <= list.clientHeight + 1) shown++;
      });
      var n = +col.dataset.count;
      if (n && shown && shown < n) {
        factsStart[col.dataset.key] = (factsStart[col.dataset.key] + shown) % n;
        moved = true;
      }
    });
    if (moved && state) render(state);
  }

  function faceFacts(data) {
    if (data.empty) {
      var box = h("div", "face stack");
      box.appendChild(h("div", "eyebrow", "DID YOU KNOW"));
      box.appendChild(h("div", "headline", "Facts are on their way"));
      box.appendChild(h("div", "subline",
        "Breakaway's award history and the league's records arrive with team sync, and appear here."));
      return box;
    }
    var grid = h("div", "face facts" + (data.ours.length && data.league.length ? "" : " one"));
    if (data.ours.length) grid.appendChild(factsColumn("BREAKAWAY", "ours", data.ours));
    if (data.ours.length && data.league.length) grid.appendChild(h("div", "facts-rule"));
    if (data.league.length) grid.appendChild(factsColumn(data.league_title || "ACROSS THE LEAGUE", "league", data.league));
    if (!factsPager) factsPager = setInterval(factsPage, (data.page_s || 12) * 1000);
    return grid;
  }

  // A face by its content key, for stops in the program.
  function faceByName(face, data) {
    if (face === "diagnostics" || face === "robot_info" || face === "analysis") {
      return faceBoard(data && data.which ? data : { which: face, empty: true });
    }
    if (face === "next_match") return faceNextMatch(data);
    if (face === "schedule") return faceSchedule(data);
    if (face === "facts") return faceFacts(data);
    if (face === "quality") return faceQuality(data);
    if (face === "bk_seasons") return faceSeasons(data);
    if (face === "dataset") return faceDataset(data);
    if (face === "fact_card") return faceFactCard(data);
    return faceUnknown();
  }

  function notOnYet(eyebrow, line) {
    var box = h("div", "face stack");
    box.appendChild(h("div", "eyebrow", eyebrow));
    box.appendChild(h("div", "headline", "Not on yet"));
    box.appendChild(h("div", "subline", line));
    return box;
  }

  function dsHead(face, d) {
    var top = h("div", "ds-top");
    top.appendChild(h("div", "ds-title", d.title || ""));
    if (d.shown_of) top.appendChild(h("div", "ds-shown", d.shown_of.toUpperCase()));
    face.appendChild(top);
    if (d.description) face.appendChild(h("div", "ds-desc", d.description));
  }

  // ── Quality Award leaders (quality_overlay.py) ─────────────────────────
  // Two columns of ranked bars; T-n for ties; Breakaway's bar the one red.
  function faceQuality(d) {
    if (!d || d.empty) return notOnYet("QUALITY AWARDS",
      "The Quality Award leaderboard appears here once it arrives from home and an adult has turned it on.");
    var face = h("div", "face ds");
    dsHead(face, d);
    var grid = h("div", "q-grid");
    var half = Math.ceil(d.rows.length / 2);
    [d.rows.slice(0, half), d.rows.slice(half)].forEach(function (chunk) {
      var col = h("div", "q-col");
      chunk.forEach(function (r) {
        var row = h("div", "q-row" + (r.ours ? " ours" : ""));
        row.appendChild(h("span", "q-rank", r.rank));
        var team = h("span", "q-team", r.team);
        if (r.nickname) team.appendChild(h("i", "q-nick", r.nickname));
        row.appendChild(team);
        var track = h("span", "q-track");
        var bar = h("i", "q-bar");
        bar.style.width = (100 * (+r.count || 0) / (d.top || 1)).toFixed(1) + "%";
        track.appendChild(bar);
        track.appendChild(h("b", "q-count", r.count == null ? "" : String(r.count)));
        row.appendChild(track);
        col.appendChild(row);
      });
      grid.appendChild(col);
    });
    face.appendChild(grid);
    return face;
  }

  // ── Breakaway season by season (seasons_overlay.py) ────────────────────
  // Stacked W/L/T bars, a win % line on its own axis, finish diamonds, a gap
  // (never a zero) for a season with no record, awards as a number row.
  function faceSeasons(d) {
    if (!d || d.empty) return notOnYet("SEASON BY SEASON",
      "Breakaway's seasons appear here once they arrive from home and an adult has turned them on.");
    var face = h("div", "face ds");
    var top = h("div", "ds-top");
    top.appendChild(h("div", "ds-title", d.title || ""));
    top.appendChild(h("div", "ds-legend",
      "■ Wins  ▪ Losses  — Win %  ◆ Won an event  ◇ Finalist  # Awards"));
    face.appendChild(top);
    var ss = d.seasons, W = 1800, H = 560, L = 64, R = 84, PB = 130;
    var pw = W - L - R, ph = H - PB, slot = pw / ss.length, bw = Math.min(64, slot * 0.56);
    var most = 1;
    ss.forEach(function (x) { most = Math.max(most, (x.wins || 0) + (x.losses || 0) + (x.ties || 0)); });
    var topN = Math.max(10, Math.ceil(most / 10) * 10);
    var NS = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 -30 " + W + " " + (H + 30));
    svg.setAttribute("class", "s-chart");
    function el2(tag, attrs, text) {
      var n = document.createElementNS(NS, tag);
      for (var k in attrs) n.setAttribute(k, attrs[k]);
      if (text !== undefined) n.textContent = text;
      svg.appendChild(n);
      return n;
    }
    [0, 0.5, 1].forEach(function (f) {
      var y = ph - ph * f;
      el2("line", { x1: L, x2: L + pw, y1: y, y2: y, "class": "s-rule" });
      el2("text", { x: L - 14, y: y + 7, "class": "s-axis", "text-anchor": "end" }, String(Math.round(topN * f)));
      el2("text", { x: L + pw + 14, y: y + 7, "class": "s-axis" }, Math.round(100 * f) + "%");
    });
    var pts = [];
    ss.forEach(function (x, i) {
      var cx = L + slot * (i + 0.5);
      var rec = [x.wins, x.losses, x.ties].some(function (v) { return typeof v === "number"; });
      if (rec) {
        var base = ph;
        [["wins", "s-win"], ["losses", "s-loss"], ["ties", "s-tie"]].forEach(function (k) {
          var n = x[k[0]] || 0;
          if (!n) return;
          var hh = ph * n / topN;
          el2("rect", { x: cx - bw / 2, y: base - hh, width: bw, height: hh, "class": k[1] });
          base -= hh;
        });
        pts.push(typeof x.pct === "number" ? [cx, ph - ph * x.pct / 100] : null);
      } else {
        pts.push(null);
        el2("text", { x: cx, y: ph - 10, "class": "s-remote", "text-anchor": "middle" }, "REMOTE");
      }
      el2("text", { x: cx, y: ph + 28, "class": "s-year", "text-anchor": "middle" }, String(x.year));
      if (x.robot) el2("text", { x: cx, y: ph + 52, "class": "s-robot", "text-anchor": "middle" }, x.robot);
      // The best finish has a row of its own, clear of the bars and the line.
      if (x.finish === "won an event" || x.finish === "finalist") {
        var my = ph + 76, r = 11;
        el2("path", { d: "M" + cx + " " + (my - r) + "L" + (cx + r) + " " + my + "L" + cx + " " +
          (my + r) + "L" + (cx - r) + " " + my + "Z",
          "class": x.finish === "won an event" ? "s-won" : "s-fin" });
      }
      el2("text", { x: cx, y: ph + 120, "class": "s-awards", "text-anchor": "middle" },
        x.awards == null ? "—" : String(x.awards));
    });
    var prev = null;
    pts.forEach(function (pt) {
      if (pt && prev) el2("line", { x1: prev[0], y1: prev[1], x2: pt[0], y2: pt[1], "class": "s-pct" });
      prev = pt;
    });
    pts.forEach(function (pt) { if (pt) el2("circle", { cx: pt[0], cy: pt[1], r: 5, "class": "s-dot" }); });
    face.appendChild(svg);
    var notes = [];
    if (ss.some(function (x) { return ![x.wins, x.losses, x.ties].some(function (v) { return typeof v === "number"; }); }))
      notes.push("A gap is a season with no record (2021 was played remotely).");
    if (ss.some(function (x) { return x.year === 2015; }))
      notes.push("2015 ranked by average score, so it has few wins or losses.");
    notes.forEach(function (n) { face.appendChild(h("div", "ds-desc", n)); });
    return face;
  }

  // ── Any dataset (dataset_overlay.py) ───────────────────────────────────
  // Picked by the server-synced clock, as the native face does: pairs that
  // turn every period, A the first, B the second (wrapping on an odd count).
  function datasetFor(d) {
    var sets = d.sets || [];
    if (!sets.length) return null;
    var pairs = Math.ceil(sets.length / 2);
    var p = Math.floor((Date.now() + clockSkew) / 1000 / (d.period_s || 30)) % pairs;
    if (d.side !== "b") return sets[2 * p];
    if (2 * p + 1 < sets.length) return sets[2 * p + 1];
    return sets.length > 1 ? sets[0] : sets[2 * p];
  }
  var datasetShown = null;

  function faceDataset(d) {
    var ds = d && !d.empty ? datasetFor(d) : null;
    datasetShown = ds ? ds.key : null;
    if (!ds) return notOnYet("DATASETS",
      "Home's datasets appear here once an adult has reviewed one and turned it on.");
    var face = h("div", "face ds");
    dsHead(face, ds);
    var table = h("div", "ds-table");
    table.style.gridTemplateColumns = "repeat(" + ds.headers.length + ", auto)";
    ds.headers.forEach(function (t) { table.appendChild(h("span", "ds-th", t.toUpperCase())); });
    ds.rows.forEach(function (r) {
      r.cells.forEach(function (c) { table.appendChild(h("span", "ds-td" + (r.ours ? " ours" : ""), c)); });
    });
    face.appendChild(table);
    return face;
  }

  // ── Fun-fact cards (fact_card_overlay.py) ──────────────────────────────
  // A: a Breakaway record as a figure card; B: a "Did you know?" sentence as
  // a statement card. Card n of each deck, by the server-synced clock.
  var cardShown = null;
  function cardFor(d) {
    var deck = d.deck || [];
    if (!deck.length) return null;
    var n = Math.floor((Date.now() + clockSkew) / 1000 / (d.period_s || 15));
    return deck[n % deck.length];
  }
  function faceFactCard(d) {
    var c = d && !d.empty ? cardFor(d) : null;
    cardShown = c ? JSON.stringify(c) : null;
    if (!c) return notOnYet("FUN FACTS",
      "Breakaway's records and facts appear here once they arrive from home and an adult has turned them on.");
    if (c.kind === "record") {
      var box = h("div", "face card-record");
      box.appendChild(h("div", "eyebrow", "BREAKAWAY'S RECORDS"));
      box.appendChild(h("div", "card-value", c.value));
      box.appendChild(h("div", "card-record-name", c.record));
      if (c.detail) box.appendChild(h("div", "subline", c.detail));
      return box;
    }
    return faceStatement({ eyebrow: "Did you know?" + (c.category === "arkansas" ? "  ·  Arkansas" : ""),
                           title: c.text });
  }

  // The generic dataset turns by the clock: re-render when its page changes.
  setInterval(function () {
    if (!state || state.on === false) return;
    if (state.face === "fact_card" && state.fact_card) {
      var c = cardFor(state.fact_card);
      if ((c ? JSON.stringify(c) : null) !== cardShown) render(state);
      return;
    }
    var d = state.face === "dataset" ? state.dataset
          : (state.rotation && state.rotation.face === "dataset" ? state.rotation.dataset : null);
    if (!d) return;
    var ds = datasetFor(d);
    if ((ds ? ds.key : null) !== datasetShown) render(state);
  }, 1000);

  // ── The event's schedule (schedule_overlay.py) ────────────────────────
  // B's half of "Next match": every match, ours on a tile with our number
  // bold, our next tagged NEXT, the one on the field ON FIELD, played ones
  // dimmed, results and our record once TBA's arrive. Zero red: red and blue
  // are column names, not colours.
  function faceSchedule(data) {
    if (data.empty || !data.rows || !data.rows.length) {
      var box = h("div", "face stack");
      box.appendChild(h("div", "eyebrow", "OUR MATCHES"));
      box.appendChild(h("div", "headline", "No matches yet"));
      box.appendChild(h("div", "subline", "Our matches appear here once the event's schedule is out."));
      return box;
    }
    var face = h("div", "face sched");
    if (data.record) {
      var rec = h("div", "sched-record");
      rec.appendChild(h("span", "sched-record-label", "OUR RECORD"));
      rec.appendChild(h("span", "sched-record-figure", data.record.join("–")));
      face.appendChild(rec);
    }
    var head = h("div", "sched-row sched-head");
    ["MATCH", "TIME", "RED", "BLUE", "RESULT"].forEach(function (t) {
      head.appendChild(h("span", null, t));
    });
    face.appendChild(head);
    data.rows.forEach(function (r) {
      var dim = r.played && !r.current;
      var row = h("div", "sched-row" + (r.ours ? " ours" : "") + (dim ? " played" : ""));
      var label = h("span", "sched-label", r.short);
      var tag = r.next ? "NEXT" : (r.current ? "ON FIELD" : "");
      if (tag) label.appendChild(h("i", "sched-next", tag));
      row.appendChild(label);
      var at = r.at_ms ? new Date(r.at_ms) : null;
      row.appendChild(h("span", "sched-mono",
        at ? String(at.getHours()).padStart(2, "0") + ":" + String(at.getMinutes()).padStart(2, "0") : "—"));
      ["red", "blue"].forEach(function (side) {
        var cell = h("span", "sched-mono sched-teams");
        r[side].forEach(function (t) { cell.appendChild(h("b", t === data.team ? "us" : null, t)); });
        row.appendChild(cell);
      });
      var res = r.result;
      row.appendChild(h("span", "sched-mono" + (r.outcome ? " sched-outcome" : ""),
        res ? (r.outcome ? r.outcome + "  " : "") + res.red + "–" + res.blue : ""));
      face.appendChild(row);
    });
    return face;
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
    if (data.headline) {
      // An analysis board brings its own words; the status set them, not us.
      box.appendChild(h("div", "board-status", data.headline.title || ""));
      if (data.headline.sentence) {
        box.appendChild(h("div", "subline", data.headline.sentence));
      }
    } else {
      box.appendChild(h("div", "board-status",
        data.worst === "fault" ? "Fault latched"
          : data.worst === "warn" ? "Check before the next match"
          : "Nothing to report"));
    }
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
    if ((data.charts || []).length) {
      // Charts are never red: the board's one red is its fault, if any.
      var row = h("div", "charts");
      data.charts.forEach(function (c) {
        var card = h("div", "chart");
        card.appendChild(h("div", "label", c.title + (c.unit ? " · " + c.unit : "")));
        if (c.kind === "bars" && (c.bars || []).length) {
          var top = Math.max.apply(null, c.bars.map(function (b) {
            return Math.abs(b.value); })) || 1;
          c.bars.forEach(function (b) {
            var line = h("div", "bar");
            line.appendChild(h("span", "bar-label", b.label));
            var track = h("span", "bar-track");
            var fill = h("span", "bar-fill");
            fill.style.width = (100 * Math.abs(b.value) / top).toFixed(1) + "%";
            track.appendChild(fill);
            line.appendChild(track);
            line.appendChild(h("span", "bar-value", String(+b.value.toPrecision(4))));
            card.appendChild(line);
          });
        } else {
          var spark = sparkline(c.points, 480, 120);
          if (spark) card.appendChild(spark);
        }
        row.appendChild(card);
      });
      box.appendChild(row);
    }
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
      if (s.rotation.face) {
        // A stop that shows a whole face (app/program.py), with its data.
        node = faceByName(s.rotation.face, s.rotation[s.rotation.face] || { empty: true });
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
    } else if (s.face === "analysis") {
      node = faceBoard(s.board || { which: "analysis", empty: true });
      el.ledgerLeft.textContent = "ANALYSIS · CHECKED AGAINST THE LOG";
    } else if (s.face === "next_match") {
      node = faceNextMatch(s.next_match || { empty: true });
      el.ledgerLeft.textContent = "NEXT MATCH";
    } else if (s.face === "schedule") {
      node = faceSchedule(s.schedule || { empty: true, rows: [] });
      el.ledgerLeft.textContent = "OUR MATCHES TODAY";
    } else if (s.face === "fact_card") {
      node = faceFactCard(s.fact_card || { empty: true });
      el.ledgerLeft.textContent = s.screen && s.screen.slice(-1) === "b" ? "DID YOU KNOW" : "BREAKAWAY'S RECORDS";
    } else if (s.face === "quality" || s.face === "bk_seasons" || s.face === "dataset") {
      node = faceByName(s.face, s[s.face] || { empty: true });
      el.ledgerLeft.textContent = s.face === "quality" ? "QUALITY AWARD LEADERS"
        : s.face === "bk_seasons" ? "SEASON BY SEASON" : "DATASETS";
    } else if (s.face === "facts") {
      node = faceFacts(s.facts || { empty: true, ours: [], league: [] });
      el.ledgerLeft.textContent = "DID YOU KNOW";
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
