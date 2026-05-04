from __future__ import annotations

from app.conversation import build_conversation, clean_subject, slugify


def test_build_conversation_resolves_you_and_callee_from_reference_text():
    segments = [
        {
            "speaker_turns": [
                {"speaker": "[Speaker 1]", "text": "Thanks for calling. How can I help?"},
                {"speaker": "[Speaker 2]", "text": "I need to reschedule my appointment."},
            ]
        }
    ]

    text, speaker_map = build_conversation(
        segments,
        callee_name="Callee",
        you_reference_text="I need to reschedule my appointment",
        callee_reference_text="Thanks for calling how can I help",
    )

    assert speaker_map == {"[Speaker 2]": "You", "[Speaker 1]": "Callee"}
    assert "[Callee]: Thanks for calling. How can I help?" in text
    assert "[You]: I need to reschedule my appointment." in text


def test_clean_subject_rejects_speaker_and_recording_noise():
    assert clean_subject("[Speaker 1]: This call may be reviewed for safety") == ""
    assert clean_subject("North Star Dental appointment") == "North Star Dental appointment"


def test_slugify_removes_windows_unsafe_characters():
    assert slugify('Billing: renewal / "May"', default="conversation") == "Billing_renewal_May"
