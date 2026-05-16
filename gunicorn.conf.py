import os


bind = f"0.0.0.0:{os.getenv('PORT', '10000')}"
workers = 1
threads = int(os.getenv("WEB_CONCURRENCY_THREADS", "100"))
accesslog = "-"
errorlog = "-"
preload_app = False
