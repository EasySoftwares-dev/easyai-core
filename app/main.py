import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from .config import Settings
from .worker import EasyAIWorker, PROCESSOR_VERSION

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
settings = Settings.from_env()
worker = EasyAIWorker(settings)
worker_task: asyncio.Task | None = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global worker_task
    worker_task = asyncio.create_task(worker.run_forever())
    yield
    worker.stop()
    if worker_task:
        await worker_task

app = FastAPI(title="EasyAI Core", version="0.1.0", lifespan=lifespan)

@app.get("/health")
async def health():
    return {
        "ok": True,
        "service": "easyai-core",
        "version": "0.1.0",
        "processor": "docling",
        "processor_version": PROCESSOR_VERSION,
    }
