import asyncio
import hashlib
import logging
import tempfile
import time
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from typing import Any

from docling.document_converter import DocumentConverter
from supabase import Client, create_client

from .config import Settings

log = logging.getLogger("easyai.worker")
PROCESSOR = "docling"
PROCESSOR_VERSION = version("docling")


def _classify_error(exc: Exception) -> str:
    msg = str(exc).lower()
    if "storage" in msg or "download" in msg:
        return "STORAGE_READ_ERROR"
    if "conversion" in msg or "docling" in msg:
        return "DOCLING_CONVERSION_ERROR"
    if "timeout" in msg or "timed out" in msg:
        return "TIMEOUT"
    return "UNKNOWN_ERROR"


def _extract_page_count(structured: dict[str, Any]) -> int | None:
    pages = structured.get("pages")
    if isinstance(pages, list):
        return len(pages)
    if isinstance(pages, dict):
        return len(pages)
    return None


class EasyAIWorker:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.supabase: Client = create_client(settings.supabase_url, settings.supabase_service_role_key)
        self.converter = DocumentConverter()
        self._stopping = False

    def stop(self) -> None:
        self._stopping = True

    def _rpc(self, name: str, params: dict[str, Any]) -> Any:
        return self.supabase.rpc(name, params).execute().data

    def _update_progress(self, job_id: str, stage: str, current: int | None = None,
                         total: int | None = None, metrics: dict[str, Any] | None = None) -> None:
        self._rpc("easyai_update_job_progress", {
            "p_job_id": job_id,
            "p_stage": stage,
            "p_current": current,
            "p_total": total,
            "p_processor": PROCESSOR,
            "p_processor_version": PROCESSOR_VERSION,
            "p_model": None,
            "p_metrics": metrics,
        })

    def _complete(self, job_id: str, result: dict[str, Any], metrics: dict[str, Any]) -> None:
        self._rpc("easyai_complete_job", {
            "p_job_id": job_id,
            "p_result": result,
            "p_metrics": metrics,
        })

    def _fail(self, job_id: str, exc: Exception, retry: bool = True) -> None:
        self._rpc("easyai_fail_job", {
            "p_job_id": job_id,
            "p_error_code": _classify_error(exc),
            "p_error_detail": str(exc)[:4000],
            "p_retry": retry,
            "p_retry_delay_seconds": 30,
        })

    def _claim(self) -> dict[str, Any] | None:
        data = self._rpc("easyai_claim_job", {
            "p_visibility_timeout": self.settings.visibility_timeout_seconds,
        })
        if not data:
            return None
        if isinstance(data, dict) and data.get("ok") is False:
            log.warning("Queue message skipped: %s", data)
            return None
        return data

    def _load_document(self, document_id: str) -> dict[str, Any]:
        resp = (self.supabase.table("easytender_opportunity_documents")
                .select("id,opportunity_id,document_name,storage_path,file_extension,mime_type,file_size_bytes,source")
                .eq("id", document_id).single().execute())
        doc = resp.data
        if not doc:
            raise RuntimeError("Document not found")
        if doc.get("source") != "USER_UPLOAD" or not doc.get("storage_path"):
            raise RuntimeError("Document is not processable")
        return doc

    def _download(self, storage_path: str) -> bytes:
        data = self.supabase.storage.from_(self.settings.documents_bucket).download(storage_path)
        if not data:
            raise RuntimeError("Storage download returned empty content")
        return data

    def _save_extraction(self, *, job_id: str, document_id: str, raw_bytes: bytes,
                         markdown: str, plain_text: str, structured: dict[str, Any],
                         duration_seconds: float) -> str:
        pages = structured.get("pages") if isinstance(structured.get("pages"), (list, dict)) else []
        tables = structured.get("tables") if isinstance(structured.get("tables"), list) else []
        payload = {
            "document_id": document_id,
            "job_id": job_id,
            "status": "READY",
            "processor": PROCESSOR,
            "processor_version": PROCESSOR_VERSION,
            "content_hash": hashlib.sha256(raw_bytes).hexdigest(),
            "page_count": _extract_page_count(structured),
            "markdown_content": markdown,
            "plain_text": plain_text,
            "structured_content": structured,
            "pages": pages,
            "tables": tables,
            "metadata": {"duration_seconds": round(duration_seconds, 3)},
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        result = self.supabase.table("easyai_document_extractions").insert(payload).execute()
        row = result.data[0] if result.data else None
        if not row:
            raise RuntimeError("Could not save extraction")
        return row["id"]

    def process_one(self) -> bool:
        claimed = self._claim()
        if not claimed:
            return False
        job = claimed["job"]
        job_id = job["id"]
        document_id = job["document_id"]
        started = time.perf_counter()
        try:
            self._update_progress(job_id, "READING_DOCUMENT")
            doc = self._load_document(document_id)
            self._update_progress(job_id, "DOWNLOADING_DOCUMENT")
            raw = self._download(doc["storage_path"])
            suffix = "." + (doc.get("file_extension") or "bin").lstrip(".")
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(raw)
                tmp_path = Path(tmp.name)
            try:
                self._update_progress(job_id, "EXTRACTING_WITH_DOCLING")
                conversion = self.converter.convert(str(tmp_path))
                ddoc = conversion.document
                markdown = ddoc.export_to_markdown()
                plain_text = ddoc.export_to_text()
                structured = ddoc.export_to_dict()
                duration = time.perf_counter() - started
                self._update_progress(job_id, "SAVING_EXTRACTION",
                                      metrics={"extraction_seconds": round(duration, 3)})
                extraction_id = self._save_extraction(
                    job_id=job_id, document_id=document_id, raw_bytes=raw,
                    markdown=markdown, plain_text=plain_text, structured=structured,
                    duration_seconds=duration,
                )
                self._complete(job_id, {
                    "extraction_id": extraction_id,
                    "document_id": document_id,
                    "processor": PROCESSOR,
                    "processor_version": PROCESSOR_VERSION,
                }, {
                    "total_seconds": round(time.perf_counter() - started, 3),
                    "file_size_bytes": len(raw),
                })
                log.info("Job %s READY", job_id)
            finally:
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    log.exception("Could not remove temp file")
        except Exception as exc:
            log.exception("Job %s failed", job_id)
            self._fail(job_id, exc, retry=True)
        return True

    async def run_forever(self) -> None:
        log.info("EasyAI worker started. Docling=%s bucket=%s", PROCESSOR_VERSION, self.settings.documents_bucket)
        while not self._stopping:
            try:
                processed = await asyncio.to_thread(self.process_one)
                if not processed:
                    await asyncio.sleep(self.settings.poll_seconds)
            except Exception:
                log.exception("Worker loop failure")
                await asyncio.sleep(self.settings.poll_seconds)
