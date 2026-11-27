web: gunicorn -w 2 -k uvicorn.workers.UvicornWorker main:app --bind 0.0.0.0:$PORT --timeout 60 --graceful-timeout 20
worker: python -m services.planner.ocr_worker
