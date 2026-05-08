"""Holdings page HTML for web_control (/holdings/)."""

from __future__ import annotations

from . import control_nav


def holdings_shell_html() -> str:
    return (
        """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Holdings Control</title>
  <style>
"""
        + control_nav.control_topnav_css()
        + """
    :root { font-family: system-ui, Segoe UI, Roboto, sans-serif; background: #121316; color: #e8e8ec; }
    body { max-width: 70rem; margin: 0 auto; padding: 1.1rem 1rem 2rem; }
    .card { background: #1c1d22; border: 1px solid #2b2c33; border-radius: 10px; padding: 0.9rem 1rem; margin: 0.8rem 0; }
    .row { display: flex; gap: 0.6rem; flex-wrap: wrap; align-items: end; }
    .col { display: flex; flex-direction: column; gap: 0.2rem; }
    input, select, textarea, button { font: inherit; }
    input, select, textarea {
      background: #121316; color: inherit; border: 1px solid #3a3b44; border-radius: 6px; padding: 0.42rem 0.5rem;
    }
    textarea { width: 100%; min-height: 10rem; }
    button { cursor: pointer; border-radius: 8px; border: 1px solid #4c6ef5; background: #4c6ef5; color: #fff; padding: 0.45rem 0.85rem; }
    button.secondary { border-color: #495057; background: transparent; color: #ced4da; }
    .hint { opacity: 0.72; font-size: 0.86rem; }
    .pill { display:inline-block; padding:0.15rem 0.45rem; border-radius:999px; background:#2b2c33; font-size:0.78rem; }
    table { border-collapse: collapse; width: 100%; }
    th, td { border: 1px solid #2b2c33; padding: 0.4rem 0.5rem; text-align: right; }
    th { background: #1a1b20; position: sticky; top: 0; }
    .scroll { max-height: 26rem; overflow: auto; border: 1px solid #2b2c33; border-radius: 8px; }
    .grid2 { display:grid; gap:0.6rem; grid-template-columns: 1fr 1fr; }
    @media (max-width: 900px) { .grid2 { grid-template-columns: 1fr; } }
    .mode-row { display:flex; gap:0.4rem; margin-bottom:0.6rem; }
    .mode { display:none; }
    .mode.active { display:block; }
    .activity-row { display:grid; grid-template-columns: 2fr 1.2fr auto; gap:0.5rem; margin:0.45rem 0; }
    .status { white-space: pre-wrap; font-family: ui-monospace, Consolas, monospace; background:#0b0c0f; border:1px solid #2b2c33; border-radius:8px; padding:0.6rem; }
  </style>
</head>
<body>
"""
        + control_nav.control_topnav_html()
        + """
  <h1>Holdings Control</h1>
  <p class="hint">Explore holdings over time and ingest missing data quickly from form rows or pasted grid.</p>

  <div class="card">
    <h2 style="margin-top:0">Timeline</h2>
    <div class="row">
      <label class="col">From <input type="date" id="tl_from"/></label>
      <label class="col">To <input type="date" id="tl_to"/></label>
      <label class="col" style="min-width:16rem">Activities
        <select id="tl_acts" multiple size="4"></select>
      </label>
      <button type="button" id="btn_tl_reload">Reload</button>
    </div>
    <p id="meta_line" class="hint"></p>
    <div id="summary" class="hint"></div>
    <div class="scroll"><table id="tl_table"></table></div>
  </div>

  <div class="card">
    <h2 style="margin-top:0">Manual Ingest</h2>
    <div class="mode-row">
      <button type="button" class="secondary" id="mode_manual_btn">One Date + Activities</button>
      <button type="button" class="secondary" id="mode_paste_btn">Paste Grid</button>
    </div>

    <div id="mode_manual" class="mode active">
      <div class="row">
        <label class="col">As of date <input type="date" id="m_date"/></label>
        <button type="button" class="secondary" id="add_activity_row">Add activity row</button>
      </div>
      <div id="activity_rows"></div>
      <button type="button" id="btn_manual_preview">Preview + Save</button>
    </div>

    <div id="mode_paste" class="mode">
      <p class="hint">Paste tab-separated grid: first column date, other columns activity types.</p>
      <textarea id="paste_grid" placeholder="תאריך\tעובר ושב\tניירות ערך\n2026-05-01\t1200\t3300"></textarea>
      <div class="row">
        <button type="button" class="secondary" id="btn_parse_paste">Parse</button>
        <button type="button" id="btn_paste_preview_save">Preview + Save</button>
      </div>
      <div id="paste_parse_hint" class="hint"></div>
    </div>

    <div id="ingest_status" class="status"></div>
  </div>

  <script>
  (function () {
    let meta = null;
    let parsedPasteRows = [];

    const metaLine = document.getElementById('meta_line');
    const tlActs = document.getElementById('tl_acts');
    const tlTable = document.getElementById('tl_table');
    const summary = document.getElementById('summary');
    const ingestStatus = document.getElementById('ingest_status');
    const activityRows = document.getElementById('activity_rows');
    const pasteParseHint = document.getElementById('paste_parse_hint');

    function setStatus(obj) {
      ingestStatus.textContent = typeof obj === 'string' ? obj : JSON.stringify(obj, null, 2);
    }

    function fetchJson(url, opts) {
      return fetch(url, opts).then(async function (r) {
        const text = await r.text();
        let data = {};
        try { data = text ? JSON.parse(text) : {}; } catch (_) {}
        return { ok: r.ok, status: r.status, data: data };
      });
    }

    function selectedActivities() {
      return Array.from(tlActs.selectedOptions || []).map(function (o) { return o.value; });
    }

    function fillActivitySelect(values) {
      tlActs.innerHTML = '';
      values.forEach(function (v) {
        const o = document.createElement('option');
        o.value = v;
        o.textContent = v;
        tlActs.appendChild(o);
      });
    }

    function addActivityRow(activity, balance) {
      const row = document.createElement('div');
      row.className = 'activity-row';
      const datalistId = 'activities_dl';
      row.innerHTML =
        '<input type="text" list="' + datalistId + '" class="ar_activity" placeholder="activity type" value="' + (activity || '') + '"/>' +
        '<input type="text" class="ar_balance" placeholder="balance ILS" value="' + (balance || '') + '"/>' +
        '<button type="button" class="secondary ar_remove">Remove</button>';
      row.querySelector('.ar_remove').onclick = function () { row.remove(); };
      activityRows.appendChild(row);
    }

    async function loadMeta() {
      const res = await fetchJson('/api/holdings/meta', { cache: 'no-store' });
      meta = res.data || {};
      fillActivitySelect(meta.activity_types || []);
      metaLine.textContent =
        'Rows: ' + (meta.row_count || 0) +
        ' · Dates: ' + (meta.date_count || 0) +
        ' · Range: ' + (meta.min_date || '-') + ' .. ' + (meta.max_date || '-');

      const existing = document.getElementById('activities_dl');
      if (existing) existing.remove();
      const dl = document.createElement('datalist');
      dl.id = 'activities_dl';
      (meta.activity_types || []).forEach(function (v) {
        const o = document.createElement('option');
        o.value = v;
        dl.appendChild(o);
      });
      document.body.appendChild(dl);
    }

    function renderTimelineRows(rows) {
      if (!rows || rows.length === 0) {
        tlTable.innerHTML = '<tr><td>No data</td></tr>';
        summary.textContent = '';
        return;
      }
      const acts = Array.from(new Set(rows.map(function (r) { return r.activity_type; }))).sort();
      const byDate = {};
      rows.forEach(function (r) {
        byDate[r.as_of_date] = byDate[r.as_of_date] || {};
        byDate[r.as_of_date][r.activity_type] = Number(r.balance_ils || 0);
      });
      const dates = Object.keys(byDate).sort();
      let html = '<thead><tr><th>Date</th>';
      acts.forEach(function (a) { html += '<th>' + a + '</th>'; });
      html += '<th>Total</th></tr></thead><tbody>';
      dates.forEach(function (d) {
        let total = 0;
        html += '<tr><td>' + d + '</td>';
        acts.forEach(function (a) {
          const v = Number((byDate[d] && byDate[d][a]) || 0);
          total += v;
          html += '<td>' + v.toLocaleString(undefined, { maximumFractionDigits: 2 }) + '</td>';
        });
        html += '<td>' + total.toLocaleString(undefined, { maximumFractionDigits: 2 }) + '</td></tr>';
      });
      html += '</tbody>';
      tlTable.innerHTML = html;
      const latest = dates[dates.length - 1];
      const prev = dates.length > 1 ? dates[dates.length - 2] : null;
      const totalLatest = acts.reduce(function (acc, a) { return acc + Number((byDate[latest] && byDate[latest][a]) || 0); }, 0);
      const totalPrev = prev ? acts.reduce(function (acc, a) { return acc + Number((byDate[prev] && byDate[prev][a]) || 0); }, 0) : 0;
      const delta = totalLatest - totalPrev;
      summary.textContent =
        'Latest total (' + latest + '): ' + totalLatest.toLocaleString(undefined, { maximumFractionDigits: 2 }) +
        (prev ? (' · Delta vs ' + prev + ': ' + delta.toLocaleString(undefined, { maximumFractionDigits: 2 })) : '');
    }

    async function loadTimeline() {
      const p = new URLSearchParams();
      const from = document.getElementById('tl_from').value;
      const to = document.getElementById('tl_to').value;
      if (from) p.set('from', from);
      if (to) p.set('to', to);
      selectedActivities().forEach(function (a) { p.append('activity', a); });
      const res = await fetchJson('/api/holdings/timeline?' + p.toString(), { cache: 'no-store' });
      renderTimelineRows((res.data && res.data.rows) || []);
    }

    function manualRowsPayload() {
      const asOf = document.getElementById('m_date').value;
      const rows = [];
      activityRows.querySelectorAll('.activity-row').forEach(function (row) {
        const activity = (row.querySelector('.ar_activity').value || '').trim();
        const balance = (row.querySelector('.ar_balance').value || '').trim();
        if (!activity || balance === '') return;
        rows.push({ as_of_date: asOf, activity_type: activity, balance_ils: balance });
      });
      return rows;
    }

    async function previewAndSaveRows(rows) {
      if (!rows || rows.length === 0) {
        setStatus('No rows to submit.');
        return;
      }
      const conflictRes = await fetchJson('/api/holdings/check-conflicts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rows: rows }),
      });
      const conflicts = (conflictRes.data && conflictRes.data.conflicts) || [];
      if (conflicts.length > 0) {
        const msg = 'Found ' + conflicts.length + ' conflict(s). Overwrite existing values?';
        if (!window.confirm(msg)) {
          setStatus({ ok: false, message: 'Cancelled by user.', conflicts: conflicts });
          return;
        }
      }
      const saveRes = await fetchJson('/api/holdings/manual-upsert-batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rows: rows, overwrite_conflicts: true }),
      });
      setStatus(saveRes.data || {});
      await loadMeta();
      await loadTimeline();
    }

    document.getElementById('btn_tl_reload').onclick = loadTimeline;
    document.getElementById('add_activity_row').onclick = function () { addActivityRow('', ''); };
    document.getElementById('btn_manual_preview').onclick = function () { previewAndSaveRows(manualRowsPayload()); };

    document.getElementById('mode_manual_btn').onclick = function () {
      document.getElementById('mode_manual').classList.add('active');
      document.getElementById('mode_paste').classList.remove('active');
    };
    document.getElementById('mode_paste_btn').onclick = function () {
      document.getElementById('mode_paste').classList.add('active');
      document.getElementById('mode_manual').classList.remove('active');
    };

    document.getElementById('btn_parse_paste').onclick = async function () {
      const text = document.getElementById('paste_grid').value || '';
      const r = await fetchJson('/api/holdings/parse-paste-grid', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: text }),
      });
      const data = r.data || {};
      parsedPasteRows = data.rows || [];
      pasteParseHint.textContent =
        (data.message || '') +
        ' Rows: ' + parsedPasteRows.length +
        ' Invalid cells: ' + ((data.invalid_cells && data.invalid_cells.length) || 0);
      setStatus(data);
    };

    document.getElementById('btn_paste_preview_save').onclick = function () {
      previewAndSaveRows(parsedPasteRows || []);
    };

    loadMeta().then(function () {
      addActivityRow('', '');
      addActivityRow('', '');
      return loadTimeline();
    }).catch(function (e) {
      setStatus('Error: ' + e);
    });
  })();
  </script>
</body>
</html>
"""
    )

