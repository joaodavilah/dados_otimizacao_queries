import asyncio
import os
import tempfile
import time
from collections import defaultdict, deque
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool
from app.services.pbix_extractor import extract_pbix

app = FastAPI(title='Análise PBIX', docs_url=None, redoc_url=None)
origins = [x.strip() for x in os.getenv('PBIX_ALLOWED_ORIGINS', '').split(',') if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=['POST', 'GET'], allow_headers=['Authorization', 'Content-Type'])
MAX_BYTES = int(os.getenv('PBIX_MAX_MB', '50')) * 1024 * 1024
busy = asyncio.Lock()
usage = defaultdict(deque)

@app.get('/api/health')
def health():
    return {'status': 'ok'}

async def authenticate(authorization):
    url, key = os.getenv('SUPABASE_URL', '').rstrip('/'), os.getenv('SUPABASE_PUBLISHABLE_KEY', '')
    if not url or not key:
        raise HTTPException(503, 'Configure a autenticação Supabase na API PBIX.')
    if not authorization or not authorization.startswith('Bearer ') or len(authorization) > 16000:
        raise HTTPException(401, 'Entre na conta para analisar o arquivo.')
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(url + '/auth/v1/user', headers={'apikey': key, 'Authorization': authorization})
        if response.status_code != 200 or not response.json().get('id'):
            raise HTTPException(401, 'Sessão inválida. Entre novamente.')
        return response.json()['id']
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, 'Não foi possível verificar a sessão.')

@app.post('/api/pbix/extract')
async def extract(file: UploadFile = File(...), authorization: str | None = Header(default=None)):
    try:
        user = await authenticate(authorization)
        if not file.filename or not file.filename.lower().endswith('.pbix'):
            raise HTTPException(400, 'Envie um arquivo .pbix.')
        now = time.monotonic()
        for owner in list(usage):
            while usage[owner] and usage[owner][0] < now - 3600:
                usage[owner].popleft()
            if not usage[owner]:
                del usage[owner]
        if len(usage[user]) >= 10:
            raise HTTPException(429, 'Limite de 10 tentativas por hora nesta instância. Tente novamente depois.')
        if busy.locked():
            raise HTTPException(429, 'A API está analisando outro arquivo. Tente novamente em instantes.')
        async with busy:
            usage[user].append(now)
            with tempfile.TemporaryDirectory(prefix='pbix-') as folder:
                path = Path(folder) / 'upload.pbix'
                size = 0
                with path.open('wb') as output:
                    while chunk := await file.read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_BYTES:
                            raise HTTPException(413, f'Use um PBIX de até {MAX_BYTES // (1024 * 1024)} MB.')
                        output.write(chunk)
                if not size:
                    raise HTTPException(400, 'O arquivo está vazio.')
                try:
                    return await run_in_threadpool(extract_pbix, path, Path(file.filename).name)
                except ValueError as error:
                    raise HTTPException(422, str(error))
                except Exception:
                    raise HTTPException(422, 'Não foi possível analisar este PBIX. Confira o formato e tente um arquivo menor.')
    finally:
        await file.close()
