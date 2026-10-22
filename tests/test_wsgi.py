"""Run real Gunicorn with an independently controlled local schedule source."""

from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from threading import Event, Lock, Thread
import time
import unittest
from urllib.request import Request, urlopen

from src.clock import today_in_ufa

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(os.name == "nt", "Gunicorn requires Linux, WSL or Docker")
class WsgiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.downloads = 0
        cls.mode = "ok"
        cls.paths = []
        cls.lock = Lock()
        cls.blocked = Event()
        cls.release = Event()
        cls.html = (ROOT / "tests/fixtures/schedule.html").read_bytes().replace(
            b"22.10.2026", today_in_ufa().strftime("%d.%m.%Y").encode())

        class SourceHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                with cls.lock:
                    cls.downloads += 1
                    cls.paths.append(self.path)
                    mode = cls.mode
                if mode == "block":
                    cls.blocked.set()
                    cls.release.wait(4)
                status = 503 if mode == "error" else 200
                body = b"Service unavailable" if status == 503 else cls.html
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *_):
                pass

        cls.source = ThreadingHTTPServer(("127.0.0.1", 0), SourceHandler)
        cls.source_thread = Thread(target=cls.source.serve_forever, daemon=True)
        cls.source_thread.start()
        cls.addClassCleanup(cls.source.server_close)
        cls.addClassCleanup(cls.source.shutdown)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        cls.base = f"http://127.0.0.1:{port}"
        env = dict(os.environ, URL=f"http://127.0.0.1:{cls.source.server_port}/custom.htm",
                   TIME_INTERVAL_MINUTES="0.002", MAX_CACHE_AGE_HOURS="0.0005",
                   HOME="/proc/spk-alice-unwritable-home")
        env.pop("GUNICORN_CMD_ARGS", None)
        env.pop("XDG_RUNTIME_DIR", None)
        cls.process = subprocess.Popen(
            [sys.executable, "-m", "gunicorn", "--config", "gunicorn.conf.py",
             "--bind", f"127.0.0.1:{port}", "wsgi:app"],
            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        cls.addClassCleanup(cls.stop_gunicorn)
        cls.wait_for(lambda: cls.downloads > 0, 10)

    @classmethod
    def wait_for(cls, condition, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cls.process.poll() is not None:
                raise RuntimeError(cls.process.communicate()[0])
            if condition():
                return
            time.sleep(0.02)
        raise AssertionError("Timed out waiting for test condition")

    @classmethod
    def stop_gunicorn(cls):
        cls.release.set()
        if cls.process.poll() is None:
            cls.process.terminate()
        try:
            output, _ = cls.process.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            cls.process.kill()
            output, _ = cls.process.communicate()
            raise AssertionError("Gunicorn failed to stop: " + output[-2000:])
        if "Exception in worker process" in output:
            raise AssertionError(output[-3000:])
        if "Control server error" in output or "Failed to start control socket" in output:
            raise AssertionError("Gunicorn tried to create a control socket: " + output[-3000:])

    def post(self, text="ТМ2-4", new=False):
        body = {"version": "1.0", "session": {"new": new},
                "request": {"original_utterance": text}}
        req = Request(self.base + "/post", data=json.dumps(body).encode(),
                      headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(req, timeout=2) as response:
            self.assertEqual(response.status, 200)
            self.assertIn("application/json", response.headers["Content-Type"])
            return json.load(response)

    def test_full_background_lifecycle(self):
        # The initial source request happened without a webhook request.
        self.assertGreater(self.downloads, 0)
        self.assertEqual(set(self.paths), {"/custom.htm"})
        self.wait_for(lambda: self.downloads >= 2, 3)
        # Warm-up completion may follow the source's arrival counter by a few milliseconds.
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if "Сегодня у группы" in self.post()["response"]["text"]:
                break
            time.sleep(0.02)
        else:
            self.fail("Background worker did not publish the initial schedule")

        with ThreadPoolExecutor(max_workers=8) as executor:
            answers = list(executor.map(lambda _: self.post(text="тэ эм два четыре"), range(8)))
        for answer in answers:
            self.assertIn("Подгруппа 1", answer["response"]["text"])
            self.assertNotRegex(answer["response"]["tts"], r"\d")
        with urlopen(self.base + "/health", timeout=1) as response:
            self.assertEqual(json.load(response), {"status": "ok"})

        # A source request remains blocked while a real webhook still completes.
        with self.lock:
            type(self).mode = "block"
        self.assertTrue(self.blocked.wait(3))
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                answer = executor.submit(self.post).result(timeout=1)
                self.assertIn("Сегодня у группы", answer["response"]["text"])
        finally:
            with self.lock:
                type(self).mode = "error"
            self.release.set()

        # Once the allowed age (1.8 s) is reached, repeated 503s cannot prolong it.
        deadline = time.monotonic() + 5
        expired = None
        while time.monotonic() < deadline:
            answer = self.post()["response"]
            if "устарело" in answer["text"]:
                expired = answer
                break
            time.sleep(0.03)
        self.assertIsNotNone(expired)
        self.assertNotRegex(expired["tts"], r"\d")
        self.assertIn("Здравствуйте", self.post(new=True)["response"]["text"])

        # Recovery is also driven by the timer, not by a synchronous user download.
        with self.lock:
            type(self).mode = "ok"
        before = self.downloads
        self.wait_for(lambda: self.downloads >= before + 2, 3)
        self.assertIn("Сегодня у группы", self.post()["response"]["text"])


if __name__ == "__main__":
    unittest.main()
