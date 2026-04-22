---
source: twitter
id: "17243"
url: https://x.com/karpathy/status/17243xxxxxxxx
author: "@karpathy"
metric: "likes=8921,retweets=1430"
scraped_at: 2026-04-21T10:15:00+08:00
---

# on long-context cost economics

people are optimistic about 1M context windows but underestimate that per-request cost scales roughly linearly. if you're doing RAG at volume, do the math on per-query cost vs vector DB, may still win.

not saying long context is useless. saying the default "just stuff it in context" meme is expensive at scale.
