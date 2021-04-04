import time
from functools import partial

class Logger:
    """Simple logger. Can be used as a mixin."""

    def start_logging(self, filepath=None, defer_print=True):
        if not self.is_logging():
            self.logs = []
            self.start_time = time.time()
            self.file = open(filepath, 'w') if filepath else None
            self.defer_print = defer_print
            self.prefix = ""
            self.indent = 0
            self.started = 0
            self.categories = []
        self.started += 1

    def end_logging(self):
        assert self.is_logging(), "start_logging must be called first"
        self.started -= 1
        if self.started > 0:
            return
        if self.defer_print:
            for log in self.logs:
                self._print_log(*log)
        del self.logs
        if self.file:
            self.file.close()
            self.file = None

    def is_logging(self):
        return hasattr(self, 'logs')

    def log(self, *args, sep=' ', category=None):
        if not self.is_logging():
            # start_logging wasn't called, so just print
            print(*args, sep=sep)
            return
        message = sep.join(str(arg) for arg in args)
        if self.prefix:
            message = f"{str(self.prefix)} {message}"
        if self.indent > 0:
            message = "  " * self.indent + message
        self.logs.append((time.time(), message, category))
        if not self.defer_print:
            self._print_log(*self.logs[-1])

    def _print_log(self, timestamp, message, category):
        if not category or category in self.categories:
            line = f"{timestamp - self.start_time:6.2f}s | {message}"
            print(line)
            if self.file:
                print(line, file=self.file)

# Singleton instance
logger = Logger()
log = logger.log
logd = partial(log, category='debug')
