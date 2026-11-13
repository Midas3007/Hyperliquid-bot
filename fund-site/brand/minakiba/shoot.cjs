// node shoot.cjs <outdir>: one PNG per design sheet in design.html
const { chromium } = require('playwright')
const path = require('path')
;(async () => {
  const out = process.argv[2] || __dirname
  const b = await chromium.launch()
  const p = await b.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 2 })
  await p.goto('file://' + path.join(__dirname, 'design.html'))
  await p.evaluate(() => document.fonts.ready)
  for (const [id, name] of [['brand', '1-brand'], ['landing', '2-site'], ['dash', '3-dashboard'], ['account', '4-account'], ['phone', '5-phone']]) {
    await p.locator('#' + id).screenshot({ path: path.join(out, `minakiba-${name}.png`) })
  }
  await b.close()
})()
