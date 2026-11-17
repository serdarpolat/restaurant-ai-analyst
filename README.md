# restaurant-ai-analyst

> [!WARNING]
> Work in progress. Demo data only — this project never connects to a real POS.

Ask a restaurant's sales database questions in plain Turkish or English
(*"Geçen haftaya göre öğle cirosu neden düştü?"*). The model writes the SQL itself;
a layered guard keeps every query **read-only**, limited and honest, and the web panel
shows each query, what the guard refused and why, the result table and a Vega-Lite chart.

Project 5 of a series on AI agents for restaurant tech. Demo data comes from
[restaurant-mcp-server](https://github.com/serdarpolat/restaurant-mcp-server) (project 1).

## Status
- [x] Demo database: project 1's seed + an analyst-only scenario layer
- [x] Read-only database layer (SQLite `mode=ro`, `query_only`, authorizer, time limit)
- [x] Static SQL guard (sqlglot) with "did you mean" hints
- [x] Attack corpus tests: every attack is blocked by the guard **and** by the database alone
- [ ] Schema card, agent loop, CLI `ask`
- [ ] Web panel (Nuxt) with step trace and Vega-Lite charts
- [ ] Evaluation: 20 Turkish questions with gold SQL
- [ ] MCP server

## Try the guard (current state)
```bash
uv venv && uv pip install -e ".[dev]"
restaurant-analyst build-db
restaurant-analyst sql "SELECT channel, COUNT(*) FROM orders GROUP BY channel"
restaurant-analyst sql "DELETE FROM orders"      # refused
python -m pytest
```
