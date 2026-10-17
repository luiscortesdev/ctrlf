"""
Gemini (Google's AI) as the device's ears and voice.

understand(): the push-to-talk recording goes straight to Gemini as audio, along with
what the device can find, what it was taught, what it's in the middle of and the last few
lines of the conversation. Gemini answers with what was said and what to do: find, teach,
yes, no, ... It never says where anything is: src/app.py works that out from what the
camera saw, so it can't make things up.

speak(): turns an answer into speech, streamed, so it starts talking about 0.75 s later.

Needs GEMINI_API_KEY (in the environment, or in .env in the repo folder) and
`pip install google-genai`. voice.py falls back to Whisper and the Mac's `say` when
there's no key, no internet, or Gemini is slow.
"""

import io
import os
import re
import time
import wave

# Measured Oct 17, a spoken question in and the JSON answer out: 3.5 Flash-Lite 0.9-1.5 s
# (1 in 8 took 15 s: voice.py asks again); 3.6 Flash 1.3-4 s plus 503/429 errors; 3.1
# Flash-Lite 2.2-24 s; 3.8 Flash 2.5-3.4 s for a one-line text reply, even on low thinking.
MODEL = "gemini-3.5-flash-lite"
TTS_MODEL = "gemini-3.8-flash-lite-tts"  # first audio after 0.74 s (3.8 Flash TTS: 0.9 s)
# The free tier allows only 10 spoken answers a day (used up on Oct 17), and the other TTS
# models need paid credits (402 "prepayment credits are depleted" on this key's project).
# Once either runs out, speak() stops asking until it comes back: see VoiceUsedUp.
TTS_VOICE = "Aoede"                      # one of Gemini's prebuilt voices
TTS_RATE = 24000                         # it sends 16-bit mono PCM at this rate

# Errors that come back this fast are tried again, up to TRIES times in all: on Oct 17 the
# new key got about 1 in 3 calls refused ("403 caller does not have permission") in 0.2 s,
# and the next try worked. Quota (429) and "high demand" (503) errors too.
TRIES = 3
RETRY_SECS = 1.0

ACTIONS = ["find", "teach", "name", "yes", "no", "cancel", "forget", "rename", "list",
           "chat", "unclear"]

INSTRUCTIONS = """You are the ears of "Ctrl+F for Your Room": a device on a shelf with a \
camera and a laser pointer. It remembers where things are in the room; when asked, it says \
where and points the laser at them. Someone pressed its talk button and spoke. Work out what \
they want it to do.

You only choose actions. Never say where anything is or make up anything about the room: \
the device works that out from its camera.

Actions ("do"), with "thing" where it says so:
- find: where is something. thing = the name from the lists below that they mean ("earbuds \
case" for "AirPods"); if it's in neither list, the words they used ("my blue scarf" -> "blue \
scarf"). One find per thing they're looking for, not for places they mention: "is my phone \
still on the couch?" is one find, the phone. A follow-up like "and my wallet?" or "what \
about my wallet?" is a find too.
- teach: they want to show it something new to learn ("learn this", "remember this one", \
"this is my retainer case"). thing = what they call it, or "" if they didn't say.
- name: they're telling it what the thing it's being taught is called (it asked, or it's \
being shown something). thing = the name. Right after it asked what something is called, \
an answer like "my phone" is a name, not a find, even for a thing on the lists below.
- yes / no: an answer to the device's yes-or-no question ("yeah that's it", "nope, wrong \
one").
- cancel: stop what it's doing, never mind.
- forget: forget a thing it was taught. thing = its name from the taught list, or "that" \
for the last one taught. Wiping its whole memory is only done with the button on screen: \
answer that with chat.
- rename: the thing it was just taught is called something else ("call it ...", "no, it's \
my ..."). thing = the new name.
- list: what it has seen or remembers.
- chat: anything else said to it (hello, thanks, what can you do?). Put a short, friendly \
reply in "reply": one or two short sentences, to be spoken aloud. It can find things by \
name, point its laser at them, say when it last saw them, and learn new things you show it \
("learn this").
- unclear: silence, noise, or talk that isn't meant for it.

Write names in lowercase, without "my", "the" or "a". Speech can be misheard: if a word \
sounds like one of the taught names, use the taught name."""

SCHEMA = {
    "type": "object",
    "properties": {
        "heard": {"type": "string", "description": "What they said, word for word."},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"do": {"type": "string", "enum": ACTIONS},
                               "thing": {"type": "string"}},
                "required": ["do"],
            },
        },
        "reply": {"type": "string", "description": "Only for chat: what to say back."},
    },
    "required": ["heard", "actions"],
}

_client = None


def _try_again(error, started):
    return getattr(error, "code", None) in (403, 429, 500, 503) and time.time() - started < RETRY_SECS


def _chain(first, rest):
    if first is not None:
        yield first
    yield from rest


def api_key():
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the repo
        with open(os.path.join(root, ".env")) as f:
            for line in f:
                name, _, value = line.strip().partition("=")
                if name == "GEMINI_API_KEY" and value:
                    return value.strip().strip('"')
    except OSError:
        pass
    return None


def client():
    """The Gemini client, or None if there's no key or no google-genai."""
    global _client
    if _client is None:
        key = api_key()
        if not key:
            return None
        try:
            from google import genai
        except ImportError:
            return None
        _client = genai.Client(api_key=key)
    return _client


def check():
    """Opens the connection and checks the key, so the first question isn't slower.
    Returns None if Gemini is ready, else what's wrong."""
    c = client()
    if c is None:
        return "no GEMINI_API_KEY in .env (or pip install google-genai)"
    for attempt in range(TRIES):
        started = time.time()
        try:
            c.models.get(model=MODEL)
            return None
        except Exception as e:
            if attempt == TRIES - 1 or not _try_again(e, started):
                return repr(e)[:160]


def to_wav(audio, rate):
    """float32 samples -> WAV file bytes."""
    import numpy as np

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())
    return buf.getvalue()


def describe_context(context):
    """What the device knows right now, as text for Gemini."""
    lines = ["Things it can find (name: other words people use):"]
    for label, words in context.get("objects", {}).items():
        others = [w for w in words if w != label]
        lines.append(f"- {label}" + (f": {', '.join(others)}" if others else ""))
    taught = context.get("taught") or []
    lines.append("Things the owner taught it: " + (", ".join(taught) if taught else "none yet"))
    if context.get("doing"):
        lines.append("Right now: " + context["doing"])
    if context.get("conversation"):
        lines.append("Conversation so far (oldest first):")
        lines += context["conversation"]
    lines.append("The new recording is attached.")
    return "\n".join(lines)


def understand(wav, context):
    """WAV bytes of someone talking -> {"heard": ..., "actions": [{"do", "thing"}], "reply"}.
    Raises on any failure (no key, no internet, bad answer). Gemini won't take a deadline
    under 10 s, so the caller decides how long to wait (voice.py)."""
    import json

    from google.genai import types

    c = client()
    if c is None:
        raise RuntimeError("no Gemini key")
    for attempt in range(TRIES):
        started = time.time()
        try:
            r = c.models.generate_content(
                model=MODEL,
                contents=[types.Part.from_bytes(data=wav, mime_type="audio/wav"),
                          describe_context(context)],
                config=types.GenerateContentConfig(
                    system_instruction=INSTRUCTIONS,
                    response_mime_type="application/json",
                    response_json_schema=SCHEMA,
                    temperature=0,
                ),
            )
            break
        except Exception as e:
            if attempt == TRIES - 1 or not _try_again(e, started):
                raise
    meaning = json.loads(r.text)
    meaning["actions"] = [a for a in meaning.get("actions", []) if a.get("do") in ACTIONS]
    return meaning


class VoiceUsedUp(Exception):
    """The daily free quota or the paid credits for speech have run out."""


_used_up_until = 0.0  # when speech can work again (time.time()); until then, don't ask


def _used_up_wait(error):
    """Seconds until speech can work again, if that's what error is about, else None."""
    code = getattr(error, "code", None)
    if code == 402:  # no paid credits left: try again in an hour, in case some were added
        return 3600
    if code == 429 and "PerDay" in str(error):
        wait = re.search(r"retryDelay'?: '(\d+)s", str(error))
        return int(wait.group(1)) if wait else 3600
    return None


def speak(text):
    """Yields the spoken text as chunks of 16-bit mono PCM at TTS_RATE, as they arrive."""
    global _used_up_until
    from google.genai import types

    c = client()
    if c is None:
        raise RuntimeError("no Gemini key")
    if time.time() < _used_up_until:
        raise VoiceUsedUp("Gemini's speech quota is used up")
    config = types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=TTS_VOICE))))
    for attempt in range(TRIES):
        started = time.time()
        try:
            chunks = iter(c.models.generate_content_stream(model=TTS_MODEL, contents=text,
                                                           config=config))
            first = next(chunks, None)  # refusals come with the first chunk
            break
        except Exception as e:
            wait = _used_up_wait(e)
            if wait is not None:
                _used_up_until = time.time() + wait
                raise VoiceUsedUp(f"Gemini's speech quota is used up ({e!r:.80})") from e
            if attempt == TRIES - 1 or not _try_again(e, started):
                raise
    for chunk in _chain(first, chunks):
        for cand in chunk.candidates or []:
            for part in (cand.content.parts if cand.content else None) or []:
                if part.inline_data and part.inline_data.data:
                    yield part.inline_data.data
