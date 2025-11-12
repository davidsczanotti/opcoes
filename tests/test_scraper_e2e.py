"""Playwright end-to-end tests for the scraper.

These tests hit https://opcoes.net.br directly to validate that we can:
1. Acessar a aplicação
2. Capturar a lista de tickers
3. Acionar os filtros (CALL, vencimentos, faixa de strikes, modalidade)

Execute-os apenas quando tiver rede liberada, passando RUN_E2E_TESTS=1.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Page, async_playwright

from opcoes.scraper import run
from opcoes.scraper import selectors


RUN_E2E = os.getenv("RUN_E2E_TESTS") == "1"
pytestmark = pytest.mark.skipif(
    not RUN_E2E, reason="Set RUN_E2E_TESTS=1 to run Playwright end-to-end tests"
)

THROTTLE = 0.6
FALLBACK_SYMBOL = "PETR4"


@pytest_asyncio.fixture(scope="module")
async def browser() -> Browser:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        yield browser
        await browser.close()


@pytest_asyncio.fixture
async def page(browser: Browser) -> Page:
    context = await browser.new_context()
    page = await context.new_page()
    await page.goto(selectors.BASE_URL, wait_until="domcontentloaded")
    yield page
    await context.close()


@pytest.mark.asyncio
async def test_site_access(page: Page) -> None:
    select = page.locator(selectors.SELECT_ID_ACAO)
    assert await select.count() == 1
    assert await select.locator("option").count() > 10


@pytest.mark.asyncio
async def test_capture_tickers(page: Page) -> None:
    symbols = await run._collect_symbols(page)
    assert symbols
    assert FALLBACK_SYMBOL in symbols


@pytest.mark.asyncio
async def test_select_filters(page: Page) -> None:
    symbols = await run._collect_symbols(page)
    symbol = FALLBACK_SYMBOL if FALLBACK_SYMBOL in symbols else symbols[0]

    await page.select_option(selectors.SELECT_ID_ACAO, value=symbol)
    await run._wait_table_update(page, THROTTLE)

    await run._ensure_calls_checked(page)
    await run._wait_table_update(page, THROTTLE)
    assert await page.locator(selectors.SELECT_CALLS_CHECKBOX).is_checked()

    await run._select_last_vencimentos(page, run.MAX_VENCIMENTOS)
    await run._wait_table_update(page, THROTTLE)
    total_checks = await page.locator(selectors.VENCIMENTOS_CHECKBOXES).count()
    selected_checks = await page.locator(f"{selectors.VENCIMENTOS_CHECKBOXES}:checked").count()
    assert selected_checks == min(run.MAX_VENCIMENTOS, total_checks)

    await run._stretch_strike_slider(page)
    await run._wait_table_update(page, THROTTLE)
    handles = page.locator(selectors.SLIDER_STRIKE_HANDLES)
    assert await handles.count() == 2

    def _percent(value: str) -> float:
        try:
            return float(value.replace("%", "").strip())
        except ValueError:
            return -1.0

    left_style = await handles.nth(0).evaluate("el => el.style.left || ''")
    right_style = await handles.nth(1).evaluate("el => el.style.left || ''")
    assert _percent(left_style) <= 1.0
    assert _percent(right_style) >= 99.0

    await run._set_modalidade_e(page)
    await run._wait_table_update(page, THROTTLE)
    mod_value = await page.locator(selectors.SELECT_MOD_FILTER).input_value()
    assert mod_value == "E"

    rows = await run._collect_table_rows(page, symbol)
    assert rows, "Esperamos pelo menos uma linha após aplicar filtros"
    assert all((row["mod"] == "E" or not row["mod"]) for row in rows)

