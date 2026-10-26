from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.core.deps import Auth, read_access, write_access
from app.jobs import queue
from app.models import ImportBatch, ImportRowIssue
from app.services import importing

router = APIRouter(prefix="/api/imports", tags=["imports"])


def _file_from_ref(ref: str) -> Path:
    uploads = get_settings().uploads_dir.resolve()
    p = (uploads / Path(ref).name).resolve()  # strip any directory component
    if uploads not in p.parents or not p.is_file():
        raise HTTPException(404, "Uploaded file not found; upload it again")
    return p


@router.post("/upload")
async def upload(kind: Literal["contacts", "history", "suppressions"] = Form("contacts"), sheet: str | None = Form(None),
                 file: UploadFile = File(...), db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    content = await file.read(importing.MAX_UPLOAD_BYTES + 1)
    try:
        path, sha = importing.save_upload(content, file.filename or "upload")
        prev = importing.preview(path, kind, sheet)
    except importing.ImportError_ as e:
        raise HTTPException(400, str(e))
    previous = db.execute(select(ImportBatch).where(ImportBatch.file_sha256 == sha, ImportBatch.kind == kind)).scalars().all()
    return {
        "file_ref": path.name, "sha256": sha, "filename": file.filename, "kind": kind, **prev,
        "previously_imported": [{"id": b.id, "status": b.status, "at": b.created_at.isoformat()} for b in previous],
    }


class StartBody(BaseModel):
    file_ref: str
    kind: Literal["contacts", "history", "suppressions"] = "contacts"
    mapping: dict[str, str | None]
    extra_columns: list[str] = Field(default_factory=list)
    sheet: str | None = None
    provenance_note: str = Field("", max_length=2000)
    history_attestation: Literal["unknown", "attested_unsent"] = "unknown"
    filename: str | None = None


@router.post("/start")
def start(body: StartBody, db: Session = Depends(get_db), auth: Auth = Depends(write_access)):
    path = _file_from_ref(body.file_ref)
    sha = body.file_ref.split("-", 1)[0]
    import hashlib

    full_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        batch = importing.create_batch(
            db, path=path, sha256=full_sha, filename=body.filename or path.name, kind=body.kind, mapping=body.mapping,
            options={"extra_columns": body.extra_columns}, sheet=body.sheet, provenance_note=body.provenance_note,
            history_attestation=body.history_attestation, created_by=auth.label,
        )
    except importing.ImportError_ as e:
        raise HTTPException(400, str(e))
    job = queue.enqueue(db, "import_batch", {"batch_id": batch.id}, title=f"Import {body.kind}: {batch.filename}",
                        idempotency_key=f"import_batch:{batch.id}", created_by=auth.label, total=batch.total_rows)
    batch.job_id = job.id
    batch.status = "queued"
    return {"batch_id": batch.id, "job_id": str(job.id), "total_rows": batch.total_rows}


def _ser(b: ImportBatch) -> dict:
    return {
        "id": b.id, "kind": b.kind, "filename": b.filename, "status": b.status, "total_rows": b.total_rows,
        "rows_processed": b.rows_processed, "inserted": b.inserted, "updated": b.updated, "duplicate_in_file": b.duplicate_in_file,
        "duplicate_existing": b.duplicate_existing, "invalid": b.invalid, "history_attestation": b.history_attestation,
        "provenance_note": b.provenance_note, "created_by": b.created_by, "created_at": b.created_at.isoformat(),
        "finished_at": b.finished_at.isoformat() if b.finished_at else None, "job_id": str(b.job_id) if b.job_id else None,
    }


@router.get("")
def list_batches(db: Session = Depends(get_db), _: Auth = Depends(read_access)):
    rows = db.execute(select(ImportBatch).order_by(ImportBatch.id.desc()).limit(100)).scalars().all()
    return [_ser(b) for b in rows]


@router.get("/{batch_id}")
def get_batch(batch_id: int, db: Session = Depends(get_db), _: Auth = Depends(read_access)):
    b = db.get(ImportBatch, batch_id)
    if b is None:
        raise HTTPException(404, "Import not found")
    issues = db.execute(select(ImportRowIssue).where(ImportRowIssue.batch_id == batch_id).order_by(ImportRowIssue.id).limit(200)).scalars().all()
    return {**_ser(b), "mapping": b.mapping, "issues": [{"row": i.source_row, "issue": i.issue, "raw": i.raw} for i in issues]}
