"""Durable move journal and conservative, repeatable undo."""

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from photo_file_manager.engine import (
    FileResult,
    FileStamp,
    MoveItem,
    MovePlan,
    RunSummary,
    ValidationError,
    _check_destination,
    _is_link,
    check_source,
    execute_plan,
    move_file,
)


class HistoryError(RuntimeError):
    """Journal failures must stop execution before the next filesystem mutation."""


class OperationCancelled(Exception):
    pass


def content_hash(path: Path, is_cancelled=lambda: False) -> str:
    before = FileStamp.read(path)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            if is_cancelled():
                raise OperationCancelled()
            digest.update(chunk)
    if FileStamp.read(path) != before:
        raise ValidationError("해시 확인 중 파일이 변경됐습니다.")
    if is_cancelled():
        raise OperationCancelled()
    return digest.hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class UndoSummary:
    restored: int
    conflicts: int
    cancelled: bool
    remaining: int


class HistoryStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        connection = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, timeout=10)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=FULL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, finished_at TEXT,
                    source_root TEXT NOT NULL, output_root TEXT NOT NULL,
                    options TEXT NOT NULL, state TEXT NOT NULL, error TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS entries (
                    job_id TEXT NOT NULL REFERENCES jobs(id),
                    ordinal INTEGER NOT NULL, source TEXT NOT NULL, destination TEXT,
                    state TEXT NOT NULL, message TEXT NOT NULL DEFAULT '',
                    sha256 TEXT, undo_message TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(job_id, ordinal)
                );
            """)
            with connection:
                yield connection
        except (OSError, sqlite3.Error) as error:
            raise HistoryError(f"작업 이력을 저장하거나 읽을 수 없습니다: {error}") from error
        finally:
            if connection is not None:
                connection.close()

    def start(self, plan: MovePlan, options: dict) -> str:
        job_id = uuid.uuid4().hex
        with self.connect() as db:
            db.execute(
                "INSERT INTO jobs VALUES (?, ?, NULL, ?, ?, ?, 'running', '')",
                (
                    job_id,
                    now(),
                    str(plan.source_root),
                    str(plan.output_root),
                    json.dumps(options, ensure_ascii=False),
                ),
            )
            rows = [
                (job_id, index, str(item.source), str(item.destination), "pending", item.note)
                for index, item in enumerate(plan.items)
            ]
            rows += [
                (
                    job_id,
                    len(plan.items) + index,
                    str(item.source),
                    str(item.destination) if item.destination else None,
                    "skipped",
                    item.message,
                )
                for index, item in enumerate(plan.skipped)
            ]
            db.executemany(
                "INSERT INTO entries (job_id, ordinal, source, destination, state, message) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
        return job_id

    def update_entry(self, job_id: str, ordinal: int, **values) -> None:
        if not values or not set(values) <= {"state", "message", "sha256", "undo_message"}:
            raise ValueError("Invalid history fields")
        with self.connect() as db:
            cursor = db.execute(
                "UPDATE entries SET "
                + ", ".join(f"{key}=?" for key in values)
                + " WHERE job_id=? AND ordinal=?",
                (*values.values(), job_id, ordinal),
            )
            if cursor.rowcount != 1:
                raise HistoryError("작업 이력 항목을 찾을 수 없습니다.")

    def finish(self, job_id: str, state: str, error: str = "") -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE jobs SET state=?, finished_at=?, error=? WHERE id=?",
                (state, now(), error, job_id),
            )

    def jobs(self) -> list[dict]:
        if not self.path.exists():
            return []
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute("""
                SELECT jobs.*,
                    (SELECT count(*) FROM entries WHERE job_id=jobs.id AND state='moved') moved,
                    (SELECT count(*) FROM entries WHERE job_id=jobs.id AND state='skipped') skipped,
                    (SELECT count(*) FROM entries WHERE job_id=jobs.id AND state='failed') failed,
                    (SELECT count(*) FROM entries WHERE job_id=jobs.id AND state='undone') undone,
                    (SELECT count(*) FROM entries WHERE job_id=jobs.id AND sha256 IS NOT NULL
                     AND state IN ('moved', 'moving', 'undoing', 'failed')) undoable
                FROM jobs ORDER BY created_at DESC
            """)
            ]

    def job(self, job_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise HistoryError("작업 이력을 찾을 수 없습니다.")
            return dict(row)

    def entries(self, job_id: str) -> list[dict]:
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM entries WHERE job_id=? ORDER BY ordinal", (job_id,)
                )
            ]


def execute_recorded_plan(
    store: HistoryStore,
    plan: MovePlan,
    options: dict,
    *,
    on_result=lambda _: None,
    on_progress=lambda _done, _total: None,
    is_cancelled=lambda: False,
) -> tuple[str, RunSummary]:
    journal_path = store.path.resolve()
    if any(journal_path.is_relative_to(root) for root in (plan.source_root, plan.output_root)):
        raise HistoryError("작업 이력 저장 폴더를 포함하는 입력·출력 경로는 사용할 수 없습니다.")
    job_id = store.start(plan, options)
    indexes = {item.source: index for index, item in enumerate(plan.items)}

    def prepare(item: MoveItem) -> None:
        try:
            check_source(item, plan.source_root)
            digest = content_hash(item.source, is_cancelled)
            check_source(item, plan.source_root)
            store.update_entry(job_id, indexes[item.source], state="moving", sha256=digest)
        except (OSError, ValidationError) as error:
            # Do not continue moving files if a reliable undo record cannot be made.
            raise HistoryError(f"이동 전 원본 확인 실패: {error}") from error

    def record(result: FileResult) -> None:
        if result.source in indexes:
            store.update_entry(
                job_id, indexes[result.source], state=result.status, message=result.message
            )
        on_result(result)

    try:
        summary = execute_plan(
            plan,
            before_move=prepare,
            on_result=record,
            on_progress=on_progress,
            is_cancelled=is_cancelled,
        )
    except OperationCancelled:
        rows = store.entries(job_id)
        summary = RunSummary(
            sum(row["state"] == "moved" for row in rows),
            sum(row["state"] == "skipped" for row in rows),
            sum(row["state"] == "failed" for row in rows),
            True,
            sum(row["state"] == "pending" for row in rows),
        )
    except Exception as error:
        store.finish(job_id, "interrupted", str(error))
        raise
    store.finish(job_id, "cancelled" if summary.cancelled else "completed")
    return job_id, summary


def undo_job(
    store: HistoryStore,
    job_id: str,
    *,
    on_result=lambda _: None,
    on_progress=lambda _done, _total: None,
    is_cancelled=lambda: False,
) -> UndoSummary:
    job = store.job(job_id)
    source_root, output_root = Path(job["source_root"]), Path(job["output_root"])
    rows = [
        row
        for row in reversed(store.entries(job_id))
        if row["sha256"] and row["state"] in {"moved", "moving", "undoing", "failed"}
    ]
    restored = conflicts = 0
    store.finish(job_id, "undoing")
    on_progress(0, len(rows))
    try:
        for index, row in enumerate(rows):
            if is_cancelled():
                break
            source, destination = Path(row["source"]), Path(row["destination"])
            try:
                _check_destination(source, source_root)
                _check_destination(destination, output_root)
                if os.path.lexists(source):
                    # Also reconcile a crash after restoration but before journal commit.
                    if (
                        not os.path.lexists(destination)
                        and not _is_link(source)
                        and source.is_file()
                        and content_hash(source, is_cancelled) == row["sha256"]
                    ):
                        store.update_entry(
                            job_id, row["ordinal"], state="undone", undo_message="원위치 확인 완료"
                        )
                        restored += 1
                        on_result(FileResult(destination, source, "moved", "원위치 확인 완료"))
                        on_progress(index + 1, len(rows))
                        continue
                    raise ValidationError("원래 위치에 파일이 있습니다. 덮어쓰지 않았습니다.")
                if _is_link(destination) or not destination.is_file():
                    raise ValidationError("이동된 파일이 없거나 일반 파일이 아닙니다.")
                stamp = FileStamp.read(destination)
                if content_hash(destination, is_cancelled) != row["sha256"]:
                    raise ValidationError("이동 후 파일 내용이 변경됐습니다. 복원하지 않았습니다.")
                if FileStamp.read(destination) != stamp:
                    raise ValidationError("확인 중 파일이 변경됐습니다.")
                source.parent.mkdir(parents=True, exist_ok=True)
                _check_destination(source, source_root)
                _check_destination(destination, output_root)
                store.update_entry(job_id, row["ordinal"], state="undoing", undo_message="")
                if FileStamp.read(destination) != stamp:
                    raise ValidationError("복원 직전 파일이 변경됐습니다.")
                move_file(destination, source)
            except OperationCancelled:
                break
            except (OSError, ValidationError) as error:
                conflicts += 1
                store.update_entry(job_id, row["ordinal"], undo_message=str(error))
                on_result(FileResult(destination, source, "failed", str(error)))
            else:
                store.update_entry(job_id, row["ordinal"], state="undone", undo_message="복원 완료")
                restored += 1
                on_result(FileResult(destination, source, "moved", "복원 완료"))
            on_progress(index + 1, len(rows))
    except Exception as error:
        store.finish(job_id, "undo_partial", str(error))
        raise
    remaining = sum(
        row["sha256"] is not None and row["state"] in {"moved", "moving", "undoing", "failed"}
        for row in store.entries(job_id)
    )
    store.finish(job_id, "undo_partial" if remaining else "undone")
    return UndoSummary(restored, conflicts, is_cancelled(), remaining)
