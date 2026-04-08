  (function () {
    const errEl = document.getElementById('err');
    const gridEl = document.getElementById('grid');
    const titleEl = document.getElementById('heatmap-title');
    const statsCat = document.getElementById('stats-cat');
    const statsMonth = document.getElementById('stats-month');
    const refreshBtn = document.getElementById('btn-refresh');
    const refreshStatus = document.getElementById('refresh-status');
    let snapshot = null;
    let currentView = 'expense';

    function showErr(msg) {
      errEl.style.display = msg ? 'block' : 'none';
      errEl.textContent = msg || '';
    }

    function applySourceToolbar(data) {
      const st = data && data.sourceStatus;
      if (!st) return;
      const can = !!st.configured;
      refreshBtn.disabled = !can;
      refreshBtn.title = can
        ? ('Read-only pull from all-time cloud tab "' + (st.sheet_name || 'Totals') + '"')
        : 'Set GOOGLE_API_USER and GOOGLE_WORKSHEET_ID to enable refresh';
      if (!data.ok && !can) {
        refreshStatus.textContent = 'Sheets API not configured (env vars missing or credentials file not found).';
      }
    }

    function detailUrl(type, ym, cat) {
      const p = new URLSearchParams();
      p.set('type', type);
      p.set('ym', ym);
      p.set('cat', cat);
      return '/heatmap/detail?' + p.toString();
    }

    function esc(s) {
      return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }

    /** Parse body as JSON; if the server returned HTML/plain, show a short preview (avoids opaque JSON.parse errors). */
    function fetchJson(url, options) {
      return fetch(url, options).then(function (r) {
        return r.text().then(function (text) {
          var data;
          try {
            data = JSON.parse(text);
          } catch (e) {
            var preview = (text || '').replace(/\s+/g, ' ').slice(0, 220);
            throw new Error('HTTP ' + r.status + ' — not JSON: ' + preview);
          }
          var out = {};
          out.status = r.status;
          out.ok = r.ok;
          out.data = data;
          return out;
        });
      });
    }

    function renderGrid(view) {
      const v = snapshot && snapshot.views && snapshot.views[view];
      if (!v) {
        gridEl.innerHTML = '<p class="no-data">אין נתונים</p>';
        return;
      }
      titleEl.textContent = v.title + ' — לחץ על תא לפירוט';
      const months = v.months;
      const cats = v.categories;
      const labels = v.labels;
      const bg = v.cellBg;
      const click = v.clickable;
      const colTotals = v.columnTotals || [];
      const rowTotals = v.rowTotals || [];
      let html = '<table class="hm-grid"><thead>';
      html += '<tr><th rowspan="2" class="corner">חודש \\ קטגוריה</th>';
      html += '<th rowspan="2" class="hm-rowsum-h">סה״כ<br/>חודש</th>';
      for (let c = 0; c < cats.length; c++) {
        const t = (colTotals[c] !== undefined && colTotals[c] !== null) ? colTotals[c] : '';
        html += '<th class="hm-colsum">' + esc(t) + '</th>';
      }
      html += '</tr><tr>';
      for (let c = 0; c < cats.length; c++) {
        html += '<th>' + esc(cats[c]) + '</th>';
      }
      html += '</tr></thead><tbody>';
      for (let i = 0; i < months.length; i++) {
        const mh = esc(months[i]);
        const rt = (rowTotals[i] !== undefined && rowTotals[i] !== null) ? esc(rowTotals[i]) : '';
        html += '<tr><th class="row-h">' + mh + '</th>';
        html += '<td class="hm-rowtot">' + rt + '</td>';
        for (let j = 0; j < cats.length; j++) {
          const lab = (labels[i] && labels[i][j] !== undefined) ? labels[i][j] : '';
          const b = (bg[i] && bg[i][j]) ? bg[i][j] : '#333';
          const cl = (click[i] && click[i][j]) ? 'cell clickable' : 'cell';
          const ym = months[i];
          const cat = cats[j];
          html += '<td class="' + cl + '" style="background-color:' + b + '" data-ym="' +
            encodeURIComponent(ym) + '" data-cat="' + encodeURIComponent(cat) + '">' +
            esc(lab) + '</td>';
        }
        html += '</tr>';
      }
      html += '</tbody></table>';
      gridEl.innerHTML = html;
      gridEl.querySelectorAll('td.cell.clickable').forEach(function (td) {
        td.addEventListener('click', function () {
          const ym = decodeURIComponent(td.getAttribute('data-ym') || '');
          const cat = decodeURIComponent(td.getAttribute('data-cat') || '');
          window.open(detailUrl(v.reportType, ym, cat), '_blank');
        });
      });
    }

    function renderStats(view) {
      const sh = snapshot && snapshot.statsHtml && snapshot.statsHtml[view];
      if (!sh) {
        statsCat.innerHTML = '';
        statsMonth.innerHTML = '';
        return;
      }
      statsCat.innerHTML = sh.byCategory || '';
      statsMonth.innerHTML = sh.byMonth || '';
    }

    function setView(view) {
      currentView = view;
      document.querySelectorAll('#tabs button').forEach(function (b) {
        b.classList.toggle('active', b.getAttribute('data-view') === view);
      });
      renderGrid(view);
      renderStats(view);
    }

    document.querySelectorAll('#tabs button').forEach(function (btn) {
      btn.addEventListener('click', function () {
        setView(btn.getAttribute('data-view'));
      });
    });

    function loadSnapshot() {
      refreshStatus.textContent = 'Loading…';
      fetchJson('/heatmap/api/data', { cache: 'no-store' })
        .then(function (res) {
          var data = res.data;
          snapshot = data;
          applySourceToolbar(data);
          if (!data.ok) {
            showErr(data.message || 'טעינת נתונים נכשלה');
            gridEl.innerHTML = '';
            statsCat.innerHTML = '';
            statsMonth.innerHTML = '';
            if (data.sourceStatus && data.sourceStatus.configured) {
              refreshStatus.textContent = 'Use Refresh to pull from Google Sheet.';
            }
            return;
          }
          showErr('');
          refreshStatus.textContent = 'Synced file: ' + (data.source || '') +
            ' — tab «' + ((data.sourceStatus && data.sourceStatus.sheet_name) || '') + '»';
          setView(currentView);
        })
        .catch(function (e) {
          showErr('שגיאת רשת: ' + e);
          refreshStatus.textContent = '';
        });
    }

    refreshBtn.addEventListener('click', function () {
      refreshBtn.disabled = true;
      refreshStatus.textContent = 'Fetching from Google…';
      fetchJson('/heatmap/api/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      })
        .then(function (res) {
          var j = res.data;
          if (!j.ok) {
            showErr(j.message || 'Refresh failed');
            refreshStatus.textContent = j.message || '';
          } else {
            showErr('');
            refreshStatus.textContent = j.message || 'Updated.';
          }
          loadSnapshot();
        })
        .catch(function (e) {
          showErr('שגיאת רשת: ' + e);
          refreshStatus.textContent = '';
          loadSnapshot();
        })
        .finally(function () { refreshBtn.disabled = false; });
    });

    currentView = 'expense';
    loadSnapshot();
  })();
