"""Generate narration audio and word-mark timings with Edge TTS, Kokoro or a local Chatterbox voice.

Usage (from the project root; SKILL is this skill's folder):
    uv run --with edge-tts $SKILL/scripts/tts.py content/ch01/narration.en.json site/ch01/audio/en
    uv run --python 3.12 --with kokoro $SKILL/scripts/tts.py ...      # narration with "engine": "kokoro"
    python3 $SKILL/scripts/tts.py ...                                 # narration with "engine": "chatterbox"

The narration file names the engine ("edge", the default, or "kokoro"), the voice and the rate. Edge needs network
access. Kokoro (Apache 2.0) runs locally: its first run downloads the model (about 330 MB) and an English dictionary;
voices start with the accent's letter (am_michael, af_heart: American; bm_george, bf_emma: British) and its word
timings cover English. Both need ffmpeg/ffprobe.

Chatterbox (engine "chatterbox") speaks in a voice you saved locally. tts.py itself needs only ffmpeg; it starts
scripts/chatterbox_worker.py with the Python of the environment where Chatterbox is installed, so the model loads once
per run. The narration file adds a block:
    "engine": "chatterbox", "voice": "juan",            (voice is a label; it keys the cache)
    "chatterbox": { "voice_dir": "~/chatterbox_voice",    (folder with voice.py: speak(text, out_path, seed, temperature))
                    "python": "~/.venvs/chatterbox/bin/python", "seed": 7, "temperature": 0.6,
                    "align": "auto" }                      ("auto", "whisper" or "estimate"; see chatterbox_worker.py)
voice_dir and python may also come from CHATTERBOX_VOICE_DIR and CHATTERBOX_PYTHON. "rate" is ignored. Chatterbox
gives no word times: they come from faster-whisper when it is installed in that environment, otherwise they are
estimated, which can put a mark a fraction of a second off. [chuckle]-style tags are spoken and left out of captions.

Narration text may contain [[mark]] tags placed before a word. The output
timings.js records each clip's duration and the time (seconds) at which the
word after every mark starts, so page animations can wait for that word,
plus one caption cue per sentence. Only beats whose text or voice changed are
regenerated; spoken word times are cached, so caption and mark rules can change
without new audio.

Narration files are read as UTF-8 (a byte order mark is allowed) on every platform.
Keep beat ids and mark names ASCII: they become file names and JS keys, and are
printed to the console.

Edge sometimes answers a request with no audio (NoAudioReceived), more often for
some voices; such a beat is requested again up to MAX_ATTEMPTS times with growing
pauses. If it still gets none, a short test phrase in the same voice tells whether
the voice is unavailable or this beat's text is the problem, and the run stops.
"""

import asyncio
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path


MARK = re.compile(r"\[\[(\w+)\]\]")
MAX_ATTEMPTS = 5          # requests per beat when Edge returns no audio
FIRST_RETRY_DELAY = 2     # seconds before the second request; doubles after each failure
PROBE_TEXT = "1, 2, 3."   # checks the voice after a beat fails; any voice reads digits in its own language


async def synth(text, voice, rate, mp3_path):
    import edge_tts
    words = []  # (seconds, word)
    comm = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
    with open(mp3_path, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                words.append((chunk["offset"] / 1e7, chunk["text"]))
    return words


async def synth_with_retries(beat, text, voice, rate, mp3_path):
    """synth(), requested again after NoAudioReceived; each attempt rewrites mp3_path from the start."""
    from edge_tts.exceptions import NoAudioReceived
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return await synth(text, voice, rate, mp3_path)
        except NoAudioReceived:
            if attempt == MAX_ATTEMPTS:
                raise
            delay = FIRST_RETRY_DELAY * 2 ** (attempt - 1)
            print(f"{beat}: Edge returned no audio, retrying in {delay} s (attempt {attempt + 1} of {MAX_ATTEMPTS})",
                  file=sys.stderr, flush=True)
            await asyncio.sleep(delay)


async def explain_no_audio(beat, voice, rate, out_dir):
    """The message for a beat that ran out of attempts: whether the voice or this beat's text failed."""
    from edge_tts.exceptions import NoAudioReceived
    with tempfile.NamedTemporaryFile(dir=out_dir, prefix=".probe-", suffix=".mp3", delete=False) as f:
        probe_path = Path(f.name)
    try:
        await synth(PROBE_TEXT, voice, rate, probe_path)
        voice_works = True
    except NoAudioReceived:
        voice_works = False
    finally:
        probe_path.unlink(missing_ok=True)
    if voice_works:
        return (f"{beat}: the voice {voice} works, but Edge returned no audio for this beat's text "
                f"{MAX_ATTEMPTS} times. Look for unusual characters in it, or split the beat.")
    return (f"{beat}: the voice {voice} returned no audio {MAX_ATTEMPTS} times, and also for a short test phrase, "
            "so the voice is unavailable at Edge right now; the text is not the cause. Completed beats are saved. "
            "Rerun later, or choose another voice for the whole book (uv run --with edge-tts edge-tts --list-voices).")


async def edge_beat(beat, text, voice, rate, mp3_path, out_dir):
    from edge_tts.exceptions import NoAudioReceived
    try:
        return await synth_with_retries(beat, text, voice, rate, mp3_path)
    except NoAudioReceived:
        raise SystemExit(await explain_no_audio(beat, voice, rate, out_dir)) from None


KOKORO = {}   # one pipeline per accent, loaded once per run


def kokoro_beat(text, voice, rate, mp3_path):
    """Synthesize locally with Kokoro; word start times come from its predicted durations."""
    import numpy as np
    from kokoro import KPipeline
    lang = voice[0]
    if lang not in KOKORO:
        KOKORO[lang] = KPipeline(lang_code=lang, repo_id="hexgrad/Kokoro-82M")
    speed = 1 + float(rate.rstrip("%")) / 100 if rate else 1
    words, chunks, offset = [], [], 0.0
    for r in KOKORO[lang](text, voice=voice, speed=speed):
        audio = np.asarray(r.audio, dtype=np.float32)
        for tk in r.tokens or []:
            if tk.start_ts is not None and any(c.isalnum() for c in tk.text):
                words.append((round(offset + tk.start_ts, 3), tk.text))
        chunks.append(audio)
        offset += len(audio) / 24000
    pcm = (np.clip(np.concatenate(chunks), -1, 1) * 32767).astype("<i2").tobytes()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ar", "24000", "-ac", "1", "-i", "-",
                    "-b:a", "64k", str(mp3_path)], input=pcm, check=True)
    return words


CHATTERBOX = {}   # the running worker process, started on the first beat


def chatterbox_worker(cfg):
    if "proc" in CHATTERBOX:
        return CHATTERBOX["proc"]
    import os
    voice_dir = Path(os.path.expanduser(cfg.get("voice_dir") or os.environ.get("CHATTERBOX_VOICE_DIR", "")))
    py = os.path.expanduser(cfg.get("python") or os.environ.get("CHATTERBOX_PYTHON", "~/.venvs/chatterbox/bin/python"))
    if not (voice_dir / "voice.py").exists():
        raise SystemExit(f"chatterbox: no voice.py in {voice_dir}; set chatterbox.voice_dir or CHATTERBOX_VOICE_DIR")
    if not Path(py).exists():
        raise SystemExit(f"chatterbox: Python not found at {py}; set chatterbox.python or CHATTERBOX_PYTHON")
    proc = subprocess.Popen([py, str(Path(__file__).with_name("chatterbox_worker.py"))], text=True, bufsize=1,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE)   # stderr stays on the terminal
    proc.stdin.write(json.dumps({"voice_dir": str(voice_dir), "align": cfg.get("align", "auto"),
                                 "whisper_model": cfg.get("whisper_model", "small"),
                                 "language": cfg.get("language", "en")}) + "\n")
    proc.stdin.flush()
    if not proc.stdout.readline():
        raise SystemExit("chatterbox: the worker exited while loading the model (see the messages above)")
    CHATTERBOX["proc"] = proc
    return proc


def chatterbox_beat(beat, text, cfg, mp3_path):
    """Synthesize with the local Chatterbox voice; word times come from the worker (whisper or an estimate)."""
    proc = chatterbox_worker(cfg)
    wav = Path(mp3_path).with_suffix(".wav")
    try:
        proc.stdin.write(json.dumps({"text": text, "wav": str(wav), "seed": cfg.get("seed"),
                                     "temperature": cfg.get("temperature", 0.8)}) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        if not line:
            raise SystemExit(f"{beat}: the chatterbox worker stopped (see the messages above)")
        reply = json.loads(line)
        if "error" in reply:
            raise SystemExit(f"{beat}: chatterbox failed: {reply['error']}")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(wav), "-ac", "1", "-b:a", "96k", str(mp3_path)], check=True)
    finally:
        wav.unlink(missing_ok=True)
    print(f"{beat}: word times by {reply['method']}", file=sys.stderr, flush=True)
    return reply["words"]


def close_chatterbox():
    proc = CHATTERBOX.pop("proc", None)
    if proc:
        proc.stdin.close()
        proc.wait(timeout=30)


def split_marks(raw):
    """Return clean text and {mark: char position in clean text}."""
    marks, clean, pos = {}, [], 0
    # MARK.split alternates text, name, text, name, ...
    for i, part in enumerate(MARK.split(raw)):
        if i % 2:
            if part in marks:
                raise ValueError(f"duplicate mark {part!r} in one beat")
            marks[part] = pos
        else:
            clean.append(part)
            pos += len(part)
    return "".join(clean), marks


def time_at(clean, words):
    """Return f(pos): start time of the first spoken word at or after char pos."""
    cursor, located = 0, []
    for t, w in words:
        i = clean.find(w, cursor)
        if i < 0:
            continue
        located.append((i, t))
        cursor = i + len(w)
    return lambda pos: next((round(t, 3) for i, t in located if i >= pos), None)


def mark_times(clean, marks, words):
    at, out = time_at(clean, words), {}
    for name, pos in marks.items():
        out[name] = at(pos)
        if out[name] is None:
            raise SystemExit(f"mark {name!r} not matched to a spoken word")
    return out


def caption_cues(clean, words):
    """One cue per sentence: [start seconds, sentence]."""
    at, cues, start = time_at(clean, words), [], 0
    for m in list(re.finditer(r"[.?!]\s+", clean)) + [None]:
        end = m.end() if m else len(clean)
        text = clean[start:end].strip()
        if text:
            cues.append([at(start) or 0, text])
        start = end
    return cues


def duration(mp3):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(mp3)],
        capture_output=True, text=True, check=True)
    return round(float(r.stdout), 3)


def write_atomic(path, text):
    """Replace output only after its complete contents have been written."""
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}-", delete=False) as f:
        tmp = Path(f.name)
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


async def main(src, out_dir):
    spec = json.loads(Path(src).read_text(encoding="utf-8-sig"))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_path = Path(src).with_suffix(".timings.json")  # build cache, kept out of the site
    cache = json.loads(cache_path.read_text(encoding="utf-8-sig")) if cache_path.exists() else {}
    engine = spec.get("engine", "edge")
    voice, rate = spec["voice"], spec.get("rate", "+0%") if engine == "chatterbox" else spec["rate"]
    if engine not in ("edge", "kokoro", "chatterbox"):
        raise SystemExit(f"unknown engine {engine!r}: use edge, kokoro or chatterbox")
    cb = spec.get("chatterbox", {})
    result = {}
    public = lambda v: {k: v[k] for k in ("dur", "marks", "cues")}
    live = {b: public(v) for b, v in cache.items()
            if {"dur", "marks", "cues"} <= v.keys() and (out_dir / f"{b}.mp3").exists()}
    timings_path = out_dir / "timings.js"
    for beat, raw in spec["beats"].items():
        mp3 = out_dir / f"{beat}.mp3"
        old = cache.get(beat)
        clean, marks = split_marks(raw)
        tag = "" if engine == "edge" else f"{engine}|"   # Edge keys stay as they were, so its audio is kept
        if engine == "chatterbox":
            tag += f"{cb.get('seed')}|{cb.get('temperature', 0.8)}|"   # another seed or temperature is another take
        key = hashlib.sha1(f"{tag}{voice}|{rate}|{clean}".encode()).hexdigest()
        # Existing caches included [[marks]] in the key; reuse an unchanged old beat once,
        # then save its spoken-text key so future mark edits do not regenerate audio.
        legacy_key = hashlib.sha1(f"{voice}|{rate}|{raw}".encode()).hexdigest()
        reusable = old and old.get("key") in (key, legacy_key if engine == "edge" else key) and "words" in old and mp3.exists() and mp3.stat().st_size > 0
        tmp = None
        if reusable:
            words = old["words"]
            clip = mp3
        else:
            with tempfile.NamedTemporaryFile(dir=out_dir, prefix=f".{beat}-", suffix=".mp3", delete=False) as f:
                tmp = Path(f.name)
            clip = tmp
        try:
            if not reusable:
                words = (kokoro_beat(clean, voice, rate, clip) if engine == "kokoro"
                         else chatterbox_beat(beat, clean, cb, clip) if engine == "chatterbox"
                         else await edge_beat(beat, clean, voice, rate, clip, out_dir))
            cues = caption_cues(clean, words)
            if engine == "chatterbox":   # [chuckle]-style tags are sounds, not caption text
                cues = [[t, re.sub(r"\s*\[[\w ]+\]", "", line).strip()] for t, line in cues]
                cues = [c for c in cues if c[1]]
            entry = {"key": key, "dur": duration(clip), "words": words,
                     "marks": mark_times(clean, marks, words), "cues": cues}
            if tmp:
                tmp.replace(mp3)
                print(f"{beat}: synthesized")
            result[beat] = entry
            cache[beat] = entry
            # A later failed beat must not discard the successful beats' cache.
            write_atomic(cache_path, json.dumps(cache, indent=1))
            # Keep the successful MP3's timings consistent even if a later beat fails.
            live[beat] = public(entry)
            write_atomic(timings_path, "window.TIMINGS = " + json.dumps(live, indent=1) + ";\n")
        finally:
            if tmp:
                tmp.unlink(missing_ok=True)
    write_atomic(cache_path, json.dumps(result, indent=1))
    js = {b: public(v) for b, v in result.items()}
    write_atomic(timings_path, "window.TIMINGS = " + json.dumps(js, indent=1) + ";\n")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(0 if sys.argv[1:] in (["-h"], ["--help"]) else 2)
    try:
        asyncio.run(main(*sys.argv[1:3]))
    finally:
        close_chatterbox()
