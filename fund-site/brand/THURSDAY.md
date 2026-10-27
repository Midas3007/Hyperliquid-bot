# Thursday: video and logo for the Midas × Kiyoshi Fund

## Reference: Kiyoshi's video (reference/kiyoshi-video.mp4, 30 s, 640x360)
Black background, thin glowing white lines, small grey captions, monospace micro-labels.
1. Two lines drift side by side: "Two markets. One currency." (labels TEXTILE FX / UNISWAP V3).
2. Lines separate, prices shown (4.9770 vs 4.9719): "Sometimes, they disagree."
3. Fan icon appears: "Kiyoshi lives in the gap."
4. Ticks between the lines: "It buys where it is cheaper, and sells where it is not."
5. "Every trade closes the gap a little more."
6. Counter (round trips) over a rising profit curve; stat row: 165 round trips · 100% profitable ·
   $23,611 filled on Textile FX · +$211 profit. "Only when both sides win."
7. End card: KIY[fan]SHI wordmark (the O is a folding fan), "Autonomous ..." tagline.

## Midas's numbers (reference/midas-wallet-stats.jpg, Mel's wallet-stats script, pool wallet 0x6cd3...81dc)
first trade 2026-10-26 02:11 UTC · 216 round trips (98% profitable) · realized P&L +$197.71 ·
Textile volume $19,073.45 · Uniswap volume $20,000.34 · total volume $39,073.79 ·
deposits/withdrawals $109.51 / $195.29 · 456 txs, 449 tagged celo_f6214d29d540.
Re-run on Thursday for fresh numbers: `npx tsx src/wallet-stats.ts 0x6cd37bc290c5c02ea92086d416a6a673bca581dc`
(script from Mel; ask Midas for it or re-create it).

## Video plan (Midas × Kiyoshi Fund)
Same language as Kiyoshi's (credit and continuity), but in the fund colours: chemical white, grey, black,
gold. Story: the gap → Kiyoshi trades it → the fund shares it. Stats from Midas's wallet; end card with the
fund logo and "Season 1" result. Rendered as an HTML/canvas animation → MP4 with ffmpeg (no paid tools).
Vertical 1080x1920 for WhatsApp status plus 16:9.

## Logo brief
Minimal finance/hedge-fund mark. References Kiyoshi's folding fan; Midas = the golden touch (King Midas).
Ideas: a fan whose ribs are gold lines rising like a chart; an M formed by fan ribs; a fan inside a coin.
Colours: gold on black / black on chemical white. Deliver SVG (site, favicon) and PNG.
