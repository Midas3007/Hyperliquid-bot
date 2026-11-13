#!/usr/bin/env python3
"""Render HEVI — "She Waits" (60 s, 1920x1080, 60 fps) entirely from glyphs.

    python3 video/render.py                 # full film -> video/out/hevi_silent.mp4 (+ HEVI.mp4, web, poster)
    python3 video/render.py --preview       # preview stills + contact sheet only
    python3 video/render.py --from 40 --to 46   # partial render -> video/out/partial_40.000-46.000.mp4
    python3 video/render.py --deliver       # only (re)build HEVI.mp4 / HEVI_web.mp4 / HEVI_poster.jpg

Deterministic: every frame depends only on its index.  The timeline is split into chunks rendered by a
process pool; each chunk is piped as raw RGB into its own libx264 encoder, then the segments are joined
losslessly with ffmpeg's concat demuxer.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from multiprocessing import get_context

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from common import W, H, FPS, NFRAMES, OUT_DIR  # noqa: E402

PREVIEW_TIMES = [3.0, 8.0, 12.0, 14.05, 17.0, 20.3, 24.0, 28.0, 32.5, 37.0, 41.0, 44.05, 45.0, 49.5, 54.5, 58.5]
SEG_DIR = os.path.join(OUT_DIR, "segments")
PREVIEW_DIR = os.path.join(OUT_DIR, "preview")
FFMPEG = "/usr/bin/ffmpeg"
FFPROBE = "/usr/bin/ffprobe"

_film = None


def _init_worker():
    global _film
    if _film is None:
        from scenes import Film
        _film = Film()


def _x264_args(path, crf=16, preset="slow"):
    return [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
            "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
            "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-threads", "2",
            "-x264-params", "keyint=120:min-keyint=1",
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
            "-video_track_timescale", "15360", "-movflags", "+faststart", path]


def render_chunk(job):
    f0, f1, path = job
    _init_worker()
    t0 = time.time()
    p = subprocess.Popen(_x264_args(path), stdin=subprocess.PIPE)
    for fi in range(f0, f1):
        p.stdin.write(_film.render(fi).tobytes())
    p.stdin.close()
    if p.wait() != 0:
        raise RuntimeError(f"ffmpeg failed for {path}")
    return f0, f1, time.time() - t0


def render_range(f0, f1, out_path, workers=4, chunk=150):
    os.makedirs(SEG_DIR, exist_ok=True)
    jobs = []
    for a in range(f0, f1, chunk):
        b = min(a + chunk, f1)
        jobs.append((a, b, os.path.join(SEG_DIR, f"seg_{a:05d}_{b:05d}.mp4")))
    t0 = time.time()
    ctx = get_context("fork")
    done = 0
    with ctx.Pool(workers, initializer=_init_worker) as pool:
        for a, b, dt in pool.imap_unordered(render_chunk, jobs):
            done += b - a
            el = time.time() - t0
            print(f"  frames {a:5d}-{b:5d} in {dt:6.1f}s   [{done}/{f1 - f0}]  elapsed {el / 60:5.1f} min", flush=True)
    lst = os.path.join(SEG_DIR, "concat.txt")
    with open(lst, "w") as fh:
        for _, _, path in jobs:
            fh.write(f"file '{path}'\n")
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                    "-i", lst, "-c", "copy", "-movflags", "+faststart", out_path], check=True)
    for _, _, path in jobs:
        os.remove(path)
    os.remove(lst)
    return time.time() - t0


def render_previews():
    from PIL import Image
    from scenes import Film
    os.makedirs(PREVIEW_DIR, exist_ok=True)
    film = Film()
    ims = []
    for t in PREVIEW_TIMES:
        fi = int(round(t * FPS))
        t0 = time.time()
        a = film.render(fi)
        im = Image.fromarray(a)
        path = os.path.join(PREVIEW_DIR, f"t{t:06.2f}.png")
        im.save(path)
        ims.append((t, im))
        print(f"  {path}  ({time.time() - t0:.2f}s)", flush=True)
    contact_sheet(ims, os.path.join(PREVIEW_DIR, "contact_sheet.png"))


def contact_sheet(ims, path, cols=4, tw=480):
    from PIL import Image, ImageDraw, ImageFont
    th = tw * H // W
    rows = (len(ims) + cols - 1) // cols
    pad, lab = 8, 22
    sheet = Image.new("RGB", (cols * (tw + pad) + pad, rows * (th + pad + lab) + pad), (12, 16, 18))
    font = ImageFont.truetype("/usr/share/fonts/truetype/jetbrains-mono/JetBrainsMono-Regular.ttf", 14)
    d = ImageDraw.Draw(sheet)
    for i, (t, im) in enumerate(ims):
        r, c = divmod(i, cols)
        x = pad + c * (tw + pad)
        y = pad + r * (th + pad + lab)
        sheet.paste(im.resize((tw, th), Image.LANCZOS), (x, y + lab))
        d.text((x, y + 3), f"t = {t:.2f} s", font=font, fill=(125, 255, 217))
    sheet.save(path)
    print(f"  {path}")


def deliver():
    silent = os.path.join(OUT_DIR, "hevi_silent.mp4")
    score = os.path.join(OUT_DIR, "score.wav")
    final = os.path.join(OUT_DIR, "HEVI.mp4")
    web = os.path.join(OUT_DIR, "HEVI_web.mp4")
    poster = os.path.join(OUT_DIR, "HEVI_poster.jpg")
    if not os.path.exists(silent):
        print("no hevi_silent.mp4 yet")
        return
    if os.path.exists(score):
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", silent, "-i", score,
                        "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "256k",
                        "-movflags", "+faststart", "-shortest", final], check=True)
        print(f"  {final}")
        has_audio = True
    else:
        print("  score.wav missing: HEVI.mp4 skipped, web version silent")
        has_audio = False
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", silent]
    if has_audio:
        cmd += ["-i", score, "-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "160k", "-shortest"]
    else:
        cmd += ["-an"]
    cmd += ["-vf", "fps=30,scale=1280:720:flags=lanczos,format=yuv420p",
            "-c:v", "libx264", "-preset", "slow", "-crf", "26", "-profile:v", "high",
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-movflags", "+faststart", web]
    subprocess.run(cmd, check=True)
    print(f"  {web}")
    # poster: the HERO moment (t = 17.0) rendered directly, JPEG quality 85
    from PIL import Image
    from scenes import Film
    film = Film()
    Image.fromarray(film.render(int(17.0 * FPS))).resize((1280, 720), Image.LANCZOS).save(
        poster, quality=85, optimize=True, progressive=True)
    print(f"  {poster}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true", help="render only the preview stills + contact sheet")
    ap.add_argument("--from", dest="t_from", type=float, default=None)
    ap.add_argument("--to", dest="t_to", type=float, default=None)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--chunk", type=int, default=150, help="frames per encoder segment")
    ap.add_argument("--deliver", action="store_true", help="only build HEVI.mp4 / web version / poster")
    ap.add_argument("--no-deliver", action="store_true")
    a = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)
    if a.preview:
        render_previews()
        return
    if a.deliver:
        deliver()
        return
    f0 = 0 if a.t_from is None else int(round(a.t_from * FPS))
    f1 = NFRAMES if a.t_to is None else int(round(a.t_to * FPS))
    full = (f0 == 0 and f1 == NFRAMES)
    out = os.path.join(OUT_DIR, "hevi_silent.mp4" if full else f"partial_{f0 / FPS:.3f}-{f1 / FPS:.3f}.mp4")
    print(f"rendering frames {f0}..{f1 - 1} -> {out}")
    el = render_range(f0, f1, out, workers=a.workers, chunk=a.chunk)
    print(f"done in {el / 60:.1f} min")
    if full:
        r = subprocess.run([FFPROBE, "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height,r_frame_rate,nb_read_frames,codec_name,pix_fmt:format=duration",
                            "-of", "default=nw=1", out], capture_output=True, text=True)
        print(r.stdout)
        if not a.no_deliver:
            deliver()


if __name__ == "__main__":
    main()
