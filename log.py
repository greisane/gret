import time
from functools import partial

class Logger:
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
        self.flush()
        self.start_time = None
        self.defer_print = False
        self.indent = 0
        self.prefix = ""

    def flush(self):
        for log in self.logs:
            self._print_log(*log)
        self.logs.clear()

    def log(self, *args, sep=' ', category=None):
        message = sep.join(str(arg) for arg in args)
        if category:
            message = f"({str(category)}) {message}"
        if self.prefix:
            message = f"{str(self.prefix)} {message}"
        if self.indent > 0:
            message = "  " * self.indent + message
        log_entry = (time.time(), message, category)
        if self.defer_print:
            self.logs.append(log_entry)
        else:
            self._print_log(*log_entry)

    def _print_log(self, timestamp, message, category):
        if category and category not in self.categories:
            return
        if self.start_time is not None:
            message = f"{timestamp - self.start_time:6.2f}s | {message}"
        print(message)

# Global instance
logger = Logger()
log = logger.log
logd = partial(log, category='DEBUG')
