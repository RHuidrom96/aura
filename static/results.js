/* Results dashboard: per-table export toolbar.
   Copy as Markdown / LaTeX / Word (rich HTML), or download CSV. */
(function () {
  "use strict";

  function matrix(table) {
    return [...table.querySelectorAll("tr")].map(tr =>
      [...tr.children].map(td => td.textContent.replace(/\s+/g, " ").trim()));
  }

  function toMarkdown(m) {
    if (!m.length) return "";
    const esc = s => s.replace(/\|/g, "\\|");
    let out = "| " + m[0].map(esc).join(" | ") + " |\n";
    out += "| " + m[0].map(() => "---").join(" | ") + " |\n";
    for (let i = 1; i < m.length; i++) out += "| " + m[i].map(esc).join(" | ") + " |\n";
    return out;
  }

  function toLatex(m, caption) {
    if (!m.length) return "";
    const ncol = m[0].length;
    const esc = s => s.replace(/([&%$#_{}])/g, "\\$1").replace(/α/g, "$\\alpha$")
                      .replace(/κ/g, "$\\kappa$").replace(/—/g, "--").replace(/·/g, "\\cdot");
    let out = "\\begin{table}[ht]\n  \\centering\n  \\begin{tabular}{" + "l".repeat(ncol) + "}\n    \\hline\n";
    out += "    " + m[0].map(esc).join(" & ") + " \\\\\n    \\hline\n";
    for (let i = 1; i < m.length; i++) out += "    " + m[i].map(esc).join(" & ") + " \\\\\n";
    out += "    \\hline\n  \\end{tabular}\n";
    if (caption) out += "  \\caption{" + esc(caption.replace(/_/g, " ")) + "}\n";
    out += "\\end{table}\n";
    return out;
  }

  function toCSV(m) {
    return m.map(r => r.map(c => /[",\n]/.test(c) ? '"' + c.replace(/"/g, '""') + '"' : c).join(",")).join("\n");
  }

  function flash(btn, msg) {
    const o = btn.dataset.label;
    btn.textContent = msg;
    btn.classList.add("copied");
    setTimeout(() => { btn.textContent = o; btn.classList.remove("copied"); }, 1300);
  }

  function download(name, content, mime) {
    const blob = new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = name; document.body.appendChild(a); a.click();
    a.remove(); URL.revokeObjectURL(url);
  }

  function copyText(text, btn) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(() => flash(btn, "Copied ✓"),
        () => download((btn.dataset.tname || "table") + ".txt", text, "text/plain"));
    } else {
      download((btn.dataset.tname || "table") + ".txt", text, "text/plain");
    }
  }

  function copyWord(table, btn) {
    // A styled HTML table pastes into Word / Google Docs as a real table.
    const html = '<meta charset="utf-8"><table border="1" cellspacing="0" cellpadding="4" ' +
      'style="border-collapse:collapse;font-family:Calibri,Arial,sans-serif;font-size:11pt">' +
      table.innerHTML + "</table>";
    if (navigator.clipboard && window.ClipboardItem) {
      const item = new ClipboardItem({
        "text/html": new Blob([html], { type: "text/html" }),
        "text/plain": new Blob([table.innerText], { type: "text/plain" }),
      });
      navigator.clipboard.write([item]).then(() => flash(btn, "Copied ✓"),
        () => copyText(html, btn));
    } else {
      copyText(html, btn);
    }
  }

  function makeBtn(label, tname, fn) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "table-tool-btn";
    b.textContent = label; b.dataset.label = label; b.dataset.tname = tname;
    b.addEventListener("click", () => fn(b));
    return b;
  }

  function build() {
    document.querySelectorAll("table.result-table").forEach(table => {
      if (table.dataset.toolbar) return;
      table.dataset.toolbar = "1";
      const name = table.getAttribute("data-table-name") || "table";
      const bar = document.createElement("div");
      bar.className = "table-toolbar";
      const tag = document.createElement("span");
      tag.className = "table-tool-tag"; tag.textContent = "Copy table:";
      bar.appendChild(tag);
      bar.appendChild(makeBtn("Markdown", name, b => copyText(toMarkdown(matrix(table)), b)));
      bar.appendChild(makeBtn("LaTeX", name, b => copyText(toLatex(matrix(table), name), b)));
      bar.appendChild(makeBtn("Word", name, b => copyWord(table, b)));
      bar.appendChild(makeBtn("CSV", name, b => download(name + ".csv", toCSV(matrix(table)), "text/csv")));
      table.parentNode.insertBefore(bar, table);
    });
    buildInteractiveCharts();
  }

  /* ---------------- interactive charts (Chart.js) ---------------- */
  const PALETTE = ["#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9", "#D55E00", "#F0E442"];

  function chartConfig(key, R) {
    const grid = { color: "#ecebe6" };
    const common = {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false },
                 tooltip: { backgroundColor: "#2c2c2a", padding: 10, cornerRadius: 6 } },
      scales: { y: { beginAtZero: true, grid: grid, border: { display: false } },
                x: { grid: { display: false }, border: { display: false },
                     ticks: { autoSkip: false, maxRotation: 30, minRotation: 0 } } },
    };
    const clone = o => JSON.parse(JSON.stringify(o));

    if (key === "criteria_means" && R.likert) {
      const cs = R.likert.criteria;
      return { type: "bar",
        data: { labels: cs.map(c => c.name),
                datasets: [{ label: "Mean", data: cs.map(c => c.mean),
                             backgroundColor: "#0072B2", borderRadius: 4,
                             _std: cs.map(c => c.std) }] },
        options: Object.assign(clone(common), {
          plugins: { legend: { display: false },
            tooltip: { callbacks: { label: (ctx) => {
              const s = ctx.dataset._std[ctx.dataIndex];
              return "Mean " + ctx.parsed.y + (s != null ? "  (\u00b1" + s + ")" : ""); } } } } }) };
    }

    if (key === "score_distribution" && R.likert) {
      const cs = R.likert.criteria;
      const vals = [...new Set(cs.flatMap(c => Object.keys(c.distribution)))].sort((a, b) => a - b);
      return { type: "bar",
        data: { labels: cs.map(c => c.name),
                datasets: vals.map((v, i) => ({ label: "Score " + v,
                  data: cs.map(c => c.distribution[v] || 0),
                  backgroundColor: PALETTE[i % PALETTE.length] })) },
        options: Object.assign(clone(common), {
          plugins: { legend: { display: true, position: "right", labels: { boxWidth: 12 } } },
          scales: { x: { stacked: true, grid: { display: false }, ticks: { autoSkip: false, maxRotation: 30, minRotation: 0 } }, y: { stacked: true, beginAtZero: true } } }) };
    }

    if (key === "system_means" && R.likert && R.likert.systems) {
      const rows = R.likert.systems;
      const systems = [...new Set(rows.map(r => r.system))];
      const crits = [...new Set(rows.map(r => r.criterion))];
      const look = {}; rows.forEach(r => look[r.system + "||" + r.criterion] = r.mean);
      return { type: "bar",
        data: { labels: crits,
                datasets: systems.map((s, i) => ({ label: s,
                  data: crits.map(c => look[s + "||" + c] || 0),
                  backgroundColor: PALETTE[i % PALETTE.length], borderRadius: 4 })) },
        options: Object.assign(clone(common), { plugins: { legend: { display: true, position: "right" } } }) };
    }

    if (key === "preference_distribution" && R.pairwise) {
      const pd = R.pairwise.preference_distribution;
      return { type: "bar",
        data: { labels: pd.map(p => p.label),
                datasets: [{ label: "Count", data: pd.map(p => p.count),
                             backgroundColor: "#0072B2", borderRadius: 4 }] },
        options: clone(common) };
    }

    if (key === "system_winrate" && R.pairwise && R.pairwise.systems) {
      const ss = R.pairwise.systems;
      return { type: "bar",
        data: { labels: ss.map(r => r.system),
                datasets: [{ label: "Win rate (%)", data: ss.map(r => (r.win_rate || 0) * 100),
                             backgroundColor: "#009E73", borderRadius: 4 }] },
        options: Object.assign(clone(common), { scales: { y: { beginAtZero: true, max: 100 }, x: { grid: { display: false }, ticks: { autoSkip: false, maxRotation: 30, minRotation: 0 } } } }) };
    }

    if (key === "spans_per_type" && R.span_only) {
      const cs = R.span_only.criteria;
      return { type: "bar",
        data: { labels: cs.map(c => c.name),
                datasets: [{ label: "Spans", data: cs.map(c => c.n_spans),
                             backgroundColor: cs.map((c, i) => PALETTE[i % PALETTE.length]), borderRadius: 4 }] },
        options: clone(common) };
    }

    if (key === "likert_spans_per_type" && R.likert && R.likert.spans) {
      const cs = R.likert.spans.criteria;
      return { type: "bar",
        data: { labels: cs.map(c => c.name),
                datasets: [{ label: "Spans", data: cs.map(c => c.n_spans),
                             backgroundColor: cs.map((c, i) => PALETTE[i % PALETTE.length]), borderRadius: 4 }] },
        options: clone(common) };
    }

    if (key === "postedit_by_system" && R.post_edit) {
      const rows = (R.post_edit.systems || []).filter(s => s.mean_norm_distance != null);
      return { type: "bar",
        data: { labels: rows.map(s => s.system),
                datasets: [{ label: "Mean edit distance", data: rows.map(s => s.mean_norm_distance),
                             backgroundColor: rows.map((s, i) => PALETTE[i % PALETTE.length]), borderRadius: 4 }] },
        options: clone(common) };
    }

    var DIFFC = { easy: "#3a9b7a", medium: "#e0962e", hard: "#c0563f" };
    if (key === "difficulty_metric" && R.difficulty) {
      const rows = (R.difficulty.rows || []).filter(r => r.metric != null);
      return { type: "bar",
        data: { labels: rows.map(r => r.level.charAt(0).toUpperCase() + r.level.slice(1)),
                datasets: [{ label: R.difficulty.metric_label || "Metric", data: rows.map(r => r.metric),
                             backgroundColor: rows.map(r => DIFFC[r.level] || "#888"), borderRadius: 4 }] },
        options: clone(common) };
    }
    if (key === "difficulty_by_criterion" && R.difficulty && R.difficulty.by_criterion) {
      const bc = R.difficulty.by_criterion;
      return { type: "bar",
        data: { labels: bc.criteria,
                datasets: bc.levels.map(lv => ({ label: lv.charAt(0).toUpperCase() + lv.slice(1),
                  data: bc.data[lv].map(v => (v == null ? 0 : v)),
                  backgroundColor: DIFFC[lv] || "#888", borderRadius: 4 })) },
        options: clone(common) };
    }
    return null;
  }

  function buildInteractiveCharts() {
    if (typeof Chart === "undefined" || !window.RESULTS) return;  // report/offline -> keep PNGs
    document.querySelectorAll(".result-figure[data-chart]").forEach(fig => {
      if (fig.dataset.built) return;
      const cfg = chartConfig(fig.dataset.chart, window.RESULTS);
      if (!cfg) return;
      const img = fig.querySelector("img.result-chart");
      if (!img) return;
      const wrap = document.createElement("div");
      wrap.className = "result-canvas-wrap";
      const canvas = document.createElement("canvas");
      wrap.appendChild(canvas);
      img.replaceWith(wrap);
      try { new Chart(canvas, cfg); fig.dataset.built = "1"; }
      catch (e) { wrap.replaceWith(img); }  // fall back to PNG on any error
    });
  }

  if (document.readyState !== "loading") build();
  else document.addEventListener("DOMContentLoaded", build);

  // Allow the live-refresh poller to rebuild charts after swapping in a new results body.
  window.renderResultCharts = build;
})();
