# Task-002: Worker Queue Module
## Scope
- New docforge/web/worker.py
- ThreadPoolExecutor with configurable MAX_WORKERS
- Move _TRACKERS and _TRACKER_LOCK from routes.py
- submit_task, cancel_task, get_queue_status, get/set/remove tracker
- Init/shutdown lifecycle
## Dependencies: Task-001
## Files: docforge/web/worker.py (new)
