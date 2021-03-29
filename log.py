import time

class Logger:
    """Simple logger for operators that take a long time to complete. Can be used as a mixin."""

    def start_logging(self, filepath=None, defer_print=True):
        if not self.is_logging():
            self.logs = []
            self.log_start_time = time.time()
            self.log_file = open(filepath, 'w') if filepath else None
            self.log_defer_print = defer_print
            self.log_prefix = ""
            self.log_indent = 0
            self.log_started = 0
        self.log_started += 1

    def end_logging(self):
        assert self.is_logging(), "start_logging must be called first"
        self.log_started -= 1
        if self.log_started > 0:
            return
        if self.log_defer_print:
            for log in self.logs:
                self._print_log(*log)
        del self.logs
        if self.log_file:
            self.log_file.close()
            self.log_file = None

    def is_logging(self):
        return hasattr(self, 'logs')

    def log(self, *args, sep=' '):
        if not self.is_logging():
            # start_logging wasn't called, so just print
            print(*args, sep=sep)
            return
        message = sep.join(str(arg) for arg in args)
        if self.log_prefix:
            message = f"{str(self.log_prefix)} {message}"
        if self.log_indent > 0:
            message = "  " * self.log_indent + message
        self.logs.append((time.time(), message))
        if not self.log_defer_print:
            self._print_log(*self.logs[-1])

    def _print_log(self, timestamp, message):
        line = f"{timestamp - self.log_start_time:6.2f}s | {message}"
        print(line)
        if self.log_file:
            print(line, file=self.log_file)

# Singleton instance
logger = Logger()
log = logger.log
