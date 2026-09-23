"""In-memory log fan-out: attach a queue-backed handler to the jobfit loggers
for the duration of a background run, so an SSE endpoint can stream it live."""

import logging
import queue


class QueueLogHandler(logging.Handler):
    def __init__(self, line_queue: "queue.Queue[str | None]"):
        super().__init__()
        self.line_queue = line_queue

    def emit(self, record: logging.LogRecord) -> None:
        self.line_queue.put(self.format(record))


def attach(
    line_queue: "queue.Queue[str | None]", logger_names: list[str]
) -> list[tuple[logging.Logger, QueueLogHandler]]:
    handler = QueueLogHandler(line_queue)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    attached = []
    for name in logger_names:
        logger = logging.getLogger(name)
        logger.addHandler(handler)
        attached.append((logger, handler))
    return attached


def detach(attached: list[tuple[logging.Logger, QueueLogHandler]]) -> None:
    for logger, handler in attached:
        logger.removeHandler(handler)
