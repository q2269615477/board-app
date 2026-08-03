"""Real Chromium chart smoke gate.

This module deliberately exercises the Flask application and the checked-in
KLineChart/KLineChart Pro bundles.  It does not replace either bundle and it
does not fulfill, mock, or abort same-origin requests.  The only request guard
is a narrow external-origin abort, which keeps this gate from depending on
the public internet.

The isolated data seed and server launcher are supplied by the browser-smoke
support task:

    tests.browser_smoke_seed.seed_smoke_environment(tmp_path)
    tests/browser_smoke_server.py

Run only this gate with:

    python -m pytest tests/test_playwright_real_chart_smoke.py -m browser -vv

Install the local browser when needed:

    python -m pip install playwright
    python -m playwright install chromium
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen

import pytest


pytestmark = pytest.mark.browser


try:
    from playwright.sync_api import Page
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - exercised on machines without Playwright
    Page = Any  # type: ignore[misc,assignment]
    sync_playwright = None  # type: ignore[assignment]


try:
    # Keep this import explicit: the helper is deliberately allowed to land in
    # a parallel change after this gate.  The fixture below gives a useful skip
    # until that support file exists instead of making ordinary collection fail.
    from tests.browser_smoke_seed import seed_smoke_environment
except (ImportError, ModuleNotFoundError):  # pragma: no cover - support task pending
    seed_smoke_environment = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parents[1]
SMOKE_SERVER = ROOT / "tests" / "browser_smoke_server.py"
PLAYWRIGHT_MISSING_REASON = (
    "Playwright/Chromium is not available; run "
    "python -m pip install playwright && python -m playwright install chromium"
)
SUPPORT_MISSING_REASON = (
    "browser smoke support is not present yet; expected "
    "tests.browser_smoke_seed and tests/browser_smoke_server.py"
)
CHART_TIMEOUT_MS = 30_000


def _find_free_port() -> int:
    """Ask the OS for an ephemeral local port, then release the probe socket."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _as_seed_environment(seed_result: Any) -> dict[str, str]:
    """Accept the small result-shape variations used by the seed helper."""

    if isinstance(seed_result, (str, Path)):
        return {"BOARD_APP_DATA_DIR": str(seed_result)}

    if not isinstance(seed_result, Mapping):
        return {}

    result: dict[str, str] = {}
    # ``seed_smoke_environment`` returns a direct env mapping in the normal
    # contract (for example BOARD_APP_DATA_DIR).  Do not require callers to
    # wrap it in another ``env`` key.
    for env_key, value in seed_result.items():
        if str(env_key).startswith(("BOARD_", "QMT_", "ANNOTATION_")) and value is not None:
            result[str(env_key)] = str(value)
    for key in ("env", "environment"):
        values = seed_result.get(key)
        if isinstance(values, Mapping):
            for env_key, value in values.items():
                if value is not None:
                    result[str(env_key)] = str(value)

    aliases = {
        "data_dir": "BOARD_APP_DATA_DIR",
        "search_index_path": "BOARD_APP_SEARCH_INDEX_PATH",
        "annotation_vault_path": "ANNOTATION_VAULT_PATH",
    }
    for source_key, env_key in aliases.items():
        value = seed_result.get(source_key)
        if value is not None and env_key not in result:
            result[env_key] = str(value)
    return result


def _server_output(log_file: Any) -> str:
    try:
        log_file.flush()
        log_file.seek(0)
        return log_file.read().decode("utf-8", errors="replace")
    except Exception:
        return "<server output unavailable>"


def _wait_for_server(process: subprocess.Popen[bytes], base_url: str) -> None:
    """Wait for a real HTTP health response, not an arbitrary startup sleep."""

    deadline = time.monotonic() + 30.0
    health_url = f"{base_url}/api/health"
    last_error = ""
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                "browser smoke server exited before becoming ready "
                f"(exit={process.returncode})"
            )
        try:
            with urlopen(health_url, timeout=0.75) as response:
                if 200 <= int(response.status) < 500:
                    return
        except (OSError, URLError, TimeoutError) as exc:
            last_error = str(exc)
        time.sleep(0.05)
    raise RuntimeError(f"browser smoke server did not become ready: {last_error}")


def _terminate_server(process: subprocess.Popen[bytes]) -> None:
    """Terminate the session server and escalate only if it ignores teardown."""

    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=8)


@pytest.fixture(scope="session")
def smoke_server(tmp_path_factory: pytest.TempPathFactory):
    """Seed once, run one isolated real Flask server, and always tear it down."""

    if sync_playwright is None:
        pytest.skip(PLAYWRIGHT_MISSING_REASON)
    if seed_smoke_environment is None or not SMOKE_SERVER.is_file():
        pytest.skip(SUPPORT_MISSING_REASON)

    smoke_root = tmp_path_factory.mktemp("browser-smoke")
    seed_result = seed_smoke_environment(smoke_root)
    server_env = os.environ.copy()
    server_env.update(_as_seed_environment(seed_result))
    server_env.update(
        {
            "BOARD_APP_AUTO_BOOTSTRAP": "0",
            "QMT_ENABLED": "0",
            "QMT_AUTO_START": "0",
            "BOARD_APP_STARTUP_PREWARM": "0",
            "FLASK_HOST": "127.0.0.1",
            "DEBUG": "0",
            "FLASK_DEBUG": "0",
            "PYTHONUNBUFFERED": "1",
            "BOARD_APP_SMOKE_RUNTIME_ROOT": str(smoke_root),
        }
    )

    port = _find_free_port()
    server_env["FLASK_PORT"] = str(port)
    server_env["BROWSER_SMOKE_PORT"] = str(port)
    base_url = f"http://127.0.0.1:{port}"
    log_file = tempfile.TemporaryFile(mode="w+b")
    process: subprocess.Popen[bytes] | None = None

    try:
        # Environment is the fallback for a simple Flask launcher; conventional
        # CLI flags support argparse-based launchers while retaining this port.
        process = subprocess.Popen(
            [
                sys.executable,
                str(SMOKE_SERVER),
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=str(ROOT),
            env=server_env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        _wait_for_server(process, base_url)
        yield type("SmokeServer", (), {"base_url": base_url, "port": port})()
    except RuntimeError as exc:
        output = _server_output(log_file)
        pytest.fail(f"{exc}\nserver output:\n{output}")
    finally:
        if process is not None:
            _terminate_server(process)
        log_file.close()


def _external_abort_guard(route: Any, origin_netloc: str, aborted: list[str]) -> None:
    """Abort only external HTTP(S) requests; continue every same-origin request."""

    parsed = urlparse(route.request.url)
    if parsed.scheme in {"http", "https"} and parsed.netloc != origin_netloc:
        aborted.append(route.request.url)
        route.abort()
        return
    route.continue_()


def _wait_for_chart(page: Page) -> None:
    page.wait_for_selector("#pro-container", state="visible", timeout=CHART_TIMEOUT_MS)
    page.wait_for_function(
        """() => {
          const chart = window.__kline_chart;
          const canvases = Array.from(document.querySelectorAll('#pro-container canvas'));
          return typeof window.klinecharts?.init === 'function' &&
            typeof window.klinechartspro?.KLineChartPro === 'function' &&
            chart && typeof chart.getDataList === 'function' &&
            chart.getDataList().length > 0 &&
            canvases.some((canvas) => {
              const rect = canvas.getBoundingClientRect();
              return canvas.width > 0 && canvas.height > 0 &&
                rect.width > 0 && rect.height > 0;
            });
        }""",
        timeout=CHART_TIMEOUT_MS,
    )


def _canvas_metrics(page: Page) -> list[dict[str, float]]:
    return page.evaluate(
        """() => Array.from(document.querySelectorAll('#pro-container canvas')).map((canvas) => {
          const rect = canvas.getBoundingClientRect();
          const context = canvas.getContext('2d');
          let opaque = 0;
          let colored = 0;
          if (context && canvas.width > 0 && canvas.height > 0) {
            const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
            const count = canvas.width * canvas.height;
            const stride = Math.max(1, Math.floor(count / 20000));
            for (let pixel = 0; pixel < count; pixel += stride) {
              const offset = pixel * 4;
              if (pixels[offset + 3] > 20) opaque += 1;
              if (pixels[offset + 3] > 20 &&
                  (pixels[offset] > 12 || pixels[offset + 1] > 12 || pixels[offset + 2] > 12)) {
                colored += 1;
              }
            }
          }
          return {
            width: rect.width,
            height: rect.height,
            backingWidth: canvas.width,
            backingHeight: canvas.height,
            opaque,
            colored,
          };
        })"""
    )


def _chart_geometry(page: Page) -> dict[str, float]:
    return page.evaluate(
        """() => {
          const container = document.querySelector('#pro-container');
          const canvas = document.querySelector('#pro-container canvas');
          const c = container?.getBoundingClientRect();
          const k = canvas?.getBoundingClientRect();
          return {
            containerWidth: c?.width || 0,
            containerHeight: c?.height || 0,
            canvasWidth: k?.width || 0,
            canvasHeight: k?.height || 0,
            canvasBackingWidth: canvas?.width || 0,
            canvasBackingHeight: canvas?.height || 0,
          };
        }"""
    )


def _wait_for_geometry_change(page: Page, before: dict[str, float]) -> None:
    """Wait for a meaningful resize and several stable animation frames.

    The panel and chart both resize through transitions/resize events.  A
    single changed measurement can therefore be an intermediate width; the
    real gate must observe a changed, usable Canvas after that motion settles.
    """

    page.wait_for_function(
        """(previous) => new Promise((resolve) => {
          let changed = false;
          let stableFrames = 0;
          let last = null;

          const read = () => {
            const container = document.querySelector('#pro-container');
            const canvas = document.querySelector('#pro-container canvas');
            if (!container || !canvas) return null;
            const c = container.getBoundingClientRect();
            const k = canvas.getBoundingClientRect();
            return {
              containerWidth: c.width,
              containerHeight: c.height,
              canvasWidth: k.width,
              canvasHeight: k.height,
              canvasBackingWidth: canvas.width,
              canvasBackingHeight: canvas.height,
            };
          };

          const tick = () => {
            const current = read();
            if (!current) {
              requestAnimationFrame(tick);
              return;
            }
            const containerThreshold = Math.max(
              1, Math.abs(Number(previous.containerWidth || 0)) * 0.005
            );
            const canvasThreshold = Math.max(
              1, Math.abs(Number(previous.canvasWidth || 0)) * 0.005
            );
            const changedEnough =
              Math.abs(current.containerWidth - Number(previous.containerWidth || 0)) >=
                containerThreshold &&
              Math.abs(current.canvasWidth - Number(previous.canvasWidth || 0)) >=
                canvasThreshold;
            const usable = current.containerWidth > 0 && current.containerHeight > 0 &&
              current.canvasWidth > 0 && current.canvasHeight > 0 &&
              current.canvasBackingWidth > 0 && current.canvasBackingHeight > 0;
            if (changedEnough && usable) {
              changed = true;
              const stable = last &&
                Math.abs(current.containerWidth - last.containerWidth) < 0.25 &&
                Math.abs(current.canvasWidth - last.canvasWidth) < 0.25 &&
                current.canvasBackingWidth === last.canvasBackingWidth &&
                current.canvasBackingHeight === last.canvasBackingHeight;
              stableFrames = stable ? stableFrames + 1 : 0;
            } else {
              stableFrames = 0;
            }
            last = current;
            if (changed && stableFrames >= 4) {
              resolve(current);
              return;
            }
            requestAnimationFrame(tick);
          };
          requestAnimationFrame(tick);
        })""",
        arg=before,
        timeout=CHART_TIMEOUT_MS,
    )


def _install_kline_loaded_probe(page: Page) -> None:
    """Record production kline-loaded payloads without intercepting requests."""

    page.evaluate(
        """() => {
          window.__browserSmokeKlineLoaded = [];
          window.addEventListener('kline-loaded', (event) => {
            const detail = event.detail || {};
            const period = detail.period;
            const canonicalPeriod = typeof period === 'string'
              ? period
              : period?.timespan === 'week'
                ? 'weekly'
                : period?.timespan === 'day'
                  ? 'daily'
                  : period?.timespan || '';
            window.__browserSmokeKlineLoaded.push({
              symbol: detail.symbol || '',
              canonicalPeriod,
              ok: detail.ok === true,
              count: Number(detail.count || 0),
            });
          });
        }"""
    )


def _period_item(page: Page, text: str):
    """Locate a Pro period item across the two DOM shapes used by Pro releases."""

    period_bar = page.locator("#pro-container .klinecharts-pro-period-bar")
    candidates = [
        period_bar.locator(".item.period").filter(has_text=text),
        period_bar.locator("button").filter(has_text=text),
        period_bar.get_by_text(text, exact=True),
    ]
    for candidate in candidates:
        if candidate.count():
            return candidate.first
    raise AssertionError(f"period item {text!r} was not found")


def _real_chart_scripts(page: Page) -> list[str]:
    return page.evaluate(
        """() => Array.from(document.scripts).map((script) => script.src).filter(Boolean)"""
    )


@pytest.fixture
def page(smoke_server) -> Page:
    """Create a real Chromium page with only the external guard installed."""

    origin_netloc = urlparse(smoke_server.base_url).netloc
    external_aborts: list[str] = []
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Exception as exc:  # pragma: no cover - machine-dependent
            pytest.skip(f"Chromium could not launch: {exc}; {PLAYWRIGHT_MISSING_REASON}")

        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            device_scale_factor=1,
        )
        context.route(
            "**/*",
            lambda route: _external_abort_guard(route, origin_netloc, external_aborts),
        )
        chart_page = context.new_page()
        page_errors: list[str] = []
        chart_page.on("pageerror", lambda error: page_errors.append(str(error)))
        chart_page.goto(
            smoke_server.base_url,
            wait_until="domcontentloaded",
            timeout=CHART_TIMEOUT_MS,
        )
        _wait_for_chart(chart_page)
        # Protect against accidentally swapping in a test-only chart bundle.
        scripts = _real_chart_scripts(chart_page)
        assert any(src.endswith("/static/js/klinecharts.min.js") for src in scripts)
        assert any(src.endswith("/static/js/klinecharts-pro.umd.js") for src in scripts)
        chart_page._browser_smoke_external_aborts = external_aborts  # type: ignore[attr-defined]
        try:
            yield chart_page
        finally:
            context.close()
            browser.close()
            assert not page_errors, f"uncaught browser errors: {page_errors}"


def test_real_first_screen_has_rendered_canvas_and_kline(page: Page):
    """首屏必须是本地真实 KLineChart Canvas，并且有真实数据。"""

    metrics = _canvas_metrics(page)
    assert metrics, "real chart did not create a Canvas"
    assert any(
        item["backingWidth"] > 0
        and item["backingHeight"] > 0
        and item["width"] > 0
        and item["height"] > 0
        and item["opaque"] > 10
        for item in metrics
    ), f"Canvas exists but no rendered pixels were observed: {metrics}"
    bars = page.evaluate("() => window.__kline_chart.getDataList().length")
    assert bars > 0
    context = page.evaluate("() => window.__board_ctx")
    assert context["code"] == "sh000001"
    assert context["period"] == "daily"


def test_real_top_index_switch(page: Page):
    """顶部指数点击必须切换真实图表上下文。"""

    target = page.locator('.idx-item[data-ticker="sz399006"]')
    target.wait_for(state="visible", timeout=CHART_TIMEOUT_MS)
    target.click()
    page.wait_for_function(
        """() => window.__board_ctx?.code === 'sz399006' &&
          window.pro?.getSymbol?.()?.ticker === 'sz399006' &&
          window.__kline_chart?.getDataList?.().length > 0""",
        timeout=CHART_TIMEOUT_MS,
    )
    assert page.evaluate("() => window.__board_ctx.code") == "sz399006"


def test_real_keyboard_search_ymkd_selects_603259(page: Page):
    """ymkd 搜索结果通过键盘确认后必须落到 603259。"""

    search = page.locator("#search-input")
    search.fill("ymkd")
    page.wait_for_function(
        """() => Array.from(document.querySelectorAll('#search-results .search-item'))
          .some((item) => item.textContent.includes('603259'))""",
        timeout=CHART_TIMEOUT_MS,
    )
    search.press("ArrowDown")
    search.press("Enter")
    page.wait_for_function(
        "() => window.__board_ctx?.code === '603259' && window.__board_ctx?.name === '药明康德'",
        timeout=CHART_TIMEOUT_MS,
    )
    assert page.evaluate("() => window.__board_ctx.name") == "药明康德"


def test_real_daily_to_weekly_period_switch(page: Page):
    """日线切换到周线必须经过真实 Pro 周期控件。"""

    page.wait_for_function(
        "() => window.__board_ctx?.period === 'daily'",
        timeout=CHART_TIMEOUT_MS,
    )
    _install_kline_loaded_probe(page)
    before_bars = page.evaluate("() => window.__kline_chart.getDataList().length")
    week = _period_item(page, "周")
    week.click()
    page.wait_for_function(
        """(previousBars) => {
          const loaded = window.__browserSmokeKlineLoaded || [];
          const weeklyLoaded = loaded.some((event) =>
            event.ok && event.canonicalPeriod === 'weekly' && event.count > 0);
          const selected = document.querySelector(
            '#pro-container .klinecharts-pro-period-bar .item.period.selected'
          );
          return weeklyLoaded && selected?.textContent.trim() === '周' &&
            window.__kline_chart?.getDataList?.().length > 0 &&
            window.__kline_chart.getDataList().length !== previousBars;
        }""",
        arg=before_bars,
        timeout=CHART_TIMEOUT_MS,
    )
    # __board_ctx remains the production canonical symbol context; the
    # canonical period/load evidence is the production kline-loaded payload.
    context = page.evaluate("() => window.__board_ctx")
    assert context["code"] == "sh000001"
    assert context["type"] == "index"
    assert context["name"] == "上证指数"
    loaded = page.evaluate("() => window.__browserSmokeKlineLoaded")
    assert any(event["canonicalPeriod"] == "weekly" and event["ok"] for event in loaded)


def test_real_canvas_bar_replay_select_step_exit(page: Page):
    """回放必须由真实 Canvas 点击选点，随后单步并恢复完整数据。"""

    page.locator("#bar-replay-btn").wait_for(state="visible", timeout=CHART_TIMEOUT_MS)
    page.locator("#bar-replay-btn").click()
    page.wait_for_function(
        "() => window.BarReplayController?.getState?.().status === 'selecting'",
        timeout=CHART_TIMEOUT_MS,
    )

    target = page.evaluate(
        """() => {
          const canvases = Array.from(document.querySelectorAll('#pro-container canvas'));
          const canvas = canvases.sort((a, b) =>
            (b.getBoundingClientRect().width * b.getBoundingClientRect().height) -
            (a.getBoundingClientRect().width * a.getBoundingClientRect().height))[0];
          const rect = canvas.getBoundingClientRect();
          return {x: rect.left + rect.width * 0.42, y: rect.top + rect.height * 0.50};
        }"""
    )
    page.mouse.click(target["x"], target["y"])
    page.wait_for_function(
        """() => window.BarReplayController?.getState?.().status === 'paused' &&
          window.BarReplayController.getState().visibleCount > 0 &&
          window.__kline_chart?.getDataList?.().length ===
            window.BarReplayController.getState().visibleCount""",
        timeout=CHART_TIMEOUT_MS,
    )
    before = page.evaluate("() => window.BarReplayController.getState().cursor")
    page.locator("#bar-replay-step").click()
    page.wait_for_function(
        "(previous) => window.BarReplayController?.getState?.().cursor === previous + 1",
        arg=before,
        timeout=CHART_TIMEOUT_MS,
    )
    page.locator("#bar-replay-exit").click()
    page.wait_for_function(
        """() => window.BarReplayController?.getState?.().status === 'idle' &&
          window.BarReplayController.getState().total === 0 &&
          getComputedStyle(document.querySelector('#bar-replay-controls')).display === 'none'""",
        timeout=CHART_TIMEOUT_MS,
    )
    assert page.evaluate("() => window.__kline_chart.getDataList().length") > before + 1


def test_real_left_and_right_panel_collapse_resize_chart(page: Page):
    """左右面板的展开/收缩必须让真实图表容器和 Canvas 改变尺寸。"""

    # Session UI is mounted asynchronously.  Establish the baseline only
    # after its initial collapsed layout has applied, otherwise its late
    # insertion can be mistaken for the left-panel resize.
    page.locator("#sess-side").wait_for(state="attached", timeout=CHART_TIMEOUT_MS)
    page.wait_for_function(
        """() => document.querySelector('#sess-side').classList.contains('collapsed') &&
          !document.body.classList.contains('sess-side-on')""",
        timeout=CHART_TIMEOUT_MS,
    )
    initial = _chart_geometry(page)

    page.locator("#nav-expand-btn").click()
    page.wait_for_function(
        """() => !document.querySelector('#app').classList.contains('nav-collapsed') &&
          document.querySelector('#nav-panel').getBoundingClientRect().width > 0""",
        timeout=CHART_TIMEOUT_MS,
    )
    _wait_for_geometry_change(page, initial)
    left_open = _chart_geometry(page)
    assert left_open["containerWidth"] < initial["containerWidth"]
    assert left_open["canvasWidth"] < initial["canvasWidth"]
    assert left_open["canvasBackingWidth"] > 0

    page.locator("#nav-panel-toggle").click()
    page.wait_for_function(
        """() => document.querySelector('#app').classList.contains('nav-collapsed')""",
        timeout=CHART_TIMEOUT_MS,
    )
    _wait_for_geometry_change(page, left_open)
    nav_closed = _chart_geometry(page)
    assert nav_closed["containerWidth"] > left_open["containerWidth"]
    assert nav_closed["canvasWidth"] > left_open["canvasWidth"]
    assert nav_closed["canvasWidth"] > 0 and nav_closed["canvasHeight"] > 0
    assert nav_closed["canvasBackingWidth"] > 0 and nav_closed["canvasBackingHeight"] > 0

    page.locator("#sess-expand-btn").click()
    page.wait_for_function(
        """() => !document.querySelector('#sess-side').classList.contains('collapsed') &&
          document.body.classList.contains('sess-side-on')""",
        timeout=CHART_TIMEOUT_MS,
    )
    _wait_for_geometry_change(page, nav_closed)
    right_open = _chart_geometry(page)
    assert right_open["containerWidth"] < nav_closed["containerWidth"]
    assert right_open["canvasWidth"] < nav_closed["canvasWidth"]
    assert right_open["canvasBackingWidth"] > 0

    page.locator("#sess-toggle-btn").click()
    page.wait_for_function(
        """() => document.querySelector('#sess-side').classList.contains('collapsed') &&
          !document.body.classList.contains('sess-side-on')""",
        timeout=CHART_TIMEOUT_MS,
    )
    _wait_for_geometry_change(page, right_open)
    right_closed = _chart_geometry(page)
    assert right_closed["containerWidth"] > right_open["containerWidth"]
    assert right_closed["canvasWidth"] > right_open["canvasWidth"]
    assert right_closed["canvasWidth"] > 0 and right_closed["canvasHeight"] > 0
    assert right_closed["canvasBackingWidth"] > 0 and right_closed["canvasBackingHeight"] > 0


def test_real_light_dark_theme_toggle(page: Page):
    """主题按钮必须切换真实 Pro 主题和外层主题状态。"""

    toggle = page.locator("#board-theme-toggle")
    toggle.wait_for(state="visible", timeout=CHART_TIMEOUT_MS)
    before = page.evaluate(
        "() => ({outer: document.body.dataset.boardTheme, pro: window.pro?.getTheme?.() || null})"
    )
    toggle.click()
    page.wait_for_function(
        "(theme) => document.body.dataset.boardTheme && document.body.dataset.boardTheme !== theme",
        arg=before["outer"],
        timeout=CHART_TIMEOUT_MS,
    )
    light = page.evaluate(
        "() => ({outer: document.body.dataset.boardTheme, pro: window.pro?.getTheme?.() || null})"
    )
    assert light["outer"] in {"light", "dark"}
    assert light["outer"] != before["outer"]
    if light["pro"] is not None:
        assert light["pro"] == light["outer"]

    toggle.click()
    page.wait_for_function(
        "(theme) => document.body.dataset.boardTheme === theme",
        arg=before["outer"],
        timeout=CHART_TIMEOUT_MS,
    )


def test_real_canvas_range_selection(page: Page):
    """用真实图表上的两次点击完成一个框选区间。"""

    range_tool = page.locator("#chart-comparison-range-select")
    range_tool.wait_for(state="visible", timeout=CHART_TIMEOUT_MS)
    range_tool.click()
    page.wait_for_function(
        """() => window.ChartComparisonController?.state?.rangeSelecting === true &&
          document.querySelector('#chart-comparison-svg [data-comparison-range-interaction]')""",
        timeout=CHART_TIMEOUT_MS,
    )
    rect = page.locator("#chart-comparison-svg").bounding_box()
    assert rect and rect["width"] > 100 and rect["height"] > 100
    page.mouse.click(rect["x"] + rect["width"] * 0.25, rect["y"] + rect["height"] * 0.35)
    page.mouse.click(rect["x"] + rect["width"] * 0.72, rect["y"] + rect["height"] * 0.65)
    page.wait_for_function(
        """() => window.ChartComparisonController?.state?.rangeSelecting === false &&
          window.ChartComparisonController.getRangeSelections().length >= 1 &&
          document.querySelectorAll('#chart-comparison-svg [data-comparison-range="true"]').length >= 1""",
        timeout=CHART_TIMEOUT_MS,
    )
    selections = page.evaluate(
        "() => window.ChartComparisonController.getRangeSelections().map((item) => ({start: item.startIndex, end: item.endIndex}))"
    )
    assert selections[0]["end"] > selections[0]["start"]
