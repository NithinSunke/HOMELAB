# Starts the RMAN Console on http://localhost:8000
$ErrorActionPreference = "Stop"
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
