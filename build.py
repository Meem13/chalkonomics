"""Renders board.html + the matching voiceover into output.mp4 (frames -> sfx -> mix)."""
import glob, json, os, subprocess, wave
import numpy as np
from playwright.sync_api import sync_playwright

def dur_of(f):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", f], capture_output=True, text=True).stdout
    return float(out.strip() or 0)

os.makedirs("frames", exist_ok=True)
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1080, "height": 1920})
    pg.goto("file://" + os.path.abspath("board.html"))
    pg.wait_for_timeout(500)
    VW = pg.evaluate("window.VIDEO_W || 1080"); VH = pg.evaluate("window.VIDEO_H || 1920")
    FPS = pg.evaluate("window.FPS || 30")
    FULL = pg.evaluate("window.DURATION")
    DUR = float(os.environ.get("QUICK_SECONDS", FULL))  # QUICK_SECONDS is only for tests
    pg.set_viewport_size({"width": VW, "height": VH})
    pg.wait_for_timeout(300)
    sfx = json.loads(pg.evaluate("getSfx()"))
    # pick the mp3 whose length is closest to the animation length
    mp3s = glob.glob("*.mp3")
    voice = min(mp3s, key=lambda f: abs(dur_of(f) - FULL))
    print("voiceover:", voice, "| video", VW, "x", VH, "@", FPS, "fps,", DUR, "s", flush=True)
    n = int(DUR * FPS)
    for i in range(n):
        pg.evaluate(f"render({i / FPS})")
        pg.screenshot(path=f"frames/f_{i:05d}.jpg", type="jpeg", quality=92)
        if i % 250 == 0:
            print("frame", i, "/", n, flush=True)
    b.close()

# ---- sound effects (soft, no chalk scratching) ----
sr = 44100
N = int(DUR * sr)
rng = np.random.default_rng(7)
def bandpass(x, lo, hi):
    X = np.fft.rfft(x); f = np.fft.rfftfreq(len(x), 1 / sr)
    m = np.clip((f - lo) / (lo * 0.3 + 1), 0, 1) * np.clip((hi - f) / (hi * 0.3 + 1), 0, 1)
    return np.fft.irfft(X * m, len(x))
swish = bandpass(rng.standard_normal(sr * 2), 350, 3200); swish /= np.abs(swish).max()
out = np.zeros(N)
def add(sig, t0, gain=1.0):
    a = int(t0 * sr)
    if a >= N or a < 0: return
    s = sig[:N - a]; out[a:a + len(s)] += s * gain
def note(freq, dur, decay, amp):
    tt = np.arange(int(dur * sr)) / sr
    return amp * np.sin(2 * np.pi * freq * tt) * np.exp(-decay * tt) * np.minimum(tt / 0.003, 1)
for e in sfx:
    k = e["k"]
    if k == "ding":
        f = [1046.5, 1318.5, 1568.0][e["n"] % 3]
        add(note(f, 0.8, 5, 0.30), e["t"]); add(note(f * 2, 0.6, 8, 0.10), e["t"])
    elif k == "pop":
        d = int(0.14 * sr); tt = np.arange(d) / sr
        ph = 2 * np.pi * np.cumsum(380 + 520 * (tt / 0.14)) / sr
        add(0.30 * np.sin(ph) * np.exp(-20 * tt) * np.minimum(tt / 0.004, 1), e["t"])
    elif k == "sp":
        for i, f in enumerate([2093, 2637, 3136]): add(note(f, 0.35, 10, 0.08), e["t"] + 0.05 * i)
    elif k == "wh":
        d = int(0.55 * sr); tt = np.arange(d) / sr
        add(swish[:d] * np.sin(np.pi * tt / 0.55) ** 2 * 0.22, e["t"] + 0.1)
    elif k == "end":
        for i, f in enumerate([523.25, 659.25, 783.99, 1046.5]):
            add(note(f, 1.4, 2.8, 0.28), e["t"] + 0.13 * i); add(note(f * 2, 1.0, 4, 0.07), e["t"] + 0.13 * i)
out = np.tanh(out * 1.1); out *= 0.85 / max(np.abs(out).max(), 1e-6)
with wave.open("sfx.wav", "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
    w.writeframes((out * 32767).astype(np.int16).tobytes())

# ---- mix voice + sfx and encode ----
subprocess.run([
    "ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", "frames/f_%05d.jpg",
    "-i", voice, "-i", "sfx.wav",
    "-filter_complex",
    f"[1:a]loudnorm=I=-16:TP=-1.5:LRA=11,apad[v];[2:a]volume=0.28,apad[s];"
    f"[v][s]amix=inputs=2:duration=longest:normalize=0,atrim=0:{DUR}[a]",
    "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-t", str(DUR),
    "-movflags", "+faststart", "output.mp4"], check=True)
print("done: output.mp4")
