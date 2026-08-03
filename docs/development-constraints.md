# Board App Development Constraints

This project is allowed to evolve, but these contracts must stay stable unless
the replacement is implemented and tested in the same change.

## Frontend Entry

- `static/index.html` is a shell only. Do not put large inline CSS or JS back in
  this file.
- The panel layout is owned by these modules:
  - `static/js/nav-panel.js`
  - `static/js/index-bar.js`
  - `static/js/chart-core.js`
  - `static/js/session-ui.js`
  - `static/js/app-init.js`
- The shell must keep the mount points `#toolbar`, `#index-bar`, `#idx-add-btn`,
  `#nav-panel`, `#pro-container`, and `#search-wrap`.
- Before changing the entry shell, run:

```powershell
.\venv\Scripts\python.exe -m pytest tests\test_frontend_entry_contract.py
```

## Frontend/Backend API Contracts

- Frontend code must not call a new `/api/*` route until the route has a test.
- Routes used during page boot must return JSON and should degrade cleanly.
  A temporary backend failure must not break the panel layout.
- The current boot-sensitive routes include:
  - `/api/system/frontend-config`
  - `/api/classification/load`
  - `/api/spot/indices`
  - `/api/annotations/counts`
  - `/api/update/debt`

## Classification Data

- `static/board_classification.json` is product data, not incidental UI data.
- Do not collapse it to a smaller temporary category set to make UI code easier.
- Any classification rebuild must run:

```powershell
.\venv\Scripts\python.exe -m pytest tests\test_classification_validation.py tests\test_search_service_tags.py
```

## Data Update Changes

- New update behavior should be wired through `services/update_task_factories.py`
  and `services/update_task_service.py`.
- Old routes under `/api/update/*` may stay as compatibility wrappers, but they
  must not start their own duplicate runners.
- Long-running update operations must be cancelable or safely deduplicated.
- A top-bar instrument is not complete until both spot data and historical
  K-line data work. When adding an item to `static/js/index-bar.js`, also verify
  `/api/kline/{type}/{code}` returns non-empty data or add a loader fallback.
- Global indices use stable frontend types: `hk_index` for `HSI`/`HSTECH`, `us`
  for overseas indices, `index` for A-share indices, and `concept` only for
  board-like entries such as `BK1158`.
- Do not restore `static/index-local.html`, `static/index-local-fixed.html`, or
  archived one-file frontend shells. The supported frontend is the modular
  `static/index.html` plus `static/js/*` layout.

## Repository Hygiene

- Do not keep generated diagnostics in the repo root. Screenshots, `_*.txt`
  probes, `.bak` files, `__pycache__`, `graft/`, `.opencode/`, and
  `.playwright-mcp/` are local scratch only.
- Keep product data separate from scratch data. `data/kline.db`,
  `static/board_classification.json`, and generated runtime caches are not
  interchangeable with migration backups.
- One-off migration scripts must either be moved into a dated docs/audit note or
  deleted after the data they produced is accepted. Do not leave scripts that
  depend on deleted backup files as active maintenance paths.
- `tests/test_repository_hygiene.py` is a hard gate. It prevents tracked
  `__pycache__`, `.pyc`, root `kline.db`, `static/index-local*.html`,
  `static/_archive/`, `.bak` files, and the old hardcoded Tushare token.

## Shared K-Line Utilities

- OHLCV resampling is owned by `data/kline_resample.py`.
- `data_loader._resample()` is compatibility only. Do not add new resampling
  logic to `data_loader.py`.
- Global index history is owned by `data/global_index_kline.py`. K-line service
  should call that module directly for `hk_index` and `us` history.
- Cached stock, A-share index, HK index, and HK stock history is owned by
  `data/market_kline.py`.
- Industry/concept board history is owned by `data/board_kline.py`.
- `data_loader.py` is a compatibility facade for these K-line entrypoints; new
  application code should import the owning module directly.

## Review Gate

Before declaring a UI/data-update change done, run the narrow gate below:

```powershell
node --check static\js\api-client.js
node --check static\js\nav-panel.js
node --check static\js\index-bar.js
node --check static\js\session-ui.js
node --check static\js\app-init.js
.\venv\Scripts\python.exe -m pytest tests\test_frontend_entry_contract.py tests\test_frontend_config.py tests\test_update_task_routes.py
.\venv\Scripts\python.exe -m pytest tests\test_repository_hygiene.py
```

For visible layout changes, also run a browser smoke check against
`http://127.0.0.1:5000/` and verify:

- The top index bar renders and refreshes.
- The `+` button opens the index manager.
- Clicking an index changes `window.__board_ctx`.
- The left nav renders second-level categories and tag chips.
- The session side panel appears.
- Clicking each top-bar item after `东方财富全A` displays that instrument's own
  K-line instead of an empty or stale chart.
