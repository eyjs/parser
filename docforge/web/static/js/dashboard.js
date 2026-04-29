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

  es.addEventListener('message', function (e) {
    var payload;
    try { payload = JSON.parse(e.data); } catch (_) { return; }

    var event = payload.event;
    var data = payload.data || {};

    if (event === 'heartbeat') return;

    var pct = typeof data.pct === 'number' ? data.pct : null;
    var msg = data.message || '';

    if (event === 'done') {
      es.close();
      updateUploadCard(cardId, 'done', '완료', taskId);
      delete _activeUploads[taskId];
      loadHistory();
      return;
    }

    if (event === 'error') {
      es.close();
      updateUploadCard(cardId, 'error', msg || '오류');
      delete _activeUploads[taskId];
      loadHistory();
      return;
    }

    if (pct !== null) {
      updateUploadCard(cardId, 'running', pct + '% - ' + msg, taskId);
    }
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

  es.addEventListener('message', function (e) {
    var payload;
    try { payload = JSON.parse(e.data); } catch (_) { return; }

    var event = payload.event;
    var data = payload.data || {};

    if (event === 'heartbeat') return;

    var pct = typeof data.pct === 'number' ? data.pct : null;
    var msg = data.message || '';

    if (pct !== null) setProgress(pct, msg);
    else if (msg) updateStatus(msg);

    if (event === 'done') {
      es.close();
      setProgress(100, '변환 완료! 잠시 후 결과 페이지로 이동합니다...');
      setTimeout(function () {
        window.location.href = '/verify/' + taskId;
      }, 1200);
    }

    if (event === 'error') {
      es.close();
      progressWrap.classList.add('hidden');
      dropZone.removeAttribute('aria-disabled');
      showAlert(msg || '파싱 오류가 발생했습니다.', 'error');
      loadHistory();
    }
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
        return;
      }
      queueBannerText.textContent = '처리 중 ' + d.running + '개 / 대기 ' + d.queued + '개';
      queueBanner.classList.remove('hidden');
    })
    .catch(function () {});
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
// Init
// ---------------------------------------------------------------------------

loadHistory();
