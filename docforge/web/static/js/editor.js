/**
 * DocForge 마크다운 편집기 페이지
 */

'use strict';

var taskId = window.__TASK_ID__;

var loadingState = document.getElementById('loadingState');
var editorContainer = document.getElementById('editorContainer');
var markdownInput = document.getElementById('markdownInput');
var markdownPreview = document.getElementById('markdownPreview');
var charCount = document.getElementById('charCount');
var btnSave = document.getElementById('btnSave');
var btnExport = document.getElementById('btnExport');
var alertArea = document.getElementById('alertArea');
var editorFilename = document.getElementById('editorFilename');

var _saveTimer = null;

// ---------------------------------------------------------------------------
// Load content
// ---------------------------------------------------------------------------

function loadContent() {
  fetch('/api/parse/' + taskId + '/result')
    .then(function (res) { return res.json(); })
    .then(function (json) {
      loadingState.classList.add('hidden');

      if (!json.success) {
        showAlert((json.error && json.error.message) || '내용을 불러올 수 없습니다.', 'error');
        return;
      }

      var data = json.data;
      if (data.filename) {
        editorFilename.textContent = data.filename + ' 편집';
      }

      btnExport.href = '/api/export/' + taskId;
      btnExport.setAttribute('download', '');

      markdownInput.value = data.markdown || '';
      updateCharCount();
      renderPreview();
      editorContainer.classList.remove('hidden');
    })
    .catch(function (err) {
      loadingState.classList.add('hidden');
      showAlert('서버 오류: ' + err.message, 'error');
    });
}

// ---------------------------------------------------------------------------
// Preview rendering — safe plain-text-based rendering
// ---------------------------------------------------------------------------

function renderPreview() {
  var markdown = markdownInput.value;

  while (markdownPreview.firstChild) {
    markdownPreview.removeChild(markdownPreview.firstChild);
  }

  if (!markdown) {
    var empty = document.createElement('p');
    empty.className = 'text-muted';
    empty.textContent = '(내용 없음)';
    markdownPreview.appendChild(empty);
    return;
  }

  var lines = markdown.split('\n');
  var inCode = false;
  var codeBuf = [];

  lines.forEach(function (line) {
    if (line.startsWith('```')) {
      if (inCode) {
        var pre = document.createElement('pre');
        var code = document.createElement('code');
        code.textContent = codeBuf.join('\n');
        pre.appendChild(code);
        markdownPreview.appendChild(pre);
        codeBuf = [];
        inCode = false;
      } else {
        inCode = true;
      }
      return;
    }

    if (inCode) {
      codeBuf.push(line);
      return;
    }

    // ATX heading
    var headingLevel = 0;
    var k = 0;
    while (k < 6 && k < line.length && line[k] === '#') {
      headingLevel++;
      k++;
    }
    if (headingLevel > 0 && line[headingLevel] === ' ') {
      var headingText = line.slice(headingLevel + 1);
      var heading = document.createElement('h' + headingLevel);
      heading.textContent = headingText;
      markdownPreview.appendChild(heading);
      return;
    }

    // Table row (simple detection)
    if (line.startsWith('|')) {
      var p2 = document.createElement('p');
      p2.style.fontFamily = 'var(--font-mono)';
      p2.style.fontSize = 'var(--font-size-xs)';
      p2.textContent = line;
      markdownPreview.appendChild(p2);
      return;
    }

    // Blank line
    if (line.trim() === '') {
      markdownPreview.appendChild(document.createElement('br'));
      return;
    }

    var p = document.createElement('p');
    p.textContent = line;
    markdownPreview.appendChild(p);
  });

  // Flush open code block
  if (inCode && codeBuf.length > 0) {
    var pre2 = document.createElement('pre');
    var code2 = document.createElement('code');
    code2.textContent = codeBuf.join('\n');
    pre2.appendChild(code2);
    markdownPreview.appendChild(pre2);
  }
}

// ---------------------------------------------------------------------------
// Character count
// ---------------------------------------------------------------------------

function updateCharCount() {
  var len = markdownInput.value.length;
  charCount.textContent = len.toLocaleString('ko-KR') + '자';
}

// ---------------------------------------------------------------------------
// Editor input events
// ---------------------------------------------------------------------------

markdownInput.addEventListener('input', function () {
  updateCharCount();

  // Debounce preview update (300ms)
  if (_saveTimer !== null) {
    clearTimeout(_saveTimer);
  }
  _saveTimer = setTimeout(function () {
    renderPreview();
    _saveTimer = null;
  }, 300);
});

// Tab key support in textarea
markdownInput.addEventListener('keydown', function (e) {
  if (e.key === 'Tab') {
    e.preventDefault();
    var start = markdownInput.selectionStart;
    var end = markdownInput.selectionEnd;
    markdownInput.value =
      markdownInput.value.slice(0, start) + '  ' + markdownInput.value.slice(end);
    markdownInput.selectionStart = start + 2;
    markdownInput.selectionEnd = start + 2;
  }

  // Ctrl+S / Cmd+S to save
  if ((e.ctrlKey || e.metaKey) && e.key === 's') {
    e.preventDefault();
    saveMarkdown();
  }
});

// ---------------------------------------------------------------------------
// Save
// ---------------------------------------------------------------------------

btnSave.addEventListener('click', saveMarkdown);

function saveMarkdown() {
  clearAlert();
  btnSave.disabled = true;
  btnSave.textContent = '저장 중...';

  fetch('/api/save/' + taskId, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ markdown: markdownInput.value }),
  })
    .then(function (res) { return res.json(); })
    .then(function (json) {
      btnSave.disabled = false;
      btnSave.textContent = '저장';
      if (json.success) {
        showAlert('저장되었습니다.', 'success');
      } else {
        showAlert((json.error && json.error.message) || '저장 실패', 'error');
      }
    })
    .catch(function (err) {
      btnSave.disabled = false;
      btnSave.textContent = '저장';
      showAlert('저장 요청 실패: ' + err.message, 'error');
    });
}

// ---------------------------------------------------------------------------
// Alert helpers
// ---------------------------------------------------------------------------

function showAlert(msg, type) {
  while (alertArea.firstChild) {
    alertArea.removeChild(alertArea.firstChild);
  }
  var div = document.createElement('div');
  div.className = 'alert alert--' + type;
  div.setAttribute('role', 'alert');
  div.textContent = msg;
  alertArea.appendChild(div);

  // Auto-clear success messages after 3s
  if (type === 'success') {
    setTimeout(clearAlert, 3000);
  }
}

function clearAlert() {
  while (alertArea.firstChild) {
    alertArea.removeChild(alertArea.firstChild);
  }
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

loadContent();
