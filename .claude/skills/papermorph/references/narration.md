# Narration for video mode

For books made to become a YouTube video, not an interactive lesson. `style.json` has `"mode": "video"` ([template](../assets/templates/style.video.json)). In video mode the chapter has no question beats, practice or score card; it plays hook to payoff and is exported with `scripts/export.py`. [authoring.md](authoring.md) still governs beat mechanics, `[[marks]]` and writing for the ear; this file decides **what the script is and how it moves**.

The model for the structure is Vox-style explainers: a claim up front, a clear arc, short beats, and something changing on screen all the time. The look stays Papermorph's own; only the script borrows from that style.

## Source material

The user may give a PDF, an outline, or a transcript of an existing video to rework. Whatever it is, the narration is **new writing**: take the ideas and facts, not the sentences. Re-sequence them into the chosen arc, and say so in `chapters/chNN.md` when you reorder or drop something. Check every fact, number and example you keep.

## The script comes before the pictures

Order of work for a video chapter:

1. Pick the arc and the hook (below).
2. Write the **beat map** in `chapters/chNN.md`: a table of `id | section | one-line purpose | feel | words`.
3. Write the narration (`content/<book>/chNN/narration.<lang>.json`).
4. **Gate: show the user the beat map and the full script and wait for approval.** Do not run `tts.py` or draw anything before then. Changing a sentence is free now and costs audio and animation later.
5. Then audio, storyboard, motion, checks and export as in [SKILL.md](../SKILL.md#chapter-loop).

## Arcs

Pick one arc for the whole video and record it in `style.json` (`narration.arc`). Recommend one from the topic and let the user confirm. Each section inside the video then runs a small arc of its own: a mini-hook, a build, a payoff.

| Arc | Use for | Shape |
|---|---|---|
| `hook_payoff` | default for a single idea | Hook, context, build, payoff, button |
| `how_it_works` | a process or system | Hook, what it is, two or three shown steps, why it matters |
| `myth_buster` | correcting a belief | The fact first, the myth, why it fails, what to believe |
| `struggle_insight` | a hard concept, a discovery | It seems fine, it breaks, it gets worse, the insight, better than before |
| `timeline` | history, "how X came to be" | Start, event, event, turning point, now, takeaway |
| `story_spine` | a narrative or a person | Once, every day, until one day, because of that, until finally |
| `listicle` | tips, rankings | Promise, item, item, the best, recap |

## The hook

The first beat is a hook, not a title card. In about 10 seconds it makes the viewer need the answer: put the payoff-promise in the first sentence. The title can appear over it or just after. Patterns:

`surprising_stat` · `direct_question` · `common_mistake` · `secret_reveal` · `pattern_interrupt` · `outcome_tease` · `bold_claim`

Never open with setup ("In this video we will…", "Today we're going to talk about…"). Don't over-promise: the payoff has to deliver what the hook said.

## Sections and beats

- A 10-minute video is about 1,400–1,600 spoken words in 4–6 sections and 40–70 beats. Plan words, not minutes: one beat is **about 20–40 words** (8–15 seconds).
- Every section opens with a short part-title beat or a one-line question, builds with lesson beats, and ends on a small payoff before the next question begins. That keeps a long video from reading as one long lecture.
- One idea per beat. Something on screen changes **every 3–5 seconds**; don't hold one picture longer than about 8 seconds. In Papermorph that means more `[[marks]]` per beat, not more beats.
- Keep `style.json` `narration.words_per_beat` as the working cap and `chapters/chNN.md` as the place the real counts are written down.

## Feel

Each beat gets a `feel` of one or two words in the beat map: `intriguing`, `brisk`, `building`, `quiet`, `wry`, `grand`, `urgent`, `warm`. Feel guides **how the sentence is written**, because the voice cannot act it out: the TTS engines have no emotion control. Say so when the user asks for more expression; the levers are:

- sentence length: short and hard for tension, longer and flowing for explanation
- punctuation: a full stop, a question mark or a dash changes the delivery
- the voice and `rate` in `style.json`
- vary the pace deliberately: a slow beat before a payoff, quicker beats while building

Keep one voice and one rate per book (see [authoring.md](authoring.md#narration)).

### Your own voice with Chatterbox

`"engine": "chatterbox"` speaks the narration in a voice saved locally with Chatterbox Turbo, with no network and no `uv`. It is the best lever here for a natural delivery: Chatterbox reads paralinguistic tags such as `[chuckle]` as sounds, so a script can laugh, and `temperature` (about 0.5 for steadier delivery) and `seed` (for a repeatable take) are set in the narration file:

```json
{ "engine": "chatterbox", "voice": "juan",
  "chatterbox": { "voice_dir": "~/personal/chatterbox_voice", "python": "~/.venvs/chatterbox/bin/python",
                  "seed": 7, "temperature": 0.6 },
  "beats": { "hook": "Money kept changing shape. [[sub]]So what is it, really? [chuckle] Let's find out." } }
```

- `voice_dir` holds a `voice.py` with `speak(text, out_path, seed, temperature)`; `python` is the environment where Chatterbox is installed. `tts.py` runs `chatterbox_worker.py` in that environment once per run, so the model loads once, not once per beat.
- `voice` is only a label that keys the cache. `rate` is ignored. Changing `seed` or `temperature` makes new takes.
- Tags in single brackets are spoken and left out of the captions. `[[marks]]` work as before.
- **Mark accuracy.** Chatterbox gives no word times. With `faster-whisper` installed in the Chatterbox environment, `tts.py` uses it to find them and a mark lands on its word. Without it the times are estimated from the text and where speech starts and ends, which in a short test put marks 0.1 to 0.4 seconds early or late; with `faster-whisper` the same marks were within about 0.1 seconds of where the speech resumed after each pause. Each beat prints `word times by whisper` or `by estimate`; if you see `estimate`, install `faster-whisper` there before animating tightly timed beats.
- Listen to every beat before the storyboard. A synthetic take can repeat or drop a word, and `lint.py` compares the script to what was generated, not to what you hear.

## Voice of the script

Set once in `style.json` as `narration.persona`, for example "a curious friend working it out with the viewer". Then:

- Talk to the viewer: "you", "let's", the occasional question and its answer.
- Concrete before abstract: the example, the picture, then the rule.
- Contrast carries interest: before and after, expected and actual, the myth and the fact.
- Plain words. No filler, no pep ("amazing", "incredible"), no announcing what you are about to say.
- Explain why, not only what, and don't talk down.
- Say symbols and units in words ([authoring.md](authoring.md#narration)).
- End a section or the video on a **button**: one short line that lands the point.

## Endings

Pick one and record it in `narration.ending`:

- `button`: the last line states the payoff and stops (default).
- `echo`: the last line mirrors the first, so the video closes the loop it opened.
- `next`: one line pointing to the next video, only if the user has one.

## Tone check at intake

Before writing the whole script, write **one beat** (the hook or the first lesson beat) in three tones, for example `casual`, `curious` and `authoritative`. Show the three side by side and let the user pick or describe a mix. Record the pick in `narration.persona` and their own words in `words`, verbatim. If they can hear a sample, generate it with `tts.py` for the chosen voice.

## YouTube extras

- **Chapters:** each section's start time is the sum of the durations of the beats before it in `timings.js`. List them as `0:00 Title` lines for the description.
- **Captions:** `export.py` writes the `.srt`; upload it with the video.
- **Title, description and a thumbnail line** can be drafted from the hook and the arc once the script is approved.
