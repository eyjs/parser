# Task-003: Routes Refactor + New APIs
## Scope
- Replace threading.Thread with worker.submit_task
- Multi-file upload in api_parse
- GET /api/queue/status
- POST /api/parse/<task_id>/cancel
- Result persistence (result.json + v0_original.md) on parse completion
- Version save on api_save
- GET /api/versions/<task_id>
- GET /api/diff/<task_id>
- Serve PDF files for viewer
## Dependencies: Task-001, Task-002
## Files: docforge/web/routes.py
