"""Qt worker threads for planning, journalled execution and undo."""

from dataclasses import asdict
from threading import Event

from PySide6.QtCore import QThread, Signal

from photo_file_manager.engine import build_plan
from photo_file_manager.history import execute_recorded_plan, undo_job


class TaskWorker(QThread):
    result_ready = Signal(object)
    progress = Signal(int, int)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.cancel_event = Event()

    def run(self):
        try:
            self.completed.emit(self.operation(self))
        except Exception as error:
            self.failed.emit(str(error))


def preview_worker(source, output, options, parent):
    def operation(worker):
        plan = build_plan(
            source, output, classification=options, is_cancelled=worker.cancel_event.is_set
        )
        return None if worker.cancel_event.is_set() else plan

    return TaskWorker(operation, parent)


def move_worker(store, plan, options, parent):
    def operation(worker):
        return execute_recorded_plan(
            store,
            plan,
            asdict(options),
            on_result=worker.result_ready.emit,
            on_progress=worker.progress.emit,
            is_cancelled=worker.cancel_event.is_set,
        )

    return TaskWorker(operation, parent)


def undo_worker(store, job_id, parent):
    def operation(worker):
        return undo_job(
            store,
            job_id,
            on_result=worker.result_ready.emit,
            on_progress=worker.progress.emit,
            is_cancelled=worker.cancel_event.is_set,
        )

    return TaskWorker(operation, parent)
