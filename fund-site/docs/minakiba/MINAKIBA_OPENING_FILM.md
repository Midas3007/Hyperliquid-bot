# MINAKIBA: "The fund is open" — film brief for Claude Code

Make the opening film for **MINAKIBA**, the Midas × Kiyoshi naira fund on Base, announcing that the fund is open.
Two cuts from one scene: **16:9 (1920×1080)** and **9:16 (1080×1920)** for WhatsApp status and phones. Length about
**48 seconds**. An original score, rendered in code. No paid tools, no stock footage, no AI image generation: the whole
film is a canvas animation rendered frame by frame, then muxed with the score.

The bar: one continuous piece where every element **morphs** into the next. No hard cuts, nothing jumps or snaps.
Calm, premium, financial. It should feel like a sunrise over a quiet market.

---

## 1. Brand (exact)

* Night `#070B18`, Base blue `#0052FF` (light variant `#4D86FF`), Midas gold `#E9C766`, Paper `#F4F1E8`,
  Profit green `#3DDC97`, naira text `#9FB0D6`, muted `#8C98B8`.
* Fonts: Inter Display (wordmark, big lines), Inter (text), JetBrains Mono (numbers, small spaced caps labels).
  Load them before the first frame (`document.fonts.ready`).
* Wordmark `MINAKIBA`, Inter Display 600, letter-spacing 0.2em.
* **The logo** (100×100 box): a square blue block (rounded corners, radius 8), a gold base line at y 61.2, Kiyoshi's
  five-blade gold fan rising from it (pivot 50,60; radius 34; inner radius 5; 3.2° gaps), and the naira's two bars
  (y 40.2 and 47.6, height 2.6) cut across the fan in the block's blue. Reference SVG:

```js
function logo(size) {
  const cx = 50, cy = 60, R = 34, r0 = 5, n = 5, gap = 3.2, gold = '#E9C766', blue = '#0052FF'
  let d = ''
  for (let i = 0; i < n; i++) {
    const a0 = (180 + i * 180 / n + gap / 2) * Math.PI / 180, a1 = (180 + (i + 1) * 180 / n - gap / 2) * Math.PI / 180
    const p = (a, r) => (cx + r * Math.cos(a)).toFixed(2) + ',' + (cy + r * Math.sin(a)).toFixed(2)
    d += `M${p(a0, r0)} L${p(a0, R)} A${R},${R} 0 0 1 ${p(a1, R)} L${p(a1, r0)} Z `
  }
  return `<svg viewBox="0 0 100 100" width="${size}" height="${size}"><rect x="2" y="2" width="96" height="96" rx="8" fill="${blue}"/>` +
    `<path d="${d}" fill="${gold}"/><rect x="12" y="40.2" width="76" height="2.6" fill="${blue}"/>` +
    `<rect x="12" y="47.6" width="76" height="2.6" fill="${blue}"/><rect x="15" y="61.2" width="70" height="3" rx="1.5" fill="${gold}"/></svg>`
}
```

In the film, draw the logo on the canvas from this geometry (not as an image) so every part can animate separately.

## 2. Storyboard (times for the 48-second cut; keep every transition a morph)

| Time | Picture | Words (one line at a time, Inter Display, centred low) |
|---|---|---|
| 0–4 s | Black to night. A thin horizon of Base-blue light. A single gold point pulses on the beat. | — |
| 4–9 s | The point stretches into the **two naira bars**, drawn left to right and right to left. | "For months, Kiyoshi traded the naira." |
| 9–14 s | The bars settle; the **gold base line** draws under them; the **fan opens blade by blade**, one blade per beat (one koto pluck each). | "Patiently. Precisely. Around the clock." |
| 14–18 s | The **blue block** grows behind the fan, rotating from a diamond into a square, with a soft low hit. The bars become cuts in the blue. | "Now it has a home on Base." |
| 18–23 s | The logo slides aside; **MINAKIBA** writes in letter by letter on the beat; under it the roots appear one by one: **MI** Midas · **NA** Naira · **KI** Kiyoshi · **BA** Base. | — |
| 23–33 s | The logo's gold line flows into **two price lines** (cNGN on Uniswap, cNGN on Textile) that drift apart and together; each gap fills gold and a small "+$0.61 / ₦976" rises. Labels: UNISWAP, TEXTILE. Top right: "FUND NAIRA RATE ₦x / $1" (use the real rate at release, or leave it out). | "Two markets. One gap. Every gap, golden." |
| 33–40 s | The lines gather into **three cards** that morph from the line: "Deposit USDC on Base" → "Kiyoshi trades the naira" → "Profit settles into your shares every day", each with a naira value under its dollar figure. | "Dollars first. Naira underneath." |
| 40–45 s | The cards fold back into the logo, now large and centred, with a slow Base-blue glow. A gold shimmer crosses the block. | **"The fund is open."** |
| 45–48 s | End card: logo, MINAKIBA, "A MIDAS × KIYOSHI FUND ON BASE", "By invitation", fine print. Fade to black. | Fine print: "Not financial advice. Past results do not promise future returns. You can lose money." |

Optional lineage line (only with the manager's OK, and only with the fine print on screen): "From the team behind
the Midas × Kiyoshi Fund: Season 1 +501% in 3.5 days, Season 2 +150%." Both were short seasons with small pools; never
present them as a rate.

## 3. Motion rules

* One scene function `draw(t)` that renders time `t` (seconds) to a canvas; no wall-clock animation, no randomness
  without a fixed seed (every frame must be reproducible).
* Easing: smootherstep for moves, a gentle back-out for the blade and block reveals. Typical transition 0.6–1.2 s.
* Morphs: build shapes from points or paths that can be interpolated (e.g. resample the gold line to N points and
  interpolate between shapes). Nothing appears from nowhere; it grows, draws, or flows out of what is already there.
* Text never overlaps a shape. Fade lines in and out over 0.5 s; never more than one sentence on screen.
* Vignette and a very soft grain are fine. No lens flares, no shaking, no neon.
* Layout: design the 16:9 and 9:16 cuts separately (positions per aspect), not by squashing one into the other.
  On 9:16 keep text inside the middle 80% (status overlays cover the edges).

## 4. Score (original, code-generated)

* Palette the manager has approved: **Japanese koto** (Karplus-Strong pluck, bright, a little pitch bend at the attack)
  carries the melody in the Japanese In scale (A, B♭, D, E, F); a **warm fingered bass**; **hand congas** (open tones,
  slaps, mutes, slightly off the grid so they feel played); **shekere**; a soft kick; warm pads under the logo reveal.
* **No log drums** (the manager found them artificial).
* About 100 BPM, A minor / D minor colours. Structure synced to the storyboard:
  intro (koto motif alone, pad swell) → one koto pluck per fan blade → low soft hit when the block appears → the groove
  enters with the wordmark → full groove under the markets → a lift into "The fund is open" (pad and a held koto chord,
  groove drops out for one bar, then returns) → end chord rings and fades.
* Mix: reverb on everything, koto slightly right, congas spread, bass centred; normalise to **−16 LUFS, −1.5 dBTP**.
* Write it in Python (numpy, scipy), output a 48 s WAV. Keep the helper functions small and readable.

## 5. Pipeline

1. `opening.html`: a canvas page exposing `window.draw(t)`, `window.DUR`, `window.READY = document.fonts.ready`, and
   reading `?w=&h=` for the size.
2. `render.cjs` (Node + Playwright, Chromium already installed):

```js
// node render.cjs <w> <h> <out.mp4> [stills]   (stills: write JPGs at TIMES instead of a video)
const { chromium } = require('playwright'), { spawn } = require('child_process'), path = require('path')
const [w, h, out, mode] = process.argv.slice(2), FPS = 30
;(async () => {
  const browser = await chromium.launch(), page = await browser.newPage({ viewport: { width: +w, height: +h } })
  await page.goto('file://' + path.join(__dirname, process.env.SCENE || 'opening.html') + `?w=${w}&h=${h}`)
  await page.evaluate(() => window.READY)
  const DUR = await page.evaluate(() => window.DUR)
  const frame = (t) => page.evaluate((t) => { window.draw(t); return document.getElementById('c').toDataURL('image/jpeg', 0.93) }, t)
  if (mode === 'stills') {
    const fs = require('fs')
    for (const t of (process.env.TIMES || '3,8,13,17,21,28,36,43,47').split(',').map(Number))
      fs.writeFileSync(`${out}-${t}.jpg`, Buffer.from((await frame(t)).split(',')[1], 'base64'))
  } else {
    const ff = spawn('ffmpeg', ['-y', '-v', 'error', '-f', 'image2pipe', '-framerate', String(FPS), '-i', '-',
      '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '18', '-preset', 'medium', '-movflags', '+faststart', out], { stdio: ['pipe', 'inherit', 'inherit'] })
    for (let i = 0; i < FPS * DUR; i++) {
      if (!ff.stdin.write(Buffer.from((await frame(i / FPS)).split(',')[1], 'base64'))) await new Promise((r) => ff.stdin.once('drain', r))
    }
    ff.stdin.end(); await new Promise((r) => ff.on('close', r))
  }
  await browser.close()
})()
```

3. Mux: `ffmpeg -i video.mp4 -i score.wav -c:v copy -af loudnorm=I=-16:TP=-1.5:LRA=11 -c:a aac -b:a 192k -shortest -movflags +faststart minakiba-the-fund-is-open-wide.mp4`
   (and the same for `-vertical`).

## 6. Quality checks before delivering

* Render a contact sheet of stills (every storyboard beat, both aspects) at low resolution and look at it: no text
  over shapes, nothing cut off at the edges, logo geometry exact, colours exact.
* Check one mid-transition frame per morph: it must look intentional, not like two things overlapping.
* Listen to the score's first 10 seconds and the lift into "The fund is open"; the blade plucks must land on the blades.
* Final files: duration 48 s ± 0.2, 30 fps, H.264, AAC, loudness −16 LUFS. Report file sizes and durations.

## 7. Deliverables

* `minakiba-the-fund-is-open-wide.mp4` (1920×1080) and `minakiba-the-fund-is-open-vertical.mp4` (1080×1920)
* `opening.html`, `render.cjs`, `score.py` (so it can be re-rendered with new numbers)
* A poster frame (`poster.jpg`, the end card) for each aspect

Keep credit and tool use lean: build the scene, check stills once, fix, render once.
