# One process shares the in-memory cache among four concurrent request threads.
# More workers/replicas would each have a separate cache and HTTP Session.
bind = "0.0.0.0:8000"
workers = 1
worker_class = "gthread"
threads = 4
timeout = 30
graceful_timeout = 30
accesslog = "-"
errorlog = "-"
capture_output = True
