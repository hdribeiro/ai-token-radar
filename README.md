# AI Token Radar (private)

Data pipeline behind a private claude.ai dashboard tracking LLM token usage, prices and mix.

- `scripts/fetch.py` runs daily in GitHub Actions (`.github/workflows/refresh.yml`) and stores dated raw snapshots in `data/raw/` plus `data/rankings_daily.csv`.
- `scripts/build.py` aggregates everything into `dist/radar.json`, which the dashboard renders.
- API keys live only in repo secrets `OPENROUTER_API_KEY` and `AA_API_KEY`.

## Sources and attribution
- OpenRouter Data API and models list. Source: OpenRouter (openrouter.ai/rankings). Licensed under CC BY 4.0.
- Artificial Analysis free API (artificialanalysis.ai). Free tier: internal use, attribution required. Do not publish this dashboard externally without checking AA's Data Platform Terms.
- AI Price Index by RoninForge (roninforge.org/data/ai-price-index/), CC BY 4.0; adapted (3:1 blend, model families).
- Epoch AI, LLM inference price trends (epoch.ai/data-insights/llm-inference-price-trends); milestone rows transcribed.
- OpenRouter & a16z, State of AI (Dec 2025), static geography and language tables.

## Caveats
- OpenRouter traffic is a lens, not the market: it excludes labs' own APIs, consumer apps and private traffic.
- Rankings cover the top 50 models per day; everything else is one "other" row that cannot be split by lab or openness.
- Usage-weighted price uses current list prices and a 3:1 input:output blend; real workloads (agentic, cached) differ.
