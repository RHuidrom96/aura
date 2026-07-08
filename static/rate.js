/* ============================================================
   MT Evaluation -- rating UI client logic
   Multi-segment-per-page layout: left = source/target/reference,
   right = Likert circles per criterion, full-width span annotation
   below each segment.
   ============================================================ */

(function () {
  "use strict";

  let statusTimer = null;

  function showStatus(message, type = "error") {
    const status = $("save-status");
    if (!status) return;

    clearTimeout(statusTimer);

    status.textContent = message;
    status.className = `save-status ${type} show`;

    statusTimer = setTimeout(() => {
      status.className = "save-status";
    }, 2500);
  }

  const CAMPAIGN    = window.CAMPAIGN || {};
  const SEGMENTS    = window.SEGMENTS || [];
  const EXISTING    = window.EXISTING || {};
  const SUBMIT_URL  = window.SUBMIT_URL || "";
  const CONFIG_URL  = window.CONFIG_URL || "";

  // These are mutable: an admin can edit the campaign while an annotator works,
  // and the page applies the new config live (see pollConfig / applyConfig).
  let CRITERIA    = window.CRITERIA || [];
  let SCALE       = window.SCALE_CONFIG || { type: "likert", design: "circles", points: 5, values: [1,2,3,4,5], labels: {} };
  let ENABLE_SPANS = window.ENABLE_SPANS !== false;
  let SPAN_SCOPE  = window.SPAN_SCOPE || "target";   // "target" or "both"
  let SPAN_INSTRUCTIONS = window.SPAN_INSTRUCTIONS || "";
  let EVAL_MODE   = window.EVAL_MODE || "likert";
  let PREFERENCES = window.PREFERENCES || [];
  // Annotator's segments-per-page. window.PER_PAGE is already the annotator's saved,
  // bounds-clamped choice from the server (falling back to the admin default), so we just
  // use it. Changes are persisted server-side (so they follow the annotator across devices)
  // via PER_PAGE_URL.
  let PER_PAGE    = Math.max(1, parseInt(window.PER_PAGE, 10) || 3);
  let PER_PAGE_MIN = Math.max(1, parseInt(window.PER_PAGE_MIN, 10) || 1);
  let PER_PAGE_MAX = Math.max(PER_PAGE_MIN, parseInt(window.PER_PAGE_MAX, 10) || PER_PAGE);
  const PER_PAGE_URL = window.PER_PAGE_URL || "";
  function clampPerPage(n) {
    n = parseInt(n, 10);
    if (!n || n < PER_PAGE_MIN) n = PER_PAGE_MIN;
    if (n > PER_PAGE_MAX) n = PER_PAGE_MAX;
    return n;
  }
  PER_PAGE = clampPerPage(PER_PAGE);
  function savePerPage(value) {
    if (!PER_PAGE_URL) return;
    try {
      fetch(PER_PAGE_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ segments_per_page: value }),
      });
    } catch (e) { /* preference save is best-effort */ }
  }
  let CONFIG_VERSION = window.CONFIG_VERSION || "";
  const AI_AVAILABLE = !!window.AI_AVAILABLE;
  const ASSIST_URL = window.ASSIST_URL || "";
  const ASSIST_FEEDBACK_URL = window.ASSIST_FEEDBACK_URL || "";

  // Derived scale helpers (recomputed whenever SCALE changes).
  let IS_CONTINUOUS, SCALE_DESIGN, SCALE_VALUES, SCALE_MIN, SCALE_MAX, SCALE_LABELS;
  function recomputeScale() {
    IS_CONTINUOUS = SCALE.type === "continuous";
    SCALE_DESIGN  = SCALE.design || (IS_CONTINUOUS ? "slider" : "circles");
    SCALE_VALUES  = IS_CONTINUOUS ? [] : (SCALE.values || []);
    SCALE_MIN     = IS_CONTINUOUS ? (SCALE.min != null ? SCALE.min : 0) : 1;
    SCALE_MAX     = IS_CONTINUOUS ? (SCALE.max != null ? SCALE.max : 100)
                                  : (SCALE.points || SCALE_VALUES.length || 5);
    SCALE_LABELS  = SCALE.labels || {};
  }
  recomputeScale();
  function labelFor(v) { return SCALE_LABELS[String(v)] || ""; }
  let NUM_PAGES = Math.max(1, Math.ceil(SEGMENTS.length / PER_PAGE));

  /* ---- per-segment state (one entry per global segment index) ---- */
  const state = {
    currentPage: 0,
    reviewMode: false,   // false during first-time annotation; true after Finish/Review
    // active error type is tracked per segment index so each card is independent
    activeErrorType: SEGMENTS.map(() => (CRITERIA[0] ? CRITERIA[0].id : null)),
    segmentStartTs: SEGMENTS.map(() => 0),
    ratings: SEGMENTS.map(seg => {
      const ex = EXISTING[seg.id];
      const candKeys = (seg._candidates || []).map(c => c.key);
      function normIncoming(spansObj) {
        const out = {};
        CRITERIA.forEach(c => {
          const arr = (spansObj && spansObj[c.id]) || [];
          out[c.id] = arr.map(sp => {
            if (sp.length >= 3) return [sp[0], sp[1], sp[2]];
            return [sp[0], sp[1], "target"];
          });
        });
        return out;
      }
      if (ex) {
        return {
          scores: { ...ex.scores },
          spans:  normIncoming(ex.spans),
          comments: ex.comments || "",
          preference: ex.preference || null,
          ranking: (ex.ranking && typeof ex.ranking === "object") ? { ...ex.ranking } : {},
          rankConfirmed: !!(ex.ranking && typeof ex.ranking === "object" &&
                            Object.keys(ex.ranking).length),
          _candKeys: candKeys,
          reviewed: !!ex.reviewed,
          edited: (ex.edited_text != null ? ex.edited_text : null),
          peTouched: (ex.edited_text != null && ex.edited_text !== ""),
          saved: true,
          dirty: false,
        };
      }
      return {
        scores: Object.fromEntries(CRITERIA.map(c => [c.id, null])),
        spans:  Object.fromEntries(CRITERIA.map(c => [c.id, []])),
        comments: "",
        preference: null,
        ranking: {},
        rankConfirmed: false,
        _candKeys: candKeys,
        reviewed: false,
        edited: null,
        peTouched: false,
        saved: false,
        dirty: false,
      };
    }),
  };

  // cards[globalIdx] = the rendered <article> element currently on screen, or null
  let cards = [];

  /* ===================== utilities ===================== */

  const $  = (id) => document.getElementById(id);
  const showScreen = (id) => {
    document.querySelectorAll(".screen").forEach(s => s.classList.remove("active"));
    $(id).classList.add("active");
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
  }

  function hexToRgba(hex, a) {
    const h = hex.replace("#", "");
    const r = parseInt(h.slice(0, 2), 16);
    const g = parseInt(h.slice(2, 4), 16);
    const b = parseInt(h.slice(4, 6), 16);
    return `rgba(${r},${g},${b},${a})`;
  }

  function arraysEqual(a, b) {
    if (a === b) return true;
    if (a.length !== b.length) return false;
    for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
    return true;
  }

  function normalizeSpans(spans) {
    if (!spans || spans.length === 0) return [];
    const sorted = spans.slice().sort((a, b) => a[0] - b[0]);
    const merged = [sorted[0].slice()];
    for (let i = 1; i < sorted.length; i++) {
      const last = merged[merged.length - 1];
      const cur = sorted[i];
      if (cur[0] <= last[1]) last[1] = Math.max(last[1], cur[1]);
      else merged.push(cur.slice());
    }
    return merged;
  }

  function normalizeSpansForPane(spans, pane) {
    const mine = spans.filter(sp => (sp[2] || "target") === pane).map(sp => [sp[0], sp[1]]);
    const others = spans.filter(sp => (sp[2] || "target") !== pane);
    const merged = normalizeSpans(mine).map(([s, e]) => [s, e, pane]);
    return others.concat(merged);
  }

  function isComplete(r) {
    if (EVAL_MODE === "preference_selection") {
      return !!(r._candKeys && r._candKeys.length &&
                r.ranking && r._candKeys.every(k => Number.isInteger(r.ranking[k])) &&
                r.rankConfirmed);
    }
    if (EVAL_MODE === "pairwise") return !!r.preference;
    if (EVAL_MODE === "span_only") return !!r.reviewed;
    if (EVAL_MODE === "post_edit") return !!r.peTouched && !!(r.edited && r.edited.trim());
    return CRITERIA.every(c => r.scores[c.id] != null);
  }

  function pageBounds(page) {
    const start = page * PER_PAGE;
    const end = Math.min(start + PER_PAGE, SEGMENTS.length);
    return { start, end };
  }

  function currentPageComplete() {
    const { start, end } = pageBounds(state.currentPage);
    for (let idx = start; idx < end; idx++) {
      if (!isComplete(state.ratings[idx])) return false;
    }
    return true;
  }

  function firstIncompleteOnCurrentPage() {
    const { start, end } = pageBounds(state.currentPage);
    for (let idx = start; idx < end; idx++) {
      if (!isComplete(state.ratings[idx])) return idx;
    }
    return -1;
  }

  function refreshNextEnabled() {
    const btn = $("btn-next");
    if (!btn) return;
  
    const complete = currentPageComplete();
  
    // Keep the button clickable so we can guide the annotator
    btn.disabled = false;
  
    btn.title = `Complete Segment ${missing + 1} first.`;
  }

  /* ===================== per-card builders ===================== */

  // Apply a chosen score and refresh dependent UI.
  function setScore(card, idx, crit, val) {
    const r = state.ratings[idx];
    if (r.scores[crit] !== val) {
      r.scores[crit] = val;
      r.dirty = true;
    }
    refreshCriteriaUIFor(card, idx);
    if (ENABLE_SPANS) refreshSpanLockFor(card, idx);
    updateGlobalProgress();
    refreshNextEnabled();
  }

  function valueAria(v) {
    const lbl = labelFor(v);
    return v + (lbl ? ": " + lbl : "");
  }

  // Build the rating control for one criterion, dispatching on the scale design.
  function buildScaleControl(card, host, idx, c) {
    if (SCALE_DESIGN === "slider") return buildSlider(card, host, idx, c);
    if (SCALE_DESIGN === "radio")  return buildRadio(card, host, idx, c);
    if (SCALE_DESIGN === "stars")  return buildStars(card, host, idx, c);
    if (SCALE_DESIGN === "buttons") return buildButtons(card, host, idx, c);
    return buildCircles(card, host, idx, c);   // default
  }

  function buildCircles(card, host, idx, c) {
    host.classList.add("likert-circle-row");
    host.setAttribute("role", "radiogroup");
    host.setAttribute("aria-label", escapeHtml(c.name) + " rating");
    host.innerHTML = SCALE_VALUES.map(v =>
      '<button type="button" class="likert-circle" data-value="' + v + '" role="radio" aria-checked="false" ' +
        'aria-label="' + escapeHtml(valueAria(v)) + '" title="' + escapeHtml(valueAria(v)) + '">' + v + '</button>'
    ).join("");
    host.addEventListener("click", (e) => {
      const btn = e.target.closest(".likert-circle");
      if (!btn) return;
      setScore(card, idx, c.id, parseInt(btn.dataset.value, 10));
    });
  }

  function buildButtons(card, host, idx, c) {
    host.classList.add("likert-row");
    host.setAttribute("role", "radiogroup");
    host.setAttribute("aria-label", escapeHtml(c.name) + " rating");
    host.innerHTML = SCALE_VALUES.map(v =>
      '<button type="button" class="likert-btn" data-value="' + v + '" role="radio" aria-checked="false">' +
        '<span class="lk-num">' + v + '</span>' +
        (labelFor(v) ? '<span class="lk-label">' + escapeHtml(labelFor(v)) + '</span>' : '') +
      '</button>'
    ).join("");
    host.addEventListener("click", (e) => {
      const btn = e.target.closest(".likert-btn");
      if (!btn) return;
      setScore(card, idx, c.id, parseInt(btn.dataset.value, 10));
    });
  }

  function buildRadio(card, host, idx, c) {
    host.classList.add("scale-radio-row");
    const name = "r_" + idx + "_" + c.id;
    host.innerHTML = SCALE_VALUES.map(v =>
      '<label class="scale-radio-item">' +
        '<input type="radio" name="' + name + '" value="' + v + '">' +
        '<span class="scale-radio-num">' + v + '</span>' +
        (labelFor(v) ? '<span class="scale-radio-label">' + escapeHtml(labelFor(v)) + '</span>' : '') +
      '</label>'
    ).join("");
    host.addEventListener("change", (e) => {
      const inp = e.target.closest('input[type="radio"]');
      if (!inp) return;
      setScore(card, idx, c.id, parseInt(inp.value, 10));
    });
  }

  function buildStars(card, host, idx, c) {
    host.classList.add("scale-stars");
    host.setAttribute("role", "radiogroup");
    host.setAttribute("aria-label", escapeHtml(c.name) + " rating");
    host.innerHTML = SCALE_VALUES.map(v =>
      '<button type="button" class="star-btn" data-value="' + v + '" role="radio" aria-checked="false" ' +
        'aria-label="' + escapeHtml(valueAria(v)) + '" title="' + escapeHtml(valueAria(v)) + '">★</button>'
    ).join("") + '<span class="star-value" aria-hidden="true"></span>';
    host.addEventListener("click", (e) => {
      const btn = e.target.closest(".star-btn");
      if (!btn) return;
      setScore(card, idx, c.id, parseInt(btn.dataset.value, 10));
    });
  }

  function buildSlider(card, host, idx, c) {
    host.classList.add("scale-slider");
    const r = state.ratings[idx];
    const cur = r.scores[c.id];
    const mid = Math.round((SCALE_MIN + SCALE_MAX) / 2);
    const minLbl = SCALE.min_label ? " · " + escapeHtml(SCALE.min_label) : "";
    const maxLbl = SCALE.max_label ? " · " + escapeHtml(SCALE.max_label) : "";

    // Build a tick for every integer in the range. Labels are shown for each
    // number when the range is small enough to stay readable; for large ranges
    // the labels thin out (every Nth) while the tick marks remain.
    const span = Math.max(1, SCALE_MAX - SCALE_MIN);
    const MAX_LABELS = 26;
    let labelStep = 1;
    if (span + 1 > MAX_LABELS) labelStep = Math.ceil(span / (MAX_LABELS - 1));
    let ticks = "";
    for (let v = SCALE_MIN; v <= SCALE_MAX; v++) {
      const pct = ((v - SCALE_MIN) / span) * 100;
      const showLabel = ((v - SCALE_MIN) % labelStep === 0) || v === SCALE_MAX;
      ticks +=
        '<span class="slider-tick" style="left:' + pct + '%">' +
          '<span class="slider-tick-mark"></span>' +
          (showLabel ? '<span class="slider-tick-label">' + v + '</span>' : '') +
        '</span>';
    }

    host.innerHTML =
      '<div class="slider-top">' +
        '<span class="slider-end slider-min">' + SCALE_MIN + minLbl + '</span>' +
        '<span class="slider-value"' + (cur != null ? '' : ' data-empty="true"') + '>' +
          (cur != null ? cur : "Not rated") + '</span>' +
        '<span class="slider-end slider-max">' + SCALE_MAX + maxLbl + '</span>' +
      '</div>' +
      '<input type="range" class="slider-input" min="' + SCALE_MIN + '" max="' + SCALE_MAX +
        '" step="1" value="' + (cur != null ? cur : mid) + '" ' +
        'aria-label="' + escapeHtml(c.name) + ' rating">' +
      '<div class="slider-ticks" aria-hidden="true">' + ticks + '</div>';
    const input = host.querySelector(".slider-input");
    const commit = () => setScore(card, idx, c.id, parseInt(input.value, 10));
    input.addEventListener("input", commit);
    // Clicking the track/thumb without dragging should also register a value.
    input.addEventListener("click", commit);
    input.addEventListener("keyup", commit);
    // Clicking a tick number jumps the slider to that value.
    host.querySelector(".slider-ticks").addEventListener("click", (e) => {
      const tick = e.target.closest(".slider-tick");
      if (!tick) return;
      const pct = parseFloat(tick.style.left) / 100;
      const v = Math.round(SCALE_MIN + pct * span);
      input.value = v;
      setScore(card, idx, c.id, v);
    });
  }

  // Build the criteria list (one control per criterion) for one segment card.
  function buildCriteriaUIFor(card, idx) {
    const container = card.querySelector(".criteria-list");
    container.innerHTML = "";
    CRITERIA.forEach((c) => {
      const div = document.createElement("div");
      div.className = "criterion";
      div.dataset.criterion = c.id;
      div.innerHTML =
        '<div class="criterion-header">' +
          '<p class="criterion-title">' +
            '<span class="criterion-swatch" style="background:' + c.color + '" aria-hidden="true"></span>' +
            escapeHtml(c.name) +
          '</p>' +
        '</div>' +
        '<div class="scale-control scale-' + SCALE_DESIGN + '"></div>';
      container.appendChild(div);
      buildScaleControl(card, div.querySelector(".scale-control"), idx, c);
    });
  }

  function refreshCriteriaUIFor(card, idx) {
    const r = state.ratings[idx];
    card.querySelectorAll(".criterion").forEach(critEl => {
      const cid = critEl.dataset.criterion;
      const selected = r.scores[cid];
      critEl.classList.toggle("rated", selected != null);
      const host = critEl.querySelector(".scale-control");
      if (!host) return;

      if (SCALE_DESIGN === "circles" || SCALE_DESIGN === "buttons") {
        host.querySelectorAll("[data-value]").forEach(btn => {
          const v = parseInt(btn.dataset.value, 10);
          const isSel = v === selected;
          btn.classList.toggle("selected", isSel);
          btn.setAttribute("aria-checked", isSel ? "true" : "false");
        });
      } else if (SCALE_DESIGN === "radio") {
        host.querySelectorAll('input[type="radio"]').forEach(inp => {
          inp.checked = (parseInt(inp.value, 10) === selected);
        });
      } else if (SCALE_DESIGN === "stars") {
        host.querySelectorAll(".star-btn").forEach(btn => {
          const v = parseInt(btn.dataset.value, 10);
          btn.classList.toggle("filled", selected != null && v <= selected);
          btn.setAttribute("aria-checked", v === selected ? "true" : "false");
        });
        const sv = host.querySelector(".star-value");
        if (sv) sv.textContent = (selected != null) ? valueAria(selected) : "";
      } else if (SCALE_DESIGN === "slider") {
        const valEl = host.querySelector(".slider-value");
        const input = host.querySelector(".slider-input");
        if (selected != null) {
          if (valEl) { valEl.textContent = selected; delete valEl.dataset.empty; }
          if (input && parseInt(input.value, 10) !== selected) input.value = selected;
        } else if (valEl) {
          valEl.textContent = "Not rated";
          valEl.dataset.empty = "true";
        }
      }
    });
    card.classList.toggle("segment-complete", isComplete(r));
  }

  /* ---- pairwise preference selector ---- */
  function buildPreferenceUI(card, idx) {
    const host = card.querySelector(".preference-options");
    if (!host) return;
    host.innerHTML = PREFERENCES.map(p =>
      '<button type="button" class="preference-pill" data-pref="' + escapeHtml(p.id) + '" ' +
        'role="radio" aria-checked="false">' + escapeHtml(p.label) + '</button>'
    ).join("");
    host.addEventListener("click", (e) => {
      const btn = e.target.closest(".preference-pill");
      if (!btn) return;
      const r = state.ratings[idx];
      if (r.preference !== btn.dataset.pref) {
        r.preference = btn.dataset.pref;
        r.dirty = true;
      }
      refreshPreferenceUI(card, idx);
      updateGlobalProgress();
      refreshNextEnabled();
    });
  }

  function refreshPreferenceUI(card, idx) {
    const r = state.ratings[idx];
    card.querySelectorAll(".preference-pill").forEach(btn => {
      const sel = btn.dataset.pref === r.preference;
      btn.classList.toggle("selected", sel);
      btn.setAttribute("aria-checked", sel ? "true" : "false");
    });
    card.classList.toggle("segment-complete", isComplete(r));
  }

  /* ---- preference selection (one source, N ranked candidates) ----
     The annotator reorders the candidates (best at the top) using up/down
     controls; a candidate's rank is derived from its position and shown in a
     small corner badge. Ranks are always distinct (1..N). System identity
     (name, and which candidate is system A/B/C…) is never revealed here. */

  // Deterministic per-segment shuffle so the initial presentation order does
  // not leak each system's fixed position across segments. Stable for a given
  // seed, so reloads show the same starting order.
  function seededShuffle(arr, seed) {
    let h = 2166136261 >>> 0;
    const s = String(seed);
    for (let i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 16777619) >>> 0;
    }
    const rand = () => {
      h += 0x6D2B79F5;
      let t = h;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
    const out = arr.slice();
    for (let i = out.length - 1; i > 0; i--) {
      const j = Math.floor(rand() * (i + 1));
      const tmp = out[i]; out[i] = out[j]; out[j] = tmp;
    }
    return out;
  }

  // Keys in current rank order (best first). Ranks are normalised to a dense
  // 1..N permutation, guaranteeing every candidate has a distinct rank.
  function orderedKeys(r) {
    return Object.keys(r.ranking)
      .filter(k => Number.isInteger(r.ranking[k]))
      .sort((a, b) => r.ranking[a] - r.ranking[b]);
  }

  function moveCandidate(r, key, dir) {
    const order = orderedKeys(r);
    const i = order.indexOf(key);
    if (i < 0) return;
    const j = dir === "up" ? i - 1 : i + 1;
    if (j < 0 || j >= order.length) return;
    const tmp = order[i]; order[i] = order[j]; order[j] = tmp;
    order.forEach((k, pos) => { r.ranking[k] = pos + 1; });
  }

  const ARROW_UP =
    '<svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true">' +
    '<path d="M10 5.5 4.5 11h11L10 5.5z" fill="currentColor"/></svg>';
  const ARROW_DOWN =
    '<svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true">' +
    '<path d="M10 14.5 4.5 9h11L10 14.5z" fill="currentColor"/></svg>';

  function renderRankList(listEl, r, byKey) {
    const order = orderedKeys(r);
    // Keep the stored ranking a dense 1..N permutation.
    order.forEach((k, pos) => { r.ranking[k] = pos + 1; });
    const N = order.length;
    listEl.innerHTML = order.map((key, pos) => {
      const rank = pos + 1;
      const isFirst = pos === 0;
      const isLast = pos === N - 1;
      return (
        '<div class="cand-card ranked" data-key="' + escapeHtml(key) + '">' +
          '<div class="cand-rank-badge" title="Rank ' + rank + ' of ' + N + '">' + rank + '</div>' +
          '<div class="cand-head">' +
            '<div class="rank-move-group" role="group" aria-label="Reorder candidate">' +
              '<button type="button" class="rank-move" data-dir="up" data-key="' + escapeHtml(key) + '"' +
                (isFirst ? ' disabled' : '') + ' aria-label="Move up">' + ARROW_UP + '</button>' +
              '<button type="button" class="rank-move" data-dir="down" data-key="' + escapeHtml(key) + '"' +
                (isLast ? ' disabled' : '') + ' aria-label="Move down">' + ARROW_DOWN + '</button>' +
            '</div>' +
          '</div>' +
          '<div class="cand-text"></div>' +
        '</div>'
      );
    }).join("");
    listEl.querySelectorAll(".cand-card").forEach(cardEl => {
      const key = cardEl.dataset.key;
      cardEl.querySelector(".cand-text").textContent = (byKey[key] && byKey[key].text) || "";
    });
  }

  function buildSelectionUI(card, idx, seg) {
    const left = card.querySelector(".segment-left");
    if (!left) return;
    const outLabel = (window.IO_LABELS && window.IO_LABELS.output) || "Translation";
    const cands = seg._candidates || [];
    const N = cands.length;
    const r = state.ratings[idx];
    if (!r.ranking || typeof r.ranking !== "object") r.ranking = {};

    const byKey = {};
    cands.forEach(c => { byKey[c.key] = c; });
    const keys = cands.map(c => c.key);

    // Establish an initial order if the annotator has none yet. We shuffle so
    // the starting arrangement doesn't reveal each system's fixed slot.
    const hasFullRanking = keys.length > 0 && keys.every(k => Number.isInteger(r.ranking[k]));
    if (!hasFullRanking) {
      r.ranking = {};
      seededShuffle(keys, String(seg.id != null ? seg.id : idx))
        .forEach((k, i) => { r.ranking[k] = i + 1; });
    }

    let block = card.querySelector(".ranking-block");
    if (!block) {
      block = document.createElement("div");
      block.className = "ranking-block";
      const refT = left.querySelector(".reference-toggle");
      if (refT) left.insertBefore(block, refT); else left.appendChild(block);
    }
    block.innerHTML =
      '<div class="ranking-help">Put the ' + N + ' ' + escapeHtml(outLabel.toLowerCase()) +
      ' candidates in order of preference — <strong>best at the top</strong>, worst at the bottom. ' +
      'Use the arrows to move a candidate up or down; each one\u2019s rank is shown in the corner. ' +
      'Every candidate gets a distinct rank.</div>' +
      '<div class="rank-list"></div>' +
      '<div class="rank-confirm-row">' +
        '<button type="button" class="rank-confirm-btn">This order looks right</button>' +
        '<span class="rank-confirm-note" hidden>Order confirmed \u2713</span>' +
      '</div>';

    const listEl = block.querySelector(".rank-list");
    renderRankList(listEl, r, byKey);

    block.addEventListener("click", (e) => {
      const moveBtn = e.target.closest(".rank-move");
      const confirmBtn = e.target.closest(".rank-confirm-btn");
      if (moveBtn && !moveBtn.disabled) {
        moveCandidate(r, moveBtn.dataset.key, moveBtn.dataset.dir);
        r.rankConfirmed = true;
        r.dirty = true;
        renderRankList(listEl, r, byKey);
      } else if (confirmBtn) {
        r.rankConfirmed = true;
        r.dirty = true;
      } else {
        return;
      }
      refreshSelectionUI(card, idx);
      updateGlobalProgress();
      refreshNextEnabled();
      refreshSavedBadge(card, idx);
    });

    refreshSelectionUI(card, idx);
  }

  function refreshSelectionUI(card, idx) {
    const r = state.ratings[idx];
    const done = !!r.rankConfirmed;
    const note = card.querySelector(".rank-confirm-note");
    const btn = card.querySelector(".rank-confirm-btn");
    if (note) note.hidden = !done;
    if (btn) btn.classList.toggle("confirmed", done);
    card.classList.toggle("segment-complete", isComplete(r));
  }

  function paneEl(card, pane) {
    return pane === "source"
      ? card.querySelector(".span-source")
      : card.querySelector(".span-target-main");
  }

  function populateSpanHelp(card) {
    const host = card.querySelector(".span-help-content");
    if (!host) return;
    if (SPAN_INSTRUCTIONS && SPAN_INSTRUCTIONS.trim()) {
      // Admin-provided instructions: render as plain text, preserving line breaks.
      host.innerHTML = '<p class="admin-instructions">' +
        escapeHtml(SPAN_INSTRUCTIONS).replace(/\n/g, "<br>") + '</p>';
    } else {
      // Built-in default explanation.
      host.innerHTML =
        '<p>For each error you noticed, <strong>pick an error type below</strong>, then ' +
        '<strong>select the words</strong> with your cursor (just like selecting text anywhere else). ' +
        'Different error types can overlap on the same words.</p>' +
        '<p class="span-help-hint">To remove a span, switch to that error type and ' +
        '<strong>click inside it</strong>. A single click without dragging marks the whole word.</p>';
    }
  }

  function buildErrorTypePickerFor(card, idx) {
    const picker = card.querySelector(".error-type-picker");
    picker.innerHTML = "";
    CRITERIA.forEach(c => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "error-type-pill";
      btn.dataset.errorType = c.id;
      btn.style.color = c.color;
      btn.innerHTML =
        '<span class="error-type-swatch" aria-hidden="true"></span>' +
        '<span class="error-type-name">' + escapeHtml(c.name) + '</span>' +
        '<span class="error-type-count" data-count-for="' + c.id + '">0</span>';
      picker.appendChild(btn);
    });
    picker.addEventListener("click", (e) => {
      const btn = e.target.closest(".error-type-pill");
      if (!btn) return;
      state.activeErrorType[idx] = btn.dataset.errorType;
      refreshErrorTypePickerFor(card, idx);
      setActiveSelectionColorFor(card, idx);
    });
  }

  function refreshErrorTypePickerFor(card, idx) {
    const active = state.activeErrorType[idx];
    card.querySelectorAll(".error-type-pill").forEach(btn => {
      btn.classList.toggle("selected", btn.dataset.errorType === active);
    });
    const r = state.ratings[idx];
    if (!r) return;
    CRITERIA.forEach(c => {
      const n = (r.spans[c.id] || []).length;
      const el = card.querySelector('[data-count-for="' + c.id + '"]');
      if (el) el.textContent = n;
    });
    setActiveSelectionColorFor(card, idx);
  }

  function setActiveSelectionColorFor(card, idx) {
    const c = CRITERIA.find(c => c.id === state.activeErrorType[idx]);
    if (!c) return;
    const col = hexToRgba(c.color, 0.28);
    const tgt = paneEl(card, "target");
    if (tgt) tgt.style.setProperty("--active-select-bg", col);
    const src = paneEl(card, "source");
    if (src) src.style.setProperty("--active-select-bg", col);
  }

  function renderPaneFor(card, idx, pane) {
    const el = paneEl(card, pane);
    if (!el) return;
    const r = state.ratings[idx];
    const text = paneText(idx, pane);
    if (!text) { el.innerHTML = ""; return; }

    function styleForTypes(typeIds) {
      if (typeIds.length === 0) return { bg: "", shadow: "" };
      const firstHex = CRITERIA.find(c => c.id === typeIds[0]).color;
      const bg = hexToRgba(firstHex, 0.14);
      const shadows = typeIds.map((id, i) => {
        const hex = CRITERIA.find(c => c.id === id).color;
        const offset = -2 - i * 3;
        return "inset 0 " + offset + "px 0 -1px " + hex;
      }).join(", ");
      return { bg, shadow: shadows };
    }

    const charTypes = new Array(text.length);
    for (let i = 0; i < text.length; i++) charTypes[i] = [];
    CRITERIA.forEach(c => {
      const spans = r.spans[c.id] || [];
      spans.forEach(sp => {
        if ((sp[2] || "target") !== pane) return;
        const s = sp[0], e = sp[1];
        for (let i = s; i < e && i < text.length; i++) {
          if (!charTypes[i].includes(c.id)) charTypes[i].push(c.id);
        }
      });
    });

    let html = "";
    let i = 0;
    while (i < text.length) {
      const types = charTypes[i];
      let j = i + 1;
      while (j < text.length && arraysEqual(charTypes[j], types)) j++;
      const chunk = text.slice(i, j);
      const style = styleForTypes(types);
      const cls = types.length ? "char in-span" : "char";
      let inline = "";
      if (types.length) inline = 'style="--span-bg:' + style.bg + '; --span-underlines:' + style.shadow + '"';
      const dataTypes = types.length ? ' data-types="' + types.join(",") + '"' : "";
      html += '<span class="' + cls + '" data-start="' + i + '" data-end="' + j + '"' +
              dataTypes + " " + inline + ">" + escapeHtml(chunk) + "</span>";
      i = j;
    }
    el.innerHTML = html;
  }

  function renderSpanTargetFor(card, idx) {
    renderPaneFor(card, idx, "target");
    if (SPAN_SCOPE === "both") renderPaneFor(card, idx, "source");
  }

  function selectionPointToOffset(card, idx, node, offsetInNode, pane) {
    if (!node) return null;
    const el = paneEl(card, pane);
    let charSpan = node;
    if (charSpan.nodeType === 3) charSpan = charSpan.parentNode;
    while (charSpan && charSpan !== el && !(charSpan.classList && charSpan.classList.contains("char"))) {
      charSpan = charSpan.parentNode;
    }
    if (!charSpan || charSpan === el) {
      if (offsetInNode <= 0) return 0;
      return paneText(idx, pane).length;
    }
    const chunkStart = parseInt(charSpan.dataset.start, 10);
    return chunkStart + Math.max(0, Math.min(offsetInNode, charSpan.textContent.length));
  }

  function pointToOffset(card, idx, clientX, clientY, pane) {
    let range = null;
    if (document.caretRangeFromPoint) {
      range = document.caretRangeFromPoint(clientX, clientY);
    } else if (document.caretPositionFromPoint) {
      const pos = document.caretPositionFromPoint(clientX, clientY);
      if (pos) {
        range = document.createRange();
        range.setStart(pos.offsetNode, pos.offset);
        range.collapse(true);
      }
    }
    if (!range) return null;
    const el = paneEl(card, pane);
    if (!el.contains(range.startContainer)) return null;
    return selectionPointToOffset(card, idx, range.startContainer, range.startOffset, pane);
  }

  function expandRangeToWords(start, end, text) {
    while (start > 0 && /\S/.test(text[start - 1])) start--;
    while (end < text.length && /\S/.test(text[end])) end++;
    return [start, end];
  }

  function addSpan(card, idx, errorType, start, end, pane) {
    if (start >= end) return;
    const r = state.ratings[idx];
    const list = r.spans[errorType] || [];
    list.push([start, end, pane]);
    r.spans[errorType] = normalizeSpansForPane(list, pane);
    r.dirty = true;
    renderSpanTargetFor(card, idx);
    refreshErrorTypePickerFor(card, idx);
    refreshSpanSummaryFor(card, idx);
  }

  function removeSpanAt(card, idx, offset, pane) {
    const r = state.ratings[idx];
    const t = state.activeErrorType[idx];
    const before = r.spans[t] || [];
    const after = [];
    let changed = false;
    before.forEach(sp => {
      const spPane = sp[2] || "target";
      if (spPane === pane && offset >= sp[0] && offset < sp[1]) { changed = true; return; }
      after.push(sp);
    });
    if (changed) {
      r.spans[t] = after;
      r.dirty = true;
      renderSpanTargetFor(card, idx);
      refreshErrorTypePickerFor(card, idx);
      refreshSpanSummaryFor(card, idx);
    }
    return changed;
  }

  function refreshSpanSummaryFor(card, idx) {
    const r = state.ratings[idx];
    if (!r) return;
    const sum = card.querySelector(".span-summary");
    const parts = [];
    CRITERIA.forEach(c => {
      const n = (r.spans[c.id] || []).length;
      if (n > 0) {
        parts.push(
          '<span class="sum-pill" style="color:' + c.color + '">' +
          '<span class="sum-dot" aria-hidden="true"></span>' +
          escapeHtml(c.name) + ': ' + n + '</span>'
        );
      }
    });
    sum.innerHTML = parts.length
      ? parts.join(" ")
      : '<span style="color:var(--text-faint)">No error spans marked yet (optional).</span>';
  }

  function refreshSpanLockFor(card, idx) {
    const r = state.ratings[idx];
    const allRated = isComplete(r);
    const section = card.querySelector(".span-section");
    if (!section) return;
    section.classList.toggle("locked", !allRated);
    let hint = section.querySelector(".span-locked-hint");
    if (!allRated) {
      if (!hint) {
        hint = document.createElement("div");
        hint.className = "span-locked-hint";
        hint.textContent = "Finish rating all criteria for this segment to start marking error spans.";
        const help = section.querySelector(".span-help");
        if (help) help.after(hint);
        else section.querySelector(".span-section-header").after(hint);
      }
    } else if (hint) {
      hint.remove();
    }
  }

  function captureNativeSelection(card, idx, clickOffset, pane) {
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return false;
    const el = paneEl(card, pane);
    const range = sel.getRangeAt(0);
    if (!el.contains(range.startContainer) || !el.contains(range.endContainer)) return false;
    let startOff = selectionPointToOffset(card, idx, range.startContainer, range.startOffset, pane);
    let endOff   = selectionPointToOffset(card, idx, range.endContainer,   range.endOffset, pane);
    if (startOff == null || endOff == null) return false;
    if (startOff > endOff) [startOff, endOff] = [endOff, startOff];
    const text = paneText(idx, pane);
    if (startOff === endOff) {
      if (clickOffset != null) {
        if (removeSpanAt(card, idx, clickOffset, pane)) return true;
        const [ws, we] = expandRangeToWords(clickOffset, clickOffset + 1, text);
        if (ws < we) addSpan(card, idx, state.activeErrorType[idx], ws, we, pane);
        return true;
      }
      return false;
    }
    addSpan(card, idx, state.activeErrorType[idx], startOff, endOff, pane);
    sel.removeAllRanges();
    return true;
  }

  function wirePaneFor(card, idx, pane) {
    const el = paneEl(card, pane);
    if (!el) return;
    function handleEnd(clickOffset) {
      const section = card.querySelector(".span-section");
      if (section && section.classList.contains("locked")) return;
      setTimeout(() => captureNativeSelection(card, idx, clickOffset, pane), 0);
    }
    el.addEventListener("mouseup", (e) => {
      handleEnd(pointToOffset(card, idx, e.clientX, e.clientY, pane));
    });
    el.addEventListener("touchend", (e) => {
      const t = e.changedTouches[0];
      handleEnd(t ? pointToOffset(card, idx, t.clientX, t.clientY, pane) : null);
    });
  }

  function initSpanInteractionsFor(card, idx) {
    wirePaneFor(card, idx, "target");
    if (SPAN_SCOPE === "both") wirePaneFor(card, idx, "source");

    card.querySelector(".span-clear-current").addEventListener("click", () => {
      const r = state.ratings[idx];
      if (!r) return;
      const t = state.activeErrorType[idx];
      if ((r.spans[t] || []).length === 0) return;
      if (!confirm("Remove all spans of this error type for this segment?")) return;
      r.spans[t] = [];
      r.dirty = true;
      renderSpanTargetFor(card, idx);
      refreshErrorTypePickerFor(card, idx);
      refreshSpanSummaryFor(card, idx);
    });

    card.querySelector(".toggle-span-help").addEventListener("click", () => {
      const body = card.querySelector(".span-help");
      const btn = card.querySelector(".toggle-span-help");
      const collapsed = body.classList.toggle("collapsed");
      btn.textContent = collapsed ? "Show help" : "Hide help";
    });
  }

  /* ===================== AI guideline assistant (floating) ===================== */

  function judgmentSummary(idx) {
    const r = state.ratings[idx];
    if (!r) return "";
    if (EVAL_MODE === "preference_selection") {
      const keys = r._candKeys || [];
      const done = keys.filter(k => Number.isInteger(r.ranking && r.ranking[k])).length;
      return keys.length && done === keys.length
        ? ("ranked " + keys.length + " candidates")
        : ("ranked " + done + " / " + keys.length + " candidates");
    }
    if (EVAL_MODE === "pairwise") {
      const p = PREFERENCES.find(x => x.id === r.preference);
      return p ? ("preference: " + p.label) : "no preference chosen yet";
    }
    if (EVAL_MODE === "span_only") {
      let n = 0; CRITERIA.forEach(c => n += (r.spans[c.id] || []).length);
      return "marked " + n + " error span(s); reviewed: " + (r.reviewed ? "yes" : "no");
    }
    if (EVAL_MODE === "post_edit") {
      if (!r.peTouched) return "not yet post-edited";
      const orig = (SEGMENTS[idx] && SEGMENTS[idx].target) || "";
      return (r.edited === orig) ? "reviewed; left unchanged" : "post-edited (changed)";
    }
    return "scores: " + CRITERIA.map(c =>
      c.name + "=" + (r.scores[c.id] == null ? "–" : r.scores[c.id])).join(", ");
  }

  function assistGeneralPresets() {
    const inLabel = (window.IO_LABELS && window.IO_LABELS.input) || "source";
    const outLabel = (window.IO_LABELS && window.IO_LABELS.output) || "translation";
    const list = ["What does the " + inLabel.toLowerCase() + " mean?"];
    if (EVAL_MODE === "pairwise") list.push("What do " + outLabel.toLowerCase() + " A and B say?");
    else list.push("What does the " + outLabel.toLowerCase() + " say?");
    list.push("Explain the instructions for this task", "How does the rating interface work?");
    CRITERIA.slice(0, 2).forEach(c => list.push(
      EVAL_MODE === "span_only" ? ("What counts as a " + c.name + " error?")
                                : ('What does "' + c.name + '" mean?')));
    return list;
  }

  // The segment the annotator is currently looking at (first on the page).
  function currentSegmentIdx() {
    const { start } = pageBounds(state.currentPage);
    return start;
  }

  // Segments to give the assistant as context: all of them for small campaigns,
  // otherwise the ones visible on the current page. Each labelled by its number
  // (server-side) and carrying the annotator's in-progress judgment.
  function assistSegmentsContext() {
    let idxs;
    if (SEGMENTS.length <= 12) {
      idxs = SEGMENTS.map((_, i) => i);
    } else {
      const { start, end } = pageBounds(state.currentPage);
      idxs = [];
      for (let i = start; i < end; i++) idxs.push(i);
    }
    return idxs.map(i => ({ id: SEGMENTS[i].id, judgment: judgmentSummary(i) }));
  }

  let assistOpen = false;
  const assistHistory = [];   // {role, content} turns for follow-up continuity

  function initFloatingAssistant() {
    const launcher = document.getElementById("assist-launcher");
    const panel = document.getElementById("assist-panel");
    if (!launcher || !panel) return;            // assistant disabled for this campaign
    launcher.hidden = false;

    const input = document.getElementById("assist-input");
    const presetWrap = document.getElementById("assist-presets");
    assistGeneralPresets().forEach(q => {
      const b = document.createElement("button");
      b.type = "button"; b.className = "assist-preset"; b.textContent = q;
      b.addEventListener("click", () => { input.value = q; askAssistant(q); });
      presetWrap.appendChild(b);
    });

    function toggle(open) {
      assistOpen = open;
      panel.classList.toggle("open", open);
      launcher.classList.toggle("active", open);
      if (open) setTimeout(() => input.focus(), 30);
    }
    launcher.addEventListener("click", () => toggle(!assistOpen));
    document.getElementById("assist-close").addEventListener("click", () => toggle(false));
    document.getElementById("assist-send").addEventListener("click", () => askAssistant(input.value.trim()));
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); askAssistant(input.value.trim()); }
    });
  }

  async function askAssistant(question) {
    if (!question) return;
    const transcript = document.getElementById("assist-transcript");
    const input = document.getElementById("assist-input");
    input.value = "";
    const turn = document.createElement("div");
    turn.className = "assist-turn";
    const q = document.createElement("div");
    q.className = "assist-q"; q.textContent = question;
    const a = document.createElement("div");
    a.className = "assist-a"; a.textContent = "Thinking…";
    turn.appendChild(q); turn.appendChild(a);
    transcript.appendChild(turn);
    transcript.scrollTop = transcript.scrollHeight;

    const idx = currentSegmentIdx();
    const seg = SEGMENTS[idx];
    try {
      const resp = await fetch(ASSIST_URL, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          segment_id: seg ? seg.id : "",
          segments: assistSegmentsContext(),
          history: assistHistory.slice(-6),
          question: question,
        }),
      });
      const data = await resp.json();
      if (!resp.ok || !data.ok) throw new Error(data.error || "Assistant unavailable");
      a.textContent = data.answer;
      assistHistory.push({ role: "user", content: question });
      assistHistory.push({ role: "assistant", content: data.answer });
      appendFeedback(turn, data.log_id);
    } catch (err) {
      a.textContent = "⚠ " + err.message;
    }
    transcript.scrollTop = transcript.scrollHeight;
  }

  function appendFeedback(turn, logId) {
    const fb = document.createElement("div");
    fb.className = "assist-feedback";
    fb.innerHTML = '<span class="assist-feedback-label">Helpful?</span>';
    [["helpful", "Yes"], ["reconsidered", "Made me reconsider"], ["dismissed", "No"]].forEach(([action, label]) => {
      const b = document.createElement("button");
      b.type = "button"; b.className = "link-btn assist-fb"; b.textContent = label;
      b.addEventListener("click", () => {
        sendAssistFeedback(logId, action);
        fb.innerHTML = '<span class="assist-feedback-label">Thanks, recorded.</span>';
      });
      fb.appendChild(b);
    });
    turn.appendChild(fb);
  }

  function sendAssistFeedback(logId, action) {
    if (!logId) return;
    fetch(ASSIST_FEEDBACK_URL, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ log_id: logId, action: action }),
    }).catch(() => {});
  }

  /* ===================== render a card ===================== */

  function setupPostEdit(card, idx, seg) {
    const block = card.querySelector(".postedit-block");
    if (!block) return;
    block.hidden = false;
    const ta = block.querySelector(".seg-postedit");
    const status = block.querySelector(".postedit-status");
    const original = seg.target || "";
    const r = state.ratings[idx];
    if (r.edited == null) r.edited = original;     // start from the original output
    ta.value = r.edited;
    function updateStatus() {
      if (!status) return;
      if (!r.peTouched) { status.textContent = ""; return; }
      status.textContent = (r.edited === original) ? "unchanged" : "edited";
    }
    updateStatus();
    ta.addEventListener("input", (e) => {
      r.edited = e.target.value;
      r.peTouched = true;
      r.dirty = true;
      updateStatus();
      updateGlobalProgress();
      refreshNextEnabled();
      refreshSavedBadge(card, idx);
    });
    ta.addEventListener("focus", () => {
      if (!r.peTouched) {
        r.peTouched = true; r.dirty = true; updateStatus();
        updateGlobalProgress(); refreshNextEnabled();
      }
    });
    const reset = block.querySelector(".postedit-reset");
    if (reset) reset.addEventListener("click", () => {
      r.edited = original; r.peTouched = true; r.dirty = true;
      ta.value = original; updateStatus();
      updateGlobalProgress(); refreshNextEnabled(); refreshSavedBadge(card, idx);
    });
  }

  function buildCard(idx) {
    const tpl = $("segment-template");
    const card = tpl.content.firstElementChild.cloneNode(true);
    card.dataset.segIndex = idx;

    const seg = SEGMENTS[idx];

    // header
    card.querySelector(".segment-number").textContent = "Segment " + (idx + 1);
    const pairText = (CAMPAIGN.source_language || "") + " → " + (CAMPAIGN.target_language || "");
    const pairChip = card.querySelector(".meta-pair");
    const domainChip = card.querySelector(".meta-domain");
    const sysChip = card.querySelector(".meta-system");
    pairChip.textContent = seg.pair || pairText;
    domainChip.textContent = seg.domain || "";
    sysChip.textContent = seg.system || "";
    pairChip.style.display  = (pairChip.textContent.trim() === "→") ? "none" : "";
    domainChip.style.display = seg.domain ? "" : "none";
    sysChip.style.display    = seg.system ? "" : "none";

    // Randomised AI experiment: mark items where the assistant is unavailable for this annotator.
    if (window.AI_AB_ENABLED && window.AI_AVAILABLE &&
        window.AI_AB_ELIGIBLE && window.AI_AB_ELIGIBLE[seg.id] === false) {
      const meta = sysChip.parentNode;
      if (meta && !meta.querySelector(".meta-ai-off")) {
        const b = document.createElement("span");
        b.className = "meta-chip meta-ai-off";
        b.textContent = "AI off (study)";
        b.title = "For this study, the AI assistant is turned off on this item.";
        meta.appendChild(b);
      }
    }

    // left: texts
    card.querySelector(".text-source").textContent = seg.source;
    card.querySelector(".text-reference").textContent = seg.reference || "(no reference provided)";

    const isPairwise = (EVAL_MODE === "pairwise");
    const isSelection = (EVAL_MODE === "preference_selection");
    const isSpanOnly = (EVAL_MODE === "span_only");
    const isPostEdit = (EVAL_MODE === "post_edit");

    if (isSelection) {
      // The ranking UI renders all N candidates itself; hide the two fixed panels.
      const tPanel = card.querySelector(".text-target").closest(".text-panel");
      if (tPanel) tPanel.hidden = true;
      const bPanel = card.querySelector(".text-panel-b");
      if (bPanel) bPanel.hidden = true;
      // Never reveal system identity (name or which is A/B/C…) to the annotator.
      if (sysChip) sysChip.style.display = "none";
    } else if (isPairwise) {
      // Two candidate panels: A (reuse .text-target) and B.
      const outLabel = (window.IO_LABELS && window.IO_LABELS.output) || "Translation";
      card.querySelector(".target-panel-label").textContent = outLabel + " A";
      card.querySelector(".text-target").textContent = seg._cand_a || "";
      const bPanel = card.querySelector(".text-panel-b");
      if (bPanel) {
        bPanel.hidden = false;
        card.querySelector(".text-target-b").textContent = seg._cand_b || "";
        card.querySelector(".target-b-panel-label").textContent = outLabel + " B";
      }
      // System chips per candidate are folded into the labels; keep generic meta only.
    } else {
      card.querySelector(".text-target").textContent = seg.target;
    }

    // RIGHT column: ratings (likert) vs preference (pairwise) vs nothing (span_only)
    const right = card.querySelector(".segment-right");
    const prefBlock = card.querySelector(".preference-block");
    if (isSelection) {
      if (right) right.style.display = "none";
      const grid = card.querySelector(".segment-grid");
      if (grid) grid.style.gridTemplateColumns = "1fr";
      buildSelectionUI(card, idx, seg);
    } else if (isPairwise) {
      // hide criteria ratings, show preference selector
      const rl = right.querySelector(".rating-block-label");
      if (rl) rl.style.display = "none";
      const cl = right.querySelector(".criteria-list");
      if (cl) cl.style.display = "none";
      if (prefBlock) prefBlock.hidden = false;
      buildPreferenceUI(card, idx);
    } else if (isSpanOnly) {
      // no scoring UI at all
      if (right) right.style.display = "none";
      // The top source/translation panels and the reference toggle just repeat
      // what the span-marking section shows, so drop them in span-only mode.
      const refToggle = card.querySelector(".reference-toggle");
      if (refToggle) refToggle.remove();
      const tgtPanel = card.querySelector(".text-target").closest(".text-panel");
      if (tgtPanel) tgtPanel.remove();
      const tgtBPanel = card.querySelector(".text-panel-b");
      if (tgtBPanel) tgtBPanel.remove();
      // The span section shows the source only when both source+target are markable.
      // If only the target is markable, keep the source panel on top so the
      // annotator can still read the source.
      if (SPAN_SCOPE === "both") {
        const srcPanel = card.querySelector(".text-source").closest(".text-panel");
        if (srcPanel) srcPanel.remove();
      }
      const left = card.querySelector(".segment-left");
      const grid = card.querySelector(".segment-grid");
      if (left && left.querySelectorAll(".text-panel").length === 0) {
        if (grid) grid.style.display = "none";   // nothing left to show up top
      } else if (grid) {
        grid.style.gridTemplateColumns = "1fr"; // single column (right is hidden)
      }
    } else if (isPostEdit) {
      // Post-editing: hide the scoring column; show the original output (read-only)
      // plus an editable corrected version below.
      if (right) right.style.display = "none";
      const refToggle = card.querySelector(".reference-toggle");
      if (refToggle) refToggle.remove();
      const grid = card.querySelector(".segment-grid");
      if (grid) grid.style.gridTemplateColumns = "1fr";
      setupPostEdit(card, idx, seg);
    } else {
      // likert: criteria circles
      buildCriteriaUIFor(card, idx);
    }

    // span section
    const spanSection = card.querySelector(".span-section");
    const spansActive = ENABLE_SPANS && !isPairwise && !isSelection && !isPostEdit;
    if (spansActive) {
      if (SPAN_SCOPE === "both") {
        const srcWrap = card.querySelector(".span-source-wrap");
        if (srcWrap) srcWrap.hidden = false;
      }
      populateSpanHelp(card);
      buildErrorTypePickerFor(card, idx);
      initSpanInteractionsFor(card, idx);
    } else if (spanSection) {
      spanSection.remove();
    }

    // span-only: explicit "reviewed" confirmation drives completeness
    const reviewedField = card.querySelector(".reviewed-field");
    if (isSpanOnly && reviewedField) {
      reviewedField.hidden = false;
      const cb = reviewedField.querySelector(".seg-reviewed");
      cb.checked = !!state.ratings[idx].reviewed;
      cb.addEventListener("change", (e) => {
        const r = state.ratings[idx];
        r.reviewed = e.target.checked;
        r.dirty = true;
        updateGlobalProgress();
        refreshNextEnabled();
        refreshSavedBadge(card, idx);
      });
    } else if (reviewedField) {
      reviewedField.remove();
    }

    // comments
    const ta = card.querySelector(".seg-comments");
    ta.value = state.ratings[idx].comments;
    ta.addEventListener("input", (e) => {
      const r = state.ratings[idx];
      if (r.comments !== e.target.value) {
        r.comments = e.target.value;
        r.dirty = true;
      }
    });

    // initial paint
    if (isSelection) refreshSelectionUI(card, idx);
    else if (isPairwise) refreshPreferenceUI(card, idx);
    else if (!isSpanOnly) refreshCriteriaUIFor(card, idx);
    if (spansActive) {
      renderSpanTargetFor(card, idx);
      refreshErrorTypePickerFor(card, idx);
      refreshSpanSummaryFor(card, idx);
      // In span-only mode there are no per-criterion scores gating spans, so no lock.
      if (!isSpanOnly) refreshSpanLockFor(card, idx);
    }
    refreshSavedBadge(card, idx);

    // Admin-chosen layout: move the rating/preference panel below the texts, and/or move the
    // span-marking section above the texts. Only applies where those panels are actually shown.
    var ratingPos = window.RATING_POSITION || "side";
    var spanPos = window.SPAN_POSITION || "below";
    var gridEl = card.querySelector(".segment-grid");
    var rightEl = card.querySelector(".segment-right");
    if (ratingPos === "below" && gridEl && rightEl &&
        rightEl.style.display !== "none" && (isPairwise || (!isSpanOnly && !isPostEdit))) {
      gridEl.style.gridTemplateColumns = "1fr";
      gridEl.parentNode.insertBefore(rightEl, gridEl.nextSibling);  // full-width, below texts
      rightEl.classList.add("rating-below");
    }
    if (spanPos === "beside" && spansActive) {
      var spanEl = card.querySelector(".span-section");
      if (spanEl && gridEl) {
        gridEl.style.gridTemplateColumns = "minmax(0,1fr) minmax(0,1fr)";
        if (rightEl && rightEl.parentNode === gridEl && rightEl.style.display !== "none") {
          rightEl.appendChild(spanEl);          // stack under the rating panel in the right column
        } else {
          if (rightEl) rightEl.style.display = "none";
          gridEl.appendChild(spanEl);           // span becomes the right column
        }
        spanEl.classList.add("span-beside");
      }
    }

    state.segmentStartTs[idx] = Date.now();
    return card;
  }

  function refreshSavedBadge(card, idx) {
    const badge = card.querySelector(".segment-saved-badge");
    if (!badge) return;
    const r = state.ratings[idx];
    if (r.saved && !r.dirty) {
      badge.textContent = "✓ Saved";
      badge.className = "segment-saved-badge saved";
    } else if (isComplete(r)) {
      badge.textContent = "Ready to save";
      badge.className = "segment-saved-badge ready";
    } else {
      badge.textContent = "";
      badge.className = "segment-saved-badge";
    }
  }

  /* ===================== page rendering ===================== */

  function renderPage(page) {
    state.currentPage = page;
    const stack = $("segment-stack");
    stack.innerHTML = "";
    cards = new Array(SEGMENTS.length).fill(null);

    const { start, end } = pageBounds(page);
    for (let idx = start; idx < end; idx++) {
      const card = buildCard(idx);
      cards[idx] = card;
      stack.appendChild(card);
    }

    $("page-current").textContent = page + 1;
    $("page-total").textContent = NUM_PAGES;
    $("btn-prev").disabled = page === 0;
    $("btn-next").textContent = (page === NUM_PAGES - 1) ? "Finish →" : "Save & next page →";

    refreshNextEnabled();
    updateGlobalProgress();
    updateSidebarNavigator();
    window.scrollTo({ top: 0, behavior: "auto" });
  }

  function updateGlobalProgress() {
    const ratedCount = state.ratings.filter(isComplete).length;
    $("rated-count").textContent = ratedCount;
    const pct = Math.round((ratedCount / SEGMENTS.length) * 100);
    $("progress-fill").style.width = pct + "%";
    // refresh saved badges for visible cards
    const { start, end } = pageBounds(state.currentPage);
    for (let idx = start; idx < end; idx++) {
      if (cards[idx]) { refreshSavedBadge(cards[idx], idx); }
    }
    updateSidebarNavigator();
  }

  function updateSidebarNavigator() {
    const thisPageList = document.getElementById("sidebar-this-page-list");
    if (thisPageList) {
      thisPageList.innerHTML = "";
      const { start, end } = pageBounds(state.currentPage);
      for (let idx = start; idx < end; idx++) {
        const seg = SEGMENTS[idx];
        const r = state.ratings[idx];
        const rated = isComplete(r);
        const div = document.createElement("div");
        div.className = "sidebar-segment-row";
        
        const accent = getComputedStyle(document.documentElement).getPropertyValue('--aura-accent').trim() || "#34433A";
        const dotColor = rated ? accent : "transparent";
        const dotBorder = rated ? accent : "#C7C1B2";
        
        div.innerHTML = 
          '<span style="width:9px; height:9px; border-radius:999px; flex-shrink:0; background:' + dotColor + '; border:1.5px solid ' + dotBorder + ';"></span>' +
          '<span style="font-size:13px; font-weight:500; color:#3A3A30; white-space:nowrap; margin-right:6px;">Segment ' + (idx + 1) + '</span>' +
          '<span style="font-size:13px; color:#B3AE9F; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; display:inline-block; max-width:120px;">' + escapeHtml(seg.target || "") + '</span>';
        
        const globalIdx = idx;
        div.addEventListener("click", () => {
          const card = cards[globalIdx];
          if (card) {
            const y = card.getBoundingClientRect().top + window.scrollY - 90;
            window.scrollTo({ top: y, behavior: 'smooth' });
          }
        });
        thisPageList.appendChild(div);
      }
    }

    const pagesGrid = document.getElementById("sidebar-pages-grid");
    if (pagesGrid) {
      pagesGrid.innerHTML = "";
      const accent = getComputedStyle(document.documentElement).getPropertyValue('--aura-accent').trim() || "#34433A";
      const pillBg = getComputedStyle(document.documentElement).getPropertyValue('--aura-accent-pill').trim() || "rgba(52, 67, 58, 0.08)";
      for (let p = 0; p < NUM_PAGES; p++) {
        const div = document.createElement("div");
        div.className = "sidebar-page-square";
        div.textContent = p + 1;
        div.style.aspectRatio = "1";
        div.style.display = "flex";
        div.style.alignItems = "center";
        div.style.justifyContent = "center";
        div.style.fontSize = "12px";
        div.style.fontWeight = "600";
        div.style.borderRadius = "6px";
        div.style.cursor = "pointer";
        div.style.border = "1px solid";
        
        const isCurrent = p === state.currentPage;
        const { start: pStart, end: pEnd } = pageBounds(p);
        let pComplete = true;
        for (let i = pStart; i < pEnd; i++) {
          if (!isComplete(state.ratings[i])) { pComplete = false; break; }
        }
        
        if (isCurrent) {
          div.style.background = accent;
          div.style.color = "#F4F0E7";
          div.style.borderColor = accent;
        } else if (pComplete) {
          div.style.background = pillBg;
          div.style.color = accent;
          div.style.borderColor = "transparent";
        } else {
          div.style.background = "#FFFFFF";
          div.style.color = "#A39E90";
          div.style.borderColor = "#E7E1D3";
        }
        
        div.addEventListener("click", async () => {
          const allow = state.reviewMode || p < state.currentPage || currentPageComplete();
          if (allow) {
            await saveCurrentPage(true);
            renderPage(p);
          } else {
            const status = $("save-status");
            if (status) {
              status.textContent = "Please rate every segment on this page before moving to another page.";
              status.className = "save-status error";
              setTimeout(() => { status.className = "save-status"; }, 3000);
            }
          }
        });
        pagesGrid.appendChild(div);
      }
    }
  }

  /* ===================== save ===================== */

  async function saveSegment(idx, silent) {
    const r = state.ratings[idx];
    if (!isComplete(r)) return { ok: false, reason: "incomplete", idx };
    if (!r.dirty && r.saved) return { ok: true, skipped: true, idx };

    const seg = SEGMENTS[idx];
    const payload = {
      segment_id: seg.id,
      scores: r.scores,
      spans: r.spans,
      comments: r.comments,
      preference: r.preference || "",
      ranking: r.ranking || {},
      reviewed: !!r.reviewed,
      edited_text: (r.edited != null ? r.edited : ""),
      time_spent_seconds: Math.round((Date.now() - (state.segmentStartTs[idx] || Date.now())) / 1000),
    };

    try {
      const resp = await fetch(SUBMIT_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await resp.json();
      if (!resp.ok || !data.ok) throw new Error(data.error || "Server error");
      r.saved = true;
      r.dirty = false;
      if (cards[idx]) refreshSavedBadge(cards[idx], idx);
      return { ok: true, idx };
    } catch (err) {
      return { ok: false, reason: err.message, idx };
    }
  }

  // Save all complete + dirty segments on the current page.
  async function saveCurrentPage(silent) {
    const { start, end } = pageBounds(state.currentPage);
    const status = $("save-status");
    if (!silent) {
      status.textContent = "Saving…";
      status.className = "save-status";
    }
    const results = [];
    for (let idx = start; idx < end; idx++) {
      const res = await saveSegment(idx, true);
      results.push(res);
    }
    const failed = results.filter(x => !x.ok && x.reason !== "incomplete");
    const savedAny = results.some(x => x.ok && !x.skipped);
    if (failed.length) {
      status.textContent = "Could not save some segments: " + failed[0].reason + ". Please try again.";
      status.className = "save-status error";
      return { ok: false };
    }
    if (!silent) {
      status.textContent = savedAny ? "✓ Saved" : "✓ Up to date";
      status.className = "save-status success";
    }
    return { ok: true };
  }

  /* ===================== navigation ===================== */

  $("btn-prev").addEventListener("click", async () => {
    if (state.currentPage === 0) return;
    await saveCurrentPage(true);
    renderPage(state.currentPage - 1);
  });

  $("btn-next").addEventListener("click", async () => {
    const btn = $("btn-next");
    if (!currentPageComplete()) {
      const missing = firstIncompleteOnCurrentPage();

      showStatus(`⚠ Please complete Segment ${missing + 1} first.`, "error");
    
      const card = cards[missing];
      if (card) {
        card.scrollIntoView({
          behavior: "smooth",
          block: "center"
        });
    
        card.classList.add("segment-missing");
    
        setTimeout(() => {
          card.classList.remove("segment-missing");
        }, 2500);
      }
    
      return;
    }
    btn.disabled = true;
    const result = await saveCurrentPage(false);
    btn.disabled = !currentPageComplete();
    if (!result.ok) return;
    if (state.currentPage < NUM_PAGES - 1) {
      setTimeout(() => renderPage(state.currentPage + 1), 250);
    } else {
      finishSession();
    }
  });

  /* ===================== jump modal ===================== */

  $("btn-jump").addEventListener("click", openJumpModal);
  $("jump-close").addEventListener("click", () => $("jump-modal").hidden = true);
  $("jump-modal").addEventListener("click", (e) => {
    if (e.target === $("jump-modal")) $("jump-modal").hidden = true;
  });

  function openJumpModal() {
    const list = $("jump-list");
    list.innerHTML = "";

    // First-time annotation: only let the annotator jump within the current page.
    // Review mode (after finishing or via the Review button): show all segments.
    const { start: pgStart, end: pgEnd } = pageBounds(state.currentPage);
    const showAll = state.reviewMode;
    const indices = [];
    if (showAll) {
      for (let i = 0; i < SEGMENTS.length; i++) indices.push(i);
    } else {
      for (let i = pgStart; i < pgEnd; i++) indices.push(i);
    }

    const titleEl = $("jump-modal").querySelector(".modal-header h3");
    if (titleEl) {
      titleEl.textContent = showAll ? "Jump to segment" : "Jump to segment on this page";
    }

    indices.forEach((i) => {
      const seg = SEGMENTS[i];
      const r = state.ratings[i];
      const div = document.createElement("div");
      div.className = "jump-item";
      const status = isComplete(r) ? "complete" : "incomplete";
      const statusLabel = isComplete(r) ? "Rated" : "Not rated";
      const pageNo = Math.floor(i / PER_PAGE) + 1;
      const pageBadge = showAll ? '<span class="jump-item-page">p.' + pageNo + '</span>' : '';
      div.innerHTML =
        '<span class="jump-item-num">#' + (i + 1) + '</span>' +
        '<span class="jump-item-text">' + escapeHtml(seg.target.slice(0, 80)) + '</span>' +
        pageBadge +
        '<span class="jump-item-status ' + status + '">' + statusLabel + '</span>';
      div.addEventListener("click", async () => {
        // In review mode, jumping is always allowed.
        // In first-pass mode, the list only contains current-page segments,
        // so a save is unnecessary (we're staying on the same page).
        $("jump-modal").hidden = true;
        const targetPage = Math.floor(i / PER_PAGE);
        if (targetPage !== state.currentPage) {
          if (state.reviewMode) {
            // saveCurrentPage will no-op for segments that aren't dirty
            await saveCurrentPage(true);
          }
          renderPage(targetPage);
        }
        const card = cards[i];
        if (card) card.scrollIntoView({ behavior: "smooth", block: "start" });
      });
      list.appendChild(div);
    });
    $("jump-modal").hidden = false;
  }

  /* ===================== finish ===================== */

  function finishSession() {
    state.reviewMode = true;
    $("progress-fill").style.width = "100%";
    showScreen("screen-done");
  }

  $("btn-review").addEventListener("click", () => {
    state.reviewMode = true;
    showScreen("screen-rate");
    openJumpModal();
  });

  $("toggle-instructions").addEventListener("click", () => {
    const body = $("instructions-body");
    const btn = $("toggle-instructions");
    const collapsed = body.classList.toggle("collapsed");
    btn.textContent = collapsed ? "Show" : "Hide";
  });

  /* ===================== unload warning ===================== */

  window.addEventListener("beforeunload", (e) => {
    const anyDirty = state.ratings.some(r => r.dirty);
    if (anyDirty) {
      e.preventDefault();
      e.returnValue = "";
    }
  });

  /* ===================== live config updates ===================== */

  // Re-render the instructions panel body (criteria definitions + scale legend)
  // from the current config, so admin edits show without a manual refresh.
  function rebuildInstructions(cfg) {
    const body = $("instructions-body");
    if (!body) return;
    const instr = cfg.instructions && cfg.instructions.trim()
      ? '<div class="admin-instructions">' + escapeHtml(cfg.instructions).replace(/\n/g, "<br>") + '</div>'
      : '<p>Read the <strong>source</strong> sentence carefully, then the candidate translation(s), and respond on the right. The reference is hidden by default to avoid biasing your judgment.</p>';

    let critDefs = "";
    if (cfg.eval_mode !== "pairwise") {
      const heading = (cfg.eval_mode === "span_only") ? "Error types you can mark" : "What each criterion means";
      critDefs = '<p class="instructions-subhead">' + heading + '</p><div class="crit-defs">' +
        cfg.criteria.map(c => {
          const def = c.guide || c.desc || "";
          return '<div class="crit-def"><span class="crit-def-swatch" style="background:' + c.color + '" aria-hidden="true"></span>' +
            '<span class="crit-def-text"><strong>' + escapeHtml(c.name) + '</strong>' +
            (def ? ": " + escapeHtml(def) : "") + '</span></div>';
        }).join("") + '</div>';
    }

    let scaleBlock = "";
    if (cfg.eval_mode === "likert") {
      const s = cfg.scale;
      const head = (s.type === "continuous")
        ? "What the scale means (" + s.min + "–" + s.max + " slider)"
        : "What each rating means (" + s.points + "-point scale)";
      scaleBlock = '<p class="instructions-subhead">' + head + '</p><div class="scale-legend">' +
        (cfg.scale_legend || []).map(it =>
          '<span class="legend-item"><span class="legend-num">' + escapeHtml(String(it.value)) + '</span>' +
          (it.label ? " " + escapeHtml(it.label) : "") + '</span>'
        ).join("") + '</div>';
    } else if (cfg.eval_mode === "pairwise") {
      scaleBlock = '<p class="instructions-subhead">Your choices</p><div class="scale-legend">' +
        (cfg.preferences || []).map(p => '<span class="legend-item">' + escapeHtml(p.label) + '</span>').join("") + '</div>';
    }
    body.innerHTML = instr + critDefs + scaleBlock;
  }

  // Reconcile each rating's score/span dicts to a new criteria set.
  function reconcileRatings() {
    const ids = CRITERIA.map(c => c.id);
    state.ratings.forEach(r => {
      const scores = {}, spans = {};
      ids.forEach(id => {
        scores[id] = (r.scores && r.scores[id] != null) ? r.scores[id] : null;
        spans[id]  = (r.spans && r.spans[id]) ? r.spans[id] : [];
      });
      r.scores = scores;
      r.spans = spans;
    });
    state.activeErrorType = state.activeErrorType.map(t => ids.includes(t) ? t : (ids[0] || null));
  }

  function segmentsChanged(cfg) {
    if (!Array.isArray(cfg.segments)) return false;
    if (cfg.segments.length !== SEGMENTS.length) return true;
    const newIds = cfg.segments.map(s => s.id).join("\u0001");
    const curIds = SEGMENTS.map(s => s.id).join("\u0001");
    if (newIds !== curIds) return true;
    for (let i = 0; i < cfg.segments.length; i++) {
      const a = cfg.segments[i], b = SEGMENTS[i] || {};
      if ((a.source || "") !== (b.source || "") ||
          (a.target || "") !== (b.target || "") ||
          (a.reference || "") !== (b.reference || "")) return true;
    }
    return false;
  }

  async function reloadForUpdate() {
    _polling = false;
    try { await saveCurrentPage(true); } catch (e) { /* save best-effort */ }
    window.location.reload();
  }

  function applyConfig(cfg) {
    // Layout (rating/span position) is read from these globals on every render.
    if (cfg.rating_position) window.RATING_POSITION = cfg.rating_position;
    if (cfg.span_position)   window.SPAN_POSITION   = cfg.span_position;

    // If the served segment set changed (difficulty serving, expertise, or edited
    // segments), the simplest correct path is to save in-progress work and refresh,
    // since segments are index-parallel with the ratings state.
    if (segmentsChanged(cfg)) { reloadForUpdate(); return; }

    CRITERIA   = cfg.criteria || [];
    SCALE      = cfg.scale || SCALE;
    recomputeScale();
    EVAL_MODE  = cfg.eval_mode || "likert";
    PREFERENCES = cfg.preferences || [];
    ENABLE_SPANS = !!cfg.enable_spans;
    SPAN_SCOPE = cfg.span_scope || "target";
    SPAN_INSTRUCTIONS = cfg.span_instructions || "";
    // Update bounds, then apply the annotator's saved choice for this campaign, which the
    // server already returns (clamped) in cfg.segments_per_page.
    if (cfg.min_segments_per_page != null) PER_PAGE_MIN = Math.max(1, parseInt(cfg.min_segments_per_page, 10) || 1);
    if (cfg.max_segments_per_page != null) PER_PAGE_MAX = Math.max(PER_PAGE_MIN, parseInt(cfg.max_segments_per_page, 10) || PER_PAGE_MIN);
    PER_PAGE   = clampPerPage(parseInt(cfg.segments_per_page, 10) || PER_PAGE);
    NUM_PAGES  = Math.max(1, Math.ceil(SEGMENTS.length / PER_PAGE));
    syncPerPageSelect();

    reconcileRatings();
    rebuildInstructions(cfg);

    if (state.currentPage > NUM_PAGES - 1) state.currentPage = NUM_PAGES - 1;
    renderPage(state.currentPage);

    const note = $("save-status");
    if (note) {
      note.textContent = "The campaign configuration was updated by the admin.";
      note.classList.add("config-updated-note");
      setTimeout(() => { note.classList.remove("config-updated-note"); note.textContent = ""; }, 6000);
    }
  }

  let _polling = true;
  async function pollConfig() {
    if (!_polling || !CONFIG_URL) return;
    try {
      const resp = await fetch(CONFIG_URL, { headers: { "X-Requested-With": "XMLHttpRequest" } });
      if (resp.status === 401) { _polling = false; return; }   // session ended; stop quietly
      if (!resp.ok) return;
      const cfg = await resp.json();
      if (!cfg || !cfg.ok) return;
      if (cfg.closed) { _polling = false; window.location.reload(); return; }
      if (cfg.version && cfg.version !== CONFIG_VERSION) {
        CONFIG_VERSION = cfg.version;
        applyConfig(cfg);
      }
    } catch (e) { /* network blip — try again next tick */ }
  }
  if (CONFIG_URL) setInterval(pollConfig, 5000);

  /* ---- Annotator per-page control ---- */
  function syncPerPageSelect() {
    var sel = document.getElementById("per-page-select");
    if (sel) sel.value = String(PER_PAGE);
  }
  (function initPerPageControl() {
    var sel = document.getElementById("per-page-select");
    if (!sel) return;
    sel.value = String(PER_PAGE);
    sel.addEventListener("change", function () {
      // Keep the annotator on the same segment across the layout change.
      var anchorSeg = state.currentPage * PER_PAGE;
      PER_PAGE = clampPerPage(sel.value);
      savePerPage(PER_PAGE);
      NUM_PAGES = Math.max(1, Math.ceil(SEGMENTS.length / PER_PAGE));
      state.currentPage = Math.min(NUM_PAGES - 1, Math.floor(anchorSeg / PER_PAGE));
      syncPerPageSelect();
      renderPage(state.currentPage);
    });
  })();

  /* ===================== init ===================== */

  // Start on the page containing the first incomplete segment, if any.
  // If every segment was already rated in a prior visit, enter review mode.
  const firstIncomplete = state.ratings.findIndex(r => !isComplete(r));
  if (firstIncomplete === -1) state.reviewMode = true;
  const startSeg = firstIncomplete === -1 ? 0 : firstIncomplete;
  const startPage = Math.floor(startSeg / PER_PAGE);
  renderPage(startPage);
  initFloatingAssistant();
})();