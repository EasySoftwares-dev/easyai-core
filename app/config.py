import os
from dataclasses import dataclass

@dataclass(frozen=True)
class Settings:
    supabase_url: str
    supabase_service_role_key: str
    documents_bucket: str = "easytender-documents"
    poll_seconds: float = 3.0
    visibility_timeout_seconds: int = 1200

    @classmethod
    def from_env(cls) -> "Settings":
        url = os.environ.get("SUPABASE_URL", "").strip()
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not url or not key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required")
        return cls(
            supabase_url=url,
            supabase_service_role_key=key,
            documents_bucket=os.environ.get("DOCUMENTS_BUCKET", "easytender-documents"),
            poll_seconds=float(os.environ.get("WORKER_POLL_SECONDS", "3")),
            visibility_timeout_seconds=int(os.environ.get("JOB_VISIBILITY_TIMEOUT_SECONDS", "1200")),
        )
