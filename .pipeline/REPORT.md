# Feedback-003 구현 완료 보고서

## 요약

2개 기능 구현 완료. 기존 테스트 201개 + 신규 17개 = **218개 통과**.

## 기능 1: 병렬 페이지 처리

### 변경 파일
| 파일 | 변경 |
|------|------|
| `infrastructure/config.py` | `max_workers`, `max_ocr_workers` 필드 추가 |
| `usecases/parse_pdf.py` | `_process_single_page()` 워커 추출, `ThreadPoolExecutor` 도입 |

### 설계 결정
- **워커별 독립 doc/plumber open/close**: PyMuPDF, pdfplumber 모두 thread-unsafe → 각 워커가 독립 핸들 사용
- **`Semaphore(max_ocr_workers)`**: GPU 메모리 보호를 위한 OCR 동시 실행 제한
- **`threading.Lock`**: `on_progress` / `on_page_done` 콜백 직렬화
- **기본값 1**: `max_workers=1`이면 기존과 동일한 순차 실행 (하위 호환 보장)
- **페이지 순서 보장**: `raw_results`를 `page_idx` 기준 정렬 후 조립

## 기능 2: LLM Fallback (Qwen2-VL-7B MLX)

### 변경/생성 파일
| 파일 | 변경 |
|------|------|
| `domain/ports.py` | `VisionLLMEngine` Protocol 추가 |
| `domain/models.py` | `LLMFallbackRecord` 모델 추가, `ParseResult.llm_fallback_records` 필드 추가 |
| `infrastructure/config.py` | LLM 관련 5개 필드 추가 |
| `processing/llm_fallback_router.py` | **신규** — 신뢰도 기반 라우팅 + 품질 게이트 |
| `adapters/vision_llm_engine.py` | **신규** — Qwen2-VL-7B MLX 어댑터 |
| `usecases/parse_pdf.py` | LLM 엔진 초기화 + fallback 호출 통합 |

### 설계 결정
- **opt-in 기본값**: `llm_fallback_enabled=False` → 명시적 활성화 필요
- **3중 품질 게이트**: LLM 호출 실패 → 기존 유지 / 문자 소실 → 기각 / 신뢰도 미개선 → 기각
- **감사 로그**: `LLMFallbackRecord`로 채택/기각 사유 기록
- **Graceful degradation**: mlx_vlm 미설치 시 자동 비활성, 예외 시 기존 OCR 결과 유지
- **지연 로딩**: 모델은 최초 `correct_page()` 호출 시 로드 (import 시 로드 X)

## 테스트 결과

```
218 passed, 4 errors (flask 미설치 기존 이슈)
```

### 신규 테스트 (17개)
- `TestParallelConfig`: 3개 — config 기본값 및 커스텀 값
- `TestLLMConfig`: 2개 — LLM config 필드
- `TestShouldInvokeLLM`: 5개 — 라우팅 조건 분기
- `TestRunLLMFallback`: 5개 — 품질 게이트 시나리오
- `TestVisionLLMEngineProtocol`: 1개 — Protocol 정의 검증
- `TestParseResultWithLLMRecords`: 1개 — ParseResult 하위호환

## 아키텍처 준수

- domain/ 레이어: Protocol 기반 포트 (VisionLLMEngine)
- processing/ 레이어: 순수 로직 (llm_fallback_router — 외부 의존 없음)
- adapters/ 레이어: 외부 라이브러리 캡슐화 (vision_llm_engine)
- 불변성: 모든 신규 모델 frozen dataclass
- Graceful degradation: 모든 실패 경로에서 기존 동작 유지
