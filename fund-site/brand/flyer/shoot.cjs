// node shoot.cjs: flyer.html -> A4 PNG (300 dpi), PDF and a phone-friendly JPG
const { chromium } = require('playwright'), path = require('path')
;(async () => {
  const b = await chromium.launch(), p = await b.newPage({ viewport: { width: 1240, height: 1754 }, deviceScaleFactor: 2 })
  await p.goto('file://' + path.join(__dirname, 'flyer.html')); await p.evaluate(() => document.fonts.ready)
  await p.locator('#flyer').screenshot({ path: path.join(__dirname, 'midas-kiyoshi-flyer.png') })
  await p.locator('#flyer').screenshot({ path: path.join(__dirname, 'midas-kiyoshi-flyer.jpg'), type: 'jpeg', quality: 88, scale: 'css' })
  await p.pdf({ path: path.join(__dirname, 'midas-kiyoshi-flyer.pdf'), width: '1240px', height: '1754px', printBackground: true, pageRanges: '1' })
  await b.close()
})()
