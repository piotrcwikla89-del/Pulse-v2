web: gunicorn -w 2 -k uvicorn.workers.UvicornWorker main:app --bind 0.0.0.0:$PORT --timeout 180 --graceful-timeout 30
worker: python -m services.planner.ocr_worker
