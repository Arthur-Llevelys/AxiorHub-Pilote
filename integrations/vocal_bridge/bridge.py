"""OpenAI-compatible, local-only adapter for Vocal's Gradio transcription API.

The adapter deliberately logs metadata and SHA-256 fingerprints only.  Audio,
filenames, prompts and transcripts are never included in logs.
"""
from __future__ import annotations

import hashlib
import ipaddress
import logging
import os
import re
import uuid
import contextlib
from pathlib import Path
import tempfile
import time
from typing import Any


LOG = logging.getLogger("axiorhub.vocal_bridge")
LOG.setLevel(logging.INFO)
if not LOG.handlers:
    LOG.addHandler(logging.StreamHandler())
LOG.propagate = False
MAX_AUDIO_BYTES = int(os.environ.get("MAX_AUDIO_BYTES", str(512 * 1024 * 1024)))
ALLOWED_MODELS = {"large-v3-turbo", "turbo", "large-v3", "large-v2"}
PARAMETER_LABELS = ["Upload File here","Input Folder Path (Optional)","Include Subdirectory Files","Save outputs at same directory","File Format","Add a timestamp to the end of the filename","Model","Language","Translate to English?","Beam Size","Log Probability Threshold","No Speech Threshold","Compute Type","Best Of","Patience","Condition On Previous Text","Prompt Reset On Temperature","Initial Prompt","Temperature","Compression Ratio Threshold","Length Penalty","Repetition Penalty","No Repeat N-gram Size","Prefix","Suppress Blank","Suppress Tokens","Max Initial Timestamp","Word Timestamps","Prepend Punctuations","Append Punctuations","Max New Tokens","Chunk Length (s)","Hallucination Silence Threshold (sec)","Hotwords","Language Detection Threshold","Language Detection Segments","Batch Size","Offload sub model when finished","Enable Silero VAD Filter","Speech Threshold","Minimum Speech Duration (ms)","Maximum Speech Duration (s)","Minimum Silence Duration (ms)","Speech Padding (ms)","Enable Diarization","Device","HuggingFace Token","Offload sub model when finished","Enable Background Music Remover Filter","Model","Device","Segment Size","Save separated files to output","Offload sub model when finished"]


class BridgeError(Exception):
    """A bounded, non-confidential error suitable for an API response."""


def peer_is_local(value: str | None) -> bool:
    """Accept loopback and private container/LAN peers, never public peers."""
    try:
        address = ipaddress.ip_address((value or "").split("%", 1)[0])
    except ValueError:
        return False
    return address.is_loopback or any(address in net for net in
      (ipaddress.ip_network('10.0.0.0/8'),ipaddress.ip_network('172.16.0.0/12'),
       ipaddress.ip_network('192.168.0.0/16'),ipaddress.ip_network('fc00::/7'))
      if address.version==net.version)


def normalize_language(value: str | None) -> str:
    language = (value or "french").strip().lower()
    aliases = {"fr": "french", "fr-fr": "french", "fra": "french"}
    language = aliases.get(language, language)
    if language not in {"french", "english", "automatic detection"}:
        raise BridgeError("language_not_allowed")
    return "Automatic Detection" if language=="automatic detection" else language


def normalize_model(value: str | None) -> str:
    model = (value or "large-v3-turbo").strip()
    if model not in ALLOWED_MODELS:
        raise BridgeError("model_not_allowed")
    return model


def _output_path(item: Any) -> Path | None:
    if isinstance(item, (str, os.PathLike)):
        return Path(item)
    if isinstance(item, dict):
        value = item.get("path") or item.get("name")
        return Path(value) if value else None
    value = getattr(item, "path", None) or getattr(item, "name", None)
    return Path(value) if value else None


def extract_text(result: Any, output_root: Path) -> str:
    """Prefer Vocal's generated TXT file, constrained to our download folder."""
    values = list(result) if isinstance(result, (list, tuple)) else [result]
    candidates: list[Any] = []
    if len(values) > 1:
        files = values[1]
        candidates.extend(files if isinstance(files, (list, tuple)) else [files])
    root = output_root.resolve()
    for item in candidates:
        path = _output_path(item)
        if not path or not path.is_file():
            continue
        resolved = path.resolve()
        if resolved != root and root not in resolved.parents:
            continue
        if resolved.stat().st_size > 20_000_000:
            raise BridgeError("transcript_too_large")
        text = resolved.read_text(encoding="utf-8", errors="replace").strip()
        if text:
            return text
    # Vocal's first string is sometimes a completion log, not the transcript.
    # Never return that log as if it were spoken text.
    raise BridgeError("empty_transcription")


def cleanup_remote(stem,roots):
    """Unlink only this random request's files in explicitly mounted Vocal roots.

    No recursive directory deletion; no following symlinks; no other job touched.
    The roots must cover Vocal upload cache AND output directory.
    """
    if not re.fullmatch(r'axiorhub-[a-f0-9]{32}',stem):raise BridgeError('cleanup_id_invalid')
    removed=0
    for root in roots:
        root=Path(root)
        if not root.is_dir() or root.is_symlink():raise BridgeError('vocal_cleanup_not_configured')
        for directory,dirs,files in os.walk(root,followlinks=False):
            dirs[:]=[d for d in dirs if not (Path(directory)/d).is_symlink()]
            for name in files:
                if not (name.startswith(stem+'.') or name.startswith(stem+'-') or name.startswith(stem+'_')):continue
                target=Path(directory)/name
                if target.is_symlink():raise BridgeError('vocal_cleanup_symlink_refused')
                target.unlink();removed+=1
    return removed


def vocal_arguments(audio_path: Path, model: str, language: str,
                    temperature: float, hotwords: str) -> list[Any]:
    """Build Vocal 0.10.x's 54 positional `/transcribe_file` parameters."""
    if temperature != 0:
        raise BridgeError("temperature_must_be_zero")
    # Import lazily so the pure validation layer remains unit-testable without
    # the bridge container dependencies.
    from gradio_client import handle_file

    return [
        [handle_file(str(audio_path))], "", False, False, "txt", False,
        model, language, False, 5, -1, 0.6, "int8", 5, 1, True, 0.5,
        hotwords or None, 0, 2.4, 1, 1, 0, None, True, "[-1]", 1, False,
        "\"'“¿([{-", "\"'.。,，!！?？:：”)]}、", None, 30, None,
        hotwords or None, 0.5, 1, 24, True, False, 0.5, 250, 9999,
        1000, 2000, False, "cpu", "", True, False,
        "UVR-MDX-NET-Inst_HQ_4", "cpu", 256, False, True,
    ]


class VocalClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def transcribe(self, audio_path: Path, output_root: Path, *, model: str,
                   language: str, temperature: float, hotwords: str) -> str:
        from gradio_client import Client

        output_root.mkdir(parents=True, exist_ok=True)
        roots=[Path('/vocal-cache'),Path('/vocal-output')]
        if os.environ.get('VOCAL_CPU_CONFIRMED')!='true' or any(not p.is_dir() for p in roots):
            raise BridgeError('vocal_cpu_or_cleanup_not_configured')
        from urllib.parse import urlsplit
        parsed=urlsplit(self.base_url)
        if parsed.scheme!='http' or parsed.hostname not in {'host.docker.internal','vocal','127.0.0.1','localhost'} or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise BridgeError('vocal_local_url_required')
        try:
            # Third-party progress output can include file names or status text.
            with open(os.devnull,'w') as silent,contextlib.redirect_stdout(silent),contextlib.redirect_stderr(silent):
                client = Client(self.base_url, download_files=str(output_root), verbose=False,
                                httpx_kwargs={'trust_env':False,'timeout':1800.0})
                spec=client.view_api(print_info=False,return_format='dict')
                endpoint=spec.get('named_endpoints',{}).get('/transcribe_file',{})
                if [p.get('label') for p in endpoint.get('parameters',[])]!=PARAMETER_LABELS:
                    raise BridgeError('vocal_api_signature_changed')
                result = client.predict(*vocal_arguments(audio_path, model, language,
                                                        temperature, hotwords),
                                        api_name="/transcribe_file")
                return extract_text(result, output_root)
        finally:
            cleanup_remote(audio_path.stem,roots)


def transcribe_stream(stream, *, model: str | None = None,
                      language: str | None = None, temperature: float = 0,
                      hotwords: str = "", client: VocalClient | None = None,
                      filename: str = 'audio.wav') -> dict:
    model = normalize_model(model)
    language = normalize_language(language)
    if temperature!=0:raise BridgeError('temperature_must_be_zero')
    extension=Path(filename).suffix.lower()
    if extension not in {'.wav','.mp3','.mp4','.m4a','.ogg','.webm','.flac','.aac','.opus'}:
        raise BridgeError('audio_extension_invalid')
    if len(hotwords) > 4000 or any(ord(char) < 32 and char not in "\t\n" for char in hotwords):
        raise BridgeError("hotwords_invalid")
    started = time.monotonic()
    sha = hashlib.sha256()
    size = 0
    status = "failed"
    with tempfile.TemporaryDirectory(prefix="axiorhub-vocal-") as folder:
        root = Path(folder)
        audio = root / ('axiorhub-'+uuid.uuid4().hex+extension)
        with audio.open("wb") as target:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_AUDIO_BYTES:
                    raise BridgeError("audio_too_large")
                sha.update(chunk)
                target.write(chunk)
        if not size:
            raise BridgeError("audio_empty")
        try:
            adapter = client or VocalClient(os.environ.get(
                "VOCAL_BASE_URL", "http://host.docker.internal:7860"))
            text = adapter.transcribe(audio, root / "outputs", model=model,
                                      language=language, temperature=temperature,
                                      hotwords=hotwords)
            status = "ok"
            return {"text": text}
        finally:
            # TemporaryDirectory removes input and downloaded output before the
            # API response leaves this function.
            LOG.info("transcription status=%s sha256=%s bytes=%d elapsed_ms=%d",
                     status, sha.hexdigest(), size,
                     round((time.monotonic() - started) * 1000))
