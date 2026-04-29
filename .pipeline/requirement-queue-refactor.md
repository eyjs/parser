# DocForge — 비동기 큐 + 원본 비교 편집기

## 생성일시
2026-04-28 00:00

## 목적
- 왜 만드는가: (1) PDF 업로드마다 스레드를 무제한 생성하는 구조를 워커 큐로 전환하여 수십~수백 개 일괄 처리를 안정화한다. (2) 원본 PDF와 변환 MD를 나란히 비교하며 파싱 깨진 부분을 사용자가 바로 수정할 수 있는 **비교 편집기**를 구현한다. 이것이 시스템의 핵심 워크플로우다.
- 누가 사용하는가: DocForge 데스크톱 앱 사용자 (PyInstaller Windows 단독 실행, 외부 인프라 없음)
- 기대 효과: 원본↔MD 비교 기반 수기 교정 워크플로우 확립, 멀티파일 일괄 처리 안정화, 실시간 파싱 진행률 가시성

## 스코프

### 포함 (이번에 만드는 것)
- [ ] `docforge/web/worker.py` 신규 — ThreadPoolExecutor 기반 파싱 워커 큐 모듈
- [ ] `docforge/web/routes.py` 리팩터 — `threading.Thread` 직접 생성 제거, 워커 큐에 작업 제출로 변경
- [ ] `docforge/web/storage.py` 확장 — TaskRecord에 `queued_at` 필드 추가, 상태값에 `queued` 추가 (`pending` 대체), `cancelled` 상태 추가
- [ ] `docforge/web/app.py` 확장 — 앱 시작 시 워커 큐 초기화, 앱 종료 시 graceful shutdown
- [ ] `GET /api/queue/status` 신규 API — 큐 현재 상태 반환 `{ running, queued, workers }`
- [ ] `POST /api/parse/<task_id>/cancel` 신규 API — 대기 중 작업 취소
- [ ] `POST /api/parse` 멀티파일 지원 — `files` 다중 파일 수신 (기존 단일 `file` 필드 하위 호환 유지)
- [ ] `dashboard.html` 수정 — 멀티파일 드래그앤드롭 UI, 큐 상태 배너 영역 추가
- [ ] `dashboard.js` 수정 — 멀티파일 업로드 루프, 큐 상태 폴링, 파일별 업로드 진행 카드

### 제외 (이번에 만들지 않는 것)
- Celery, Redis, RQ 등 외부 메시지 브로커
- 작업 우선순위 변경 UI (FIFO 고정)
- 클라우드 배포용 인프라 변경
- 기존 단일 파일 업로드 흐름 제거 (멀티파일과 공존)
- 실행 중(`running`) 작업 강제 중단

## 기술스택
- 언어: Python 3.13 / Windows
- 프레임워크: Flask (현행 유지)
- 큐 구현: `concurrent.futures.ThreadPoolExecutor` + `queue.Queue` (표준 라이브러리만, 외부 의존성 없음)
- 프론트엔드: Vanilla JS (현행 유지)
- 실시간 스트리밍: SSE — ProgressTracker (현행 유지)
- 패키징: PyInstaller (Windows 데스크톱 앱)
- 영속화: JSON 파일 기반 TaskStore (현행 유지)

## 핵심 기능

### P0 (필수)

- **ThreadPoolExecutor 워커 큐 (`worker.py`)**
  - 최대 동시 실행 워커 수: `min(4, os.cpu_count())` 기본값, `app.config["MAX_WORKERS"]`로 오버라이드 가능
  - 내부 큐: `queue.Queue` (FIFO)
  - 앱 시작 시 초기화, 종료 시 `shutdown(wait=False)` (PyInstaller 호환)
  - `_TRACKERS` 딕셔너리 및 `_TRACKER_LOCK`을 `worker.py`로 이전하여 워커 큐와 함께 관리
  - `Future` 객체를 task_id 키로 보관하여 취소 가능 여부 판별에 활용

- **TaskRecord 상태 전이 확장**
  - `pending` → `queued` (이름 변경, 큐 삽입 시점)
  - `queued → running → done / error / cancelled`
  - `queued_at` 필드 추가 (큐 삽입 시각 ISO 문자열)

- **개별 작업 실패 격리**
  - 단일 PDF 파싱 예외가 큐 전체에 영향 없음
  - 해당 task만 `error` 상태로 기록, 다음 작업 계속 실행

- **`GET /api/queue/status`**
  - 응답: `{ "success": true, "data": { "running": N, "queued": M, "workers": W } }`

- **`POST /api/parse` 멀티파일 지원**
  - `request.files.getlist("files")` 또는 기존 `request.files["file"]` 모두 수용
  - 각 파일마다 TaskRecord 생성 + 큐 삽입
  - 응답: `{ "success": true, "data": { "task_ids": [...] } }`
  - 기존 단일 파일 응답 `{ "data": { "task_id": "..." } }` 하위 호환 유지

- **기존 SSE 진행률 스트리밍 유지**
  - `ProgressTracker`, `GET /api/parse/<task_id>/status` 변경 없음
  - 각 작업은 독립 ProgressTracker 인스턴스 소유

- **`POST /api/parse/<task_id>/cancel`**
  - 대기 중(`queued`) 작업: Future.cancel() + 상태 `cancelled` 갱신
  - 실행 중(`running`) 작업: 취소 불가, `409 Conflict` 응답

### P1 (중요)

- **대시보드 큐 상태 배너**
  - "처리 중 N개 / 대기 M개" 실시간 표시
  - `GET /api/queue/status` 5초 폴링
  - 큐가 비면(running=0, queued=0) 배너 자동 숨김

- **멀티파일 드래그앤드롭 UI**
  - `<input type="file" multiple accept=".pdf">`
  - 드롭/선택 시 파일 목록 표시, 파일별 업로드 상태 카드 (파일명 + 상태 배지)
  - JS: 파일별 개별 `fetch('/api/parse')` 루프 (단일 멀티파트 아닌 파일별 개별 요청)

- **히스토리 테이블 `queued` 배지 추가**
  - 기존 `pending` 배지를 `queued`로 교체
  - `cancelled` 상태 배지 추가

### P0 (필수 — 원본 비교 편집기, 시스템 핵심)

- **원본 PDF | MD 사이드바이사이드 비교 편집기 (`/verify/<task_id>`)**
  - 좌측: 원본 PDF 뷰어 (pdf.js 기반 페이지별 렌더링, 스크롤/줌)
  - 우측: 변환된 마크다운 편집기 (편집 가능한 textarea + 실시간 프리뷰 토글)
  - 핵심 UX: 원본 PDF의 특정 부분을 보면서 깨진 마크다운을 바로 수정
  - **페이지 단위 검증 네비게이션**: 이전/다음 페이지 버튼, 페이지 번호 직접 입력, 키보드 ← → 지원
  - PDF 페이지 이동 시 우측 MD 편집기가 해당 페이지의 마크다운 섹션으로 자동 스크롤 (역방향도 동일)
  - 페이지별 검증 상태 표시: 미검증 / 검증완료 / 수정됨 (체크박스 또는 배지)
  - 검증 진행률 표시: "12/56 페이지 검증 완료"
  - Ctrl+S 저장, 저장 시 `POST /api/save/<task_id>`
  - 기존 `/edit/<task_id>` 편집기를 비교 편집기로 통합 (별도 편집 페이지 불필요)

- **파싱 중 실시간 라이브 모드**
  - 파싱 완료 전에도 `/verify/<task_id>` 진입 가능
  - SSE로 페이지별 마크다운 청크 실시간 수신 (`EVT_PAGE_RESULT` 이벤트)
  - 페이지 처리 완료 시마다 우측 편집기에 마크다운이 실시간으로 붙어감
  - 좌측 PDF도 해당 페이지로 자동 스크롤
  - 파싱 완료 후 전체 편집 모드로 전환

- **구현 변경 사항**
  - `parse_pdf.py`: `on_page_done: Callable[[int, str], None] | None` 콜백 추가 (page_num, page_markdown)
  - `sse.py`: `EVT_PAGE_RESULT = "page_result"` 이벤트 추가
  - `routes.py` (`_run_parse`): 페이지별 마크다운을 `tracker.push()` 로 SSE 전송
  - `verify.html`: 사이드바이사이드 레이아웃 (좌 PDF 뷰어 + 우 MD 편집기)
  - `verify.js`: SSE 구독 라이브 모드 + pdf.js 뷰어 초기화 + 편집/저장 기능 통합
  - `dashboard.js`: done 이벤트 시 `/verify/<task_id>`로 이동 (변경 없음)
  - pdf.js: CDN 또는 로컬 번들 (`static/js/lib/pdf.min.js`)

### P0 (필수 — 변환 결과 영속화 + 버전 비교)

- **파일별 변환 결과 JSON 영속화**
  - 현재 문제: 서버 재시작/새로고침 시 in-memory 마크다운·메타데이터 유실 (tasks.json에 상태만 저장)
  - 변환 완료 시 파일별 결과를 JSON으로 저장: `uploads/<task_id>/result.json`
  - result.json: `{ "markdown": "...", "metadata": {...}, "stats": {...}, "completed_at": "..." }`
  - `/verify/<task_id>` 및 `/api/parse/<task_id>/result` 진입 시 result.json에서 로드 → 서버 재시작 후에도 결과 확인 가능

- **변환 결과 버전 관리 (git diff 스타일, 파일 단위)**
  - 최초 변환 결과를 원본 버전으로 보존: `uploads/<task_id>/versions/v0_original.md`
  - 사용자가 MD를 수정/저장할 때마다 버전 생성: `uploads/<task_id>/versions/v<N>_<timestamp>.md`
  - 버전 목록 API: `GET /api/versions/<task_id>` → 저장 이력 목록
  - 버전 비교 API: `GET /api/diff/<task_id>?v1=<version1>&v2=<version2>` → 두 버전 간 diff
  - 기본 비교: 원본(v0, 변환 직후) vs 현재(최신 저장본) — 파싱 깨진 부분 수정 이력 추적
  - diff 알고리즘: Python `difflib.unified_diff` 기반, 줄 단위 변경/추가/삭제
  - 프론트엔드: 변경된 줄 빨강(삭제)/초록(추가) 하이라이트, git diff 스타일 뷰어

- **구현 변경 사항**
  - `storage.py`: 결과 JSON 읽기/쓰기 메서드 추가, 버전 관리 메서드 추가
  - `routes.py`: 결과 로드 시 result.json 파일에서 직접 읽기, 버전/diff API 추가
  - `routes.py` (`_run_parse`): 파싱 완료 시 result.json + v0_original.md 저장
  - `routes.py` (`api_save`): 저장 시 새 버전 파일 생성
  - `verify.js`: diff 뷰어 토글 버튼 (원본 vs 수정본 비교 모드)

### P2 (있으면 좋음)

- **일괄 업로드 결과 요약 토스트**: N개 중 M개 성공 메시지
- **워커 수 환경변수 오버라이드**: `DOCFORGE_MAX_WORKERS` 환경변수 지원

## 제약사항
- PyInstaller 단독 실행 환경 — 외부 프로세스/브로커 절대 불가, 표준 라이브러리만 사용
- `multiprocessing` 사용 금지 — PyInstaller + Windows fork 제약으로 `threading` 기반만 허용
- Python 3.13 GIL: PDF 파싱은 I/O 및 C 확장(PyMuPDF, PaddleOCR) 중심이므로 스레드 병렬 효과 유효
- Flask 개발 서버 (`threaded=True`) — 워커 큐는 Flask 스레드와 분리된 독립 풀로 운영
- 기존 `TaskStore._lock` (threading.Lock) 유지 — 다수 워커의 동시 상태 업데이트에 안전
- 기존 `ProgressTracker` (queue.Queue 기반) 변경 없음

## 성공 기준
1. PDF 50개를 동시 드래그앤드롭 업로드했을 때 서버가 크래시 없이 큐에 순서대로 적재된다
2. 동시 처리 워커 수가 `MAX_WORKERS`를 초과하지 않는다 (로그로 검증)
3. 큐에서 한 파일이 파싱 오류를 내도 나머지 파일이 계속 처리된다
4. `GET /api/queue/status` 응답이 실제 큐 상태와 일치한다
5. 기존 단일 파일 업로드 + SSE 진행률 흐름이 변경 없이 동작한다
6. `POST /api/parse/<task_id>/cancel`로 대기 중 작업 취소 시 해당 task 상태가 `cancelled`로 변경된다
7. PyInstaller 빌드 후 Windows에서 앱 시작/종료 정상 동작
8. `/verify/<task_id>` 진입 시 좌측 원본 PDF + 우측 MD 편집기가 사이드바이사이드로 표시된다
9. 페이지 이전/다음 버튼으로 한 장씩 넘기며 원본과 MD를 비교 검증할 수 있다
10. MD 편집기에서 깨진 부분을 수정하고 Ctrl+S로 저장할 수 있다
11. 파싱 중 진입 시 페이지별 마크다운이 실시간으로 추가되며, 완료 후 전체 편집 모드로 전환된다
12. 페이지별 검증 상태(미검증/검증완료/수정됨)를 표시하고 진행률을 추적할 수 있다

## 변경 대상 파일 요약
| 파일 | 변경 유형 | 주요 내용 |
|---|---|---|
| `docforge/web/worker.py` | 신규 생성 | ThreadPoolExecutor 워커 큐, ProgressTracker 레지스트리 |
| `docforge/web/routes.py` | 리팩터 | Thread 생성 → 워커 큐 submit, 멀티파일 수신, cancel/queue-status 라우트 추가 |
| `docforge/web/storage.py` | 확장 | `queued_at` 필드, `queued`/`cancelled` 상태 추가 |
| `docforge/web/app.py` | 확장 | 워커 큐 초기화/종료 연동 |
| `docforge/web/static/js/dashboard.js` | 수정 | 멀티파일 업로드, 큐 상태 폴링, 파일별 상태 카드 |
| `docforge/web/templates/dashboard.html` | 수정 | 멀티파일 input, 큐 상태 배너, 파일별 상태 카드 영역 |
| `docforge/web/templates/verify.html` | 수정 | 사이드바이사이드 레이아웃 (원본 PDF \| MD 뷰어) |
| `docforge/web/static/js/verify.js` | 수정 | SSE 구독 라이브 모드, 페이지별 마크다운 실시간 append |
| `docforge/usecases/parse_pdf.py` | 확장 | `on_page_done` 콜백 추가 (페이지별 마크다운 즉시 전달) |
| `docforge/web/sse.py` | 확장 | `EVT_PAGE_RESULT` 이벤트 추가 |

## 특이사항
- `_TRACKERS` 딕셔너리와 `_TRACKER_LOCK`은 현재 `routes.py` 모듈 레벨에 있으나, `worker.py`로 이전하여 워커 큐와 함께 응집도 있게 관리한다
- Flask `app_context` 전달 방식은 현행(`app._get_current_object()`) 유지
- `queue.Queue`에 삽입되기 전 `Future` 취소는 `concurrent.futures.Future.cancel()`로 처리하며, 이미 실행 중인 Future는 cancel()이 False를 반환하므로 409로 응답한다
