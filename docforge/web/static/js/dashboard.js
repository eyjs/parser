/**
 * DocForge 대시보드 -- 멀티파일 업로드, SSE 진행률, 큐 상태, 이력 관리
 */

'use strict';

// ---------------------------------------------------------------------------
// DOM references
// ---------------------------------------------------------------------------

var dropZone = document.getElementById('dropZone');
var fileInput = document.getElementById('fileInput');
var progressWrap = document.getElementById('progressWrap');
var progressFill = document.getElementById('progressFill');
var progressBar = document.getElementById('progressBar');
var progressStatus = document.getElementById('progressStatus');
var alertArea = document.getElementById('alertArea');
var historyBody = document.getElementById('historyBody');
var queueBanner = document.getElementById('queueBanner');
var queueBannerText = document.getElementById('queueBannerText');
var uploadCards = document.getElementById('uploadCards');

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

var _activeUploads = {};  // task_id -> { filename, status, es }
var _queuePollTimer = null;
// Latest registry snapshot keyed by task_id — populated by pollQueueStatus
// so the history table can render in-flight progress.
var _activeStateByTaskId = {};

// Live preview state (most recently active task drives the preview panel)
var _livePreview = {
  taskId: null,
  totalPages: 0,
  pages: {},        // page_num -> markdown
  recentOrder: [],  // page_nums in arrival order
  maxRecent: 5,
};
var livePreviewSection = document.getElementById('livePreviewSection');
var livePreviewGrid = document.getElementById('livePreviewGrid');
var livePreviewStages = document.getElementById('livePreviewStages');
var livePreviewTail = document.getElementById('livePreviewTail');
var livePreviewCounter = document.getElementById('livePreviewCounter');
var livePreviewToggle = document.getElementById('livePreviewToggle');
var _tailCollapsed = false;

if (livePreviewToggle) {
  livePreviewToggle.addEventListener('click', function () {
    _tailCollapsed = !_tailCollapsed;
    livePreviewTail.classList.toggle('hidden', _tailCollapsed);
    livePreviewToggle.textContent = _tailCollapsed ? '펼치기' : '접기';
  });
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}

function ensureLivePreview(taskId) {
  if (_livePreview.taskId === taskId) return;
  _livePreview = {
    taskId: taskId,
    totalPages: 0,
    pages: {},
    recentOrder: [],
    maxRecent: 5,
  };
  livePreviewGrid.innerHTML = '';
  livePreviewTail.innerHTML = '<p class="text-muted text-sm" style="padding: 1rem; text-align: center;">처리가 시작되면 여기에 페이지별 마크다운이 표시됩니다.</p>';
  livePreviewCounter.textContent = '0 / 0';
  livePreviewSection.classList.remove('hidden');
  // Reset stage pills to idle
  Array.prototype.forEach.call(livePreviewStages.querySelectorAll('.stage-pill'), function (el) {
    el.className = 'stage-pill stage-pill--idle';
    el.dataset.stage = el.dataset.stage;
  });
}

function updateStagePill(eventName) {
  // Mark prior stages done, current as active
  var order = ['strategy_report', 'profiling', 'noise_learning', 'page_progress', 'table_merging', 'assembling'];
  var currentIdx = order.indexOf(eventName);
  if (currentIdx === -1) return;
  Array.prototype.forEach.call(livePreviewStages.querySelectorAll('.stage-pill'), function (el) {
    var stage = el.dataset.stage;
    var idx = order.indexOf(stage);
    if (idx < currentIdx) {
      el.className = 'stage-pill stage-pill--done';
    } else if (idx === currentIdx) {
      el.className = 'stage-pill stage-pill--active';
    } else {
      el.className = 'stage-pill stage-pill--idle';
    }
  });
}

function ensurePageGrid(total) {
  if (_livePreview.totalPages === total) return;
  _livePreview.totalPages = total;
  livePreviewGrid.innerHTML = '';
  for (var i = 1; i <= total; i++) {
    var cell = document.createElement('button');
    cell.type = 'button';
    cell.className = 'page-cell page-cell--pending';
    cell.dataset.page = String(i);
    cell.title = i + '페이지 (대기)';
    cell.textContent = i;
    cell.disabled = true;
    cell.addEventListener('click', onPageCellClick);
    livePreviewGrid.appendChild(cell);
  }
}

function onPageCellClick(e) {
  var cell = e.currentTarget;
  var page = parseInt(cell.dataset.page, 10);
  if (!_livePreview.pages.hasOwnProperty(page)) return;
  openPageViewer(page);
}

function markPageActive(page, total) {
  ensurePageGrid(total);
  var cell = livePreviewGrid.querySelector('[data-page="' + page + '"]');
  if (cell && !cell.classList.contains('page-cell--done')) {
    cell.className = 'page-cell page-cell--active';
    cell.title = page + '페이지 (처리 중)';
  }
  livePreviewCounter.textContent = page + ' / ' + total;
}

function markPageDone(page, total, markdown) {
  ensurePageGrid(total);
  var cell = livePreviewGrid.querySelector('[data-page="' + page + '"]');
  if (cell) {
    cell.className = 'page-cell page-cell--done';
    cell.title = page + '페이지 (클릭하여 보기 · ' + (markdown || '').length + '자)';
    cell.disabled = false;
  }
  _livePreview.pages[page] = markdown || '';
  // Track recent order — newest first
  var idx = _livePreview.recentOrder.indexOf(page);
  if (idx !== -1) _livePreview.recentOrder.splice(idx, 1);
  _livePreview.recentOrder.unshift(page);
  if (_livePreview.recentOrder.length > _livePreview.maxRecent) {
    _livePreview.recentOrder.length = _livePreview.maxRecent;
  }
  renderTail();
  // If viewer is open and showing this page, refresh content live.
  if (_pageViewer.openPage === page) {
    renderPageViewer(page);
  }
}

function renderTail() {
  if (_livePreview.recentOrder.length === 0) {
    livePreviewTail.innerHTML = '<p class="text-muted text-sm" style="padding: 1rem; text-align: center;">처리된 페이지가 없습니다.</p>';
    return;
  }
  var html = '';
  _livePreview.recentOrder.forEach(function (page) {
    var md = _livePreview.pages[page] || '';
    var preview = md.length > 600 ? md.slice(0, 600) + '\n\n…(생략)' : md;
    html += '<details class="tail-page" open>'
      + '<summary>페이지 ' + page + ' <span class="text-muted text-sm">(' + md.length + '자)</span>'
      + ' <button type="button" class="btn btn--secondary btn--sm tail-page__open" data-page="' + page + '">전체 보기</button>'
      + '</summary>'
      + '<pre class="tail-page__body">' + escapeHtml(preview) + '</pre>'
      + '</details>';
  });
  livePreviewTail.innerHTML = html;
  // Wire "전체 보기" buttons
  Array.prototype.forEach.call(livePreviewTail.querySelectorAll('.tail-page__open'), function (btn) {
    btn.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopPropagation();
      var page = parseInt(btn.dataset.page, 10);
      openPageViewer(page);
    });
  });
}

function finalizeLivePreview() {
  Array.prototype.forEach.call(livePreviewStages.querySelectorAll('.stage-pill'), function (el) {
    el.className = 'stage-pill stage-pill--done';
  });
  livePreviewCounter.textContent = '✓ ' + _livePreview.totalPages + ' / ' + _livePreview.totalPages;
}

// ---------------------------------------------------------------------------
// Page viewer modal — click any done page cell to inspect its markdown
// ---------------------------------------------------------------------------

var _pageViewer = { openPage: null };
var pageViewerModal = document.getElementById('pageViewerModal');
var pageViewerBody = document.getElementById('pageViewerBody');
var pageViewerTitle = document.getElementById('page-viewer-title');
var pageViewerCounter = document.getElementById('pageViewerCounter');
var pageViewerPrev = document.getElementById('pageViewerPrev');
var pageViewerNext = document.getElementById('pageViewerNext');
var pageViewerCopy = document.getElementById('pageViewerCopy');
var pageViewerClose = document.getElementById('pageViewerClose');

function openPageViewer(page) {
  _pageViewer.openPage = page;
  renderPageViewer(page);
  pageViewerModal.classList.remove('hidden');
  // Keyboard hooks
  document.addEventListener('keydown', onPageViewerKey);
}

function closePageViewer() {
  _pageViewer.openPage = null;
  pageViewerModal.classList.add('hidden');
  document.removeEventListener('keydown', onPageViewerKey);
}

function renderPageViewer(page) {
  var md = _livePreview.pages[page];
  if (md === undefined) {
    pageViewerBody.textContent = '(아직 처리되지 않은 페이지입니다)';
  } else {
    pageViewerBody.textContent = md || '(빈 페이지)';
  }
  pageViewerTitle.textContent = '페이지 ' + page + ' 미리보기';
  pageViewerCounter.textContent = page + ' / ' + (_livePreview.totalPages || '?');
  // Update prev/next disabled state
  pageViewerPrev.disabled = !findAdjacentDonePage(page, -1);
  pageViewerNext.disabled = !findAdjacentDonePage(page, +1);
}

function findAdjacentDonePage(current, direction) {
  // Walk through completed pages (in numeric order) looking for the
  // nearest neighbour in ``direction``. Returns the page number or null.
  var donePages = Object.keys(_livePreview.pages)
    .map(function (k) { return parseInt(k, 10); })
    .filter(function (n) { return !isNaN(n); })
    .sort(function (a, b) { return a - b; });
  if (donePages.length === 0) return null;
  if (direction > 0) {
    for (var i = 0; i < donePages.length; i++) {
      if (donePages[i] > current) return donePages[i];
    }
  } else {
    for (var j = donePages.length - 1; j >= 0; j--) {
      if (donePages[j] < current) return donePages[j];
    }
  }
  return null;
}

function onPageViewerKey(e) {
  if (e.key === 'Escape') {
    closePageViewer();
  } else if (e.key === 'ArrowLeft') {
    var prev = findAdjacentDonePage(_pageViewer.openPage, -1);
    if (prev) openPageViewer(prev);
  } else if (e.key === 'ArrowRight') {
    var next = findAdjacentDonePage(_pageViewer.openPage, +1);
    if (next) openPageViewer(next);
  }
}

if (pageViewerClose) {
  pageViewerClose.addEventListener('click', closePageViewer);
}
if (pageViewerModal) {
  pageViewerModal.addEventListener('click', function (e) {
    if (e.target === pageViewerModal) closePageViewer();
  });
}
if (pageViewerPrev) {
  pageViewerPrev.addEventListener('click', function () {
    var prev = findAdjacentDonePage(_pageViewer.openPage, -1);
    if (prev) openPageViewer(prev);
  });
}
if (pageViewerNext) {
  pageViewerNext.addEventListener('click', function () {
    var next = findAdjacentDonePage(_pageViewer.openPage, +1);
    if (next) openPageViewer(next);
  });
}
if (pageViewerCopy) {
  pageViewerCopy.addEventListener('click', function () {
    var page = _pageViewer.openPage;
    if (page === null) return;
    var md = _livePreview.pages[page] || '';
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(md).then(function () {
        pageViewerCopy.textContent = '✓ 복사됨';
        setTimeout(function () { pageViewerCopy.textContent = '복사'; }, 1500);
      });
    }
  });
}

// ---------------------------------------------------------------------------
// Drop zone interaction
// ---------------------------------------------------------------------------

dropZone.addEventListener('click', function () { fileInput.click(); });

dropZone.addEventListener('keydown', function (e) {
  if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault();
    fileInput.click();
  }
});

dropZone.addEventListener('dragover', function (e) {
  e.preventDefault();
  dropZone.classList.add('drop-zone--active');
});

dropZone.addEventListener('dragleave', function () {
  dropZone.classList.remove('drop-zone--active');
});

dropZone.addEventListener('drop', function (e) {
  e.preventDefault();
  dropZone.classList.remove('drop-zone--active');
  var files = Array.from(e.dataTransfer.files);
  if (files.length > 0) startMultiUpload(files);
});

fileInput.addEventListener('change', function () {
  if (fileInput.files.length > 0) {
    startMultiUpload(Array.from(fileInput.files));
    fileInput.value = '';
  }
});

// ---------------------------------------------------------------------------
// Upload + parse (multi-file)
// ---------------------------------------------------------------------------

/**
 * @param {File[]} files
 */
function startMultiUpload(files) {
  clearAlert();

  var pdfFiles = files.filter(function (f) {
    return f.name.toLowerCase().endsWith('.pdf');
  });

  if (pdfFiles.length === 0) {
    showAlert('PDF 파일만 업로드할 수 있습니다.', 'error');
    return;
  }

  var oversized = pdfFiles.filter(function (f) { return f.size > 100 * 1024 * 1024; });
  if (oversized.length > 0) {
    showAlert(oversized.length + '개 파일이 100MB를 초과합니다.', 'error');
    return;
  }

  // Single file: use classic progress bar
  if (pdfFiles.length === 1) {
    startSingleUpload(pdfFiles[0]);
    return;
  }

  // Multi-file: per-file cards
  dropZone.setAttribute('aria-disabled', 'true');

  pdfFiles.forEach(function (file) {
    uploadSingleFile(file);
  });

  startQueuePolling();
}

/**
 * Single file upload (backward compatible with original flow).
 * @param {File} file
 */
function startSingleUpload(file) {
  var formData = new FormData();
  formData.append('file', file);

  setProgress(0, '업로드 중...');
  progressWrap.classList.remove('hidden');
  dropZone.setAttribute('aria-disabled', 'true');

  fetch('/api/parse', { method: 'POST', body: formData })
    .then(function (res) { return res.json(); })
    .then(function (json) {
      if (!json.success) {
        throw new Error((json.error && json.error.message) || '업로드 실패');
      }
      var taskId = json.data.task_id;
      subscribeToProgress(taskId);
    })
    .catch(function (err) {
      showAlert(err.message, 'error');
      progressWrap.classList.add('hidden');
      dropZone.removeAttribute('aria-disabled');
    });
}

/**
 * Upload a single file in multi-file mode and show a status card.
 * @param {File} file
 */
function uploadSingleFile(file) {
  var cardId = 'card-' + Date.now() + '-' + Math.random().toString(36).slice(2, 8);
  addUploadCard(cardId, file.name, 'uploading');

  var formData = new FormData();
  formData.append('file', file);

  fetch('/api/parse', { method: 'POST', body: formData })
    .then(function (res) { return res.json(); })
    .then(function (json) {
      if (!json.success) {
        updateUploadCard(cardId, 'error', (json.error && json.error.message) || '업로드 실패');
        return;
      }
      var taskId = json.data.task_id;
      updateUploadCard(cardId, 'queued', '대기 중', taskId);
      _activeUploads[taskId] = { filename: file.name, cardId: cardId };

      // Subscribe to SSE for this task
      subscribeToProgressMulti(taskId, cardId);
    })
    .catch(function (err) {
      updateUploadCard(cardId, 'error', err.message);
    });
}

/**
 * @param {string} taskId
 * @param {string} cardId
 */
function subscribeToProgressMulti(taskId, cardId) {
  var es = new EventSource('/api/parse/' + taskId + '/status');
  ensureLivePreview(taskId);

  function parseData(e) {
    try { return JSON.parse(e.data); } catch (_) { return null; }
  }

  es.addEventListener('page_progress', function (e) {
    var data = parseData(e);
    if (!data) return;
    if (typeof data.completed_pages === 'number') {
      markPageActive(data.completed_pages, data.total_pages || _livePreview.totalPages);
      updateStagePill('page_progress');
    }
    var pct = typeof data.pct === 'number' ? data.pct : null;
    if (pct !== null) {
      updateUploadCard(cardId, 'running', pct + '% - ' + (data.message || ''), taskId);
    }
  });

  es.addEventListener('page_result', function (e) {
    var data = parseData(e);
    if (!data) return;
    if (typeof data.page_num === 'number') {
      markPageDone(data.page_num, data.total_pages || _livePreview.totalPages, data.markdown || '');
    }
  });

  ['profiling', 'noise_learning', 'table_merging', 'assembling', 'strategy_report'].forEach(function (evt) {
    es.addEventListener(evt, function (e) {
      var data = parseData(e);
      if (!data) return;
      updateStagePill(evt);
      var pct = typeof data.pct === 'number' ? data.pct : null;
      if (pct !== null) {
        updateUploadCard(cardId, 'running', pct + '% - ' + (data.message || ''), taskId);
      }
    });
  });

  es.addEventListener('done', function () {
    es.close();
    finalizeLivePreview();
    updateUploadCard(cardId, 'done', '완료', taskId);
    delete _activeUploads[taskId];
    loadHistory();
  });

  es.addEventListener('error', function (e) {
    if (!e.data) return;
    var data = parseData(e);
    es.close();
    updateUploadCard(cardId, 'error', (data && data.message) || '오류');
    delete _activeUploads[taskId];
    loadHistory();
  });

  es.onerror = function () {
    es.close();
    updateUploadCard(cardId, 'error', '연결 끊김');
    delete _activeUploads[taskId];
  };
}

// ---------------------------------------------------------------------------
// Upload card helpers
// ---------------------------------------------------------------------------

function addUploadCard(cardId, filename, status) {
  var card = document.createElement('div');
  card.className = 'upload-card';
  card.id = cardId;

  var nameEl = document.createElement('span');
  nameEl.className = 'upload-card__name';
  nameEl.textContent = filename;
  card.appendChild(nameEl);

  var badgeEl = document.createElement('span');
  badgeEl.className = 'upload-card__badge badge badge--' + statusToBadgeClass(status);
  badgeEl.textContent = statusToLabel(status);
  card.appendChild(badgeEl);

  uploadCards.appendChild(card);
}

function updateUploadCard(cardId, status, message, taskId) {
  var card = document.getElementById(cardId);
  if (!card) return;

  var badge = card.querySelector('.upload-card__badge');
  if (badge) {
    badge.className = 'upload-card__badge badge badge--' + statusToBadgeClass(status);
    badge.textContent = statusToLabel(status);
    if (message) badge.title = message;
  }

  // Add link to verify if done
  if (status === 'done' && taskId) {
    var existing = card.querySelector('.upload-card__link');
    if (!existing) {
      var link = document.createElement('a');
      link.className = 'upload-card__link btn btn--secondary btn--sm';
      link.href = '/verify/' + taskId;
      link.textContent = '검증';
      card.appendChild(link);
    }
  }
}

function statusToBadgeClass(status) {
  var map = {
    uploading: 'running',
    queued: 'pending',
    running: 'running',
    done: 'success',
    error: 'error',
    cancelled: 'error',
  };
  return map[status] || 'pending';
}

function statusToLabel(status) {
  var map = {
    uploading: '업로드 중',
    queued: '대기',
    running: '처리 중',
    done: '완료',
    error: '오류',
    cancelled: '취소됨',
  };
  return map[status] || status;
}

// ---------------------------------------------------------------------------
// Single-file SSE progress (original flow)
// ---------------------------------------------------------------------------

/**
 * @param {string} taskId
 */
function subscribeToProgress(taskId) {
  var es = new EventSource('/api/parse/' + taskId + '/status');
  ensureLivePreview(taskId);

  function parseData(e) {
    try { return JSON.parse(e.data); } catch (_) { return null; }
  }

  es.addEventListener('page_progress', function (e) {
    var data = parseData(e);
    if (!data) return;
    if (typeof data.completed_pages === 'number') {
      markPageActive(data.completed_pages, data.total_pages || _livePreview.totalPages);
      updateStagePill('page_progress');
    }
    var pct = typeof data.pct === 'number' ? data.pct : null;
    var msg = data.message || '';
    if (pct !== null) setProgress(pct, msg);
    else if (msg) updateStatus(msg);
  });

  es.addEventListener('page_result', function (e) {
    var data = parseData(e);
    if (!data) return;
    if (typeof data.page_num === 'number') {
      markPageDone(data.page_num, data.total_pages || _livePreview.totalPages, data.markdown || '');
    }
  });

  ['profiling', 'noise_learning', 'table_merging', 'assembling', 'strategy_report'].forEach(function (evt) {
    es.addEventListener(evt, function (e) {
      var data = parseData(e);
      if (!data) return;
      updateStagePill(evt);
      var pct = typeof data.pct === 'number' ? data.pct : null;
      var msg = data.message || '';
      if (pct !== null) setProgress(pct, msg);
      else if (msg) updateStatus(msg);
    });
  });

  es.addEventListener('done', function () {
    es.close();
    finalizeLivePreview();
    setProgress(100, '변환 완료! 잠시 후 결과 페이지로 이동합니다...');
    setTimeout(function () {
      window.location.href = '/verify/' + taskId;
    }, 1200);
  });

  es.addEventListener('error', function (e) {
    if (!e.data) return;
    var data = parseData(e);
    es.close();
    progressWrap.classList.add('hidden');
    dropZone.removeAttribute('aria-disabled');
    showAlert((data && data.message) || '파싱 오류가 발생했습니다.', 'error');
    loadHistory();
  });

  es.onerror = function () {
    es.close();
    progressWrap.classList.add('hidden');
    dropZone.removeAttribute('aria-disabled');
    showAlert('서버 연결이 끊겼습니다. 새로고침 후 다시 시도하세요.', 'error');
    loadHistory();
  };
}

// ---------------------------------------------------------------------------
// Queue status polling
// ---------------------------------------------------------------------------

function startQueuePolling() {
  if (_queuePollTimer) return;
  pollQueueStatus();
  _queuePollTimer = setInterval(pollQueueStatus, 5000);
}

function stopQueuePolling() {
  if (_queuePollTimer) {
    clearInterval(_queuePollTimer);
    _queuePollTimer = null;
  }
}

function pollQueueStatus() {
  fetch('/api/queue/status')
    .then(function (res) { return res.json(); })
    .then(function (json) {
      if (!json.success) return;
      var d = json.data;
      if (d.running === 0 && d.queued === 0) {
        queueBanner.classList.add('hidden');
        stopQueuePolling();
        dropZone.removeAttribute('aria-disabled');
      } else {
        queueBannerText.textContent = '처리 중 ' + d.running + '개 / 대기 ' + d.queued + '개';
        queueBanner.classList.remove('hidden');
      }
    })
    .catch(function () {});

  // Refresh per-task progress for the history table.
  fetch('/api/parse/active')
    .then(function (res) { return res.json(); })
    .then(function (json) {
      if (!json.success) return;
      _activeStateByTaskId = {};
      (json.data || []).forEach(function (t) { _activeStateByTaskId[t.task_id] = t; });
      // Refresh history rows in-place so the user sees live progress.
      annotateHistoryRowsWithProgress();
    })
    .catch(function () {});
}

function annotateHistoryRowsWithProgress() {
  if (!historyBody) return;
  Array.prototype.forEach.call(historyBody.querySelectorAll('tr[data-task-id]'), function (row) {
    var taskId = row.dataset.taskId;
    var snap = _activeStateByTaskId[taskId];
    var statusCell = row.children[1];
    if (!statusCell) return;
    if (snap && (snap.status === 'queued' || snap.status === 'running')) {
      var label = snap.status === 'queued'
        ? '대기 (' + (snap.completed_pages || 0) + '/' + (snap.total_pages || 0) + ')'
        : '처리 중 ' + (snap.completed_pages || 0) + '/' + (snap.total_pages || 0)
          + ' — ' + (snap.pct || 0) + '%';
      statusCell.innerHTML = '';
      var span = document.createElement('span');
      span.className = 'badge badge--running';
      span.textContent = label;
      statusCell.appendChild(span);
    }
  });
}

// ---------------------------------------------------------------------------
// Progress helpers
// ---------------------------------------------------------------------------

/**
 * @param {number} pct  0-100
 * @param {string} msg
 */
function setProgress(pct, msg) {
  progressFill.style.width = pct + '%';
  progressBar.setAttribute('aria-valuenow', String(pct));
  progressStatus.textContent = msg;
}

function updateStatus(msg) {
  progressStatus.textContent = msg;
}

// ---------------------------------------------------------------------------
// History
// ---------------------------------------------------------------------------

function loadHistory() {
  fetch('/api/history')
    .then(function (res) { return res.json(); })
    .then(function (json) {
      if (!json.success) return;
      renderHistory(json.data);
    })
    .catch(function () {});
}

/**
 * @param {Array} records
 */
function renderHistory(records) {
  while (historyBody.firstChild) {
    historyBody.removeChild(historyBody.firstChild);
  }

  if (records.length === 0) {
    var tr = document.createElement('tr');
    var td = document.createElement('td');
    td.setAttribute('colspan', '5');
    td.className = 'text-muted text-sm';
    td.style.textAlign = 'center';
    td.style.padding = '2rem';
    td.textContent = '변환 이력이 없습니다.';
    tr.appendChild(td);
    historyBody.appendChild(tr);
    return;
  }

  records.forEach(function (r) {
    var tr = document.createElement('tr');
    tr.dataset.taskId = r.task_id;

    // Filename
    var tdFilename = document.createElement('td');
    tdFilename.textContent = r.filename;
    tdFilename.title = r.task_id;
    tr.appendChild(tdFilename);

    // Status badge
    var tdStatus = document.createElement('td');
    tdStatus.appendChild(buildStatusBadge(r.status));
    tr.appendChild(tdStatus);

    // Created at
    var tdCreated = document.createElement('td');
    tdCreated.textContent = formatDate(r.created_at);
    tr.appendChild(tdCreated);

    // Completed at
    var tdCompleted = document.createElement('td');
    tdCompleted.textContent = r.completed_at ? formatDate(r.completed_at) : '-';
    tr.appendChild(tdCompleted);

    // Actions
    var tdActions = document.createElement('td');
    var actionsDiv = document.createElement('div');
    actionsDiv.className = 'actions';

    if (r.status === 'done') {
      actionsDiv.appendChild(buildLinkButton('/verify/' + r.task_id, '검증'));
      var dlBtn = buildLinkButton('/api/export/' + r.task_id, '다운로드');
      dlBtn.setAttribute('download', '');
      actionsDiv.appendChild(dlBtn);
    }

    if (r.status === 'queued') {
      var cancelBtn = document.createElement('button');
      cancelBtn.className = 'btn btn--secondary btn--sm';
      cancelBtn.textContent = '취소';
      cancelBtn.setAttribute('aria-label', r.filename + ' 취소');
      cancelBtn.addEventListener('click', function () {
        cancelTask(r.task_id);
      });
      actionsDiv.appendChild(cancelBtn);
    }

    var deleteBtn = document.createElement('button');
    deleteBtn.className = 'btn btn--danger btn--sm';
    deleteBtn.textContent = '삭제';
    deleteBtn.setAttribute('aria-label', r.filename + ' 삭제');
    deleteBtn.addEventListener('click', function () {
      if (window.confirm('이 항목을 삭제하시겠습니까?')) {
        deleteHistoryItem(r.task_id);
      }
    });
    actionsDiv.appendChild(deleteBtn);

    tdActions.appendChild(actionsDiv);
    tr.appendChild(tdActions);

    historyBody.appendChild(tr);
  });

  annotateHistoryRowsWithProgress();
}

/**
 * @param {string} href
 * @param {string} label
 * @returns {HTMLAnchorElement}
 */
function buildLinkButton(href, label) {
  var a = document.createElement('a');
  a.href = href;
  a.className = 'btn btn--secondary btn--sm';
  a.textContent = label;
  return a;
}

/**
 * @param {string} taskId
 */
function cancelTask(taskId) {
  fetch('/api/parse/' + taskId + '/cancel', { method: 'POST' })
    .then(function (res) { return res.json(); })
    .then(function (json) {
      if (json.success) loadHistory();
      else showAlert((json.error && json.error.message) || '취소 실패', 'error');
    })
    .catch(function () { showAlert('취소 요청 실패', 'error'); });
}

/**
 * @param {string} taskId
 */
function deleteHistoryItem(taskId) {
  fetch('/api/history/' + taskId, { method: 'DELETE' })
    .then(function (res) { return res.json(); })
    .then(function (json) {
      if (json.success) loadHistory();
      else showAlert((json.error && json.error.message) || '삭제 실패', 'error');
    })
    .catch(function () { showAlert('삭제 요청 실패', 'error'); });
}

// ---------------------------------------------------------------------------
// Alert helpers
// ---------------------------------------------------------------------------

/**
 * @param {string} msg
 * @param {'error'|'success'} type
 */
function showAlert(msg, type) {
  while (alertArea.firstChild) {
    alertArea.removeChild(alertArea.firstChild);
  }
  var div = document.createElement('div');
  div.className = 'alert alert--' + type;
  div.setAttribute('role', 'alert');
  div.textContent = msg;
  alertArea.appendChild(div);
}

function clearAlert() {
  while (alertArea.firstChild) {
    alertArea.removeChild(alertArea.firstChild);
  }
}

// ---------------------------------------------------------------------------
// Utility
// ---------------------------------------------------------------------------

/**
 * @param {string} status
 * @returns {HTMLSpanElement}
 */
function buildStatusBadge(status) {
  var map = {
    pending: ['pending', '대기'],
    queued: ['pending', '대기'],
    running: ['running', '처리 중'],
    done: ['success', '완료'],
    error: ['error', '오류'],
    cancelled: ['error', '취소됨'],
  };
  var entry = map[status] || ['pending', status];
  var span = document.createElement('span');
  span.className = 'badge badge--' + entry[0];
  span.textContent = entry[1];
  return span;
}

/**
 * @param {string} iso
 * @returns {string}
 */
function formatDate(iso) {
  if (!iso) return '-';
  try {
    var d = new Date(iso);
    return d.toLocaleString('ko-KR', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch (_) {
    return iso;
  }
}

// ---------------------------------------------------------------------------
// Architecture modal
// ---------------------------------------------------------------------------

var archModal = document.getElementById('archModal');
var btnArchitecture = document.getElementById('btnArchitecture');
var btnCloseArch = document.getElementById('btnCloseArch');

if (btnArchitecture && archModal) {
  btnArchitecture.addEventListener('click', function () {
    archModal.classList.remove('hidden');
  });

  btnCloseArch.addEventListener('click', function () {
    archModal.classList.add('hidden');
  });

  archModal.addEventListener('click', function (e) {
    if (e.target === archModal) archModal.classList.add('hidden');
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && !archModal.classList.contains('hidden')) {
      archModal.classList.add('hidden');
    }
  });
}

// ---------------------------------------------------------------------------
// Background task restoration (page reload)
// ---------------------------------------------------------------------------

/**
 * On page load, fetch active tasks and rebuild upload cards + live preview.
 * Then re-subscribe via SSE to merge into the live stream (server emits a
 * `catchup` event first so we don't miss late events between REST + SSE).
 */
function restoreActiveTasks() {
  return fetch('/api/parse/active')
    .then(function (res) { return res.json(); })
    .then(function (json) {
      if (!json.success || !Array.isArray(json.data) || json.data.length === 0) return;

      var tasks = json.data;

      tasks.forEach(function (task) {
        var cardId = 'restore-' + task.task_id;
        if (document.getElementById(cardId)) return;
        addUploadCard(cardId, task.filename, task.status === 'queued' ? 'queued' : 'running');
        updateUploadCard(cardId, task.status === 'queued' ? 'queued' : 'running',
                         (task.pct || 0) + '% - ' + (task.completed_pages || 0) + '/' + (task.total_pages || 0),
                         task.task_id);
        _activeUploads[task.task_id] = { filename: task.filename, cardId: cardId };
      });

      // Restore the most recent task's live preview, then re-subscribe.
      var latest = tasks[0];
      return restoreLivePreview(latest.task_id).then(function () {
        // Subscribe each task to SSE — same multi-file handler is reused.
        tasks.forEach(function (task) {
          var entry = _activeUploads[task.task_id];
          if (entry) subscribeToProgressMulti(task.task_id, entry.cardId);
        });
        startQueuePolling();
      });
    })
    .catch(function () { /* silent — best effort */ });
}

/**
 * Pull state + completed page list, then fetch each page's markdown.
 * Restores grid colours and the recent-pages tail.
 */
function restoreLivePreview(taskId) {
  ensureLivePreview(taskId);

  return fetch('/api/parse/' + taskId + '/state')
    .then(function (res) { return res.json(); })
    .then(function (json) {
      if (!json.success) return null;
      var state = json.data;
      if (state.total_pages > 0) ensurePageGrid(state.total_pages);
      _livePreview.totalPages = state.total_pages || 0;
      // Surface stage state on the timeline if available
      if (state.current_stage) updateStagePill(state.current_stage);
      livePreviewCounter.textContent = (state.completed_pages || 0) + ' / ' + (state.total_pages || 0);
      return state;
    })
    .then(function (state) {
      if (!state) return;
      return fetch('/api/parse/' + taskId + '/pages')
        .then(function (res) { return res.json(); })
        .then(function (json) {
          if (!json.success) return;
          var completed = json.data.completed || [];
          // Fetch page markdown in parallel (best-effort).
          return Promise.all(completed.map(function (pageNum) {
            return fetch('/api/parse/' + taskId + '/page/' + pageNum)
              .then(function (r) { return r.json(); })
              .then(function (pj) {
                if (pj.success) {
                  markPageDone(pageNum, state.total_pages || _livePreview.totalPages,
                               pj.data.markdown || '');
                }
              })
              .catch(function () {});
          }));
        });
    })
    .catch(function () {});
}

// Augment the SSE handler with a one-shot `catchup` listener attached to
// the EventSource created in subscribeToProgressMulti / subscribeToProgress.
// Because both functions create their own EventSource we patch each by
// monkey-wrapping via a delegated listener inside this file.
//
// Implementation: rather than modify both subscribe* functions we add the
// listener via `addEventListener('catchup', ...)` after they create the
// EventSource. To do that without rewriting them, we wrap EventSource here
// to auto-attach a catchup handler when the URL targets /api/parse/.
(function patchEventSource() {
  if (!window.EventSource || window.EventSource.__docforgePatched) return;
  var Original = window.EventSource;
  function PatchedES(url, opts) {
    var es = new Original(url, opts);
    if (typeof url === 'string' && url.indexOf('/api/parse/') === 0) {
      es.addEventListener('catchup', function (e) {
        try {
          var snapshot = JSON.parse(e.data);
          // Catch-up arrives once per (re)connection. Only the live preview
          // panel needs visual refresh — REST restoration already filled in
          // page markdowns, so we just sync counters/stage.
          if (_livePreview.taskId === snapshot.task_id) {
            if (snapshot.total_pages > 0) ensurePageGrid(snapshot.total_pages);
            _livePreview.totalPages = snapshot.total_pages || _livePreview.totalPages;
            if (snapshot.current_stage) updateStagePill(snapshot.current_stage);
            livePreviewCounter.textContent =
              (snapshot.completed_pages || 0) + ' / ' + (snapshot.total_pages || 0);
          }
        } catch (_) {}
      });
    }
    return es;
  }
  PatchedES.prototype = Original.prototype;
  PatchedES.CONNECTING = Original.CONNECTING;
  PatchedES.OPEN = Original.OPEN;
  PatchedES.CLOSED = Original.CLOSED;
  PatchedES.__docforgePatched = true;
  window.EventSource = PatchedES;
})();

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

loadHistory();
restoreActiveTasks();
