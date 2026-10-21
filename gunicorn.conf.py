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
preload_app = False
control_socket_disable = True


def post_worker_init(worker):
    # Start only after the worker has forked and loaded its Flask application.
    worker.wsgi.extensions["schedule_api"].start_background_refresh()


def worker_exit(server, worker):
    app = getattr(worker, "wsgi", None)
    if app is not None:
        app.extensions["schedule_api"].stop_background_refresh()
