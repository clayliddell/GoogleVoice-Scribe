from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from .audio import load_wav_mono_float32, resample_linear
from .config import Settings


SAA_PROMPT = (
    "<|audio|> Speaker attribution: Transcribe and denote who is speaking "
    "by adding [Speaker 1]: and [Speaker 2]: tags before speaker turns."
)
ASR_PROMPT = "<|audio|> Transcribe the audio."
TITLE_SYSTEM_PROMPT = (
    "You write short file-safe subjects for phone call transcripts. "
    "Return the callee's company name if present, otherwise the callee's personal name if present, otherwise the main reason for the call. "
    "Do not include speaker labels, phone numbers, timestamps, quotes, punctuation, or greetings."
)
TITLE_USER_PROMPT = "Create a short title for this phone call transcript:\n\n{transcript}"
TITLE_TRANSCRIPT_MAX_CHARS = 3000
TITLE_CONTEXT_SAFETY_TOKENS = 16
LLAMA_CPP_BACKENDS = {"llama_cpp", "llamacpp", "gguf"}
TRANSFORMERS_BACKENDS = {"transformers", "hf", "huggingface"}
GGML_TYPE_IDS = {
    "f32": 0,
    "f16": 1,
    "q4_0": 2,
    "q4_1": 3,
    "q5_0": 6,
    "q5_1": 7,
    "q8_0": 8,
    "q2_k": 10,
    "q3_k": 11,
    "q4_k": 12,
    "q5_k": 13,
    "q6_k": 14,
    "q8_k": 15,
}


@dataclass
class TranscriptionResult:
    model: str
    mode: str
    sample_rate: int
    full_text: str
    segments: list[dict[str, Any]]


class GraniteTranscriber:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.device: str | None = None
        self.dtype: Any = None
        self.processor: Any = None
        self.tokenizer: Any = None
        self.model: Any = None
        self.title_processor: Any = None
        self.title_tokenizer: Any = None
        self.title_model: Any = None
        self.title_backend: str | None = None
        self.title_model_path: Path | None = None
        self.last_subject_error: str | None = None

    def transcribe_wav(self, wav_path: Path, *, mode: str = "speaker_attributed_asr") -> TranscriptionResult:
        audio, source_rate = load_wav_mono_float32(wav_path)
        return self.transcribe_audio(audio, source_rate, mode=mode)

    def warm_speech_model(self) -> None:
        self._load_model()

    def is_speech_model_loaded(self) -> bool:
        return self.model is not None

    def transcribe_audio(
        self,
        audio: np.ndarray,
        source_rate: int,
        *,
        mode: str = "speaker_attributed_asr",
        start_offset_seconds: float = 0.0,
        initial_prefix_text: str = "",
    ) -> TranscriptionResult:
        self._load_model()

        audio = resample_linear(audio, source_rate, self.settings.sample_rate)

        samples_per_segment = max(1, self.settings.segment_seconds * self.settings.sample_rate)
        text_parts: list[str] = [initial_prefix_text.strip()] if initial_prefix_text.strip() else []
        emitted_parts: list[str] = []
        segments: list[dict[str, Any]] = []

        for index, start in enumerate(range(0, audio.size, samples_per_segment)):
            end = min(start + samples_per_segment, audio.size)
            segment_audio = audio[start:end]
            if segment_audio.size == 0:
                continue

            previous_text = " ".join(text_parts).strip()
            prefix_text = previous_text[-4000:] if previous_text else None
            prompt = SAA_PROMPT if mode == "speaker_attributed_asr" else ASR_PROMPT
            text = self._transcribe_segment(segment_audio, prompt=prompt, prefix_text=prefix_text).strip()
            text_parts.append(text)
            emitted_parts.append(text)

            start_seconds = start_offset_seconds + start / float(self.settings.sample_rate)
            end_seconds = start_offset_seconds + end / float(self.settings.sample_rate)
            segments.append(
                {
                    "index": index,
                    "start_seconds": round(start_seconds, 3),
                    "end_seconds": round(end_seconds, 3),
                    "text": text,
                    "speaker_turns": parse_speaker_turns(text) if mode == "speaker_attributed_asr" else [],
                }
            )

        return TranscriptionResult(
            model=self.settings.model_name,
            mode=mode,
            sample_rate=self.settings.sample_rate,
            full_text="\n".join(part for part in emitted_parts if part).strip(),
            segments=segments,
        )

    def summarize_subject(self, transcript_text: str) -> str:
        transcript_text = transcript_text.strip()
        if not transcript_text:
            return ""

        self.last_subject_error = None
        try:
            title_backend = normalize_title_backend(self.settings.title_backend)
            if title_backend in LLAMA_CPP_BACKENDS:
                return self._summarize_subject_llama_cpp(transcript_text)
            if title_backend not in TRANSFORMERS_BACKENDS:
                raise ValueError(f"Unsupported title backend: {self.settings.title_backend}")
            if is_gemma4_title_model(self.settings.title_model_name):
                return self._summarize_subject_gemma4(transcript_text)
            if is_gemma3n_title_model(self.settings.title_model_name):
                return self._summarize_subject_gemma3n(transcript_text)
            return self._summarize_subject_seq2seq(transcript_text)
        except Exception as error:
            self.last_subject_error = str(error)
            return ""
        finally:
            if self.last_subject_error or not self.settings.keep_title_model:
                self._release_title_model()

    def _summarize_subject_llama_cpp(self, transcript_text: str) -> str:
        self._release_speech_model()
        self._load_llama_cpp_title_model()

        prompt = self._llama_cpp_title_prompt(transcript_text)
        response = self.title_model(
            prompt,
            max_tokens=self.settings.title_max_tokens,
            temperature=0.0,
            top_p=1.0,
            stop=["Transcript:", "Title:"],
            echo=False,
        )
        return first_title_line(completion_text(response))

    def _llama_cpp_title_prompt(self, transcript_text: str) -> str:
        transcript_text = prepare_title_transcript(transcript_text)
        prefix = (
            f"{TITLE_SYSTEM_PROMPT} Return only one line. Do not think step by step.\n\n"
            "Examples:\n"
            "Transcript: Thanks for calling North Star Dental, this is Maria. How can I help? I need to move my cleaning appointment.\n"
            "Title: North Star Dental\n"
            "Transcript: Hi, this is Alex speaking. I can help with that. I need support with my router setup.\n"
            "Title: Alex\n"
            "Transcript: Hi, I'm ChatGPT. Hey, I was just calling to check in. How is your day going?\n"
            "Title: ChatGPT\n"
            "Transcript: I need help with account billing renewal. The renewal charge looks wrong.\n"
            "Title: account billing renewal\n"
            "Transcript: Hey, how are you doing? I was just calling to check in.\n"
            "Title: casual check in\n\n"
            "Now create the title for this transcript. Follow this priority exactly: "
            "company name first, personal name second, call reason third.\n"
            "Transcript: "
        )
        suffix = "\nTitle:"
        return self._token_budgeted_llama_cpp_prompt(prefix, transcript_text, suffix)

    def _token_budgeted_llama_cpp_prompt(self, prefix: str, transcript_text: str, suffix: str) -> str:
        budget = self.settings.title_context_tokens - max(0, self.settings.title_max_tokens) - TITLE_CONTEXT_SAFETY_TOKENS
        if budget <= 0:
            raise ValueError(
                "GV_TITLE_CONTEXT_TOKENS is too small for title generation after reserving output tokens."
            )

        prompt = self._build_token_budgeted_llama_cpp_prompt(prefix, transcript_text, suffix, budget)
        if llama_cpp_token_count(self.title_model, prompt) <= budget:
            return prompt

        minimal_prefix = (
            f"{TITLE_SYSTEM_PROMPT}\n"
            "Priority: callee company, else callee personal name, else call reason.\n"
            "Transcript: "
        )
        prompt = self._build_token_budgeted_llama_cpp_prompt(minimal_prefix, transcript_text, suffix, budget)
        if llama_cpp_token_count(self.title_model, prompt) <= budget:
            return prompt

        empty_prompt = minimal_prefix + suffix
        if llama_cpp_token_count(self.title_model, empty_prompt) <= budget:
            return empty_prompt

        raise ValueError(
            "GV_TITLE_CONTEXT_TOKENS is too small for the title prompt; increase it or lower GV_TITLE_MAX_TOKENS."
        )

    def _build_token_budgeted_llama_cpp_prompt(self, prefix: str, transcript_text: str, suffix: str, budget: int) -> str:
        base_prompt = prefix + suffix
        base_tokens = llama_cpp_token_count(self.title_model, base_prompt)
        available_transcript_tokens = max(0, budget - base_tokens)
        transcript_tokens = llama_cpp_tokenize(self.title_model, transcript_text, add_bos=False)[:available_transcript_tokens]

        prompt = prefix + llama_cpp_detokenize(self.title_model, transcript_tokens) + suffix
        while transcript_tokens and llama_cpp_token_count(self.title_model, prompt) > budget:
            overflow = llama_cpp_token_count(self.title_model, prompt) - budget
            transcript_tokens = transcript_tokens[: max(0, len(transcript_tokens) - overflow - 1)]
            prompt = prefix + llama_cpp_detokenize(self.title_model, transcript_tokens) + suffix

        return prompt

    def _summarize_subject_gemma4(self, transcript_text: str) -> str:
        import torch

        self._release_speech_model()
        self._load_gemma4_title_model()

        prompt = self._gemma4_title_prompt(transcript_text)
        inputs = self.title_processor(text=prompt, return_tensors="pt")
        model_device = first_parameter_device(self.title_model)
        inputs = move_inputs_to_device(inputs, model_device)
        input_length = inputs["input_ids"].shape[-1]

        with torch.inference_mode():
            outputs = self.title_model.generate(
                **inputs,
                max_new_tokens=12,
                do_sample=False,
                num_beams=1,
            )

        generated = outputs[0][input_length:]
        decoded = self.title_processor.decode(generated, skip_special_tokens=False)
        if hasattr(self.title_processor, "parse_response"):
            try:
                parsed = self.title_processor.parse_response(decoded)
                if isinstance(parsed, dict):
                    parsed_text = str(parsed.get("answer") or parsed.get("content") or parsed.get("response") or "").strip()
                else:
                    parsed_text = str(parsed).strip()
                if parsed_text:
                    return first_title_line(parsed_text)
            except Exception:
                pass
        return first_title_line(self.title_processor.decode(generated, skip_special_tokens=True))

    def _gemma4_title_prompt(self, transcript_text: str) -> str:
        transcript_text = prepare_title_transcript(transcript_text)
        return (
            "Transcript: Thanks for calling North Star Dental, this is Maria. How can I help? I need to move my cleaning appointment.\n"
            "Title: North Star Dental\n\n"
            "Transcript: Hi, this is Alex speaking. I can help with that. I need support with my router setup.\n"
            "Title: Alex\n\n"
            "Transcript: Hi, I'm ChatGPT. Hey, I was just calling to check in. How is your day going?\n"
            "Title: ChatGPT\n\n"
            "Transcript: I need help with account billing renewal. The renewal charge looks wrong.\n"
            "Title: account billing renewal\n\n"
            "Transcript: Hey, how are you doing? I was just calling to check in.\n"
            "Title: casual check in\n\n"
            "Instruction: Write a 3 to 8 word title. Return only the title. "
            "Priority order: company name first, personal name second, call reason third. "
            "If the callee says their company or name, use that even when the call reason is just a check-in. "
            "Ignore greetings, speaker labels, phone numbers, timestamps, and recording disclosures.\n"
            f"Transcript: {transcript_text[:TITLE_TRANSCRIPT_MAX_CHARS]}\n"
            "Title:"
        )

    def _summarize_subject_gemma3n(self, transcript_text: str) -> str:
        import torch

        self._release_speech_model()
        self._load_gemma3n_title_model()

        messages = [
            {
                "role": "system",
                "content": [{"type": "text", "text": TITLE_SYSTEM_PROMPT}],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": TITLE_USER_PROMPT.format(transcript=transcript_text[:TITLE_TRANSCRIPT_MAX_CHARS]),
                    }
                ],
            },
        ]
        inputs = self.title_processor.apply_chat_template(
            messages,
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
        )
        model_device = first_parameter_device(self.title_model)
        inputs = move_inputs_to_device(inputs, model_device)
        input_length = inputs["input_ids"].shape[-1]

        with torch.inference_mode():
            outputs = self.title_model.generate(
                **inputs,
                max_new_tokens=32,
                do_sample=False,
                num_beams=1,
            )

        generated = outputs[0][input_length:]
        return self.title_processor.decode(generated, skip_special_tokens=True).strip()

    def _summarize_subject_seq2seq(self, transcript_text: str) -> str:
        import torch

        self._load_seq2seq_title_model()
        prompt = (
            f"{TITLE_SYSTEM_PROMPT}\n\n"
            f"{TITLE_USER_PROMPT.format(transcript=transcript_text[:TITLE_TRANSCRIPT_MAX_CHARS])}"
        )
        inputs = self.title_tokenizer(prompt, return_tensors="pt", truncation=True, max_length=1024)
        inputs = inputs.to("cpu")

        with torch.inference_mode():
            outputs = self.title_model.generate(
                **inputs,
                max_new_tokens=32,
                do_sample=False,
                num_beams=1,
            )

        return self.title_tokenizer.decode(
            outputs[0],
            add_special_tokens=False,
            skip_special_tokens=True,
        ).strip()

    def _load_seq2seq_title_model(self) -> None:
        if self.title_model is not None and self.title_backend == "transformers":
            return
        self._release_title_model()

        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        self.title_tokenizer = hf_from_pretrained_cached_first(AutoTokenizer, self.settings, self.settings.title_model_name)
        self.title_model = hf_from_pretrained_cached_first(
            AutoModelForSeq2SeqLM,
            self.settings,
            self.settings.title_model_name,
        )
        self.title_model.to("cpu")
        self.title_model.eval()
        self.title_backend = "transformers"

    def _load_gemma3n_title_model(self) -> None:
        if self.title_model is not None and self.title_processor is not None and self.title_backend == "transformers":
            return
        self._release_title_model()

        import torch
        from transformers import AutoProcessor, Gemma3nForConditionalGeneration

        cuda_available = torch.cuda.is_available() and not self.settings.force_cpu
        dtype = choose_dtype(torch, self.settings.torch_dtype, cuda_available) if cuda_available else torch.float32
        model_kwargs: dict[str, Any] = {
            "low_cpu_mem_usage": True,
        }
        if cuda_available:
            model_kwargs["device_map"] = "auto"

        self.title_processor = hf_from_pretrained_cached_first(AutoProcessor, self.settings, self.settings.title_model_name)
        try:
            self.title_model = hf_from_pretrained_cached_first(
                Gemma3nForConditionalGeneration,
                self.settings,
                self.settings.title_model_name,
                dtype=dtype,
                **model_kwargs,
            )
        except TypeError:
            self.title_model = hf_from_pretrained_cached_first(
                Gemma3nForConditionalGeneration,
                self.settings,
                self.settings.title_model_name,
                torch_dtype=dtype,
                **model_kwargs,
            )

        if not cuda_available:
            self.title_model.to("cpu")
        elif not getattr(self.title_model, "hf_device_map", None):
            self.title_model.to("cuda")

        self.title_model.eval()
        self.title_backend = "transformers"

    def _load_gemma4_title_model(self) -> None:
        if self.title_model is not None and self.title_processor is not None and self.title_backend == "transformers":
            return
        self._release_title_model()

        from transformers import AutoModelForCausalLM, AutoProcessor

        model_kwargs: dict[str, Any] = {
            "low_cpu_mem_usage": True,
        }

        self.title_processor = hf_from_pretrained_cached_first(AutoProcessor, self.settings, self.settings.title_model_name)
        try:
            self.title_model = hf_from_pretrained_cached_first(
                AutoModelForCausalLM,
                self.settings,
                self.settings.title_model_name,
                dtype="auto",
                **model_kwargs,
            )
        except TypeError:
            self.title_model = hf_from_pretrained_cached_first(
                AutoModelForCausalLM,
                self.settings,
                self.settings.title_model_name,
                torch_dtype="auto",
                **model_kwargs,
            )

        self.title_model.to("cpu")
        self.title_model.eval()
        self.title_backend = "transformers"

    def _load_llama_cpp_title_model(self) -> None:
        if self.title_model is not None and self.title_backend == "llama_cpp":
            return
        self._release_title_model()

        add_torch_cuda_dll_directory()
        from llama_cpp import Llama
        import llama_cpp

        model_path = self._resolve_title_gguf_path()
        gpu_layers = 0 if self.settings.force_cpu else self.settings.title_gpu_layers
        kwargs: dict[str, Any] = {
            "model_path": str(model_path),
            "n_ctx": self.settings.title_context_tokens,
            "n_batch": self.settings.title_batch_tokens,
            "n_gpu_layers": gpu_layers,
            "verbose": False,
            "flash_attn": self.settings.title_flash_attn,
        }

        if self.settings.title_batch_tokens > 0:
            kwargs["n_ubatch"] = self.settings.title_batch_tokens
        if self.settings.title_threads > 0:
            kwargs["n_threads"] = self.settings.title_threads
            kwargs["n_threads_batch"] = self.settings.title_threads

        type_k = llama_ggml_type_id(llama_cpp, self.settings.title_cache_type_k)
        type_v = llama_ggml_type_id(llama_cpp, self.settings.title_cache_type_v)
        if type_k is not None:
            kwargs["type_k"] = type_k
        if type_v is not None:
            kwargs["type_v"] = type_v

        try:
            self.title_model = Llama(**kwargs)
        except Exception:
            fallback_kwargs = dict(kwargs)
            fallback_kwargs.pop("type_v", None)
            fallback_kwargs.pop("type_k", None)
            fallback_kwargs.pop("flash_attn", None)
            self.title_model = Llama(**fallback_kwargs)

        self.title_backend = "llama_cpp"
        self.title_model_path = model_path

    def _resolve_title_gguf_path(self) -> Path:
        if self.settings.title_gguf_path is not None:
            path = self.settings.title_gguf_path
            if not path.exists():
                raise FileNotFoundError(f"Configured GV_TITLE_GGUF_PATH does not exist: {path}")
            return path

        return Path(
            hf_hub_download_cached_first(
                self.settings,
                self.settings.title_model_name,
                self.settings.title_gguf_filename,
            )
        )

    def _load_model(self) -> None:
        if self.model is not None:
            return
        if self.title_model is not None and self.title_backend == "llama_cpp":
            self._release_title_model()

        import torch
        from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor

        cuda_available = torch.cuda.is_available() and not self.settings.force_cpu
        self.device = "cuda" if cuda_available else "cpu"
        self.dtype = choose_dtype(torch, self.settings.torch_dtype, cuda_available)

        self.processor = hf_from_pretrained_cached_first(AutoProcessor, self.settings, self.settings.model_name)
        self.tokenizer = self.processor.tokenizer

        model_kwargs: dict[str, Any] = {
            "low_cpu_mem_usage": True,
        }

        try:
            self.model = hf_from_pretrained_cached_first(
                AutoModelForSpeechSeq2Seq,
                self.settings,
                self.settings.model_name,
                dtype=self.dtype,
                **model_kwargs,
            )
        except TypeError:
            self.model = hf_from_pretrained_cached_first(
                AutoModelForSpeechSeq2Seq,
                self.settings,
                self.settings.model_name,
                torch_dtype=self.dtype,
                **model_kwargs,
            )

        self.model.to(self.device)
        self.model.eval()

    def _release_speech_model(self) -> None:
        model = self.model
        self.model = None
        self.processor = None
        self.tokenizer = None
        self.device = None
        self.dtype = None
        del model
        clear_cuda_cache()

    def _release_title_model(self) -> None:
        model = self.title_model
        self.title_model = None
        self.title_processor = None
        self.title_tokenizer = None
        self.title_backend = None
        self.title_model_path = None
        close = getattr(model, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
        del model
        clear_cuda_cache()

    def _transcribe_segment(self, audio: np.ndarray, *, prompt: str, prefix_text: str | None) -> str:
        import torch

        today = datetime.now().strftime("%B %d, %Y")
        system_prompt = (
            "Knowledge Cutoff Date: April 2024.\n"
            f"Today's Date: {today}.\n"
            "You are Granite, developed by IBM. You are a helpful AI assistant"
        )
        chat = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
        extra = {"prefix_text": prefix_text} if prefix_text else {}
        prompt_text = self.tokenizer.apply_chat_template(
            chat,
            tokenize=False,
            add_generation_prompt=True,
            **extra,
        )

        try:
            inputs = self.processor(
                prompt_text,
                audio,
                device=self.device,
                return_tensors="pt",
            )
        except TypeError:
            inputs = self.processor(prompt_text, audio, return_tensors="pt")

        if hasattr(inputs, "to"):
            inputs = inputs.to(self.device)
        else:
            inputs = {
                key: value.to(self.device) if hasattr(value, "to") else value
                for key, value in inputs.items()
            }

        with torch.inference_mode():
            outputs = self.model.generate(
                **inputs,
                max_new_tokens=self.settings.max_new_tokens,
                do_sample=False,
                num_beams=1,
            )

        input_length = inputs["input_ids"].shape[-1]
        new_tokens = outputs[0, input_length:]
        return self.tokenizer.decode(
            new_tokens,
            add_special_tokens=False,
            skip_special_tokens=True,
        )


def choose_dtype(torch: Any, requested: str, cuda_available: bool) -> Any:
    if requested in {"float32", "fp32"}:
        return torch.float32
    if requested in {"float16", "fp16"}:
        return torch.float16
    if requested in {"bfloat16", "bf16"}:
        return torch.bfloat16
    if cuda_available:
        is_bf16_supported = getattr(torch.cuda, "is_bf16_supported", lambda: False)
        return torch.bfloat16 if is_bf16_supported() else torch.float16
    return torch.float32


def normalize_title_backend(value: str) -> str:
    return (value or "transformers").strip().lower().replace("-", "_")


def hf_from_pretrained_cached_first(factory: Any, settings: Settings, model_name: str, **kwargs: Any) -> Any:
    if settings.hf_local_files_only or settings.hf_local_first:
        try:
            return factory.from_pretrained(model_name, local_files_only=True, **kwargs)
        except Exception as error:
            if isinstance(error, TypeError):
                raise
            if settings.hf_local_files_only:
                raise

    return factory.from_pretrained(model_name, local_files_only=False, **kwargs)


def hf_hub_download_cached_first(settings: Settings, repo_id: str, filename: str) -> str:
    from huggingface_hub import hf_hub_download

    if settings.hf_local_files_only or settings.hf_local_first:
        try:
            return hf_hub_download(repo_id=repo_id, filename=filename, local_files_only=True)
        except Exception:
            if settings.hf_local_files_only:
                raise

    return hf_hub_download(repo_id=repo_id, filename=filename, local_files_only=False)


def llama_ggml_type_id(llama_cpp: Any, value: str) -> int | None:
    normalized = (value or "").strip().lower()
    if normalized in {"", "none", "default", "auto"}:
        return None

    attr = f"GGML_TYPE_{normalized.upper()}"
    for module in (llama_cpp, getattr(llama_cpp, "llama_cpp", None)):
        if module is None:
            continue
        candidate = getattr(module, attr, None)
        if candidate is not None:
            return int(candidate)

    return GGML_TYPE_IDS.get(normalized)


def chat_completion_text(response: Any) -> str:
    if not isinstance(response, dict):
        return ""
    choices = response.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return str(message.get("content") or "").strip()


def completion_text(response: Any) -> str:
    if not isinstance(response, dict):
        return ""
    choices = response.get("choices") or []
    if not choices:
        return ""
    return str(choices[0].get("text") or "").strip()


def llama_cpp_token_count(model: Any, text: str) -> int:
    return len(llama_cpp_tokenize(model, text, add_bos=True))


def llama_cpp_tokenize(model: Any, text: str, *, add_bos: bool) -> list[int]:
    return list(model.tokenize(text.encode("utf-8"), add_bos=add_bos, special=False))


def llama_cpp_detokenize(model: Any, tokens: list[int]) -> str:
    if not tokens:
        return ""
    value = model.detokenize(tokens)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return str(value)


def is_gemma3n_title_model(model_name: str) -> bool:
    return "gemma-3n" in model_name.lower()


def is_gemma4_title_model(model_name: str) -> bool:
    return "gemma-4" in model_name.lower() or "gemma4" in model_name.lower()


def first_parameter_device(model: Any) -> Any:
    device = getattr(model, "device", None)
    if device is not None:
        return device

    try:
        return next(model.parameters()).device
    except StopIteration:
        return "cpu"


def move_inputs_to_device(inputs: Any, device: Any) -> Any:
    if hasattr(inputs, "to"):
        return inputs.to(device)
    return {
        key: value.to(device) if hasattr(value, "to") else value
        for key, value in inputs.items()
    }


def clear_cuda_cache() -> None:
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        return


def add_torch_cuda_dll_directory() -> None:
    try:
        import os
        import sys

        if os.name != "nt":
            return

        candidates = [
            Path(sys.prefix) / "Lib" / "site-packages" / "torch" / "lib",
            Path(sys.executable).resolve().parents[1] / "Lib" / "site-packages" / "torch" / "lib",
        ]
        for path in candidates:
            if path.exists():
                os.add_dll_directory(str(path))
                path_text = str(path)
                if path_text not in os.environ.get("PATH", ""):
                    os.environ["PATH"] = path_text + os.pathsep + os.environ.get("PATH", "")
                return
    except Exception:
        return


def prepare_title_transcript(text: str) -> str:
    text = re.sub(r"\[(?:Speaker\s*\d+|You|Callee|Caller|Unknown)\]:", " ", text, flags=re.I)
    text = re.sub(r"\b(?:this\s+)?call\s+may\s+be\s+(?:reviewed|recorded)(?:\s+for\s+safety)?\b", " ", text, flags=re.I)
    text = re.sub(r"\b(?:this\s+)?call\s+is\s+being\s+recorded\b", " ", text, flags=re.I)
    text = re.sub(r"\bhow\s+can\s+i\s+(?:help|assist)(?:\s+you)?(?:\s+today)?\b", " ", text, flags=re.I)
    text = re.sub(r"\b(?:just\s+)?letting\s+you\s+know\b", " ", text, flags=re.I)
    text = re.sub(r"\bis\s+that\s+okay\b", " ", text, flags=re.I)
    text = re.sub(r"\bthat\s+time\s+with\s+me\b", " ", text, flags=re.I)
    text = re.sub(r"\s+[?.!,;:](?=\s|$)", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def first_title_line(text: str) -> str:
    text = strip_title_model_artifacts(text)
    for line in re.split(r"[\r\n]+", text):
        line = re.sub(r"^(?:title|subject|category title|folder name|final answer|answer)\s*:\s*", "", line.strip(), flags=re.I)
        line = line.strip(" \"'`*.-")
        if line:
            return line
    return ""


def strip_title_model_artifacts(text: str) -> str:
    text = re.sub(r"<\|channel\|>thought.*?<\|channel\|>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<\|[^>]+?\|>", " ", text)
    text = re.sub(r"\b(?:thought|analysis)\s*:\s*.*?(?=(?:final answer|answer|title)\s*:|$)", " ", text, flags=re.I | re.S)
    return text.strip()


def parse_speaker_turns(text: str) -> list[dict[str, str]]:
    parts = re.split(r"(\[Speaker \d+\]:)", text)
    turns: list[dict[str, str]] = []
    current_speaker: str | None = None

    for part in parts:
        part = part.strip()
        if not part:
            continue

        if re.fullmatch(r"\[Speaker \d+\]:", part):
            current_speaker = part.removesuffix(":")
            continue

        turns.append(
            {
                "speaker": current_speaker or "Unknown",
                "text": part,
            }
        )

    return turns
