// node shoot-variants.cjs: each flyer in variants.html -> PNG (2x) and a sharing JPG
const { chromium } = require('playwright'), path = require('path'), fs = require('fs')
;(async () => {
  const qr = fs.readFileSync(process.argv[2], 'utf8').replace(/width="[^"]+" height="[^"]+"/, '').replace('<path ', '<path fill="#111" ')
  const b = await chromium.launch(), p = await b.newPage({ viewport: { width: 1754, height: 1754 }, deviceScaleFactor: 2 })
  await p.goto('file://' + path.join(__dirname, 'variants.html')); await p.evaluate(() => document.fonts.ready)
  await p.evaluate((s) => document.querySelectorAll('.qr').forEach((el) => { el.innerHTML = s }), qr)
  for (const [id, name] of [['ledger', 'a-ledger'], ['swiss', 'b-swiss'], ['story', 'c-story'], ['invite', 'd-invitation']]) {
    const el = p.locator('#' + id)
    await el.screenshot({ path: path.join(__dirname, `flyer-${name}.png`) })
    await el.screenshot({ path: path.join(__dirname, `flyer-${name}.jpg`), type: 'jpeg', quality: 88, scale: 'css' })
  }
  await b.close()
})()
