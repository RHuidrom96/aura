/* Shared client logic for the campaign create/edit forms:
   - evaluation-mode switching (likert / pairwise / span_only)
   - add/remove criterion + preference rows
   - span-scope enable/disable
   - Likert vs continuous scale config + dynamic point labels
*/
(function () {
  "use strict";

  function currentMode() {
    const picked = document.querySelector('input[name="eval_mode"]:checked');
    return picked ? picked.value : "likert";
  }

  // Show/hide [data-mode-block] elements based on the active mode.
  window.onEvalModeChange = function () {
    const mode = currentMode();
    document.querySelectorAll("[data-mode-block]").forEach(el => {
      const modes = el.getAttribute("data-mode-block").split(/\s+/);
      el.style.display = modes.includes(mode) ? "" : "none";
    });
    // Criteria labelling differs between likert (scored) and span_only (categories).
    const lbl = document.getElementById("criteria-label");
    const hint = document.getElementById("criteria-hint");
    if (lbl && hint) {
      if (mode === "span_only") {
        lbl.innerHTML = 'Error-type categories <span class="req">*</span>';
        hint.textContent = "Each becomes a colour-coded error type annotators can mark spans with. Give each a name and a short definition.";
      } else {
        lbl.innerHTML = 'Evaluation criteria <span class="req">*</span>';
        hint.textContent = "Define what annotators will rate. Each has a name and a definition shown to annotators.";
      }
    }
    // In span_only the spans are always on, so hide the enable toggle but keep scope.
    const enableField = document.getElementById("span-enable-field");
    if (enableField) enableField.style.display = (mode === "likert") ? "" : "none";
    if (mode === "likert") toggleSpanScope();
    else {
      const block = document.getElementById("span-scope-block");
      if (block && mode === "span_only") block.style.display = "";
    }
  };

  // --- criterion rows -------------------------------------------------------
  window.addCriterionRow = function (name, desc) {
    const container = document.getElementById('criteria-rows');
    const row = document.createElement('div');
    row.className = 'criterion-row';
    row.setAttribute('style', 'display:block; border:1px solid var(--aura-border); border-radius:8px; padding:18px; background:var(--aura-bg); position:relative;');
    row.innerHTML =
      '<div style="margin-bottom:12px; margin-right:48px;">' +
        '<input type="text" name="crit_name" placeholder="Criterion name (e.g. Accuracy)" class="ff-in crit-name-input" style="font-weight:600;">' +
      '</div>' +
      '<button type="button" class="btn-remove-row" title="Remove criterion" onclick="removeCriterionRow(this)" style="position:absolute; top:18px; right:18px; width:36px; height:36px; border-radius:6px; border:1px solid #E2D6D1; background:#FFFFFF; color:#B07A6E; font-size:18px; line-height:1; cursor:pointer; display:flex; align-items:center; justify-content:center;">×</button>' +
      '<textarea name="crit_desc" placeholder="Describe what this criterion measures and how annotators should judge it…" class="ff-in crit-desc-input" style="min-height:128px;"></textarea>';
    container.appendChild(row);
    if (name) row.querySelector('.crit-name-input').value = name;
    if (desc) row.querySelector('.crit-desc-input').value = desc;
  };
  window.removeCriterionRow = function (btn) {
    const rows = document.querySelectorAll('#criteria-rows .criterion-row');
    if (rows.length <= 1) { alert('You need at least one.'); return; }
    btn.closest('.criterion-row').remove();
  };

  // --- preference rows ------------------------------------------------------
  window.addPreferenceRow = function (label) {
    const container = document.getElementById('preference-rows');
    const row = document.createElement('div');
    row.className = 'criterion-row';
    row.setAttribute('style', 'display:flex; align-items:center; gap:12px;');
    row.innerHTML =
      '<input type="text" name="pref_label" placeholder="e.g. A is better" class="ff-in pref-label-input" style="font-weight:600;">' +
      '<button type="button" class="btn-remove-row" title="Remove" onclick="removePreferenceRow(this)" style="flex-shrink:0; width:36px; height:36px; border-radius:6px; border:1px solid #E2D6D1; background:#FFFFFF; color:#B07A6E; font-size:18px; line-height:1; cursor:pointer; display:flex; align-items:center; justify-content:center;">×</button>';
    container.appendChild(row);
    if (label) row.querySelector('.pref-label-input').value = label;
  };
  window.removePreferenceRow = function (btn) {
    const rows = document.querySelectorAll('#preference-rows .criterion-row');
    if (rows.length <= 2) { alert('You need at least two preference options.'); return; }
    btn.closest('.criterion-row').remove();
  };

  // --- span scope -----------------------------------------------------------
  window.toggleSpanScope = function () {
    if (currentMode() !== "likert") return;
    const enabled = document.getElementById('enable_spans').checked;
    document.getElementById('span-scope-block').style.display = enabled ? '' : 'none';
  };

  // --- scale type switching -------------------------------------------------
  window.onScaleTypeChange = function () {
    const picked = document.querySelector('input[name="scale_type"]:checked');
    const type = picked ? picked.value : 'likert';
    const isLikert = (type === 'likert');
    const likert = document.getElementById('likert-config');
    const cont = document.getElementById('continuous-config');
    const ld = document.getElementById('scale_design_likert');
    const cd = document.getElementById('scale_design_continuous');
    if (likert) likert.style.display = isLikert ? '' : 'none';
    if (cont) cont.style.display = isLikert ? 'none' : '';
    if (ld && cd) {
      ld.disabled = !isLikert; cd.disabled = isLikert;
      ld.name = isLikert ? 'scale_design' : '';
      cd.name = isLikert ? '' : 'scale_design';
    }
    updateLikertDesignDesc();
  };

  function updateLikertDesignDesc() {
    const sel = document.getElementById('scale_design_likert');
    const out = document.getElementById('likert-design-desc');
    if (sel && out && window.SCALE_LIKERT_DESC_MAP) {
      out.textContent = window.SCALE_LIKERT_DESC_MAP[sel.value] || '';
    }
  }

  // --- Likert per-point labels ---------------------------------------------
  window.rebuildLikertLabels = function () {
    const grid = document.getElementById('likert-labels');
    if (!grid) return;
    const ptsInput = document.getElementById('scale_points');
    let pts = parseInt(ptsInput && ptsInput.value, 10);
    if (isNaN(pts)) pts = 5;
    pts = Math.max(2, Math.min(11, pts));
    const existing = {};
    grid.querySelectorAll('input[data-point]').forEach(inp => { existing[inp.dataset.point] = inp.value; });
    const prefill = window.SCALE_LABELS_PREFILL || {};
    const defaultsByPts = window.SCALE_DEFAULT_LABELS_BY_POINTS || {};
    const defaults = defaultsByPts[pts] || defaultsByPts[String(pts)] || [];
    grid.innerHTML = '';
    for (let v = 1; v <= pts; v++) {
      const wrap = document.createElement('div');
      wrap.className = 'likert-label-cell';
      let val;
      if (existing[v] != null && existing[v] !== '') {
        val = existing[v];
      } else if (prefill[v] != null && prefill[v] !== '') {
        val = prefill[v];
      } else {
        val = defaults[v - 1] || '';
      }
      wrap.innerHTML =
        '<span class="likert-label-num">' + v + '</span>' +
        '<input type="text" name="scale_label_' + v + '" data-point="' + v + '" ' +
        'value="' + String(val).replace(/"/g, '&quot;') + '" placeholder="Label for ' + v + '" class="ff-in">';
      grid.appendChild(wrap);
    }
  };

  // --- AI assistant config --------------------------------------------------
  window.onAiToggle = function () {
    const on = document.getElementById('ai_enabled').checked;
    const cfg = document.getElementById('ai-config');
    if (cfg) cfg.style.display = on ? '' : 'none';
    if (on) onAiProvider();
  };

  window.onAiProvider = function () {
    const sel = document.getElementById('ai_provider');
    if (!sel) return;
    const meta = (window.AI_PROVIDERS || {})[sel.value] || {};
    const keyField = document.getElementById('ai-key-field');
    const urlField = document.getElementById('ai-base-url-field');
    const desc = document.getElementById('ai-provider-desc');
    const model = document.getElementById('ai_model');
    const baseUrl = document.getElementById('ai_base_url');
    if (keyField) keyField.style.display = meta.needs_key ? '' : 'none';
    if (urlField) urlField.style.display = meta.needs_base_url ? '' : 'none';
    if (desc) desc.textContent = meta.desc || '';
    if (model) model.placeholder = meta.default_model || 'model name';
    if (baseUrl) baseUrl.placeholder = meta.default_base_url || 'https://…';
  };

  window.testAiConnection = function () {
    const out = document.getElementById('ai-test-result');
    const sel = document.getElementById('ai_provider');
    if (!sel) return;
    out.textContent = 'Testing…'; out.className = 'ai-test-result';
    const body = {
      provider: sel.value,
      model: (document.getElementById('ai_model') || {}).value || '',
      base_url: (document.getElementById('ai_base_url') || {}).value || '',
      api_key: (document.getElementById('ai_api_key') || {}).value || '',
      campaign_id: window.AI_CAMPAIGN_ID || '',
    };
    fetch(window.AI_TEST_URL, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(r => r.json()).then(d => {
      out.textContent = (d.ok ? '✓ ' : '✗ ') + (d.message || (d.ok ? 'OK' : 'Failed'));
      out.className = 'ai-test-result ' + (d.ok ? 'ok' : 'err');
    }).catch(() => { out.textContent = '✗ Request failed'; out.className = 'ai-test-result err'; });
  };

  // --- Scripts: per-language options, labels, legacy sync, validation -------
  function currentTaskPreset() {
    var sel = document.getElementById('task_type');
    var presets = window.TASK_PRESETS || {};
    return (sel && presets[sel.value]) || presets['translation'] || null;
  }

  function getLanguageScripts() {
    if (window.__LANG_SCRIPTS) return window.__LANG_SCRIPTS;
    var el = document.getElementById('language-scripts-data');
    var data = {};
    if (el) { try { data = JSON.parse(el.textContent || '{}'); } catch (e) { data = {}; } }
    var norm = {};
    Object.keys(data).forEach(function (k) { norm[k.toLowerCase()] = data[k]; });
    window.__LANG_SCRIPTS = norm;
    return norm;
  }

  function allowedScriptsFor(language) {
    var map = getLanguageScripts();
    var key = (language || '').trim().toLowerCase();
    if (!key) return null;                        // no language -> unrestricted
    if (map[key]) return map[key];
    var base = key.replace(/\(.*?\)/g, '').trim();
    if (base && map[base]) return map[base];
    return null;                                  // unknown language -> unrestricted
  }

  function filterScriptSelect(select, language) {
    if (!select) return;
    var allowed = allowedScriptsFor(language);    // null = all allowed
    Array.prototype.forEach.call(select.options, function (opt) {
      if (opt.value === '') { opt.hidden = false; opt.disabled = false; return; }
      var ok = (allowed === null) || allowed.indexOf(opt.value) !== -1;
      opt.hidden = !ok;
      opt.disabled = !ok;
    });
    if (select.value && allowed !== null && allowed.indexOf(select.value) === -1) {
      select.value = '';
    }
  }

  function syncLegacyScript() {
    var hidden = document.getElementById('script');
    if (!hidden) return;
    var t = (document.getElementById('target_script') || {}).value || '';
    var s = (document.getElementById('source_script') || {}).value || '';
    hidden.value = t || s || '';
  }

  function checkSameLangScript() {
    var warn = document.getElementById('script-warning');
    if (!warn) return;
    var preset = currentTaskPreset();
    // Only inherently bilingual tasks (MT) forbid identical source/target.
    if (!preset || preset.cross_lingual !== 'required') { warn.style.display = 'none'; return; }
    var srcLang = ((document.getElementById('source_language') || {}).value || '').trim().toLowerCase();
    var tgtLang = ((document.getElementById('target_language') || {}).value || '').trim().toLowerCase();
    var srcScript = (document.getElementById('source_script') || {}).value || '';
    var tgtScript = (document.getElementById('target_script') || {}).value || '';
    if (srcLang && tgtLang && srcLang === tgtLang && srcScript === tgtScript) {
      warn.textContent = 'Source and target are identical (same language and script). '
        + 'Use two different languages, or the same language with two different scripts '
        + '(for a transliteration / script-conversion task).';
      warn.style.display = '';
    } else {
      warn.style.display = 'none';
    }
  }

  window.updateScriptUI = function () {
    var srcLang = (document.getElementById('source_language') || {}).value || '';
    var tgtLang = (document.getElementById('target_language') || {}).value || '';
    var srcSel = document.getElementById('source_script');
    var tgtSel = document.getElementById('target_script');
    filterScriptSelect(srcSel, srcLang);
    filterScriptSelect(tgtSel, tgtLang);

    var preset = currentTaskPreset();
    var mono = !!(preset && preset.cross_lingual === 'none');
    var srcField = document.getElementById('source-script-field');
    if (srcField) srcField.style.display = mono ? 'none' : '';
    if (mono && srcSel) srcSel.value = '';

    var srcLabel = document.getElementById('source-script-label');
    var tgtLabel = document.getElementById('target-script-label');
    if (srcLabel) srcLabel.textContent = srcLang.trim() ? (srcLang.trim() + ' script') : 'Source language script';
    if (tgtLabel) {
      if (mono) tgtLabel.textContent = tgtLang.trim() ? (tgtLang.trim() + ' script') : 'Script';
      else tgtLabel.textContent = tgtLang.trim() ? (tgtLang.trim() + ' script') : 'Target language script';
    }
    syncLegacyScript();
    checkSameLangScript();
  };

  // --- Pairwise wording: reflect the task's output noun (translations/summaries/…) ---
  var OUTPUT_PLURALS = {
    translation: 'translations', summarization: 'summaries', qa: 'answers',
    dialogue: 'responses', simplification: 'simplifications', factuality: 'outputs',
    general: 'outputs', custom: 'outputs',
  };
  function outputNounPlural() {
    var sel = document.getElementById('task_type');
    var tid = sel ? sel.value : 'translation';
    if (OUTPUT_PLURALS[tid]) return OUTPUT_PLURALS[tid];
    var preset = currentTaskPreset();
    var out = (preset && preset.output ? preset.output : 'output').trim().toLowerCase();
    return out ? out + 's' : 'outputs';
  }
  window.updatePairwiseWording = function () {
    var noun = outputNounPlural();
    var desc = document.getElementById('pairwise-mode-desc');
    if (desc) {
      desc.textContent = 'Annotators compare two candidate ' + noun
        + ' and choose which is better, using preference options you define.';
    }
    var hint = document.getElementById('pairwise-pref-hint');
    if (hint) {
      hint.innerHTML = 'The choices an annotator picks from when comparing candidate '
        + '<strong>A</strong> vs <strong>B</strong>. Order them as you want them shown. At least two.';
    }
  };

  // --- init -----------------------------------------------------------------
  // ---- Task type: relabel input/output + suggest criteria, toggle languages ----
  window.onTaskTypeChange = function (prefill) {
    const sel = document.getElementById('task_type');
    if (!sel) return;
    const presets = window.TASK_PRESETS || {};
    const p = presets[sel.value] || presets['translation'] || { input: 'Source', output: 'Translation', criteria: [], bilingual: true };
    const inp = document.getElementById('input_label');
    const out = document.getElementById('output_label');
    if (inp) inp.placeholder = p.input;
    if (out) out.placeholder = p.output;
    // Languages only required for bilingual tasks (e.g. translation).
    const row = document.getElementById('language-row');
    if (row) {
      row.querySelectorAll('.lang-req').forEach(function (el) { el.style.display = p.bilingual ? '' : 'none'; });
      const sl = document.getElementById('source_language');
      if (sl) { sl.closest('.field').querySelector('label').firstChild.textContent =
        p.bilingual ? 'Source language ' : 'Source language (optional) '; }
      const tl = document.getElementById('target_language');
      if (tl) { tl.closest('.field').querySelector('label').firstChild.textContent =
        p.bilingual ? 'Target language ' : 'Target language (optional) '; }
    }
    // Keep the per-side script fields and pairwise wording in sync with the task type.
    if (typeof window.updateScriptUI === 'function') window.updateScriptUI();
    if (typeof window.updatePairwiseWording === 'function') window.updatePairwiseWording();
  };

  document.addEventListener('DOMContentLoaded', function () {
    if (document.querySelectorAll('#criteria-rows .criterion-row').length === 0) addCriterionRow();
    if (document.querySelectorAll('#preference-rows .criterion-row').length === 0) {
      addPreferenceRow('A is better'); addPreferenceRow('About the same'); addPreferenceRow('B is better');
    }
    onScaleTypeChange();
    rebuildLikertLabels();
    onEvalModeChange();
    if (document.getElementById('ai_enabled')) onAiToggle();
    const tt = document.getElementById('task_type');
    if (tt) {
      tt.addEventListener('change', function () { onTaskTypeChange(true); });
      onTaskTypeChange(false);   // set placeholders/labels without overwriting criteria
    }
    const dm = document.getElementById('difficulty_method');
    if (dm) {
      const toggleDiff = function () {
        const row = document.getElementById('difficulty-length-row');
        if (row) row.style.display = (dm.value === 'length') ? '' : 'none';
        const served = document.getElementById('served-difficulty-row');
        if (served) served.style.display = (dm.value === 'none') ? 'none' : '';
        const exp = document.getElementById('expertise_matching');
        const checks = document.getElementById('served-difficulty-checks');
        if (checks) checks.style.display = (exp && exp.checked) ? 'none' : '';
      };
      const expChk = document.getElementById('expertise_matching');
      if (expChk) expChk.addEventListener('change', toggleDiff);
      dm.addEventListener('change', toggleDiff);
      toggleDiff();
    }

    const ld = document.getElementById('scale_design_likert');
    if (ld) ld.addEventListener('change', updateLikertDesignDesc);

    // Per-side script fields: react to language typing and script selection.
    ['source_language', 'target_language'].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.addEventListener('input', function () { window.updateScriptUI(); });
    });
    ['source_script', 'target_script'].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.addEventListener('change', function () { syncLegacyScript(); checkSameLangScript(); });
    });
    if (typeof window.updateScriptUI === 'function') window.updateScriptUI();
    if (typeof window.updatePairwiseWording === 'function') window.updatePairwiseWording();

    const form = document.querySelector('form.register-form');
    if (form) {
      form.addEventListener('submit', function (e) {
        const mode = currentMode();
        if (mode === 'likert' && document.getElementById('enable_spans').checked) {
          if (!document.querySelector('input[name="span_scope"]:checked')) {
            e.preventDefault();
            const hint = document.getElementById('span-scope-required-hint');
            if (hint) hint.style.display = '';
            document.getElementById('span-scope-block').scrollIntoView({ behavior: 'smooth', block: 'center' });
          }
        }
      });
    }
  });
})();
