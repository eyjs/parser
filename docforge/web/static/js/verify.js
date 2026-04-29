/**
 * DocForge 원본 비교 편집기 -- PDF.js 연속 스크롤 + MD 편집기 + 스크롤 동기화
 */

'use strict';

var taskId = window.__TASK_ID__;

// ---------------------------------------------------------------------------
// DOM references
// ---------------------------------------------------------------------------

var loadingState = document.getElementById('loadingState');
var loadingMessage = document.getElementById('loadingMessage');
var compareEditor = document.getElementById('compareEditor');
var verifyTitle = document.getElementById('verifyTitle');
var verifyProgress = document.getElementById('verifyProgress');

// PDF viewer
var pdfViewerContainer = document.getElementById('pdfViewerContainer');
var btnPrevPage = document.getElementById('btnPrevPage');
var btnNextPage = document.getElementById('btnNextPage');
var pageNumInput = document.getElementById('pageNumInput');
var totalPagesEl = document.getElementById('totalPages');

// MD editor
var markdownInput = document.getElementById('markdownInput');
var markdownPreview = document.getElementById('markdownPreview');
var mdPanelBody = document.getElementById('mdPanelBody');
var previewPanel = document.getElementById('previewPanel');
var charCount = document.getElementById('charCount');

// Buttons
var btnSave = document.getElementById('btnSave');
var btnExport = document.getElementById('btnExport');
var btnTogglePreview = document.getElementById('btnTogglePreview');
var btnToggleSync = document.getElementById('btnToggleSync');
var alertArea = document.getElementById('alertArea');

// Live badge
var liveBadge = document.getElementById('liveBadge');
var liveBadgeText = document.getElementById('liveBadgeText');

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

var _pdfDoc = null;
var _totalPages = 0;
var _currentPage = 1;
var _pdfCanvases = [];       // canvas elements per page
var _pagePositions = [];     // { top, bottom } of each canvas in scroll container
var _isLiveMode = false;
var _livePageMarkdowns = {};
var _previewMode = false;
var _saveTimer = null;
var _syncEnabled = true;     // scroll sync on by default
var _syncingScroll = false;  // prevents infinite scroll loop

// ---------------------------------------------------------------------------
// Initialization
// ---------------------------------------------------------------------------

function init() {
  fetch('/api/parse/' + taskId + '/result')
    .then(function (res) { return res.json(); })
    .then(function (json) {
      if (json.success) {
        onResultReady(json.data);
      } else if (json.error && json.error.code === 'NOT_READY') {
        enterLiveMode();
      } else {
        showError((json.error && json.error.message) || '결과를 불러올 수 없습니다.');
      }
    })
    .catch(function (err) {
      showError('서버 오류: ' + err.message);
    });
}

function onResultReady(data) {
  loadingState.classList.add('hidden');

  if (data.filename) {
    verifyTitle.textContent = data.filename + ' — 원본 비교';
  }

  btnExport.href = '/api/export/' + taskId;
  btnExport.setAttribute('download', '');

  markdownInput.value = stripFrontMatter(data.markdown || '');
  updateCharCount();

  if (data.pdf_path) {
    var parts = data.pdf_path.replace(/\\/g, '/').split('/');
    var taskIdx = parts.indexOf(taskId);
    if (taskIdx >= 0) {
      var relative = parts.slice(taskIdx).join('/');
      loadPdf('/uploads/' + relative);
    }
  }

  compareEditor.classList.remove('hidden');
  setupScrollSync();
}

// ---------------------------------------------------------------------------
// PDF.js — continuous scroll (all pages rendered vertically)
// ---------------------------------------------------------------------------

function loadPdf(url) {
  if (!window.pdfjsLib) return;

  pdfjsLib.getDocument(url).promise.then(function (doc) {
    _pdfDoc = doc;
    _totalPages = doc.numPages;
    totalPagesEl.textContent = String(_totalPages);
    pageNumInput.max = _totalPages;
    btnPrevPage.disabled = false;
    btnNextPage.disabled = false;

    renderAllPages();
  }).catch(function (err) {
    console.error('PDF load error:', err);
  });
}

function renderAllPages() {
  // Clear container
  while (pdfViewerContainer.firstChild) {
    pdfViewerContainer.removeChild(pdfViewerContainer.firstChild);
  }
  _pdfCanvases = [];

  var containerWidth = pdfViewerContainer.clientWidth - 32;
  var chain = Promise.resolve();

  for (var i = 1; i <= _totalPages; i++) {
    (function (pageNum) {
      chain = chain.then(function () {
        return _pdfDoc.getPage(pageNum).then(function (page) {
          var viewport = page.getViewport({ scale: 1.0 });
          var scale = containerWidth / viewport.width;
          var scaledViewport = page.getViewport({ scale: scale });

          var canvas = document.createElement('canvas');
          canvas.width = scaledViewport.width;
          canvas.height = scaledViewport.height;
          canvas.dataset.page = pageNum;
          canvas.setAttribute('aria-label', 'PDF 페이지 ' + pageNum);

          pdfViewerContainer.appendChild(canvas);
          _pdfCanvases.push(canvas);

          return page.render({
            canvasContext: canvas.getContext('2d'),
            viewport: scaledViewport,
          }).promise;
        });
      });
    })(i);
  }

  chain.then(function () {
    cachePagePositions();
    updateCurrentPageFromScroll();
  });
}

function cachePagePositions() {
  _pagePositions = [];
  var containerTop = pdfViewerContainer.scrollTop;
  var containerOffset = pdfViewerContainer.getBoundingClientRect().top;

  for (var i = 0; i < _pdfCanvases.length; i++) {
    var rect = _pdfCanvases[i].getBoundingClientRect();
    _pagePositions.push({
      top: rect.top - containerOffset + containerTop,
      bottom: rect.bottom - containerOffset + containerTop,
    });
  }
}

// ---------------------------------------------------------------------------
// Scroll synchronization
// ---------------------------------------------------------------------------

function setupScrollSync() {
  pdfViewerContainer.addEventListener('scroll', onPdfScroll);
  markdownInput.addEventListener('scroll', onMdScroll);
}

var _syncTimeout = null;

function onPdfScroll() {
  updateCurrentPageFromScroll();
  if (!_syncEnabled || _syncingScroll) return;
  // PDF scroll does not drive MD — MD is the master
}

function onMdScroll() {
  if (_syncingScroll || !_syncEnabled) return;

  _syncingScroll = true;
  syncPdfFromMd();
  clearTimeout(_syncTimeout);
  _syncTimeout = setTimeout(function () { _syncingScroll = false; }, 80);
}

function syncPdfFromMd() {
  if (_pagePositions.length === 0 || _totalPages === 0) return;

  var mdEl = markdownInput;
  var lineHeight = getTextareaLineHeight(mdEl);
  var topVisibleLine = Math.floor(mdEl.scrollTop / lineHeight);

  var lines = mdEl.value.split('\n');

  var page = 1;
  for (var i = 0; i < lines.length; i++) {
    if (lines[i].trim() === PAGE_SEP) {
      if (i <= topVisibleLine) {
        page++;
      } else {
        break;
      }
    }
  }

  if (page < 1) page = 1;
  if (page > _totalPages) page = _totalPages;

  if (page !== _currentPage) {
    _currentPage = page;
    pageNumInput.value = page;
    btnPrevPage.disabled = (page <= 1);
    btnNextPage.disabled = (page >= _totalPages);

    var pageIdx = page - 1;
    if (pageIdx < _pagePositions.length) {
      pdfViewerContainer.scrollTop = _pagePositions[pageIdx].top;
    }
  }
}


function getTextareaLineHeight(textarea) {
  var style = window.getComputedStyle(textarea);
  var lh = parseFloat(style.lineHeight);
  if (isNaN(lh)) {
    lh = parseFloat(style.fontSize) * 1.5;
  }
  return lh;
}

function updateCurrentPageFromScroll() {
  if (_pagePositions.length === 0) return;

  // Use top of viewport + small offset to determine current page
  var scrollRef = pdfViewerContainer.scrollTop + 50;
  var page = 1;
  for (var i = 0; i < _pagePositions.length; i++) {
    if (scrollRef >= _pagePositions[i].top) {
      page = i + 1;
    }
  }

  if (page !== _currentPage) {
    _currentPage = page;
    pageNumInput.value = page;
    btnPrevPage.disabled = (page <= 1);
    btnNextPage.disabled = (page >= _totalPages);
  }
}

function scrollPdfToPage(num) {
  if (num < 1 || num > _totalPages || _pagePositions.length === 0) return;
  _currentPage = num;
  pageNumInput.value = num;
  btnPrevPage.disabled = (num <= 1);
  btnNextPage.disabled = (num >= _totalPages);

  _syncingScroll = true;

  var targetTop = _pagePositions[num - 1].top;
  var maxScroll = pdfViewerContainer.scrollHeight - pdfViewerContainer.clientHeight;
  pdfViewerContainer.scrollTo({
    top: Math.min(targetTop, maxScroll),
    behavior: 'smooth',
  });

  scrollMdToPage(num);

  clearTimeout(_syncTimeout);
  _syncTimeout = setTimeout(function () { _syncingScroll = false; }, 200);
}

function scrollMdToPage(num) {
  var mdEl = markdownInput;
  var lines = mdEl.value.split('\n');
  var lineHeight = getTextareaLineHeight(mdEl);

  if (num <= 1) {
    mdEl.scrollTop = 0;
    return;
  }

  var sepCount = 0;
  for (var i = 0; i < lines.length; i++) {
    if (lines[i].trim() === PAGE_SEP) {
      sepCount++;
      if (sepCount === num - 1) {
        mdEl.scrollTop = (i + 1) * lineHeight;
        return;
      }
    }
  }
}

// Page navigation buttons
btnPrevPage.addEventListener('click', function () {
  if (_currentPage > 1) scrollPdfToPage(_currentPage - 1);
});

btnNextPage.addEventListener('click', function () {
  if (_currentPage < _totalPages) scrollPdfToPage(_currentPage + 1);
});

pageNumInput.addEventListener('change', function () {
  var num = parseInt(pageNumInput.value, 10);
  if (num >= 1 && num <= _totalPages) scrollPdfToPage(num);
  else pageNumInput.value = _currentPage;
});

// Keyboard navigation (only when not typing in textarea)
document.addEventListener('keydown', function (e) {
  if (e.target === markdownInput) return;

  if (e.key === 'ArrowLeft') {
    e.preventDefault();
    if (_currentPage > 1) scrollPdfToPage(_currentPage - 1);
  }
  if (e.key === 'ArrowRight') {
    e.preventDefault();
    if (_currentPage < _totalPages) scrollPdfToPage(_currentPage + 1);
  }
});

// ---------------------------------------------------------------------------
// Live mode (SSE during parsing)
// ---------------------------------------------------------------------------

function enterLiveMode() {
  _isLiveMode = true;
  loadingMessage.textContent = '파싱 중... 실시간 결과를 수신합니다.';
  liveBadge.classList.remove('hidden');

  fetch('/api/history')
    .then(function (res) { return res.json(); })
    .then(function (json) {
      if (!json.success) return;
      var task = json.data.find(function (r) { return r.task_id === taskId; });
      if (task) {
        verifyTitle.textContent = (task.filename || '') + ' — 실시간 수신';
      }
    })
    .catch(function () {});

  var es = new EventSource('/api/parse/' + taskId + '/status');

  es.addEventListener('message', function (e) {
    var payload;
    try { payload = JSON.parse(e.data); } catch (_) { return; }

    var event = payload.event;
    var data = payload.data || {};

    if (event === 'heartbeat') return;

    if (event === 'page_result') {
      var pageNum = data.page;
      var pageMd = data.markdown || '';
      _livePageMarkdowns[pageNum] = pageMd;
      rebuildLiveMarkdown();
      updateCharCount();

      if (compareEditor.classList.contains('hidden')) {
        loadingState.classList.add('hidden');
        compareEditor.classList.remove('hidden');
        setupScrollSync();
      }

      liveBadgeText.textContent = '실시간 수신 중... (' + Object.keys(_livePageMarkdowns).length + ' 페이지)';
    }

    if (event === 'page_progress') {
      var pct = data.pct || 0;
      verifyProgress.textContent = pct + '%';
    }

    if (event === 'done') {
      es.close();
      _isLiveMode = false;
      liveBadge.classList.add('hidden');
      verifyProgress.textContent = '완료';

      setTimeout(function () {
        fetch('/api/parse/' + taskId + '/result')
          .then(function (res) { return res.json(); })
          .then(function (json) {
            if (json.success) onResultReady(json.data);
          });
      }, 500);
    }

    if (event === 'error') {
      es.close();
      _isLiveMode = false;
      liveBadge.classList.add('hidden');
      showError(data.message || '파싱 오류가 발생했습니다.');
    }
  });

  es.onerror = function () {
    es.close();
    _isLiveMode = false;
    liveBadge.classList.add('hidden');
  };
}

function rebuildLiveMarkdown() {
  var pages = Object.keys(_livePageMarkdowns).map(Number).sort(function (a, b) { return a - b; });
  var parts = pages.map(function (p) { return _livePageMarkdowns[p]; });
  markdownInput.value = parts.join('\n' + PAGE_SEP + '\n');
}

// ---------------------------------------------------------------------------
// Editor features
// ---------------------------------------------------------------------------

var PAGE_SEP = '<!-- pagebreak -->';

function stripFrontMatter(md) {
  if (!md.startsWith('---')) return md;
  var endIdx = md.indexOf('\n---', 3);
  if (endIdx < 0) return md;
  md = md.slice(endIdx + 4).replace(/^\n+/, '');

  // Replace page separators: --- on its own line surrounded by blank lines
  md = md.replace(/\n---\n/g, '\n' + PAGE_SEP + '\n');
  return md;
}

function updateCharCount() {
  var len = markdownInput.value.length;
  charCount.textContent = len.toLocaleString('ko-KR') + '자';
}

markdownInput.addEventListener('input', function () {
  updateCharCount();
  if (_previewMode) {
    if (_saveTimer !== null) clearTimeout(_saveTimer);
    _saveTimer = setTimeout(function () {
      renderPreview();
      _saveTimer = null;
    }, 300);
  }
});

markdownInput.addEventListener('keydown', function (e) {
  if (e.key === 'Tab') {
    e.preventDefault();
    var start = markdownInput.selectionStart;
    var end = markdownInput.selectionEnd;
    markdownInput.value = markdownInput.value.slice(0, start) + '  ' + markdownInput.value.slice(end);
    markdownInput.selectionStart = start + 2;
    markdownInput.selectionEnd = start + 2;
  }

  if ((e.ctrlKey || e.metaKey) && e.key === 's') {
    e.preventDefault();
    saveMarkdown();
  }
});

// ---------------------------------------------------------------------------
// Preview toggle
// ---------------------------------------------------------------------------

btnTogglePreview.addEventListener('click', function () {
  _previewMode = !_previewMode;
  if (_previewMode) {
    mdPanelBody.classList.add('hidden');
    previewPanel.classList.remove('hidden');
    btnTogglePreview.textContent = '편집';
    renderPreview();
  } else {
    previewPanel.classList.add('hidden');
    mdPanelBody.classList.remove('hidden');
    btnTogglePreview.textContent = '프리뷰';
  }
});

var _tableBuf = [];

function _flushTable(container, rows) {
  var table = document.createElement('table');
  rows.forEach(function (row, idx) {
    // Skip separator rows (| --- | --- |)
    if (/^\|\s*[-:]+/.test(row) && !/[^|\s\-:]/.test(row)) return;
    var tr = document.createElement('tr');
    var cells = row.split('|').slice(1);
    if (cells.length > 0 && cells[cells.length - 1].trim() === '') cells.pop();
    cells.forEach(function (cell) {
      var td = document.createElement(idx === 0 ? 'th' : 'td');
      td.textContent = cell.trim();
      tr.appendChild(td);
    });
    table.appendChild(tr);
  });
  container.appendChild(table);
}

function renderPreview() {
  var markdown = markdownInput.value;
  _tableBuf = [];
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
    if (inCode) { codeBuf.push(line); return; }

    var headingLevel = 0;
    var k = 0;
    while (k < 6 && k < line.length && line[k] === '#') { headingLevel++; k++; }
    if (headingLevel > 0 && line[headingLevel] === ' ') {
      var heading = document.createElement('h' + headingLevel);
      heading.textContent = line.slice(headingLevel + 1);
      markdownPreview.appendChild(heading);
      return;
    }

    if (line.startsWith('|')) {
      _tableBuf.push(line);
      return;
    }

    if (_tableBuf.length > 0) {
      _flushTable(markdownPreview, _tableBuf);
      _tableBuf = [];
    }

    if (line.trim() === '') {
      markdownPreview.appendChild(document.createElement('br'));
      return;
    }

    var p = document.createElement('p');
    p.textContent = line;
    markdownPreview.appendChild(p);
  });

  if (_tableBuf.length > 0) {
    _flushTable(markdownPreview, _tableBuf);
    _tableBuf = [];
  }

  if (inCode && codeBuf.length > 0) {
    var pre2 = document.createElement('pre');
    var code2 = document.createElement('code');
    code2.textContent = codeBuf.join('\n');
    pre2.appendChild(code2);
    markdownPreview.appendChild(pre2);
  }
}

// ---------------------------------------------------------------------------
// Scroll sync toggle
// ---------------------------------------------------------------------------

btnToggleSync.addEventListener('click', function () {
  _syncEnabled = !_syncEnabled;
  btnToggleSync.textContent = _syncEnabled ? '동기화 ON' : '동기화 OFF';
  btnToggleSync.className = _syncEnabled
    ? 'btn btn--primary btn--sm'
    : 'btn btn--secondary btn--sm';
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
// Error / Alert helpers
// ---------------------------------------------------------------------------

function showError(msg) {
  loadingState.classList.add('hidden');
  showAlert(msg, 'error');
}

function showAlert(msg, type) {
  while (alertArea.firstChild) {
    alertArea.removeChild(alertArea.firstChild);
  }
  var div = document.createElement('div');
  div.className = 'alert alert--' + type;
  div.setAttribute('role', 'alert');
  div.textContent = msg;
  alertArea.appendChild(div);

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

init();
