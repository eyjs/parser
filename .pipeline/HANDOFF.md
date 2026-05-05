# DocForge — 핸드오프 문서

> 이 문서 + requirement.md를 읽으면 현재 상태부터 즉시 진행 가능.
> 생성일: 2026-04-27
> 최종 업데이트: 2026-05-06

---

## 1. 프로젝트 정체성

**DocForge = 한국어 PDF → Markdown 파싱 엔진.** SaaS가 아니라 엔진.
별도 SaaS Wrapper(ai-platform)가 인증/Frontend/요금제 담당.

두 가지 진입점 동시 운영:
- DocForge Flask UI (`localhost:5000`) — 개발/테스트/데모
- ai-platform Frontend (Next.js) — 일반 사용자 (DocForge API를 HTTP/SSE로 호출)

---

## 2. 최근 변경 (2026-05-06)

### 완료: 코드 리뷰 수정 (커밋 19789fd)

**코드 리뷰 7건 수정**:

| # | 수정 | 파일 |
|---|------|------|
| 1 | `_resolve_provider()` sentinel 캐싱 버그 — None을 "미탐색"으로 오인 | `cloud_vlm_engine.py` |
| 2 | Anthropic 응답 타입 안전성 강화 (`hasattr` 체크) | `cloud_vlm_engine.py` |
| 3 | `vlm_provider` 환경변수 매핑 (`DOCFORGE_VLM_PROVIDER`) | `config.py` |
| 4 | `elements` 타입 어노테이션 `ParsedImage` 누락 수정 | `markdown_assembler.py` |
| 5 | `cloud_vlm` extras 그룹 추가 (openai, anthropic) | `pyproject.toml` |
| 6 | Dockerfile Python 3.13 + cloud_vlm 패키지 포함 | `Dockerfile` |
| 7 | Docker VLM 환경변수 3건 추가 | `docker-compose.yml` |

### 완료: VLM 파이프라인 Phase 1 (커밋 22f72bd)

**문제**: VLM 엔진이 파이프라인에 주입되지 않아 (1) 표 VLM 재추출, (2) 이미지 캡션 생성이 전혀 작동하지 않았음.

**해결 내용 (14파일, +1127줄)**:

| 항목 | 변경 | 핵심 파일 |
|------|------|-----------|
| VLM 폴백 체인 | 로컬 Qwen2-VL → OpenAI GPT-4o → Anthropic Claude | `_parse_pdf_helpers.py:build_llm_engine()` |
| 이미지 캡션 | `describe_image()` → `ParsedImage.alt_text` 자동 생성 | `image_vlm_captioner.py` (신규) |
| Cloud VLM | OpenAI/Anthropic 어댑터 (mlx 없는 Linux 환경 대응) | `cloud_vlm_engine.py` (신규) |
| Config 기본값 | `llm_fallback_enabled=True`, `image_extraction_enabled=True`, `vlm_provider="auto"` | `config.py` |
| is_available() 강화 | 모델 캐시 파일 존재 여부 확인 | `vision_llm_engine.py` |
| 표 중복 렌더링 | IoU > 0.8 기반 `_deduplicate_tables()` | `markdown_assembler.py` |
| 테스트 | 39개 신규 (총 576 passed, 0 failures) | `tests/unit/test_*.py` |

**VLM 사용법**:
- 로컬: `mlx-vlm` 설치 + Qwen2-VL 모델 캐시 → 자동 감지
- 클라우드: `OPENAI_API_KEY` 또는 `ANTHROPIC_API_KEY` 환경변수 설정
- 강제 지정: `ParserConfig(vlm_provider="openai")` 등

### 이전 진행 기록
- Phase 0 (Apple Vision PoC): 완료 — EasyOCR 제거, Apple Vision 강화
- v1 REST API: 완료 — 동기 파싱 엔드포인트 + 인증 + CORS
- CSV/Excel 파서: 완료 — 범용 파싱 서비스 승격

---

## 3. 코드베이스 현황 (2026-04-27 기준 코드 분석)

### 이미 구현된 것 (건드리지 말 것)
- OCR 엔진 2종: `apple_vision_engine.py` (macOS 1순위), `paddle_ocr.py` (Linux/한국어)
- `ocr_factory.py`: macOS에서 **이미 Apple Vision 1순위** (코드 변경 불필요)
- VLM 엔진 3종: `vision_llm_engine.py` (로컬 Qwen2-VL), `cloud_vlm_engine.py` (OpenAI/Anthropic)
- VLM 폴백 체인: `build_llm_engine()` — auto/local/openai/anthropic 선택
- 이미지 캡션: `image_vlm_captioner.py` — VLM으로 `ParsedImage.alt_text` 자동 생성
- 처리 파이프라인 17개 모듈: line_merger, heading_hierarchy, noise_detector, column_detector, markdown_assembler, region_vlm_router, text_structurer, domain_profiles(2종), caption_matcher, image_extractor, image_vlm_captioner, layout_router, page_classifier(6타입), block_splitter, table_quality_scorer, preprocessing_router
- Kiwi 형태소 분석기: `morpheme_analyzer.py` (NullAnalyzer 폴백 포함)
- Chunking 4종: by_page, by_title, semantic, fixed
- Web: Flask + SSE + TaskRegistry + Worker Queue + Storage(버전관리)
- v1 REST API: `/api/v1/parse` + 인증 + CORS
- CLI: `docforge/cli.py` (chunking output 모드 포함)

### 완료된 정비 항목
| 항목 | 상태 | 비고 |
|------|------|------|
| `pyobjc-framework-Vision` | 설치됨 | Apple Vision OCR 활성 |
| API 경로 | `/api/v1/parse/...` | versioned API 완료 |
| 인증 | CF-Connecting-IP 기반 내부/외부 분기 | `X-API-Key` 미들웨어 |
| CORS | `DOCFORGE_ALLOWED_ORIGINS` | 설정 완료 |
| VLM 엔진 | 로컬 + 클라우드 폴백 | `vlm_provider="auto"` |

### 다음 정비 대상
| 항목 | 현재 | 필요 |
|------|------|------|
| 레이아웃 감지 | `layout_detection_enabled=False` | Surya 또는 DocLayout-YOLO 연동 |
| OCR 좌표계 | Apple Vision 픽셀 vs PyMuPDF 포인트 | 통일 변환 레이어 |
| 병렬 처리 | `max_workers=1` | CPU 코어 기반 자동 설정 |
| OCR 교정 사전 | 2개 | 보험 특화 100개+ 확장 |

---

## 4. 환경 (Mac Studio M1 Max)

```bash
# 설치
cd /path/to/parser
python -m venv venv && source venv/bin/activate
pip install -e ".[ocr,morpheme,web]"
pip install pyobjc-framework-Vision mlx-vlm

# Phase 0 PoC 실행
python -m docforge "sample/OCR/[도서] 영업비밀 전직금지 Q&A_260415.pdf" \
  --force-ocr -o test_apple_vision.md

# Flask UI
python -m flask --app docforge.web.app run --port 5000 --debug
```

OCR 우선순위 (Mac Studio, `ocr_factory.py` L69-74):
```
Darwin: apple_vision → easyocr → paddleocr
```
→ `pyobjc-framework-Vision` 설치하면 자동으로 Apple Vision 사용.

---

## 5. Phase 로드맵 요약

| 순서 | Phase | 상태 | 핵심 작업 |
|------|-------|------|----------|
| 1 | ~~Phase 0: Apple Vision PoC~~ | **완료** | EasyOCR 제거 + Apple Vision 강화 |
| 2 | ~~Phase 4: API Contract~~ | **완료** | v1 REST API + 인증 + CORS |
| 3 | ~~Phase VLM: 파이프라인 복구~~ | **완료** | VLM 엔진 주입 + 이미지 캡션 + Cloud 폴백 |
| 4 | Phase 1: Y축 라인 머저 | 미착수 | `processing/precise_line_merger.py` — SCANNED 전용 |
| 5 | Phase 2: Kiwi + 도메인 사전 | 미착수 | 사전 파일 3종 + ocr_corrector 확장 |
| 6 | Phase 3: 레이아웃 감지 | 미착수 | Surya/DocLayout-YOLO + layout_detection_enabled=True |
| 7 | Phase 5: 데이터 플라이휠 | 미착수 | `POST /feedback` + diff 누적 + 자동 사전 추가 |
| 8 | Phase 6: TTL 캐시 | 미착수 | SHA256 키 + LRU 50GB + 30일 TTL |

---

## 6. 주요 파일 참조

```
docforge/
├── adapters/
│   ├── apple_vision_engine.py    ← macOS OCR 1순위
│   ├── paddle_ocr.py             ← Linux/한국어 OCR
│   ├── vision_llm_engine.py      ← 로컬 Qwen2-VL MLX
│   ├── cloud_vlm_engine.py       ← Cloud VLM (OpenAI/Anthropic) [신규]
│   ├── morpheme_analyzer.py      ← Kiwi 형태소 분석
│   └── layout/surya_adapter.py
├── processing/
│   ├── image_vlm_captioner.py    ← VLM 이미지 캡션 생성 [신규]
│   ├── line_merger.py
│   ├── region_vlm_router.py      ← 표 VLM 재추출
│   ├── heading_hierarchy.py
│   ├── markdown_assembler.py     ← 표 중복 제거 포함
│   ├── page_classifier.py
│   └── domain_profiles/__init__.py
├── usecases/
│   ├── parse_pdf.py              ← 메인 오케스트레이터
│   ├── _parse_pdf_helpers.py     ← build_llm_engine() 폴백 체인
│   ├── page_processor.py         ← 페이지별 파이프라인 + VLM 캡션 통합
│   ├── ocr_factory.py            ← OCR 선택
│   └── chunking/                 ← 4종 전략
├── web/
│   ├── app.py                    ← Flask factory
│   ├── routes.py                 ← v1 API 엔드포인트
│   ├── v1_routes.py              ← /api/v1/ 라우트
│   ├── sse.py                    ← SSE 스트리밍
│   ├── task_state.py             ← TaskRegistry
│   └── worker.py                 ← ThreadPoolExecutor 큐
├── domain/
│   └── ports.py                  ← VisionLLMEngine Protocol (describe_image 포함)
└── infrastructure/
    └── config.py                 ← ParserConfig (vlm_provider 추가)
```

---

## 7. 개발 규칙

- **불변성**: frozen dataclass 패턴 유지, 기존 객체 직접 변경 금지
- **테스트**: 새 기능 = 테스트 필수 (AAA 패턴, 커버리지 80%+)
- **커밋**: `type(scope): description` (feat, fix, refactor 등)
- **파일**: 200-400줄 적정, 800줄 상한
- **에러**: 모든 레벨에서 처리, 조용히 삼키기 금지
- **어댑터 패턴**: `domain/ports.py`의 Protocol 준수, 새 엔진은 기존 패턴 따르기

---

## 8. 알려진 함정

| # | 함정 | 대응 |
|---|------|------|
| 1 | `RawImage`를 넘기는 곳에서 numpy ndarray가 올 수 있음 | 타입 체크 필수 |
| 2 | `apple_vision_engine.py`의 bbox가 normalized coords → pixel 변환 주의 | 기존 변환 코드 확인 |
| 3 | `TaskRegistry`가 in-memory — 서버 재시작 시 날아감 | `TaskStore`(JSON 파일)와 혼동 주의 |
| 4 | `page_processor.py`의 COVER/TOC 분기가 heading_hierarchy 스킵 | 의도적 설계 |
| 5 | `ParserConfig`가 frozen이라 런타임 변경 불가 | `dataclasses.replace()` 사용 |
| 6 | Cloud VLM은 `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` 환경변수 필요 | `.env`에 설정, Docker 환경변수로 전달 |
| 7 | `vlm_provider="local"`은 macOS Apple Silicon 전용 | Linux Docker에서는 `"auto"` 또는 `"openai"` 사용 |
| 8 | `image_extraction_enabled=True`가 기본값으로 변경됨 | 이미지 bytes 추출 + VLM 캡션이 기본 활성 |

---

## 9. 점수 목표

| 단계 | 점수 | 상태 |
|------|------|------|
| ~~EasyOCR 시절~~ | 30 / 100 | 폐기 |
| ~~Phase 0 (Apple Vision)~~ | 92 / 100 | 완료 |
| **현재 (+ VLM 파이프라인)** | **93 / 100** | 완료 |
| Phase 1+2+3 (레이아웃+사전+VLM고도화) | 96 / 100 | 미착수 |
| + Phase 5 (6개월 누적 피드백) | 98 / 100 | 미착수 |

---

## 10. ai-platform 통합 (Phase 4 참고)

ai-platform 저장소: `/path/to/ai-platform/`
```
apps/api/        FastAPI (RAG + 기존 PDF 파서)
apps/bff/        NestJS (인증 X-API-Key)
apps/frontend/   Next.js App Router
```

ai-platform의 기존 파싱: `pipeline/parsing/pdf_parser.py`
- TEXT_ONLY → PyMuPDF
- TABLES → Docling
- IMAGE_HEAVY → VLM OCR (외부 HTTP)

DocForge 통합 시: IMAGE_HEAVY 또는 한국어 → DocForge로 라우팅.
DocForge 측 작업은 API contract 안정화 + X-Internal-Key 인증만.

---

## 11. 첫 메시지 템플릿 (새 에이전트에게 복사)

```
프로젝트: DocForge (한국어 PDF → Markdown 파싱 엔진)
환경: Mac Studio M1 Max, Python 3.13
역할: 파싱 품질 고도화

다음 문서 읽고 현재 상태 파악:
1. .pipeline/HANDOFF.md — 아키텍처 + 완료 항목 + 함정
2. .pipeline/requirement.md — 전체 로드맵 + Phase 상세

현재 상태:
- Phase 0 (Apple Vision), Phase 4 (API), VLM 파이프라인 Phase 1 완료
- VLM 폴백 체인 작동 (로컬 Qwen2-VL → OpenAI → Anthropic)
- 이미지 캡션 자동 생성 + 표 중복 제거 구현

다음 작업: Phase 1~3 (레이아웃 감지, OCR 교정 사전, VLM 고도화)
```
