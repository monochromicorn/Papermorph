"""Worker for tts.py's "chatterbox" engine. Run it with the Python of the environment that has Chatterbox installed.

    ~/.venvs/chatterbox/bin/python chatterbox_worker.py        # tts.py starts it; you do not run it by hand

It speaks a line protocol on stdin/stdout so the model loads once per run, not once per beat:
  first line   {"voice_dir": "...", "align": "auto|whisper|estimate", "whisper_model": "small", "language": "en"}
  each request {"text": "...", "wav": "out.wav", "seed": null, "temperature": 0.8}
  each reply   {"words": [[seconds, "word"], ...], "method": "whisper|estimate"} or {"error": "..."}

voice_dir holds a voice.py with speak(text, out_path, seed=None, temperature=0.8) that writes a wav in the saved voice.
Chatterbox gives no word times, so they are found afterwards: with faster-whisper when it is installed (word times of
the spoken audio matched to the script), otherwise estimated from the text and where the speech starts and ends.
Single-bracket tags such as [chuckle] are spoken as sounds, not words, and are left out of the word list.
"""
import difflib
import json
import re
import sys

TAG = re.compile(r"^\[[\w ]+\]$")
PAUSE = {",": 3, ";": 3, ":": 3, "—": 3, "–": 3, ".": 6, "?": 6, "!": 6}   # extra weight after a token, in letters
TAG_WEIGHT = 8                                                           # a [chuckle] takes about this many letters


def norm(w):
    return re.sub(r"[^a-z0-9']", "", w.lower())


def script_tokens(text):
    """[(token, is_word)] for every whitespace-separated piece of the script."""
    return [(t, not TAG.match(t)) for t in re.findall(r"\S+", text)]


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
    """Match faster-whisper's word times to the script's words; None when too little matches."""
    from faster_whisper import WhisperModel
    if model_name not in _whisper:
        _whisper[model_name] = WhisperModel(model_name, device="cpu", compute_type="int8")
    segs, _ = _whisper[model_name].transcribe(wav, word_timestamps=True, language=language, beam_size=5)
    heard = [(norm(w.word), w.start) for s in segs for w in s.words if norm(w.word)]
    words = [t for t, is_word in tokens if is_word and norm(t)]
    if not words or not heard:
        return None
    times = [None] * len(words)
    sm = difflib.SequenceMatcher(None, [norm(t) for t in words], [h for h, _ in heard], autojunk=False)
    for tag, a0, a1, b0, _ in sm.get_opcodes():
        if tag == "equal":
            for k in range(a1 - a0):
                times[a0 + k] = heard[b0 + k][1]
    if sum(t is not None for t in times) < 0.6 * len(words):
        return None
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
    return [[round(t, 3), w] for t, w in zip(times, words)]


def main():
    proto, sys.stdout = sys.stdout, sys.stderr          # library chatter goes to stderr; only replies use the protocol
    cfg = json.loads(sys.stdin.readline())
    sys.path.insert(0, cfg["voice_dir"])
    from voice import speak
    reply = lambda obj: (proto.write(json.dumps(obj) + "\n"), proto.flush())
    reply({"ready": True})
    mode = cfg.get("align", "auto")
    for line in sys.stdin:
        try:
            req = json.loads(line)
            speak(req["text"], req["wav"], seed=req.get("seed"), temperature=req.get("temperature", 0.8))
            tokens = script_tokens(req["text"])
            words = method = None
            if mode in ("auto", "whisper"):
                try:
                    words = whisper_words(req["wav"], tokens, cfg.get("whisper_model", "small"), cfg.get("language", "en"))
                    method = "whisper"
                except ImportError:
                    if mode == "whisper":
                        raise RuntimeError("align is 'whisper' but faster-whisper is not installed in this environment")
            if words is None:
                words, method = estimate(req["wav"], tokens), "estimate"
            reply({"words": words, "method": method})
        except Exception as e:                           # report and keep serving the next beat
            reply({"error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    main()
