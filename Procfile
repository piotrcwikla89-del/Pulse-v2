web: gunicorn -w 3 -k uvicorn.workers.UvicornWorker main:app --bind 0.0.0.0:$PORT --timeout 90 --graceful-timeout 20 --max-requests 80 --max-requests-jitter 20
