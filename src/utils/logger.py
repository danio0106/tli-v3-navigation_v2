import logging
import os
import sys
import io
import threading
from datetime import datetime


class BotLogger:
    _instance = None
    _callbacks = []
    _callbacks_lock = threading.RLock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        self.logger = logging.getLogger("TLBot")
        self.logger.setLevel(logging.DEBUG)

        os.makedirs("logs", exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._log_filepath = os.path.abspath(f"logs/bot_{timestamp}.log")

        self._file_handler = logging.FileHandler(self._log_filepath, encoding="utf-8")
        self._file_handler.setLevel(logging.DEBUG)

        ch = logging.StreamHandler(stream=io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace'))
        ch.setLevel(logging.INFO)

        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)-7s] %(message)s",
            datefmt="%H:%M:%S"
        )
        self._file_handler.setFormatter(fmt)
        ch.setFormatter(fmt)

        self.logger.addHandler(self._file_handler)
        self.logger.addHandler(ch)

        self._flush_timer = None
        self._start_periodic_flush()

    @property
    def log_filepath(self) -> str:
        return self._log_filepath

    def _start_periodic_flush(self):
        self._flush_timer = threading.Timer(10.0, self._periodic_flush)
        self._flush_timer.daemon = True
        self._flush_timer.start()

    def _periodic_flush(self):
        self.flush()
        self._start_periodic_flush()

    def flush(self):
        try:
            self._file_handler.flush()
        except Exception:
            pass

    def add_callback(self, callback):
        with self._callbacks_lock:
            self._callbacks.append(callback)

    def remove_callback(self, callback):
        with self._callbacks_lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

    def _notify(self, level, message):
        with self._callbacks_lock:
            callbacks = list(self._callbacks)
        for cb in callbacks:
            try:
                cb(level, message)
            except Exception:
                pass

    def debug(self, msg):
        self.logger.debug(msg)
        # DEBUG lines are often very high-frequency (scanner/navigation loops).
        # Keep them in file/terminal logs, but do not push into GUI callbacks.

    def info(self, msg):
        self.logger.info(msg)
        self._notify("INFO", msg)

    def warning(self, msg):
        self.logger.warning(msg)
        self._notify("WARNING", msg)

    def error(self, msg):
        self.logger.error(msg)
        self._notify("ERROR", msg)

    def critical(self, msg):
        self.logger.critical(msg)
        self._notify("CRITICAL", msg)


log = BotLogger()
