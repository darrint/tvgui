import os
import queue
import re
import subprocess
import sys
import threading
import time
import wave

from attendance import IN, OUT

PIPER_BIN = os.environ.get("TVGUI_PIPER", "/usr/local/bin/piper")
PIPER_MODEL = os.environ.get(
    "TVGUI_PIPER_MODEL",
    "/usr/local/share/tvgui/voices/en_US-bryce-medium.onnx",
)
PIPER_RATE = 22050
PIPER_SILENCE = 0.2
READY_PHRASE = "Badge system for Team Roboto 4 4 7 ready"
PAUSE_SECS = 0.35
PROMPTS = {
    "_ready": READY_PHRASE,
    "_welcome-mentor": "Welcome mentor",
    "_welcome-student": "Welcome student",
    "_welcome-parent": "Welcome parent",
    "_goodbye-mentor": "good bye mentor",
    "_goodbye-student": "good bye student",
    "_goodbye-parent": "good bye parent",
    "_enrolled": "enrolled",
}
_AUDIO_RE = re.compile(rb"audio=([0-9.]+) sec")
_lock = threading.Lock()
_piper = None
_piper_err = queue.Queue()
_piper_buf = bytearray()
_piper_cv = threading.Condition()


def tts_dir():
    if os.path.isdir("/var/lib/tvgui"):
        path = "/var/lib/tvgui/tts"
    else:
        path = os.path.join("/tmp", "tvgui-tts")
    os.makedirs(path, exist_ok=True)
    return path


def _safe(username):
    return "".join(c if c.isalnum() else "_" for c in (username or "")) or "user"


def wav_path(username, direction):
    return os.path.join(tts_dir(), f"{_safe(username)}-{direction}.wav")


def name_wav_path(username):
    return os.path.join(tts_dir(), f"{_safe(username)}-name.wav")


def prompt_wav_path(name):
    return os.path.join(tts_dir(), f"{name}.wav")


def ready_wav_path():
    return prompt_wav_path("_ready")


def greet_phrase(member, direction):
    lead = "Welcome" if direction == IN else "good bye"
    return f"{lead} {member.role}. {member.pronounce}"


def lead_prompt(member, direction):
    prefix = "_welcome" if direction == IN else "_goodbye"
    return f"{prefix}-{member.role}"


def _pw_env():
    env = os.environ.copy()
    runtime = env.get("XDG_RUNTIME_DIR") or "/run/user/1001"
    env.setdefault("XDG_RUNTIME_DIR", runtime)
    env.setdefault("PIPEWIRE_RUNTIME_DIR", runtime)
    env.setdefault("PULSE_SERVER", f"unix:{runtime}/pulse/native")
    env.setdefault("OMP_NUM_THREADS", "4")
    return env


def play_wav(path):
    if not path or not os.path.isfile(path):
        return False
    subprocess.run(
        ["pw-play", "--latency", "20ms", "--target", "alsa-hdmi", path],
        env=_pw_env(),
        check=False,
    )
    return True


def _write_wav(path, pcm):
    tmp = path + ".tmp"
    with wave.open(tmp, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(PIPER_RATE)
        w.writeframes(pcm)
    os.replace(tmp, path)


def _pcm_from_wav(path):
    with wave.open(path, "rb") as w:
        return w.readframes(w.getnframes())


def _silence(secs):
    n = int(round(secs * PIPER_RATE)) * 2
    n -= n % 2
    return b"\x00" * max(0, n)


def _stitch(path, wavs):
    parts = []
    for i, wav in enumerate(wavs):
        if i:
            parts.append(_silence(PAUSE_SECS))
        parts.append(_pcm_from_wav(wav))
    _write_wav(path, b"".join(parts))
    print(f"tts: stitched {path}", flush=True)


def _ensure_piper():
    global _piper
    if _piper and _piper.poll() is None:
        return True
    binary = os.path.realpath(PIPER_BIN)
    if not os.path.isfile(binary) or not os.path.isfile(PIPER_MODEL):
        return False
    env = os.environ.copy()
    env.setdefault("OMP_NUM_THREADS", "4")
    _piper = subprocess.Popen(
        [
            binary,
            "-m",
            PIPER_MODEL,
            "--output_raw",
            "--sentence_silence",
            str(PIPER_SILENCE),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
        env=env,
    )

    def _read_err():
        while True:
            line = _piper.stderr.readline()
            if not line:
                break
            m = _AUDIO_RE.search(line)
            if m:
                _piper_err.put(float(m.group(1)))
            else:
                sys.stderr.buffer.write(line)
                sys.stderr.buffer.flush()

    def _read_out():
        while True:
            chunk = _piper.stdout.read(8192)
            if not chunk:
                break
            with _piper_cv:
                _piper_buf.extend(chunk)
                _piper_cv.notify_all()

    threading.Thread(target=_read_err, daemon=True).start()
    threading.Thread(target=_read_out, daemon=True).start()
    return True


def _synth_piper(phrase):
    if not _ensure_piper():
        return None
    with _piper_cv:
        start = len(_piper_buf)
    _piper.stdin.write((" ".join(phrase.split()) + "\n").encode())
    _piper.stdin.flush()
    try:
        secs = _piper_err.get(timeout=90)
    except queue.Empty:
        print("piper: timed out", flush=True)
        return None
    n = int(round((secs + PIPER_SILENCE) * PIPER_RATE * 2))
    n -= n % 2
    if n <= 0:
        return None
    deadline = time.monotonic() + 30
    with _piper_cv:
        while len(_piper_buf) - start < n:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                print(
                    f"piper: short audio have={len(_piper_buf) - start} want={n}",
                    flush=True,
                )
                break
            _piper_cv.wait(remaining)
        data = bytes(_piper_buf[start : start + n])
    return data or None


def render_phrase(phrase, path):
    with _lock:
        pcm = _synth_piper(phrase)
        if not pcm:
            return False
        _write_wav(path, pcm)
        print(f"tts: wrote {path} ({len(pcm)} bytes)", flush=True)
        return True


def enrolled_phrase(member):
    return f"{member.pronounce} enrolled"


def enrolled_wav_path(username):
    return os.path.join(tts_dir(), f"{_safe(username)}-enrolled.wav")


def _ensure_phrase(path, phrase, force=False):
    if not force and os.path.isfile(path):
        return "skip"
    if render_phrase(phrase, path):
        return "wrote"
    return "fail"


def stitch_member(member):
    name = name_wav_path(member.username)
    welcome = prompt_wav_path(lead_prompt(member, IN))
    goodbye = prompt_wav_path(lead_prompt(member, OUT))
    enrolled = prompt_wav_path("_enrolled")
    for path in (name, welcome, goodbye, enrolled):
        if not os.path.isfile(path):
            return False
    _stitch(wav_path(member.username, IN), (welcome, name))
    _stitch(wav_path(member.username, OUT), (goodbye, name))
    _stitch(enrolled_wav_path(member.username), (name, enrolled))
    return True


def render_member(member, force=False):
    ok = True
    if (
        _ensure_phrase(name_wav_path(member.username), member.pronounce, force)
        == "fail"
    ):
        ok = False
    for direction in (IN, OUT):
        key = lead_prompt(member, direction)
        if _ensure_phrase(prompt_wav_path(key), PROMPTS[key], force=False) == "fail":
            ok = False
    if _ensure_phrase(prompt_wav_path("_enrolled"), PROMPTS["_enrolled"], force=False) == "fail":
        ok = False
    if ok:
        ok = stitch_member(member)
    return ok


def play_greet(member, direction):
    path = wav_path(member.username, direction)
    if not os.path.isfile(path):
        render_member(member, force=False)
    if play_wav(path):
        return
    with _lock:
        pcm = _synth_piper(greet_phrase(member, direction))
        if pcm:
            _play_raw(pcm)


def _play_raw(pcm):
    subprocess.run(
        [
            "pw-play",
            "--raw",
            "--rate",
            str(PIPER_RATE),
            "--channels",
            "1",
            "--format",
            "s16",
            "--latency",
            "20ms",
            "--target",
            "alsa-hdmi",
            "-",
        ],
        input=pcm,
        env=_pw_env(),
        check=False,
    )


def say(phrase):
    with _lock:
        try:
            pcm = _synth_piper(phrase)
            if not pcm:
                print("piper: no audio", flush=True)
                return
            _play_raw(pcm)
        except OSError as e:
            print(f"say: {e}", flush=True)


def say_ready():
    path = ready_wav_path()
    if not os.path.isfile(path):
        render_phrase(READY_PHRASE, path)
    if not play_wav(path):
        say(READY_PHRASE)


def backfill(store, force=False):
    wrote = skipped = failed = 0
    here = {m.username for m, _ in store.who()}
    people = store.people()
    people.sort(key=lambda m: 0 if m.username in here else 1)
    jobs = [(prompt_wav_path(name), phrase) for name, phrase in PROMPTS.items()]
    seen = set()
    for member in people:
        path = name_wav_path(member.username)
        if path in seen:
            continue
        seen.add(path)
        jobs.append((path, member.pronounce))
    for path, phrase in jobs:
        try:
            result = _ensure_phrase(path, phrase, force)
        except Exception as e:
            failed += 1
            print(f"tts backfill {path}: {e}", flush=True)
            continue
        if result == "wrote":
            wrote += 1
        elif result == "skip":
            skipped += 1
        else:
            failed += 1
    stitched = 0
    for member in people:
        try:
            if stitch_member(member):
                stitched += 1
            else:
                failed += 1
        except Exception as e:
            failed += 1
            print(f"tts stitch {member.username}: {e}", flush=True)
    print(
        f"tts backfill wrote={wrote} skipped={skipped} stitched={stitched} failed={failed} dir={tts_dir()}",
        flush=True,
    )
    return wrote, skipped, failed
