"""HTTP entry point for the local AxiorHub Vocal bridge."""
from __future__ import annotations

import hmac
import os
from pathlib import Path
import threading

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from bridge import BridgeError, peer_is_local, transcribe_stream


app = FastAPI(title="AxiorHub Vocal Bridge", version="3.0.0",
              docs_url=None, redoc_url=None, openapi_url=None)
processing=threading.Lock()


def _token() -> str:
    path = Path(os.environ.get("BRIDGE_TOKEN_FILE", "/run/secrets/bridge-token"))
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return value if len(value) >= 32 else ""


def _authorize(request: Request, authorization: str) -> None:
    if not peer_is_local(request.client.host if request.client else ""):
        raise HTTPException(status_code=403, detail="local_network_only")
    expected = _token()
    supplied = authorization.removeprefix("Bearer ").strip()
    if not authorization.startswith('Bearer ') or not expected or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="invalid_local_token")


@app.get("/health")
def health(request: Request):
    if not peer_is_local(request.client.host if request.client else ""):
        raise HTTPException(status_code=403, detail="local_network_only")
    return {"status": "ok", "service": "axiorhub-vocal-bridge",
            "model": "large-v3-turbo", "language": "french",
            "compute_type": "int8", "device": "cpu",
            "vocal_tested": False, "device_validation": "administrator_required"}


@app.post("/v1/audio/transcriptions")
def audio_transcription(
    request: Request,
    file: UploadFile = File(...),
    model: str = Form("large-v3-turbo"),
    language: str = Form("french"),
    response_format: str = Form("json"),
    temperature: float = Form(0),
    prompt: str = Form(""),
    hotwords: str = Form(""),
    authorization: str = Header(""),
):
    _authorize(request, authorization)
    if response_format not in {"json", "text", "txt"}:
        raise HTTPException(status_code=400, detail="response_format_not_allowed")
    if not processing.acquire(blocking=False):
        file.file.close()
        raise HTTPException(status_code=429,detail='transcription_already_running',headers={'Retry-After':'15'})
    try:
        result = transcribe_stream(file.file, model=model, language=language,
                                  temperature=temperature,
                                  hotwords=(hotwords or prompt),filename=file.filename or 'audio.wav')
        return JSONResponse(result)
    except BridgeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None
    except Exception:
        raise HTTPException(status_code=502, detail="vocal_transcription_failed") from None
    finally:
        file.file.close()
        processing.release()


class Guard:
    """Authorize and cap request bytes before multipart spooling."""
    def __init__(self,app):self.inner=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http':return await self.inner(scope,receive,send)
        request=Request(scope)
        try:
            if scope.get('path')!='/health':_authorize(request,request.headers.get('authorization',''))
            elif not peer_is_local(request.client.host if request.client else ''):raise HTTPException(403,'local_network_only')
            limit=int(os.environ.get('MAX_AUDIO_BYTES','536870912'))+65536
            if int(request.headers.get('content-length','0'))>limit:raise HTTPException(413,'audio_too_large')
        except (ValueError,HTTPException) as error:
            code=error.status_code if isinstance(error,HTTPException) else 400
            return await JSONResponse({'error':'request_refused'},status_code=code)(scope,receive,send)
        received=0
        async def limited_receive():
            nonlocal received
            message=await receive();received+=len(message.get('body',b''))
            if received>limit:raise HTTPException(413,'audio_too_large')
            return message
        await self.inner(scope,limited_receive,send)


app.add_middleware(Guard)
