"""Offline Chinese speech on a background worker, using installed Windows voices."""
import json
import os
from pathlib import Path
import queue
import subprocess
import threading


def worker_command():
    system = Path(os.environ.get('SystemRoot', r'C:\Windows'))
    return [str(system/'System32/WindowsPowerShell/v1.0/powershell.exe'),
            '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
            '-File', str(Path(__file__).with_name('speech_worker.ps1'))]


class SpeechAnnouncer:
    def __init__(self, callback):
        self.callback = callback
        self.requests = queue.Queue(maxsize=20)
        self.process = None
        self.closed = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def say(self, text, rate=0):
        if self.closed:
            return
        try:
            self.requests.put_nowait(dict(text=text, rate=rate))
        except queue.Full:
            self.callback('speech_error', '播报队列已满，本次语音跳过；考勤记录已保存')

    def _run(self):
        while not self.closed:
            request = self.requests.get()
            if request is None or self.closed:
                self._terminate()
                return
            try:
                if self.process is None or self.process.poll() is not None:
                    self.process = subprocess.Popen(worker_command(), stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding='utf-8',
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    self._read_result()
                if self.closed:
                    self._terminate()
                    return
                self.process.stdin.write(json.dumps(request, ensure_ascii=True) + '\n')
                self.process.stdin.flush()
                self._read_result()
            except (OSError, ValueError, RuntimeError) as exc:
                if not self.closed:
                    self.callback('speech_error', str(exc))
                self._terminate()

    def _read_result(self):
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError('Windows 语音服务已退出')
        result = json.loads(line)
        kind, message = result['kind'], result['message']
        if kind == 'speech_error':
            raise RuntimeError(message)
        if kind == 'speech_ready':
            self.callback(kind, message)

    def clear(self):
        while True:
            try:
                self.requests.get_nowait()
            except queue.Empty:
                return

    def _terminate(self):
        process, self.process = self.process, None
        if process:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
            for stream in (process.stdin, process.stdout):
                if stream:
                    stream.close()

    def close(self):
        self.closed = True
        self.clear()
        self.requests.put_nowait(None)
        # Killing a busy worker is bounded; never wait for queued announcements.
        process = self.process
        if process and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
