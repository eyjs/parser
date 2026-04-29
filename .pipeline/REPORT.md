# 파이프라인 실행 리포트

## 요약
- 요구사항: DocForge 비동기 큐 + 원본 비교 편집기 (ThreadPoolExecutor 워커 큐, 멀티파일 업로드, PDF 사이드바이사이드 비교 편집기, 버전 관리)
- 실행 시간: 2026-04-28
- 최종 상태: 성공 (130 tests pass)

## 구현 결과

### 생성/수정된 파일

| 파일 | 변경 유형 | 설명 |
|------|-----------|------|
| `docforge/web/worker.py` | 신규 | ThreadPoolExecutor 기반 워커 큐, tracker 레지스트리 |
| `docforge/web/routes.py` | 리팩터 | Thread -> 워커큐, 멀티파일, cancel/queue-status/versions/diff API |
| `docforge/web/storage.py` | 확장 | queued/cancelled 상태, result.json, 버전관리 |
| `docforge/web/app.py` | 확장 | 워커큐 초기화/종료 |
| `docforge/web/sse.py` | 확장 | EVT_PAGE_RESULT 이벤트, push_page_result 메서드 |
| `docforge/usecases/parse_pdf.py` | 확장 | on_page_done 콜백 |
| `docforge/web/templates/dashboard.html` | 수정 | 멀티파일 input, 큐 배너, 업로드 카드 영역 |
| `docforge/web/static/js/dashboard.js` | 재작성 | 멀티파일 업로드, 큐 폴링, 취소 기능 |
| `docforge/web/templates/verify.html` | 재작성 | 사이드바이사이드 비교 편집기 레이아웃 |
| `docforge/web/static/js/verify.js` | 재작성 | pdf.js 뷰어, SSE 라이브 모드, 편집/프리뷰/diff |
| `docforge/web/static/css/main.css` | 확장 | compare-editor, upload-cards, queue-banner, diff, live-badge |
| `docforge/web/static/js/lib/pdf.min.js` | 신규 | pdf.js v3.11.174 로컬 번들 |
| `docforge/web/static/js/lib/pdf.worker.min.js` | 신규 | pdf.js worker 로컬 번들 |
| `tests/unit/test_web.py` | 수정 | pending->queued, editor redirect 반영 |
| `tests/unit/test_worker_queue.py` | 신규 | 워커큐, 새 API, storage 확장, SSE 확장 테스트 15개 |

### 핵심 구현 사항

1. **비동기 워커 큐 (worker.py)**
   - ThreadPoolExecutor(max_workers=min(4, cpu_count))
   - Future 기반 취소 지원
   - _TRACKERS 레지스트리를 routes.py에서 이전
   - 실패 격리: 한 태스크 오류가 다른 태스크에 영향 없음

2. **멀티파일 업로드**
   - POST /api/parse: files[] 멀티파일 + file 단일파일 하위 호환
   - 단일 파일: {task_id: "..."}, 멀티: {task_ids: [...]}
   - 파일별 개별 SSE 구독

3. **원본 비교 편집기 (/verify)**
   - 좌측: pdf.js 기반 PDF 뷰어 (페이지 네비게이션, 키보드 지원)
   - 우측: MD 편집기 (프리뷰 토글, Ctrl+S 저장)
   - SSE 라이브 모드: 파싱 중 페이지별 마크다운 실시간 수신
   - Diff 뷰어: 원본 vs 수정본 비교 (빨강/초록 하이라이트)
   - /edit 경로는 /verify로 리다이렉트

4. **결과 영속화 + 버전 관리**
   - uploads/<task_id>/result.json 저장/로드
   - uploads/<task_id>/versions/v0_original.md 원본 보존
   - 저장 시마다 새 버전 생성
   - GET /api/versions/<task_id>, GET /api/diff/<task_id>

5. **새로운 API 엔드포인트**
   - GET /api/queue/status
   - POST /api/parse/<task_id>/cancel
   - GET /api/versions/<task_id>
   - GET /api/diff/<task_id>?v1=...&v2=...

## 빌드/테스트 결과
- 기존 115개 테스트: 모두 통과
- 신규 15개 테스트: 모두 통과
- 총 130개 테스트 통과

## 남은 이슈
| # | 이슈 | 심각도 | 설명 |
|---|------|--------|------|
| 1 | PDF 스크롤 연동 | Low | PDF 페이지와 MD 섹션 자동 스크롤 연동은 페이지 구분자(---) 기반으로 간접 지원. 정밀 매핑은 추후 개선 필요 |
| 2 | 페이지별 검증 상태 | Low | 검증 체크박스/배지 UI는 프론트엔드만으로는 영속화 불가. 추후 서버 상태 관리 필요 |
| 3 | PyInstaller 테스트 | Low | 실제 PyInstaller 빌드 후 atexit + shutdown 동작 검증 필요 |

## 생성된 문서
| 문서 | 경로 | 용도 |
|------|------|------|
| Plan | .pipeline/plan.md | 아키텍처 계획 |
| Tasks | .pipeline/tasks/task-001~007.md | 태스크 분할 |
| Report | .pipeline/REPORT.md | 최종 리포트 |
