from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from typing import Any


TRACKS = ("mixed", "mic", "callee")
TRACK_FILENAMES = {
    "mixed": ("audio.pcm", "audio.wav"),
    "mic": ("you.pcm", "you.wav"),
    "callee": ("callee.pcm", "callee.wav"),
}

STOP_WORDS = {
    "a",
    "about",
    "an",
    "and",
    "are",
    "as",
    "at",
    "asking",
    "be",
    "but",
    "by",
    "can",
    "doing",
    "for",
    "from",
    "great",
    "hello",
    "hey",
    "hi",
    "how",
    "i",
    "im",
    "in",
    "is",
    "it",
    "its",
    "me",
    "my",
    "may",
    "of",
    "on",
    "or",
    "so",
    "that",
    "thank",
    "thanks",
    "the",
    "this",
    "to",
    "was",
    "we",
    "what",
    "whats",
    "with",
    "you",
    "your",
}

BAD_NAME_WORDS = {
    "call",
    "chat",
    "going",
    "hello",
    "help",
    "okay",
    "recording",
    "there",
    "today",
    "you",
}


def build_conversation(
    segments: list[dict[str, Any]],
    *,
    callee_name: str,
    you_reference_text: str = "",
    callee_reference_text: str = "",
) -> tuple[str, dict[str, str]]:
    turns = flatten_turns(segments)
    if not turns:
        return "", {}

    speaker_map = resolve_speaker_map(
        turns,
        callee_name=callee_name,
        you_reference_text=you_reference_text,
        callee_reference_text=callee_reference_text,
    )
    merged_turns: list[tuple[str, str]] = []

    for turn in turns:
        speaker = speaker_map.get(turn["speaker"], turn["speaker"])
        text = normalize_space(turn["text"])
        if not text:
            continue

        if merged_turns and merged_turns[-1][0] == speaker:
            merged_turns[-1] = (speaker, f"{merged_turns[-1][1]} {text}")
        else:
            merged_turns.append((speaker, text))

    lines = [f"{speaker}: {text}" if speaker.startswith("[") else f"[{speaker}]: {text}" for speaker, text in merged_turns]
    return "\n".join(lines).strip() + ("\n" if lines else ""), speaker_map


def flatten_turns(segments: list[dict[str, Any]]) -> list[dict[str, str]]:
    turns: list[dict[str, str]] = []

    for segment in segments:
        segment_turns = segment.get("speaker_turns") or parse_speaker_turns(str(segment.get("text") or ""))
        for turn in segment_turns:
            speaker = normalize_space(str(turn.get("speaker") or "Unknown"))
            text = normalize_space(str(turn.get("text") or ""))
            if text:
                turns.append({"speaker": speaker, "text": text})

    return turns


def parse_speaker_turns(text: str) -> list[dict[str, str]]:
    parts = re.split(r"(\[Speaker \d+\]:)", text)
    turns: list[dict[str, str]] = []
    current_speaker = "Unknown"

    for part in parts:
        part = normalize_space(part)
        if not part:
            continue

        if re.fullmatch(r"\[Speaker \d+\]:", part):
            current_speaker = part.removesuffix(":")
            continue

        turns.append({"speaker": current_speaker, "text": part})

    return turns


def resolve_speaker_map(
    turns: list[dict[str, str]],
    *,
    callee_name: str,
    you_reference_text: str,
    callee_reference_text: str,
) -> dict[str, str]:
    aggregates: dict[str, str] = defaultdict(str)
    for turn in turns:
        aggregates[turn["speaker"]] = f"{aggregates[turn['speaker']]} {turn['text']}"

    speakers = list(aggregates)
    if not speakers:
        return {}

    you_tokens = token_set(you_reference_text)
    callee_tokens = token_set(callee_reference_text)
    scores: dict[str, tuple[float, float]] = {}

    for speaker, text in aggregates.items():
        turn_tokens = token_set(text)
        scores[speaker] = (
            overlap_score(turn_tokens, you_tokens),
            overlap_score(turn_tokens, callee_tokens),
        )

    speaker_map: dict[str, str] = {}
    if you_tokens:
        you_speaker = max(speakers, key=lambda speaker: scores[speaker][0] - scores[speaker][1])
        if scores[you_speaker][0] > 0:
            speaker_map[you_speaker] = "You"

    remaining = [speaker for speaker in speakers if speaker not in speaker_map]
    if callee_tokens and remaining:
        callee_speaker = max(remaining, key=lambda speaker: scores[speaker][1] - scores[speaker][0])
        if scores[callee_speaker][1] > 0:
            speaker_map[callee_speaker] = callee_name

    remaining = [speaker for speaker in speakers if speaker not in speaker_map]
    if len(speakers) == 2 and "You" in speaker_map.values() and remaining:
        speaker_map[remaining[0]] = callee_name
    elif len(speakers) == 2 and callee_name in speaker_map.values() and remaining:
        speaker_map[remaining[0]] = "You"

    for speaker in speakers:
        speaker_map.setdefault(speaker, callee_name)

    return speaker_map


def resolve_callee_name(callee_label: str, transcript_text: str) -> str:
    label = clean_callee_label(callee_label)
    if label and not looks_like_phone(label):
        return label

    mentioned_name = extract_addressed_name(transcript_text)
    if mentioned_name:
        return mentioned_name

    if label:
        return label

    phone = extract_phone(callee_label)
    return phone or "Unknown"


def clean_callee_label(value: str) -> str:
    value = normalize_space(value)
    if not value:
        return ""

    value = re.sub(r"\b(?:call|voice call|audio call|phone call|calling|hang up|end call)\b", " ", value, flags=re.I)
    value = re.sub(r"\b(?:google voice|calls|messages|contacts|keypad)\b", " ", value, flags=re.I)
    value = normalize_space(value.strip(":-|, "))
    if len(value) > 80:
        value = value[:80].rsplit(" ", 1)[0]
    return value


def extract_addressed_name(text: str) -> str:
    patterns = [
        r"\b(?:hi|hello|hey|thanks|thank you),?\s+([a-z][a-z0-9 .'-]{1,32})\b",
        r"\b(?:speaking with|calling)\s+([a-z][a-z0-9 .'-]{1,32})\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if not match:
            continue

        candidate = normalize_space(match.group(1).split(".")[0])
        first = candidate.split(" ", 1)[0].lower()
        if first and first not in BAD_NAME_WORDS:
            return title_name(candidate)

    return ""


def extract_phone(value: str) -> str:
    match = re.search(r"(?:\+?1[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]?\d{4}", value)
    return normalize_space(match.group(0)) if match else ""


def looks_like_phone(value: str) -> bool:
    return bool(extract_phone(value)) or bool(re.fullmatch(r"[\d\s()+.-]{7,}", value.strip()))


def fallback_subject(text: str, *, default: str = "conversation") -> str:
    if is_greeting_only_conversation(text):
        return "casual check in"

    text = re.sub(r"\[Speaker \d+\]:", " ", text)
    text = re.sub(r"\bSpeaker\s*\d+\b:?", " ", text, flags=re.I)
    sentences = re.split(r"(?<=[.!?])\s+", text)
    scored: list[tuple[int, int, str]] = []

    for index, sentence in enumerate(sentences):
        words = [word for word in re.findall(r"[A-Za-z0-9']+", sentence) if word.lower() not in STOP_WORDS]
        if not words:
            continue

        lower = sentence.lower()
        penalty = 0
        if re.search(r"\bcall\s+may\s+be\s+(recorded|reviewed)\b", lower):
            penalty += 12
        if "how can i help" in lower or "thanks for calling" in lower:
            penalty += 8
        if "how are you" in lower or "thanks for asking" in lower or "what's going on" in lower:
            penalty += 8
        if re.match(r"^\s*(hi|hello|hey)\b", lower):
            penalty += 4
        if len(words) < 3:
            penalty += 3
        candidate = clean_subject(" ".join(words[:8]))
        if candidate:
            scored.append((len(words) - penalty, -index, candidate))

    if scored:
        scored.sort(reverse=True)
        return scored[0][2]

    tokens = [token for token in re.findall(r"[A-Za-z0-9']+", text) if token.lower() not in STOP_WORDS]
    return clean_subject(" ".join(tokens[:8])) or default


def is_greeting_only_conversation(text: str) -> bool:
    lower = text.lower()
    has_greeting = any(phrase in lower for phrase in ("how are you", "how about you", "thanks for asking", "what's going on"))
    if not has_greeting:
        return False

    content_tokens = [
        token
        for token in re.findall(r"[A-Za-z0-9']+", lower)
        if token not in STOP_WORDS and token not in {"callee", "speaker", "reviewed", "safety", "help", "paul"}
    ]
    return len(content_tokens) <= 4


def clean_subject(value: str) -> str:
    original = value
    value = normalize_space(value)
    value = re.sub(r"^[\"'`]+|[\"'`]+$", "", value)
    value = re.sub(r"^\s*[-*]+\s*", "", value)
    value = re.sub(r"^(subject|title)\s*:\s*", "", value, flags=re.I)
    value = re.sub(r"[.?!]+$", "", value)
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'&+-]*", value)
    candidate = " ".join(words[:8])
    return "" if is_bad_subject(candidate, original=original) else candidate


def is_bad_subject(value: str, *, original: str = "") -> bool:
    if "\n" in original or "[" in original or "]" in original:
        return True

    lower = normalize_space(value).lower()
    if not lower:
        return True
    if re.search(r"\b(?:speaker\s*\d+|callee|caller|you)\b", lower):
        return True
    if any(phrase in lower for phrase in ("safety review", "reviewed for safety", "may be reviewed", "may be recorded", "how can i help")):
        return True
    if re.fullmatch(r"(?:speaker|callee|caller|unknown|you)(?:\s+\d+)?", lower):
        return True
    if re.match(r"^(?:speaker|callee|caller|unknown|you)\s+\d*\b", lower):
        return True
    if lower in {"call", "phone call", "voice call", "conversation", "transcript", "call summary"}:
        return True
    return False


def slugify(value: str, *, default: str, max_length: int = 60) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", " ", value)
    value = re.sub(r"[^A-Za-z0-9._ -]+", " ", value)
    value = re.sub(r"\s+", "_", value.strip())
    value = re.sub(r"_+", "_", value).strip("._- ")
    value = value[:max_length].strip("._- ")
    if not value:
        value = default
    if value.upper() in {"CON", "PRN", "AUX", "NUL", "COM1", "COM2", "LPT1", "LPT2"}:
        value = f"{value}_call"
    return value


def token_set(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[A-Za-z0-9']+", text.lower())
        if len(token) > 1 and token not in STOP_WORDS
    }


def overlap_score(turn_tokens: set[str], reference_tokens: set[str]) -> float:
    if not turn_tokens or not reference_tokens:
        return 0.0
    return len(turn_tokens & reference_tokens) / max(1, len(turn_tokens))


def title_name(value: str) -> str:
    return " ".join(part.capitalize() if part.islower() else part for part in value.split())


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()
