# EasyAI Core v0.1

Worker desacoplado para procesamiento pesado de documentos de EasyTender.

## Flujo
1. Lee trabajos desde `easyai_document_jobs`.
2. Descarga el archivo desde `easytender-documents`.
3. Convierte con Docling.
4. Guarda Markdown, texto, estructura, tablas, páginas, hash y métricas.
5. Actualiza progreso/reintentos en `easyai_jobs`.
6. Archiva el mensaje al completar.

## Backend Supabase ya creado
- `public.easyai_jobs`
- `public.easyai_document_extractions`
- `public.easyai_document_processing_status`
- cola `easyai_document_jobs`
- RPCs `easyai_claim_job`, `easyai_update_job_progress`, `easyai_complete_job`, `easyai_fail_job`
- Edge Function `easyai-enqueue-document`

## Variables
Configurar `.env` desde `.env.example`. La service role nunca debe ir al frontend.

## Ejecutar
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
set -a && source .env && set +a
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Docker
```bash
docker build -t easyai-core:0.1 .
docker run --rm -p 8000:8000 --env-file .env easyai-core:0.1
```
