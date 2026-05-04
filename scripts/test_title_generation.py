from __future__ import annotations

import json
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE_DIR = REPO_ROOT / "service"
sys.path.insert(0, str(SERVICE_DIR))

from app.config import Settings  # noqa: E402
from app.conversation import clean_subject, fallback_subject, slugify  # noqa: E402
from app.transcriber import GraniteTranscriber  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/test_title_generation.py <session-folder-or-transcript.json>")

    target = Path(sys.argv[1]).expanduser()
    if target.is_dir():
        transcript_path = target / "transcript.json"
    else:
        transcript_path = target

    payload = json.loads(transcript_path.read_text(encoding="utf-8"))
    text = payload.get("full_text") or payload.get("raw_model_full_text") or ""
    settings = Settings.from_env()
    transcriber = GraniteTranscriber(settings)

    print(f"backend: {settings.title_backend}", flush=True)
    print(f"model: {settings.title_model_name}", flush=True)
    if settings.title_backend == "llama_cpp":
        print(f"gguf_file: {settings.title_gguf_filename}", flush=True)
        print(
            "llama_cpp: "
            f"n_ctx={settings.title_context_tokens} "
            f"n_gpu_layers={settings.title_gpu_layers} "
            f"cache_k={settings.title_cache_type_k} "
            f"cache_v={settings.title_cache_type_v} "
            f"flash_attn={settings.title_flash_attn}",
            flush=True,
        )
    print(f"transcript_path: {transcript_path}", flush=True)
    print(f"input_text: {text}", flush=True)

    started = time.perf_counter()
    raw_title = transcriber.summarize_subject(text)
    elapsed = time.perf_counter() - started
    clean_title = clean_subject(raw_title)
    fallback_title = fallback_subject(text, default="conversation")
    chosen_title = clean_title or fallback_title

    print(f"raw_title: {raw_title!r}", flush=True)
    print(f"seconds: {elapsed:.2f}", flush=True)
    print(f"last_subject_error: {transcriber.last_subject_error!r}", flush=True)
    print(f"clean_title: {clean_title!r}", flush=True)
    print(f"fallback_title: {fallback_title!r}", flush=True)
    print(f"chosen_title: {chosen_title!r}", flush=True)
    print(f"folder_slug: {slugify(chosen_title, default='conversation', max_length=255)}", flush=True)


if __name__ == "__main__":
    main()
