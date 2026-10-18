"""Worker for tts.py's "chatterbox" engine. Run it with the Python of the environment that has Chatterbox installed.

    ~/.venvs/chatterbox/bin/python chatterbox_worker.py        # tts.py starts it; you do not run it by hand

It speaks a line protocol on stdin/stdout so the model loads once per run, not once per beat:
  first line   {"voice_dir": "...", "align": "auto|whisper|estimate", "whisper_model": "small", "language": "en"}
  each request {"id": 1, "text": "...", "wav": "out.wav", "seed": null, "temperature": 0.8, "chunk_words": 25,
                "seed_offset": 0}
  each reply   {"id": 1, "words": [[seconds, "word"], ...], "method": "whisper|estimate", "match": 0.97 or null,
                "chunks": 2, "truncated": [chunk numbers]} or {"id": 1, "error": "..."}
Requests are served one at a time, in order; the id is echoed so the caller can check a reply belongs to its request.

voice_dir holds a voice.py with speak(text, out_path, seed=None, temperature=0.8) that writes a wav in the saved voice.

Chatterbox Turbo stops after 1000 speech tokens (about 40 seconds) without any warning, cutting a long line off
mid-sentence. So each beat is spoken in chunks of whole sentences (at most chunk_words words), joined with a short
pause; a chunk that still comes out near the cap is reported in "truncated".

Chatterbox gives no word times, so they are found afterwards: with faster-whisper when it is installed (word times of
the spoken audio matched to the script), otherwise estimated from the text and where the speech starts and ends.
"match" is the share of script words whisper heard in order: a low value means a dropped or repeated word.
Single-bracket tags such as [chuckle] are spoken as sounds, not words, and are left out of the word list.
"""
import difflib
import json
import re
import sys

TAG = re.compile(r"^\[[\w ]+\]$")
PAUSE = {",": 3, ";": 3, ":": 3, "—": 3, "–": 3, ".": 6, "?": 6, "!": 6}   # extra weight after a token, in letters
TAG_WEIGHT = 8                                                           # a [chuckle] takes about this many letters
GAP = 0.15                      # seconds of silence between chunks
CAP_SECONDS = 38                # Turbo's 1000 speech tokens are about 40 s; a chunk this long was probably cut off


def norm(w):
    return re.sub(r"[^a-z0-9']", "", w.lower())


def script_tokens(text):
    """[(token, is_word)] for every whitespace-separated piece of the script."""
    return [(t, not TAG.match(t)) for t in re.findall(r"\S+", text)]


def chunk_text(text, max_words):
    """Split at sentence ends, then clause marks, then words; pack the pieces into chunks of at most max_words."""
    pieces = []
    for s in re.split(r"(?<=[.?!])\s+", text.strip()):
        parts = [s] if len(s.split()) <= max_words else re.split(r"(?<=[,;:])\s+", s)
        for p in parts:
            w = p.split()
            pieces += [" ".join(w[i:i + max_words]) for i in range(0, len(w), max_words)] if len(w) > max_words else [p]
    chunks, cur = [], []
    for p in pieces:
        if cur and len(" ".join(cur + [p]).split()) > max_words:
            chunks.append(" ".join(cur)); cur = []
        cur.append(p)
    if cur:
        chunks.append(" ".join(cur))
    return [c for c in chunks if c.strip()]


def estimate(wav, tokens):
    """Word start times spread over the speech by weight: letters plus a pause after punctuation."""
    import numpy as np
    import soundfile as sf
    data, sr = sf.read(wav, dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    hop = max(1, sr // 100)
    n = len(data) // hop
    env = np.sqrt((data[:n * hop].reshape(n, hop) ** 2).mean(axis=1)) if n else np.zeros(1)
    loud = np.nonzero(env > max(env.max() * 0.04, 1e-4))[0] if env.size else []
    start = loud[0] * hop / sr if len(loud) else 0.0
    end = (loud[-1] + 1) * hop / sr if len(loud) else len(data) / sr
    weights = []
    for tok, is_word in tokens:
        w = (len(norm(tok)) + 1) if is_word else TAG_WEIGHT
        w += PAUSE.get(tok[-1:], 0)
        weights.append(w)
    total = sum(weights) or 1
    out, acc = [], 0
    for (tok, is_word), w in zip(tokens, weights):
        if is_word:
            out.append([round(start + (end - start) * acc / total, 3), tok])
        acc += w
    return out


_whisper = {}


def whisper_words(wav, tokens, model_name, language):
    """(words, match): faster-whisper's word times matched to the script's words, and the share that matched.
    words is None when too little matches to trust the times."""
    from faster_whisper import WhisperModel
    if model_name not in _whisper:
        _whisper[model_name] = WhisperModel(model_name, device="cpu", compute_type="int8")
    segs, _ = _whisper[model_name].transcribe(wav, word_timestamps=True, language=language, beam_size=5)
    heard = [(norm(w.word), w.start) for s in segs for w in s.words if norm(w.word)]
    words = [t for t, is_word in tokens if is_word and norm(t)]
    if not words:
        return None, 1.0
    if not heard:
        return None, 0.0
    times = [None] * len(words)
    sm = difflib.SequenceMatcher(None, [norm(t) for t in words], [h for h, _ in heard], autojunk=False)
    for tag, a0, a1, b0, _ in sm.get_opcodes():
        if tag == "equal":
            for k in range(a1 - a0):
                times[a0 + k] = heard[b0 + k][1]
    match = sum(t is not None for t in times) / len(words)
    if match < 0.6:
        return None, match
    known = [i for i, t in enumerate(times) if t is not None]
    for i, t in enumerate(times):                       # fill gaps by interpolating between matched neighbours
        if t is not None:
            continue
        before = max((k for k in known if k < i), default=None)
        after = min((k for k in known if k > i), default=None)
        if before is None:
            times[i] = max(0.0, times[after] - 0.25 * (after - i))
        elif after is None:
            times[i] = times[before] + 0.25 * (i - before)
        else:
            times[i] = times[before] + (times[after] - times[before]) * (i - before) / (after - before)
    return [[round(t, 3), w] for t, w in zip(times, words)], round(match, 3)


def speak_chunks(speak, req):
    """Speak the text in chunks, join them into req['wav'], and return the chunk count and the numbers of any
    chunk that ran into the token cap."""
    import os
    import numpy as np
    import soundfile as sf
    texts = chunk_text(req["text"], int(req.get("chunk_words", 25)))
    if " ".join(texts).split() != req["text"].split():
        raise RuntimeError("splitting the beat into chunks changed its words or their order")
    seed = req.get("seed")
    parts, truncated, sr = [], [], 24000
    for i, t in enumerate(texts):
        path = f"{req['wav']}.{i}.wav"
        try:
            speak(t, path, seed=None if seed is None else seed + req.get("seed_offset", 0) + i,
                  temperature=req.get("temperature", 0.8))
            data, sr = sf.read(path, dtype="float32")
        finally:
            if os.path.exists(path):
                os.remove(path)
        if data.ndim > 1:
            data = data.mean(axis=1)
        if len(data) / sr >= CAP_SECONDS:
            truncated.append(i + 1)
        parts += [data, np.zeros(int(GAP * sr), dtype="float32")] if i + 1 < len(texts) else [data]
    sf.write(req["wav"], np.concatenate(parts), sr)
    return len(texts), truncated


def main():
    proto, sys.stdout = sys.stdout, sys.stderr          # library chatter goes to stderr; only replies use the protocol
    cfg = json.loads(sys.stdin.readline())
    sys.path.insert(0, cfg["voice_dir"])
    from voice import speak
    reply = lambda obj: (proto.write(json.dumps(obj) + "\n"), proto.flush())
    reply({"ready": True})
    mode = cfg.get("align", "auto")
    for line in sys.stdin:
        rid = None
        try:
            req = json.loads(line)
            rid = req.get("id")
            n, truncated = speak_chunks(speak, req)
            tokens = script_tokens(req["text"])
            words = method = match = None
            if mode in ("auto", "whisper"):
                try:
                    words, match = whisper_words(req["wav"], tokens, cfg.get("whisper_model", "small"),
                                                 cfg.get("language", "en"))
                    method = "whisper"
                except ImportError:
                    if mode == "whisper":
                        raise RuntimeError("align is 'whisper' but faster-whisper is not installed in this environment")
            if words is None:
                words, method = estimate(req["wav"], tokens), "estimate"
            reply({"id": rid, "words": words, "method": method, "match": match, "chunks": n, "truncated": truncated})
        except Exception as e:                           # report and keep serving the next beat
            reply({"id": rid, "error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    main()
