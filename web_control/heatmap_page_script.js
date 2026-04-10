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
      refreshBtn.disabled = st.ledger_exists === false;
      refreshBtn.title = 'Clear cache and reload from SQLite ledger';
      if (!data.ok && st.ledger_exists === false) {
        refreshStatus.textContent = 'Ledger missing — compile the pipeline to create ledger.sqlite.';
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
      const fg = v.cellFg || [];
      const click = v.clickable;
      const colTotals = v.columnTotals || [];
      const colAverages = v.columnAverages || [];
      const rowTotals = v.rowTotals || [];
      const rowAverages = v.rowAverages || [];
      const rowYtdSums = v.rowYtdSums || [];
      const rowYtdAverages = v.rowYtdAverages || [];
      const rowRolling12Sums = v.rowRolling12Sums || [];
      const rowRolling12Averages = v.rowRolling12Averages || [];
      let html = '<table class="hm-grid"><thead>';
      html += '<tr><th rowspan="2" class="corner">חודש \\ קטגוריה</th>';
      html += '<th rowspan="2" class="hm-metric-h hm-rowsum-h">סה״כ<br/>חודש</th>';
      html += '<th rowspan="2" class="hm-metric-h hm-rowavg-h">ממוצע<br/>חודש</th>';
      html += '<th rowspan="2" class="hm-metric-h hm-ytdsum-h">YTD<br/>סה״כ</th>';
      html += '<th rowspan="2" class="hm-metric-h hm-ytdavg-h">YTD<br/>ממוצע</th>';
      html += '<th rowspan="2" class="hm-metric-h hm-l12sum-h">12M<br/>סה״כ</th>';
      html += '<th rowspan="2" class="hm-metric-h hm-l12avg-h">12M<br/>ממוצע</th>';
      for (let c = 0; c < cats.length; c++) {
        const t = (colTotals[c] !== undefined && colTotals[c] !== null) ? colTotals[c] : '';
        const a = (colAverages[c] !== undefined && colAverages[c] !== null) ? colAverages[c] : '';
        html += '<th class="hm-colsum"><div class="colsum-wrap">' +
          '<div><span class="metric-label">Σ</span><span class="metric-val">' + esc(t) + '</span></div>' +
          '<div><span class="metric-label">Avg</span><span class="metric-val">' + esc(a) + '</span></div>' +
          '</div></th>';
      }
      html += '</tr><tr>';
      for (let c = 0; c < cats.length; c++) {
        html += '<th>' + esc(cats[c]) + '</th>';
      }
      html += '</tr></thead><tbody>';
      for (let i = 0; i < months.length; i++) {
        const monthRaw = String(months[i] || '');
        const mh = esc(monthRaw);
        const prevMonthRaw = i > 0 ? String(months[i - 1] || '') : '';
        const year = monthRaw.slice(0, 4);
        const prevYear = prevMonthRaw.slice(0, 4);
        const monthNum = monthRaw.slice(5, 7);
        const isYearBoundary = i > 0 && year && prevYear && year !== prevYear;
        const isL12Boundary = (i > 0 && (i % 12) === 0);
        const rowClass = (isYearBoundary ? ' year-start' : '') + (isL12Boundary ? ' group-boundary' : '');
        const l12Chip = isL12Boundary ? ('<span class="l12-chip">12m</span>') : '';
        const rt = (rowTotals[i] !== undefined && rowTotals[i] !== null) ? esc(rowTotals[i]) : '';
        const ra = (rowAverages[i] !== undefined && rowAverages[i] !== null) ? esc(rowAverages[i]) : '';
        const ytds = (rowYtdSums[i] !== undefined && rowYtdSums[i] !== null) ? esc(rowYtdSums[i]) : '';
        const ytda = (rowYtdAverages[i] !== undefined && rowYtdAverages[i] !== null) ? esc(rowYtdAverages[i]) : '';
        const l12s = (rowRolling12Sums[i] !== undefined && rowRolling12Sums[i] !== null) ? esc(rowRolling12Sums[i]) : '';
        const l12a = (rowRolling12Averages[i] !== undefined && rowRolling12Averages[i] !== null) ? esc(rowRolling12Averages[i]) : '';
        html += '<tr class="' + rowClass.trim() + '"><th class="row-h"><span class="month-markers">' + l12Chip + '</span><span class="month-label">' + mh + '</span></th>';
        html += '<td class="hm-rowtot hm-rowmetric">' + rt + '</td>';
        html += '<td class="hm-rowavg hm-rowmetric">' + ra + '</td>';
        html += '<td class="hm-ytdsum hm-rowmetric">' + ytds + '</td>';
        html += '<td class="hm-ytdavg hm-rowmetric">' + ytda + '</td>';
        html += '<td class="hm-l12sum hm-rowmetric">' + l12s + '</td>';
        html += '<td class="hm-l12avg hm-rowmetric">' + l12a + '</td>';
        for (let j = 0; j < cats.length; j++) {
          const lab = (labels[i] && labels[i][j] !== undefined) ? labels[i][j] : '';
          const b = (bg[i] && bg[i][j]) ? bg[i][j] : '#333';
          const f = (fg[i] && fg[i][j]) ? fg[i][j] : '#f4f6fb';
          const cl = (click[i] && click[i][j]) ? 'cell clickable' : 'cell';
          const ym = months[i];
          const cat = cats[j];
          html += '<td class="' + cl + '" style="background-color:' + b + ';color:' + f + ';text-shadow:none" data-ym="' +
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
            if (data.sourceStatus && data.sourceStatus.ledger_exists === false) {
              refreshStatus.textContent = 'No ledger.sqlite — run compile first.';
            }
            return;
          }
          showErr('');
          var st = data.sourceStatus || {};
          refreshStatus.textContent = 'Ledger: ' + (st.ledger_path || data.source || '') +
            (typeof st.transaction_count === 'number' && st.transaction_count >= 0
              ? (' · ' + st.transaction_count + ' rows')
              : '');
          setView(currentView);
        })
        .catch(function (e) {
          showErr('שגיאת רשת: ' + e);
          refreshStatus.textContent = '';
        });
    }

    refreshBtn.addEventListener('click', function () {
      refreshBtn.disabled = true;
      refreshStatus.textContent = 'Reloading from ledger…';
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
