// node render.cjs <w> <h> <out.mp4> [stills]   (stills: write PNGs at key times instead of a video)
const { chromium } = require('playwright')
const { spawn } = require('child_process')
const path = require('path')
const [w, h, out, mode] = process.argv.slice(2)
const FPS = 30
;(async () => {
  const browser = await chromium.launch()
  const page = await browser.newPage({ viewport: { width: +w, height: +h } })
  await page.goto('file://' + path.join(__dirname, process.env.SCENE || 'scene.html') + `?w=${w}&h=${h}`)
  await page.evaluate(() => document.fonts.ready)
  const DUR = await page.evaluate(() => window.DUR || 30)
  const frame = (t) => page.evaluate((t) => { window.draw(t); return document.getElementById('c').toDataURL('image/jpeg', 0.93) }, t)
  if (mode === 'stills') {
    const fs = require('fs')
    for (const t of (process.env.TIMES || '2.5,6.5,10.5,14.5,18.5,22.5,27,30.5').split(',').map(Number)) {
      fs.writeFileSync(`${out}-${t}.jpg`, Buffer.from((await frame(t)).split(',')[1], 'base64'))
    }
  } else {
    const ff = spawn('ffmpeg', ['-y', '-v', 'error', '-f', 'image2pipe', '-framerate', String(FPS), '-i', '-',
      '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '20', '-preset', 'medium', '-movflags', '+faststart', out], { stdio: ['pipe', 'inherit', 'inherit'] })
    for (let i = 0; i < FPS * DUR; i++) {
      const ok = ff.stdin.write(Buffer.from((await frame(i / FPS)).split(',')[1], 'base64'))
      if (!ok) await new Promise((r) => ff.stdin.once('drain', r))
    }
    ff.stdin.end()
    await new Promise((r) => ff.on('close', r))
  }
  await browser.close()
})()
