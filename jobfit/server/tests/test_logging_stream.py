import logging
import queue

from jobfit.server import logging_stream


def test_attach_forwards_log_lines_to_the_queue_and_detach_stops_it():
    line_queue: queue.Queue = queue.Queue()
    logger = logging.getLogger("jobfit.tests.logging_stream")
    logger.setLevel(logging.INFO)

    attached = logging_stream.attach(line_queue, ["jobfit.tests.logging_stream"])
    logger.info("hello %s", "world")

    line = line_queue.get(timeout=1)
    assert "hello world" in line

    logging_stream.detach(attached)
    logger.info("should not appear")
    assert line_queue.empty()
