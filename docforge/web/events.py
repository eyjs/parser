"""SSE event name constants — single source of truth.

Extracted from ``sse.py`` to break the circular import between
``sse`` and ``task_state``.  Both modules import from here.
"""

EVT_STRATEGY_REPORT = "strategy_report"
EVT_PROFILING = "profiling"
EVT_NOISE_LEARNING = "noise_learning"
EVT_PAGE = "page_progress"
EVT_TABLE_MERGING = "table_merging"
EVT_ASSEMBLING = "assembling"
EVT_DONE = "done"
EVT_ERROR = "error"
EVT_HEARTBEAT = "heartbeat"
EVT_PAGE_RESULT = "page_result"
