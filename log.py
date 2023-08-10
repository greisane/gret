from functools import partial
import io
import time

class Logger:
    """Simple logger with the ability to defer printing until the logging session ends."""

    categories = set()
    logs = []
    start_time = None
    defer_print = False
    indent = 0
    prefix = ""

    def start_logging(self, timestamps=True):
        self.start_time = time.time() if timestamps else None
        self.defer_print = True
        self.indent = 0
        self.prefix = ""

    def end_logging(self):
        with io.StringIO() as file:
            self.flush(file=file)
            string = file.getvalue()
        self.start_time = None
        self.defer_print = False
        self.indent = 0
        self.prefix = ""
        return string

    def flush(self, file=None):
        for log in self.logs:
            self._print_log(*log, file=file)
        self.logs.clear()

    def log(self, *args, sep=' ', category=None, max_len=0):
        if category and category not in self.categories:
            return
        message = sep.join(str(arg) for arg in args)
        if category:
            message = f"({str(category)}) {message}"
        if self.prefix:
            message = f"{str(self.prefix)} {message}"
        if self.indent > 0:
            message = "  " * self.indent + message
        if max_len > 0 and len(message) > max_len:
            message = message[:max_len] + "..."
        log_entry = (time.time(), message)
        if self.defer_print:
            self.logs.append(log_entry)
        else:
            self._print_log(*log_entry)

    def _print_log(self, timestamp, message, file=None):
        if self.start_time is not None:
            message = f"{timestamp - self.start_time:6.2f}s | {message}"
        print(message)
        if file is not None:
            print(message, file=file)

    @property
    def time_elapsed(self):
        if self.start_time is None:
            return 0.0
        return time.time() - self.start_time

# Global instance
logger = Logger()
log = logger.log
logd = partial(log, category='DEBUG')
