import os
import re
import json
import time
import queue
import random
import difflib
import threading
from collections import deque
from itertools import chain

import requests
import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel
from piper import PiperVoice, SynthesisConfig   # needs: pip install -U piper-tts
from memory import Memory
# ---- Config ----
SERVER_URL = "http://127.0.0.1:5000/current_problem"
CONTROL_URL = "http://127.0.0.1:5000/set_mode"
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:3b"          # used for tips, free chat and choosing the next mode; "qwen2.5:1.5b" is faster
POLL_SECONDS = 0.4
TIP_TIMEOUT = 20                     # seconds to wait for qwen for each tip try
TIP_RETRIES = 2                      # extra tries before the fixed tip is used as a last resort
MODE_TIMEOUT = 15                    # seconds to wait for qwen when it picks the next mode

# Game modes and levels qwen may choose from (must match the game's dropdown values)
MODES = ["count", "add"]
LEVELS = ["easy", "medium", "hard"]

PIPER_MODEL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voices", "en_US-lessac-medium.onnx")
SYN = SynthesisConfig(length_scale=1.1)   # >1 = slower, calmer speech

WHISPER_MODEL = "base.en"            # "tiny.en" is faster
WHISPER_COMPUTE_TYPE = "int8"

# ---- Audio devices ----
# Leave these as None to pick automatically:
#   input  -> first device whose name contains INPUT_HINT, else the system default, else any input
#   output -> first device whose name contains OUTPUT_HINT, else the system default, else any output
# To force a device, set a number from sd.query_devices() (or a name string).
INPUT_DEVICE = None
OUTPUT_DEVICE = None
INPUT_HINT = "USB"                   # your USB microphone
OUTPUT_HINT = "MAX98357"             # your I2S amplifier
VOLUME = 0.6                         # 0.0 - 1.0, lower it if the amp sounds harsh or loud

# Chosen at start-up by pick_devices()
IN_DEV = None
OUT_DEV = None

# ---- Listening (shorter than before) ----
SAMPLE_RATE = 16000
BLOCK_SEC = 0.05            # smaller blocks = reacts faster
SPEECH_RMS = 0.015          # minimum volume that counts as speech (auto-raised in noisy rooms)
END_SILENCE_BLOCKS = 8      # 0.4 s of quiet ends the answer
MIN_SPEECH_BLOCKS = 3       # ignore clicks and bumps shorter than 0.15 s
MAX_WAIT_BLOCKS = 60        # stop waiting after 3 s of nobody talking
MAX_SPEECH_BLOCKS = 80      # cap one answer at 4 s

# ---- What the helper says: short, simple, calm ----
SYSTEM_PROMPT = (
    "You help a young child learn. Use very short, simple words. "
    "Say ONE sentence with 8 words or less. Be kind and calm. "
    "No idioms. Never say the answer. Give a tiny tip."
)

INTROS = ["Ready?", "New one!", "Your turn!", "Look!"]
PRAISE = ["Great job!", "You did it!", "Well done!", "Yay!"]

INTRO_QUESTIONS = {
    "count": "How many do you see?",
    "add": "How many in all?",
    "sub": "How many are left?",
    "pattern": "What color is next?",
    "odd": "Which one is different?",
    "picture": "What is this?",
    "letter": "Which letter is missing?",
}
# These types read the question from the game, because it holds the numbers.
PROMPT_TYPES = {"compare", "place"}

# Tip ideas, in order. The LLM says each one in its own words.
# If the LLM is slow or fails, the idea itself is spoken, so help always works.
TIPS = {
    "count": ["Point at each one.", "Count slowly.", "Touch each one. Say a number."],
    "add": ["Count the first group.", "Now count the next group.", "Count them all together."],
    "sub": ["Count the first group.", "Some are faded. Do not count those.", "Count the ones that are not faded."],
    "compare": ["Look at both numbers.", "Look at the first digit of each number.", "Read the question again."],
    "place": ["Look at each digit.", "Ones are on the right.", "Tens are next to the ones."],
    "pattern": ["Look at the shapes.", "The colors repeat.", "What comes after the last one?"],
    "odd": ["Look at each one.", "Which one is not like the rest?", "Take your time."],
    "picture": ["Look at the picture.", "What do you see?"],
    "letter": ["Look at the word.", "Say the word slowly.", "Which sound is missing?"],
    "default": ["Look closely.", "Take your time."],
}

FIXED_PHRASES = (
    ["Take your time.", "Here is a tip.", "Not yet.", "Good try! Tap your answer.",
     "Good try! Look again."]
    + INTROS + PRAISE + list(INTRO_QUESTIONS.values())
    + [t for tips in TIPS.values() for t in tips]
)

# Helps Whisper hear the words we expect. "help hint" is always added so it is not
# mistaken for a number word.
NUMBER_HINT = ("one two three four five six seven eight nine ten eleven twelve "
               "thirteen fourteen fifteen twenty thirty forty fifty hundred")
WHISPER_HINTS = {
    "count": NUMBER_HINT,
    "add": NUMBER_HINT,
    "sub": NUMBER_HINT,
    "compare": NUMBER_HINT,
    "place": NUMBER_HINT,
    "pattern": "red orange yellow green blue purple",
}

REPEAT_WORDS = {"again", "repeat", "pardon", "sorry", "what"}

# ---- "Help" detection ----
# Whisper often mishears a short word like "help", so we also accept close sounds.
HELP_WORDS = {"help", "helps", "helped", "helping", "halp", "held", "hint", "hints",
              "stuck", "tip", "tips", "clue", "confused"}
HELP_PHRASES = ("don't know", "dont know", "do not know", "not sure", "no idea",
                "i can't", "i cant", "i cannot", "give me a", "tell me")
HELP_FUZZY_TARGETS = ["help", "hint"]

# Round types we can check by voice with an English-only STT/TTS pipeline.
# "picture" and "letter" are Mongolian, so those are answered by tapping.
VERIFIABLE_TYPES = {"count", "add", "sub", "compare", "place", "pattern", "odd"}
NUMBER_TYPES = {"count", "add", "sub", "compare", "place"}

print("[agent] Loading models...")
whisper_model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type=WHISPER_COMPUTE_TYPE)
voice = PiperVoice.load(PIPER_MODEL)
http = requests.Session()     # reuses the connection: less delay on every poll


# ---------------- device selection ----------------
def find_device(hint, kind, preset):
    """Returns a device index (or the preset). Tries the name hint, then the default, then any device."""
    if preset is not None:
        return preset
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    devs = sd.query_devices()
    if hint:
        for i, d in enumerate(devs):
            if d[key] > 0 and hint.lower() in d["name"].lower():
                return i
    try:
        i = sd.default.device[0 if kind == "input" else 1]
        if i is not None and i >= 0 and devs[i][key] > 0:
            return i
    except Exception:
        pass
    for i, d in enumerate(devs):
        if d[key] > 0:
            return i
    return None


def pick_devices():
    global IN_DEV, OUT_DEV
    IN_DEV = find_device(INPUT_HINT, "input", INPUT_DEVICE)
    OUT_DEV = find_device(OUTPUT_HINT, "output", OUTPUT_DEVICE)
    for label, dev in (("input", IN_DEV), ("output", OUT_DEV)):
        if dev is None:
            print(f"[agent] WARNING: no {label} device found. Check 'arecord -l' and 'aplay -l'.")
        else:
            print(f"[agent] {label} device: {dev} = {sd.query_devices(dev)['name']}")


# ---------------- menu watcher ----------------
# Set while the display is on index.html (the game menu). The server clears the
# current problem when the menu opens, so "no problem" means "stay quiet".
menu_event = threading.Event()


def server_has_problem():
    """Quiet check used by the watcher thread (its own request, so no log spam)."""
    try:
        p = requests.get(SERVER_URL, timeout=2).json()
        return bool(p.get("prompt"))
    except Exception:
        return True          # server unreachable: do not change anything


def menu_watcher():
    """Runs all the time in its own thread. When the menu opens, it cuts the speech
    that is playing right now. When a game starts again, it lets the agent talk."""
    while True:
        if not server_has_problem():
            if not menu_event.is_set():
                menu_event.set()
                sd.stop()                     # cut the audio that is playing
                print("[agent] menu is open: staying quiet")
        elif menu_event.is_set():
            menu_event.clear()
            print("[agent] game started: helping again")
        time.sleep(POLL_SECONDS)


# ---------------- small audio helpers ----------------
def resample(x, src, dst):
    if src == dst or len(x) == 0:
        return x
    n = max(1, int(len(x) * dst / src))
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def rms(x):
    return float(np.sqrt(np.mean(x ** 2)))


# ---------------- speech out ----------------
_cache = {}     # text -> [(audio_int16, rate), ...]; built once so fixed phrases play instantly
_FIXED = set(FIXED_PHRASES)


def get_audio(text):
    """Yields (audio, rate) chunks for a sentence. Fixed phrases come from the cache."""
    if text in _cache:
        yield from _cache[text]
        return
    chunks = []
    for c in voice.synthesize(text, syn_config=SYN):
        item = (np.frombuffer(c.audio_int16_bytes, dtype=np.int16), c.sample_rate)
        chunks.append(item)
        yield item
    if text in _FIXED:
        _cache[text] = chunks


def warm_cache():
    for t in FIXED_PHRASES:
        try:
            list(get_audio(t))
        except Exception as e:
            print(f"[agent] cache error for '{t}': {e}")


# The output format that works is found once, then reused.
# Hardware devices (hw:) often accept only stereo, 32-bit, 44100/48000 Hz,
# so we try combinations until the device says yes.
_out_cfg = {}            # source rate -> (rate, channels, dtype)
_out_failed = False


def pick_output_format(src_rate):
    try:
        info = sd.query_devices(OUT_DEV, kind="output")
    except Exception as e:
        print(f"[agent] cannot read output device: {e}")
        return None
    max_ch = int(info["max_output_channels"])
    rates = [src_rate, int(info["default_samplerate"]), 48000, 44100, 32000, 22050, 16000]
    for rate in dict.fromkeys(rates):
        for ch in (2, 1):
            if ch > max_ch:
                continue
            for dt in ("int32", "int16", "float32"):
                try:
                    sd.check_output_settings(device=OUT_DEV, samplerate=rate, channels=ch, dtype=dt)
                    print(f"[agent] speaker format: {rate} Hz, {ch} ch, {dt}")
                    return rate, ch, dt
                except Exception:
                    continue
    return None


def convert_for_output(audio_int16, src_rate, cfg):
    rate, ch, dt = cfg
    x = audio_int16.astype(np.float32) / 32768.0
    x = resample(x, src_rate, rate)
    x = np.clip(x * VOLUME, -1.0, 0.999)
    if ch == 2:
        x = np.column_stack([x, x])
    if dt == "int32":
        return (x.astype(np.float64) * 2147483647).astype(np.int32), rate
    if dt == "int16":
        return (x * 32767).astype(np.int16), rate
    return x.astype(np.float32), rate


def play(audio, rate):
    global _out_failed
    if _out_failed or OUT_DEV is None:
        return
    cfg = _out_cfg.get(rate)
    if cfg is None:
        cfg = pick_output_format(rate)
        if cfg is None:
            _out_failed = True
            print("[agent] audio output error: the speaker accepts none of the formats tried. "
                  "Run: aplay -l   and   aplay --dump-hw-params -D hw:<card>,0 /dev/zero")
            return
        _out_cfg[rate] = cfg
    try:
        data, out_rate = convert_for_output(audio, rate, cfg)
        sd.play(data, out_rate, device=OUT_DEV)
        sd.wait()
    except Exception as e:
        print(f"[agent] audio output error: {e}")
        _out_cfg.pop(rate, None)       # try to find a format again next time


def speak_stream(sentences):
    """Speaks sentences one after another. The next one is made while the last one plays.
    `sentences` can be a list or a generator (for example, a tip coming from the LLM).
    Everything stops at once if the menu opens."""
    q = queue.Queue()

    def player():
        while True:
            item = q.get()
            if item is None:
                return
            if menu_event.is_set():
                continue                      # menu is open: drop the rest of the audio
            play(*item)

    t = threading.Thread(target=player, daemon=True)
    t.start()
    try:
        for s in sentences:
            if menu_event.is_set():
                break
            s = s.strip()
            if not s:
                continue
            print(f"[agent says] {s}")
            for item in get_audio(s):
                if menu_event.is_set():
                    break
                q.put(item)
    except Exception as e:
        print(f"[agent] TTS error: {e}")
    finally:
        q.put(None)
        t.join()
    time.sleep(0.1)    # let the speaker tail die out before the mic listens


def speak(text):
    speak_stream([text])


# ---------------- speech in ----------------
class Mic:
    """One input stream kept open. Opening it each time is slow, and it can fail on some ALSA setups."""

    def __init__(self):
        self.stream = None
        self.rate = SAMPLE_RATE
        self.threshold = SPEECH_RMS

    def open(self):
        if self.stream:
            return
        if IN_DEV is None:
            raise RuntimeError("No microphone found. Check that the USB mic shows up in 'arecord -l' "
                               "and 'lsusb', then start the agent again.")
        rates = [SAMPLE_RATE]
        try:
            rates.append(int(sd.query_devices(IN_DEV, kind="input")["default_samplerate"]))
        except Exception:
            pass
        rates += [48000, 44100]
        last = None
        for rate in dict.fromkeys(rates):       # try 16 kHz first, then the device's own rate
            try:
                s = sd.InputStream(samplerate=rate, channels=1, dtype="float32",
                                   blocksize=int(rate * BLOCK_SEC), device=IN_DEV)
                s.start()
                self.stream, self.rate = s, rate
                print(f"[agent] mic open at {rate} Hz")
                return
            except Exception as e:
                last = e
        raise RuntimeError(f"Could not open the microphone: {last}")

    def flush(self):
        n = self.stream.read_available
        if n:
            self.stream.read(n)

    def block(self):
        data, _ = self.stream.read(int(self.rate * BLOCK_SEC))
        return data.flatten()

    def calibrate(self, blocks=10):
        """Measure room noise for 0.5 s so quiet voices work and noisy rooms don't trigger."""
        self.flush()
        levels = [rms(self.block()) for _ in range(blocks)]
        self.threshold = max(SPEECH_RMS, float(np.median(levels)) * 3)
        print(f"[agent] speech threshold: {self.threshold:.4f}")

    def close(self):
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None


mic = Mic()


def listen(prompt=None):
    """Waits for a short answer. Stops 0.4 s after the child stops talking.
    Returns "" at once if the menu opens."""
    try:
        mic.flush()                       # drop anything heard while we were speaking
        pre = deque(maxlen=4)             # keep the start of the first word
        frames, started, silent, waited = [], False, 0, 0
        while True:
            if menu_event.is_set():
                return ""
            data = mic.block()
            loud = rms(data) > mic.threshold
            if started:
                frames.append(data)
                silent = 0 if loud else silent + 1
                if silent >= END_SILENCE_BLOCKS or len(frames) >= MAX_SPEECH_BLOCKS:
                    break
            elif loud:
                started = True
                frames = list(pre) + [data]
            else:
                pre.append(data)
                waited += 1
                if waited >= MAX_WAIT_BLOCKS:
                    return ""
        if len(frames) - silent < MIN_SPEECH_BLOCKS:
            return ""                     # too short, just a bump
        audio = resample(np.concatenate(frames), mic.rate, SAMPLE_RATE)
        segments, _ = whisper_model.transcribe(
            audio, language="en", beam_size=1, condition_on_previous_text=False,
            without_timestamps=True, initial_prompt=prompt,
        )
        return " ".join(s.text.strip() for s in segments).strip()
    except Exception as e:
        print(f"[agent] STT error: {e}")
        return ""


def whisper_prompt(ptype):
    base = WHISPER_HINTS.get(ptype, "")
    return (base + " help hint").strip()


# ---------------- answer checking ----------------
UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
         "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
         "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
         "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20}
TENS = {"thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
        "seventy": 70, "eighty": 80, "ninety": 90}
HOMOPHONES = {"to": 2, "too": 2, "for": 4, "won": 1, "ate": 8}
NUM_WORDS = {v: k for k, v in UNITS.items()}

COLOR_NAMES = {"🔴": "red", "🟠": "orange", "🟡": "yellow",
               "🟢": "green", "🔵": "blue", "🟣": "purple"}
COLOR_WORDS = {v: k for k, v in COLOR_NAMES.items()}


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
        elif t in TENS:
            total += TENS[t]
            found = True
        elif t == "hundred":
            total = (total or 1) * 100      # "three hundred" -> 300, "hundred" -> 100
            found = True
        elif len(tokens) <= 3 and t in HOMOPHONES:
            total += HOMOPHONES[t]
            found = True
    return total if found else None


def parse_spoken_color(text):
    for t in re.findall(r"[a-z]+", text.lower()):
        if t in COLOR_WORDS:
            return COLOR_WORDS[t]
    return None


def parse_spoken_word(text, target):
    if not target:
        return None
    if re.search(rf"\b{re.escape(str(target).lower())}\b", text.lower()):
        return target
    return None


def parse_guess(problem, heard):
    ptype = problem.get("type")
    if ptype in NUMBER_TYPES:
        return parse_spoken_number(heard)
    if ptype == "pattern":
        return parse_spoken_color(heard)
    if ptype == "odd":
        return parse_spoken_word(heard, problem.get("answer"))
    return None


def say_word(value):
    """How to say a guess or answer out loud, in simple words."""
    if isinstance(value, int):
        return NUM_WORDS.get(value, str(value))
    return COLOR_NAMES.get(value, str(value))


def wants(heard, words):
    return any(t in words for t in re.findall(r"[a-z]+", heard.lower()))


def wants_help(heard):
    """True for 'help', 'hint', 'I don't know', and sounds close to them (Whisper mishears short words)."""
    h = heard.lower()
    tokens = re.findall(r"[a-z']+", h)
    if any(t in HELP_WORDS for t in tokens):
        return True
    if any(p in h for p in HELP_PHRASES):
        return True
    return any(difflib.get_close_matches(t, HELP_FUZZY_TARGETS, n=1, cutoff=0.75)
               for t in tokens if len(t) >= 3)


# ---------------- tips ----------------
def next_tip(ptype, i):
    tips = TIPS.get(ptype, TIPS["default"])
    return tips[i % len(tips)]


# ---------------- Ollama ----------------
def ask_ollama_stream(user_prompt, timeout=30, temperature=0.6):
    buf = ""
    try:
        with http.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "system": SYSTEM_PROMPT, "prompt": user_prompt,
                  "stream": True, "keep_alive": "30m",
                  "options": {"num_predict": 32, "temperature": temperature, "num_ctx": 512}},
            stream=True, timeout=timeout,
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
    """A short task description for the LLM. It must never contain the answer."""
    ptype = problem.get("type")
    hint = problem.get("hint") or ""
    if ptype == "add":
        m = re.search(r"(\d+)\s*\+\s*(\d+)", hint)
        if m:
            return f"Add {m.group(1)} and {m.group(2)}."
        return "Add the two groups."
    if ptype == "sub":
        m = re.search(r"Start with (\d+), then take away (\d+)", hint)
        if m:
            return f"Start with {m.group(1)} and take away {m.group(2)}."
        return "Take some away. Count what is left."
    if ptype == "compare":
        return "Pick the bigger or smaller of two numbers."
    if ptype == "place":
        return "Find a digit in the ones, tens or hundreds place."
    return {
        "count": "Count the pictures.",
        "pattern": "Find which color comes next.",
        "odd": "Find the one that is different.",
        "picture": "Say what the picture is.",
        "letter": "Find the missing letter.",
    }.get(ptype, "Look at the screen.")


def reveals_answer(sentence, answer):
    if answer is None:
        return False
    s = sentence.lower()
    a = str(answer).lower()
    if a and a in s:
        return True
    if isinstance(answer, int) and NUM_WORDS.get(answer, "@@") in re.findall(r"[a-z]+", s):
        return True
    if answer in COLOR_NAMES and COLOR_NAMES[answer] in s:
        return True
    return False


def tip_reply(problem, tip_i, heard=""):
    """A tip written by OLLAMA_MODEL (qwen2.5:3b). It tries up to TIP_RETRIES + 1 times.
    A sentence is only rejected if it says the answer or is too long.
    The fixed tip is a last resort, and the terminal says FALLBACK when it is used."""
    ptype = problem.get("type")
    idea = next_tip(ptype, tip_i)
    said = f' The child said: "{heard}".' if heard else ""
    prompt = (f"Task: {describe_problem(problem)}\n"
              f"Tip idea: {idea}{said}\n"
              "Say the tip idea in your own simple words. One short sentence. "
              "Do not say any number or color that could be the answer.")
    for attempt in range(TIP_RETRIES + 1):
        if menu_event.is_set():
            return                            # menu opened: no need to make a tip
        temp = 0.5 + 0.25 * attempt           # a little more variety on each retry
        for s in ask_ollama_stream(prompt, timeout=TIP_TIMEOUT, temperature=temp):
            s = s.strip()
            if not s:
                continue
            if len(s.split()) > 12:
                print(f"[agent] tip rejected (too long): {s}")
                continue
            if reveals_answer(s, problem.get("answer")):
                print(f"[agent] tip rejected (shows the answer): {s}")
                continue
            print(f"[agent] tip made by {OLLAMA_MODEL}")
            yield s
            return
        print(f"[agent] {OLLAMA_MODEL} gave no usable tip (try {attempt + 1} of {TIP_RETRIES + 1})")
    print(f"[agent] FALLBACK: {OLLAMA_MODEL} failed, speaking the fixed tip")
    yield idea


def chat_reply(problem, heard):
    """One short, safe sentence from the LLM. Stops after the first good sentence."""
    prompt = (f"Task: {describe_problem(problem)}\n"
              f"The child said: \"{heard}\". Reply in one short sentence.")
    for s in ask_ollama_stream(prompt):
        if not reveals_answer(s, problem.get("answer")):
            yield s
            return
    yield "Good try! Tap your answer."


# ---------------- game server ----------------
def get_problem():
    try:
        p = http.get(SERVER_URL, timeout=3).json()
        return p if p.get("prompt") else None
    except Exception as e:
        print(f"[agent] Can't reach server.py ({e}). Is it running?")
        return None


def problem_key(p):
    return (p.get("type"), p.get("answer"), p.get("hint"))


def ask_question(problem):
    ptype = problem.get("type")
    if ptype in PROMPT_TYPES:
        return problem.get("prompt") or "What do you think?"
    return INTRO_QUESTIONS.get(ptype, "What do you think?")


def check_ollama():
    """Checks that Ollama is running and has the model. Prints a clear message if not."""
    base = OLLAMA_URL.rsplit("/api/", 1)[0]
    try:
        names = [m.get("name", "") for m in http.get(base + "/api/tags", timeout=3).json().get("models", [])]
    except Exception as e:
        print(f"[agent] WARNING: Ollama is not reachable at {base} ({e}). Start it with: ollama serve")
        return False
    if OLLAMA_MODEL not in names:
        print(f"[agent] WARNING: model {OLLAMA_MODEL} is not installed. Run: ollama pull {OLLAMA_MODEL}")
        print(f"[agent] installed models: {', '.join(names) or 'none'}")
        return False
    print(f"[agent] Ollama OK, model {OLLAMA_MODEL} found")
    return True

def log_round(mem, ptype, correct):
    try:
        mem.log_result(ptype, correct)
    except Exception as e:
        print(f"[agent] memory error: {e}")     # never let saving break the game


# ---------------- qwen chooses the next mode and level ----------------
def rule_pick(mem, mode, level, wrong):
    """Plain-rule choice, used when qwen is slow or gives bad JSON."""
    recent = [c for (c,) in mem.db.execute(
        "SELECT correct FROM results WHERE round_type=? ORDER BY ts DESC LIMIT 3", (mode,))]
    i = LEVELS.index(level) if level in LEVELS else 1
    if len(recent) == 3 and all(recent):
        i = min(i + 1, len(LEVELS) - 1)        # 3 clean solves in a row: harder
    elif wrong >= 2:
        i = max(i - 1, 0)                      # struggling: easier
    weak = mem.weak_spots(1)
    return (weak[0] if weak and weak[0] in MODES else mode), LEVELS[i]


def pick_next(mem, problem, wrong):
    """Asks qwen for the next mode and level. Only values from MODES and LEVELS are accepted."""
    mode = problem.get("mode") or problem.get("type")
    level = problem.get("level") or "medium"
    if mode not in MODES:
        mode = "count"
    rows = mem.db.execute(
        "SELECT round_type, SUM(correct), COUNT(*) FROM "
        "(SELECT * FROM results ORDER BY ts DESC LIMIT 30) GROUP BY round_type").fetchall()
    stats = ", ".join(f"{t}: {c}/{n} right" for t, c, n in rows) or "no data yet"
    prompt = (f"Child's recent results: {stats}. Last problem was '{mode}', "
              f"level '{level}', wrong tries: {wrong}.\n"
              f"Choose the next game mode from {MODES} and level from {LEVELS}. "
              "Practice the weaker mode. Raise the level if the child is doing well, "
              "lower it if struggling. Reply only JSON: {\"mode\": \"...\", \"level\": \"...\"}")
    try:
        r = http.post(OLLAMA_URL, json={
            "model": OLLAMA_MODEL, "prompt": prompt, "stream": False, "format": "json",
            "keep_alive": "30m",
            "options": {"num_predict": 40, "temperature": 0.3, "num_ctx": 512}},
            timeout=MODE_TIMEOUT).json()
        d = json.loads(r["response"])
        if d.get("mode") in MODES and d.get("level") in LEVELS:
            print(f"[agent] mode made by {OLLAMA_MODEL}")
            return d["mode"], d["level"]
        print(f"[agent] mode choice rejected: {d}")
    except Exception as e:
        print(f"[agent] mode choice error: {e}")
    print("[agent] FALLBACK: using rule-based mode choice")
    try:
        return rule_pick(mem, mode, level, wrong)
    except Exception as e:
        print(f"[agent] rule pick error: {e}")
        return mode, level


def choose_and_send(mem, problem, wrong):
    """Runs in a thread while the praise is spoken. The game waits for it (see /control busy)."""
    try:
        http.post(CONTROL_URL, json={"busy": True}, timeout=3)
    except Exception as e:
        print(f"[agent] could not send busy flag: {e}")
    mode, level = pick_next(mem, problem, wrong)
    print(f"[agent] chose mode={mode}, level={level}")
    try:
        http.post(CONTROL_URL, json={"mode": mode, "level": level}, timeout=3)
    except Exception as e:
        print(f"[agent] could not send mode: {e}")


def main():
    mem = Memory()
    pick_devices()
    mic.open()
    warm_cache()
    if check_ollama():
        print(f"[agent] Loading {OLLAMA_MODEL} into memory (first time can take a while)...")
        list(ask_ollama_stream("Say hi.", timeout=120))     # load it now, so the first tip is fast
    mic.calibrate()
    threading.Thread(target=menu_watcher, daemon=True).start()
    print("[agent] Ready. Waiting for problems from the game...")

    last_key, praised, wrong, tip_i, idle = None, False, 0, 0, 0
    try:
        while True:
            problem = get_problem()
            if not problem:
                last_key = None            # menu or no game: the next game counts as new
                time.sleep(POLL_SECONDS)
                continue

            if menu_event.is_set():        # the watcher has not seen the game start yet
                time.sleep(POLL_SECONDS)
                continue

            key = problem_key(problem)
            if key != last_key:
                last_key, praised, wrong, tip_i, idle = key, False, 0, 0, 0
                speak_stream([random.choice(INTROS), ask_question(problem)])
                continue

            if problem.get("solved"):
                if not praised:
                    praised = True
                    log_round(mem, problem.get("type"), wrong == 0)
                    # qwen picks the next mode and level while the praise is spoken
                    threading.Thread(target=choose_and_send,
                                     args=(mem, problem, wrong), daemon=True).start()
                    speak(random.choice(PRAISE))
                time.sleep(POLL_SECONDS)
                continue

            ptype = problem.get("type")
            heard = listen(whisper_prompt(ptype))

            if menu_event.is_set():        # the menu opened while we were listening
                continue

            # --- nobody spoke: be patient, then help gently ---
            if not heard:
                idle += 1
                if idle == 2:
                    speak("Take your time.")
                elif idle == 5:
                    speak_stream(chain(["Here is a tip."], tip_reply(problem, tip_i)))
                    tip_i += 1
                elif idle >= 8:
                    idle = 0
                    speak(ask_question(problem))
                continue
            idle = 0
            print(f"[agent heard] {heard}")

            fresh = get_problem()     # the child may have tapped the answer while talking
            if not fresh or problem_key(fresh) != key or fresh.get("solved"):
                continue

            # --- "help" always comes first, so it works even if a number was heard too ---
            if wants_help(heard):
                speak_stream(tip_reply(problem, tip_i, heard))
                tip_i += 1
                continue

            if wants(heard, REPEAT_WORDS):
                speak(ask_question(problem))
                continue

            answer = problem.get("answer")
            guess = parse_guess(problem, heard) if ptype in VERIFIABLE_TYPES else None

            if guess is not None and guess == answer:
                speak(f"Yes, {say_word(answer)}! Now tap it.")
            elif guess is not None:
                wrong += 1
                speak_stream(chain([f"I heard {say_word(guess)}.", "Not yet."],
                                   tip_reply(problem, tip_i)))
                tip_i += 1
            else:
                speak_stream(chat_reply(problem, heard))
    finally:
        mic.close()
        mem.close()


if __name__ == "__main__":
    main()
