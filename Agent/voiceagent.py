import os
import re
import json
import time
import queue
import random
import threading
from collections import deque
from math import gcd

import requests
import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel
from piper import PiperVoice, SynthesisConfig   # needs: pip install -U piper-tts

try:
    from scipy.signal import resample_poly      # optional: pip install scipy
except ImportError:
    resample_poly = None

# ---- Config ----
SERVER_URL = "http://127.0.0.1:5000/current_problem"
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:3b"          # only used for free chat now; 1.5b is faster
POLL_SECONDS = 0.4

PIPER_MODEL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voices", "en_US-lessac-medium.onnx")
SYN = SynthesisConfig(length_scale=1.1)   # >1 = slower speech

WHISPER_MODEL = "base.en"            # "tiny.en" is faster
WHISPER_COMPUTE_TYPE = "int8"

# Listening: stop as soon as the child stops talking (no fixed 4 s wait)
SAMPLE_RATE = 16000         # rate Whisper expects (mic may use a different one, see pick_input_rate)
BLOCK_SEC = 0.1
SPEECH_RMS = 0.015          # raise if room noise triggers it, lower if it misses quiet voices
END_SILENCE_BLOCKS = 6      # 0.6 s of silence ends the utterance
MAX_WAIT_BLOCKS = 40        # give up if nobody speaks for 4 s
MAX_SPEECH_BLOCKS = 60      # cap at 6 s

# Optional: force a specific input device index (see sd.query_devices()). None = system default.
INPUT_DEVICE = None
# Same for the speaker. None = system default.
OUTPUT_DEVICE = None

SYSTEM_PROMPT = (
    "You are a warm, cheerful learning helper for a young child. "
    "Reply in ONE short, simple sentence. Never give the answer "
    "to the problem. Just encourage and give a tiny tip."
)

# Instant replies: no LLM, no waiting
INTROS = ["Ready? Let's go!", "Here's a new one!", "Okay, your turn!", "Take a look!"]
PRAISE = ["Great job!", "You got it!", "Awesome work!", "Well done!", "Super!"]
TRY_AGAIN = ["Good try! Let's look again, slowly.", "Almost! Try once more.",
             "Not quite. Take another careful look."]

# What the assistant asks out loud when a new round of each type appears.
INTRO_QUESTIONS = {
    "count": "How many pictures do you see?",
    "add": "How many are there altogether?",
    "pattern": "What comes next in the pattern?",
    "odd": "Which one is different from the rest?",
    "picture": "What is the name of this picture?",
    "letter": "Which letter is missing?",
}

# Round types where we can reliably check a *spoken* answer with an
# English-only STT/TTS pipeline. "picture" and "letter" are Mongolian
# vocabulary/letters, so we don't try to verify those by voice — see the
# note in parse_guess() below.
VERIFIABLE_TYPES = {"count", "add", "pattern", "odd"}

print("[agent] Loading models...")
whisper_model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type=WHISPER_COMPUTE_TYPE)
voice = PiperVoice.load(PIPER_MODEL)   # loaded ONCE, not on every sentence


# ---------------- speaker setup: find a format/rate/channels the hardware accepts ----------------
_OUT_CFG = {}             # source sample rate -> (rate, channels, dtype)


def resample(audio, from_sr, to_sr):
    """Resample mono float32 audio."""
    if from_sr == to_sr:
        return audio
    if resample_poly is not None:
        g = gcd(to_sr, from_sr)
        return resample_poly(audio, to_sr // g, from_sr // g).astype(np.float32)
    n = int(len(audio) * to_sr / from_sr)
    return np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)


def pick_output_config(src_sr):
    """Find (rate, channels, dtype) the output device accepts."""
    if src_sr in _OUT_CFG:
        return _OUT_CFG[src_sr]

    rates = [src_sr]
    try:
        info = sd.query_devices(OUTPUT_DEVICE, "output")
        rates.append(int(info["default_samplerate"]))
    except Exception:
        pass
    rates += [48000, 44100, 32000, 22050, 16000]

    seen = set()
    for sr in rates:
        if sr in seen:
            continue
        seen.add(sr)
        for dt in ("float32", "int16"):
            for ch in (1, 2):
                try:
                    sd.check_output_settings(device=OUTPUT_DEVICE, samplerate=sr, channels=ch, dtype=dt)
                    _OUT_CFG[src_sr] = (sr, ch, dt)
                    print(f"[agent] Speaker: {sr} Hz, {ch} ch, {dt}")
                    return _OUT_CFG[src_sr]
                except Exception:
                    continue
    raise RuntimeError("No supported speaker format found. Check sd.query_devices() / `aplay -l`.")


def play_audio(int16_audio, src_sr):
    """Convert Piper's int16 audio to something the speaker accepts, then play it."""
    try:
        sr, ch, dt = pick_output_config(src_sr)
        audio = int16_audio.astype(np.float32) / 32768.0
        audio = resample(audio, src_sr, sr)
        if ch == 2:
            audio = np.column_stack([audio, audio])
        if dt == "int16":
            audio = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
        else:
            audio = audio.astype(np.float32)
        sd.play(audio, sr, device=OUTPUT_DEVICE)
        sd.wait()
    except Exception as e:
        print(f"[agent] Playback error: {e}")


# ---------------- speech out: synthesize next sentence while the last one plays ----------------
def speak_stream(sentences):
    q = queue.Queue()

    def player():
        while True:
            item = q.get()
            if item is None:
                return
            play_audio(item[0], item[1])

    t = threading.Thread(target=player, daemon=True)
    t.start()
    try:
        for s in sentences:
            s = s.strip()
            if not s:
                continue
            print(f"[agent says] {s}")
            for chunk in voice.synthesize(s, syn_config=SYN):
                q.put((np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16), chunk.sample_rate))
    except Exception as e:
        print(f"[agent] TTS error: {e}")
    finally:
        q.put(None)
        t.join()
    time.sleep(0.15)   # let the speaker tail die out before the mic opens


def speak(text):
    speak_stream([text])


# ---------------- mic setup: find a sample rate the hardware accepts ----------------
_INPUT_SR = None          # cached rate the mic actually supports


def pick_input_rate():
    """Find a sample rate the input device accepts (tries 16 kHz first)."""
    global _INPUT_SR
    if _INPUT_SR:
        return _INPUT_SR

    candidates = [SAMPLE_RATE]
    try:
        info = sd.query_devices(INPUT_DEVICE, "input")
        candidates.append(int(info["default_samplerate"]))
    except Exception:
        pass
    candidates += [48000, 44100, 32000, 22050, 8000]

    seen = set()
    for sr in candidates:
        if sr in seen:
            continue
        seen.add(sr)
        try:
            sd.check_input_settings(device=INPUT_DEVICE, samplerate=sr, channels=1, dtype="float32")
            _INPUT_SR = sr
            print(f"[agent] Mic sample rate: {sr} Hz")
            return sr
        except Exception:
            continue
    raise RuntimeError("No supported mic sample rate found. Check `arecord -l` and sd.query_devices().")


def to_16k(audio, sr):
    """Resample mono float32 audio from `sr` to 16 kHz for Whisper."""
    if sr == SAMPLE_RATE:
        return audio
    if resample_poly is not None:
        g = gcd(SAMPLE_RATE, sr)
        return resample_poly(audio, SAMPLE_RATE // g, sr // g).astype(np.float32)
    # fallback without scipy: linear interpolation
    n = int(len(audio) * SAMPLE_RATE / sr)
    return np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)


# ---------------- speech in: record until the child stops talking ----------------
def listen():
    try:
        in_sr = pick_input_rate()
    except Exception as e:
        print(f"[agent] STT error: {e}")
        time.sleep(2)             # don't spin in a tight error loop
        return ""

    block = int(in_sr * BLOCK_SEC)
    pre = deque(maxlen=3)          # keep the start of the first word
    frames, started, silent, waited = [], False, 0, 0
    try:
        with sd.InputStream(device=INPUT_DEVICE, samplerate=in_sr, channels=1,
                            dtype="float32", blocksize=block) as stream:
            while True:
                data, _ = stream.read(block)
                data = data.flatten()
                loud = float(np.sqrt(np.mean(data ** 2))) > SPEECH_RMS
                if loud:
                    if not started:
                        frames.extend(pre)
                    started, silent = True, 0
                elif started:
                    silent += 1
                else:
                    waited += 1
                    pre.append(data)
                if started:
                    frames.append(data)
                    if silent >= END_SILENCE_BLOCKS or len(frames) >= MAX_SPEECH_BLOCKS:
                        break
                elif waited >= MAX_WAIT_BLOCKS:
                    return ""
        audio = to_16k(np.concatenate(frames), in_sr)
        segments, _ = whisper_model.transcribe(
            audio, language="en", beam_size=1, condition_on_previous_text=False,
            initial_prompt="one two three four five six seven eight nine ten eleven twelve",
        )
        return " ".join(s.text.strip() for s in segments).strip()
    except Exception as e:
        print(f"[agent] STT error: {e}")
        time.sleep(1)             # backoff so errors don't flood the log
        return ""


# ---------------- answer checking: numbers (math) ----------------
UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
         "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
         "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
         "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20}
HOMOPHONES = {"to": 2, "too": 2, "for": 4, "won": 1, "ate": 8}
NUM_WORDS = {v: k for k, v in UNITS.items()}


def parse_spoken_number(text):
    digits = re.findall(r"\d+", text)
    if digits:
        return int(digits[0])
    tokens = re.findall(r"[a-z]+", text.lower())
    total, found = 0, False
    for t in tokens:
        if t in UNITS:
            total += UNITS[t]
            found = True
        elif len(tokens) <= 3 and t in HOMOPHONES:
            total += HOMOPHONES[t]
            found = True
    return total if found else None


# ---------------- answer checking: colors (logic pattern round) ----------------
COLOR_NAMES = {
    "🔴": "red", "🟠": "orange", "🟡": "yellow",
    "🟢": "green", "🔵": "blue", "🟣": "purple",
}
COLOR_WORDS = {v: k for k, v in COLOR_NAMES.items()}


def parse_spoken_color(text):
    """Returns the matching color emoji if the child names a color, else None."""
    tokens = re.findall(r"[a-z]+", text.lower())
    for t in tokens:
        if t in COLOR_WORDS:
            return COLOR_WORDS[t]
    return None


# ---------------- answer checking: plain words (logic odd-one-out round) ----------------
def parse_spoken_word(text, target):
    """Returns `target` if the child's speech contains that exact word, else None.

    Only meaningful for English-language answers (the logic game's
    odd-one-out words are English). Not used for Mongolian content.
    """
    if not target:
        return None
    if re.search(rf"\b{re.escape(target.lower())}\b", text.lower()):
        return target
    return None


def parse_guess(problem, heard):
    """Dispatches to the right parser for this round's type.

    Returns a value comparable to problem['answer'], or None if we
    couldn't (or shouldn't try to) verify what was said.
    """
    ptype = problem.get("type")
    if ptype in ("count", "add"):
        return parse_spoken_number(heard)
    if ptype == "pattern":
        return parse_spoken_color(heard)
    if ptype == "odd":
        return parse_spoken_word(heard, problem.get("answer"))
    # "picture" and "letter": Mongolian vocabulary/letters — the English
    # STT here can't reliably transcribe these, so we don't attempt to
    # verify. The child still answers by tapping; this just skips to the
    # general encouragement/chat branch below.
    return None


# ---------------- Ollama: stream, yield each sentence as soon as it's done ----------------
def ask_ollama_stream(user_prompt):
    buf = ""
    try:
        with requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "prompt": f"{SYSTEM_PROMPT}\n\n{user_prompt}",
                  "stream": True, "keep_alive": "30m",
                  "options": {"num_predict": 40, "temperature": 0.7, "num_ctx": 1024}},
            stream=True, timeout=30,
        ) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                buf += json.loads(line).get("response", "")
                parts = re.split(r"(?<=[.!?])\s+", buf)
                if len(parts) > 1:
                    for p in parts[:-1]:
                        yield p
                    buf = parts[-1]
    except Exception as e:
        print(f"[agent] Ollama error: {e}")
    if buf.strip():
        yield buf


def describe_problem(problem):
    """Plain-English description of the current task, used in LLM prompts."""
    ptype = problem.get("type")
    if ptype == "add":
        m = re.search(r"(\d+)\s*\+\s*(\d+)", problem.get("hint") or "")
        if m:
            return f"Add {m.group(1)} and {m.group(2)} to find the total number of pictures."
        return "Add the two groups of pictures together."
    if ptype == "count":
        return "Count how many pictures are on the screen."
    if ptype == "pattern":
        return "Look at the repeating pattern of colored shapes and figure out which color comes next."
    if ptype == "odd":
        return "Look at the group of pictures and find the one that does not belong with the rest."
    if ptype == "picture":
        return "Look at the picture on screen and try to say what it is called."
    if ptype == "letter":
        return "Look at the word with a missing letter and figure out which letter is missing."
    return "Look at the problem on the screen and think it through."


def reveals_answer(sentence, answer):
    """Safety net so hint sentences never blurt out the actual answer."""
    if answer is None:
        return False
    s = sentence.lower()
    answer_str = str(answer).lower()
    if answer_str and answer_str in s:
        return True
    if isinstance(answer, int) and NUM_WORDS.get(answer, "@@") in re.findall(r"[a-z]+", s):
        return True
    if answer in COLOR_NAMES and COLOR_NAMES[answer] in s:
        return True
    return False


def tip_sentences(task, guess, wrong, tips_given, answer):
    yield "Good try!"                       # instant, so there's no silence
    prompt = (
        f"Task: {task}\nThe child guessed \"{guess}\", which is wrong (attempt {wrong}). "
        f"Give ONE new tip that is different from these earlier tips: {' | '.join(tips_given) or 'none'}. "
        "Try a different idea each time, such as looking closely, comparing one at a time, "
        "or thinking about what's the same and what's different. Do not say the answer."
    )
    said = []
    for s in ask_ollama_stream(prompt):
        if reveals_answer(s, answer):       # safety net: never speak the answer
            continue
        said.append(s)
        yield s
    if said:
        tips_given.append(" ".join(said))
    else:
        yield TRY_AGAIN[(wrong - 1) % len(TRY_AGAIN)]   # fallback if the LLM fails


def get_problem():
    try:
        p = requests.get(SERVER_URL, timeout=3).json()
        return p if p.get("prompt") else None
    except Exception as e:
        print(f"[agent] Can't reach server.py ({e}). Is it running?")
        return None


def problem_key(p):
    # the prompt text is the same every round, so identify a round by its content
    return (p.get("type"), p.get("answer"), p.get("hint"))


def main():
    # warm the LLM in the background so the first chat reply isn't slow
    threading.Thread(target=lambda: list(ask_ollama_stream("Say hi.")), daemon=True).start()

    # check the mic once at startup so problems show up immediately
    try:
        pick_input_rate()
    except Exception as e:
        print(f"[agent] Mic problem: {e}")

    print("[agent] Ready. Waiting for problems from the game...")

    last_key, praised, wrong, tips_given = None, False, 0, []
    while True:
        problem = get_problem()
        if not problem:
            time.sleep(POLL_SECONDS)
            continue

        key = problem_key(problem)
        if key != last_key:
            last_key, praised, wrong, tips_given = key, False, 0, []
            question = INTRO_QUESTIONS.get(problem.get("type"), "What do you think?")
            speak(random.choice(INTROS) + " " + question)
            continue

        if problem.get("solved"):
            if not praised:
                praised = True
                speak(random.choice(PRAISE))
            time.sleep(POLL_SECONDS)
            continue

        heard = listen()          # returns quickly when the child stops talking
        if not heard:
            continue
        print(f"[agent heard] {heard}")

        fresh = get_problem()     # the child may have tapped the answer while talking
        if not fresh or problem_key(fresh) != key or fresh.get("solved"):
            continue

        answer = problem.get("answer")
        guess = parse_guess(problem, heard) if problem.get("type") in VERIFIABLE_TYPES else None

        if guess is not None and guess == answer:
            spoken_answer = COLOR_NAMES.get(answer, answer)
            speak(f"Yes, {spoken_answer}! Now tap it on the screen.")
        elif guess is not None:
            wrong += 1
            speak_stream(tip_sentences(describe_problem(problem), guess, wrong, tips_given, answer))
        else:
            task = describe_problem(problem)
            speak_stream(ask_ollama_stream(
                f"Task: {task}\nThe child said: \"{heard}\". Reply kindly and help them with the task. Do not say the answer."))


if __name__ == "__main__":
    main()