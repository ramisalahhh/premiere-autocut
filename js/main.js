/* AutoCut panel logic (CEP 11, Chromium + Node in mixed context) */
(function () {
  'use strict';

  var nodeRequire = (window.cep_node && window.cep_node.require) ? window.cep_node.require
    : (typeof require !== 'undefined' ? require : null);
  if (!nodeRequire) { alert('AutoCut: Node.js is not enabled in this panel.'); return; }
  var cp = nodeRequire('child_process');
  var fs = nodeRequire('fs');
  var path = nodeRequire('path');
  var os = nodeRequire('os');

  var IS_WIN = navigator.platform.indexOf('Win') >= 0;
  var EXT = (function () {
    var p = decodeURI(window.__adobe_cep__.getSystemPath('extension'));
    return IS_WIN ? p.replace(/^file:\/\/\//, '') : p.replace(/^file:\/\//, '');
  })();
  var ENGINE = path.join(EXT, 'engine');

  var $ = function (id) { return document.getElementById(id); };
  var busy = false;
  var lastSeqId = null;

  // ---------------------------------------------------------------- settings
  var FIELDS = {
    cut_gaps: 'bool', trim_ends: 'bool', cut_retakes: 'bool', cut_stutters: 'bool', cut_fillers: 'bool',
    cut_mistakes: 'bool', script: 'str', off_script_mode: 'str',
    reading_mode: 'str', noise_mode: 'str', model: 'str', language: 'str', python_path: 'str',
    min_gap: 'num', pad: 'num', retake_similarity: 'num', gaze_z: 'num', silence_margin_db: 'num'
  };
  var STORE = 'autocut.settings.v1';
  var DEFAULTS = {};

  function readSettings() {
    var s = {};
    Object.keys(FIELDS).forEach(function (k) {
      var el = $(k);
      s[k] = FIELDS[k] === 'bool' ? el.checked : FIELDS[k] === 'num' ? parseFloat(el.value) : el.value;
    });
    return s;
  }
  function applySettings(s) {
    Object.keys(s || {}).forEach(function (k) {
      var el = $(k);
      if (!el || !(k in FIELDS)) return;
      if (FIELDS[k] === 'bool') el.checked = !!s[k]; else el.value = s[k];
    });
    updateLabels();
  }
  function updateLabels() {
    [['min_gap', 's'], ['pad', 's'], ['retake_similarity', '%'], ['gaze_z', ''], ['silence_margin_db', ' dB']]
      .forEach(function (x) { var v = $('v_' + x[0]); if (v) v.textContent = $(x[0]).value + x[1]; });
  }
  function save() { try { localStorage.setItem(STORE, JSON.stringify(readSettings())); } catch (e) {} }

  // ---------------------------------------------------------------- UI utils
  function log(msg) {
    var el = $('log');
    var t = new Date().toTimeString().slice(0, 8);
    el.textContent += '[' + t + '] ' + msg + '\n';
    el.scrollTop = el.scrollHeight;
  }
  function setStatus(p, text) {
    $('status').classList.remove('hidden');
    if (p !== null) $('barFill').style.width = Math.round(p * 100) + '%';
    if (text) $('statusText').textContent = text;
  }
  function setBusy(b) {
    busy = b;
    ['btnBuild', 'btnPreview', 'btnClear'].forEach(function (id) { $(id).disabled = b; });
  }
  function fmt(sec) {
    sec = Math.max(0, Math.round(sec));
    return Math.floor(sec / 60) + ':' + ('0' + (sec % 60)).slice(-2);
  }
  function esc(s) { return String(s).replace(/[&<>]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]; }); }

  // ------------------------------------------------------------- JSX bridge
  function jsx(fn, arg) {
    return new Promise(function (resolve, reject) {
      var code = 'AutoCut.' + fn + '(' + (arg === undefined ? '' : JSON.stringify(arg)) + ')';
      window.__adobe_cep__.evalScript(code, function (res) {
        if (!res || res === 'EvalScript error.') return reject(new Error('ExtendScript error in ' + fn));
        var o;
        try { o = JSON.parse(res); } catch (e) { return reject(new Error('Bad reply from Premiere: ' + res)); }
        if (!o.ok) return reject(new Error(o.error));
        resolve(o);
      });
    });
  }
  function ensureHost() {
    return new Promise(function (resolve) {
      window.__adobe_cep__.evalScript('typeof AutoCut', function (r) {
        if (r === 'object') return resolve();
        var f = path.join(EXT, 'jsx', 'host.jsx').replace(/\\/g, '/');
        window.__adobe_cep__.evalScript('$.evalFile("' + f + '")', function () { resolve(); });
      });
    });
  }

  // ---------------------------------------------------------------- engine
  function pythonExe(s) {
    if (s.python_path) return s.python_path;
    var venv = IS_WIN ? path.join(ENGINE, '.venv', 'Scripts', 'python.exe') : path.join(ENGINE, '.venv', 'bin', 'python');
    if (fs.existsSync(venv)) return venv;
    return IS_WIN ? 'python' : 'python3';
  }

  function runEngine(clips, s) {
    return new Promise(function (resolve, reject) {
      var dir = fs.mkdtempSync(path.join(os.tmpdir(), 'autocut-'));
      var jobFile = path.join(dir, 'job.json');
      var outFile = path.join(dir, 'result.json');
      var settings = {};
      Object.keys(s).forEach(function (k) { if (k !== 'python_path') settings[k] = s[k]; });
      fs.writeFileSync(jobFile, JSON.stringify({ clips: clips, settings: settings, cacheDir: path.join(ENGINE, 'cache') }), 'utf8');

      var exe = pythonExe(s);
      log('Engine: ' + exe);
      var proc = cp.spawn(exe, ['-u', path.join(ENGINE, 'analyze.py'), '--job', jobFile, '--out', outFile],
        { cwd: ENGINE, windowsHide: true, env: Object.assign({}, process.env, { PYTHONIOENCODING: 'utf-8' }) });

      var buf = '', errTail = [];
      proc.stdout.on('data', function (d) {
        buf += d.toString('utf8');
        var lines = buf.split(/\r?\n/);
        buf = lines.pop();
        lines.forEach(function (line) {
          var m = line.match(/^PROGRESS ([\d.]+) (.*)$/);
          if (m) return setStatus(parseFloat(m[1]), m[2]);
          if (line.indexOf('LOG ') === 0) return log(line.slice(4));
          if (line.trim() && line !== 'DONE ') log(line);
        });
      });
      proc.stderr.on('data', function (d) {
        d.toString('utf8').split(/\r?\n/).forEach(function (l) {
          if (!l.trim()) return;
          errTail.push(l); if (errTail.length > 15) errTail.shift();
        });
      });
      proc.on('error', function (e) {
        reject(new Error('Could not start Python (' + e.message + '). Run install.bat or set the Python path in Advanced.'));
      });
      proc.on('close', function (code) {
        if (code !== 0 || !fs.existsSync(outFile)) {
          errTail.forEach(log);
          return reject(new Error('Engine stopped (code ' + code + '). See Log.'));
        }
        try { resolve(JSON.parse(fs.readFileSync(outFile, 'utf8'))); }
        catch (e) { reject(e); }
      });
    });
  }

  // ---------------------------------------------------------------- summary
  var NAMES = { gap: 'Gaps', head: 'Start trim', tail: 'End trim', retake: 'Retakes', stutter: 'Quick repeats',
    filler: 'Fillers', reading: 'Reading script', 'look-away': 'Looking away', noise: 'Noise',
    mistake: 'Mistake talk', 'off-script': 'Off-script' };
  var TAGS = { gap: 'silence', head: 'start', tail: 'end', retake: 'retake', stutter: 'repeat', filler: 'filler',
    reading: 'reading', 'look-away': 'looking away', noise: 'noise', mistake: 'mistake', 'mistake?': 'maybe mistake',
    'off-script': 'not in script', 'flat-voice': 'flat voice' };

  function showTranscript(r) {
    var box = $('transcript');
    var list = r.transcript || [];
    box.innerHTML = list.map(function (p, i) {
      var cls = p.cut ? 'cut' : (p.check ? 'check' : '');
      var tag = p.cut ? TAGS[p.cut] || p.cut : (p.check ? TAGS[p.check] || p.check : '');
      if (r.script_used && p.match !== null && p.match !== undefined) tag += (tag ? ' · ' : '') + 'script ' + p.match + '%';
      return '<div class="ph ' + cls + '" data-t="' + p.t + '"><span class="tm">' + fmt(p.t) + '</span>' +
        '<span class="tx">' + esc(p.text) + '</span><span class="tag">' + esc(tag) + '</span></div>';
    }).join('') || '<div class="dim" style="padding:6px">No speech found.</div>';
    $('trBox').classList.remove('hidden');
    $('trBox').open = true;
  }

  function showSummary(r, mode, extra) {
    var s = r.summary, el = $('summary');
    var rows = Object.keys(s.counts).map(function (k) {
      return '<tr><td>' + (NAMES[k] || k) + ' × ' + s.counts[k] + '</td><td>−' + fmt(s.removed_by_reason[k] || 0) + '</td></tr>';
    }).join('');
    var pct = s.original ? Math.round(100 * s.removed / s.original) : 0;
    el.innerHTML =
      '<div class="big">' + fmt(s.original) + ' → <b>' + fmt(s.kept) + '</b> <span style="color:#8c8c8c">(−' + pct + '%)</span></div>' +
      '<table>' + rows + '</table>' +
      (s.reviews ? '<div class="note">' + s.reviews + ' yellow/colored "AC: CHECK" markers need your review.</div>' : '') +
      (r.skipped && r.skipped.length ? '<div class="note">Skipped: ' + esc(r.skipped.map(function (x) { return x.name; }).join(', ')) + '</div>' : '') +
      '<div style="color:#8c8c8c;margin-top:6px">' + esc(extra) + ' · ' + r.elapsed + 's</div>';
    el.classList.remove('hidden');
  }
  function showError(e) {
    $('summary').innerHTML = '<div class="error">' + esc(e.message || e) + '</div>';
    $('summary').classList.remove('hidden');
    setStatus(null, 'Failed');
    log('ERROR: ' + (e.message || e));
  }

  // ---------------------------------------------------------------- actions
  function analyze(mode) {
    if (busy) return;
    var s = readSettings(); save();
    setBusy(true); $('summary').classList.add('hidden');
    setStatus(0, 'Reading timeline…');
    var info;
    ensureHost()
      .then(function () { return jsx('getClips'); })
      .then(function (r) {
        info = r;
        lastSeqId = r.seqId;
        (r.warnings || []).forEach(function (w) { log('Warning: ' + w); });
        log('Sequence "' + r.seqName + '": ' + r.clips.length + ' clip(s) on V1');
        return runEngine(r.clips, s);
      })
      .then(function (res) {
        showTranscript(res);
        if (res.script_used) log('Script used: phrases matched against your script.');
        if (!res.pieces.length) throw new Error('Nothing left to keep - check the settings (everything was detected as a cut).');
        setStatus(0.97, mode === 'build' ? 'Building sequence in Premiere…' : 'Adding markers…');
        if (mode === 'build') {
          return jsx('build', { seqId: info.seqId, seqName: info.seqName + ' - AutoCut', pieces: res.pieces, markers: res.new_markers })
            .then(function (o) { showSummary(res, mode, 'New sequence "' + o.name + '" · ' + o.pieces + ' pieces'); });
        }
        return jsx('addMarkers', { seqId: info.seqId, markers: res.preview_markers })
          .then(function (o) { showSummary(res, mode, o.markers + ' markers added to the original (red = cut)'); });
      })
      .then(function () { setStatus(1, 'Done ✓'); })
      .catch(showError)
      .then(function () { setBusy(false); });
  }

  function clearMarkers() {
    ensureHost().then(function () { return jsx('clearMarkers'); })
      .then(function (o) { log('Removed ' + o.removed + ' AutoCut markers'); setStatus(null, 'Markers cleared'); })
      .catch(showError);
  }

  // ---------------------------------------------------------------- init
  DEFAULTS = readSettings();
  try { applySettings(JSON.parse(localStorage.getItem(STORE) || '{}')); } catch (e) {}
  updateLabels();
  Object.keys(FIELDS).forEach(function (k) {
    $(k).addEventListener('input', function () { updateLabels(); save(); });
    $(k).addEventListener('change', save);
  });
  $('btnBuild').addEventListener('click', function () { analyze('build'); });
  $('btnPreview').addEventListener('click', function () { analyze('preview'); });
  $('btnClear').addEventListener('click', clearMarkers);
  $('transcript').addEventListener('click', function (e) {
    var el = e.target.closest('.ph');
    if (!el || !lastSeqId) return;
    ensureHost().then(function () { return jsx('seek', { seqId: lastSeqId, t: parseFloat(el.getAttribute('data-t')) }); })
      .catch(function (err) { log('Jump failed: ' + err.message); });
  });
  $('btnLoadScript').addEventListener('click', function () { $('scriptFile').click(); });
  $('scriptFile').addEventListener('change', function () {
    var f = this.files && this.files[0];
    if (!f) return;
    try {
      $('script').value = fs.readFileSync(f.path, 'utf8').replace(/^\uFEFF/, '');
    } catch (e) {
      var rd = new FileReader();
      rd.onload = function () { $('script').value = rd.result; save(); };
      rd.readAsText(f, 'utf-8');
    }
    save();
    log('Script loaded: ' + f.name);
    this.value = '';
  });
  $('btnClearScript').addEventListener('click', function () { $('script').value = ''; save(); });
  $('btnReset').addEventListener('click', function () {
    var keepScript = $('script').value;
    applySettings(DEFAULTS); $('script').value = keepScript; save();
  });
  log('AutoCut ready. Engine folder: ' + ENGINE);
  if (!fs.existsSync(path.join(ENGINE, '.venv'))) log('Engine not installed yet - run install.bat (or engine\\setup_engine.bat).');
})();
