from __future__ import annotations

import contextlib
import math
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from playwright.async_api import (
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
    Locator,
    Page,
)

from .selectors import (
    BASE_URL,
    SELECT_CALLS_CHECKBOX,
    SELECT_CALLS_LABEL,
    SELECT_ID_ACAO,
    SELECT_MOD_FILTER,
    SLIDER_STRIKE_HANDLES,
    SLIDER_STRIKE_TRACK,
    TABELA_LENGTH,
    TABELA_TBODY_ROWS,
    VENCIMENTOS_CHECKBOXES,
    VENCIMENTOS_CONTAINER,
)
from .storage import append_rows_dedup, load_existing_tickers
from .fundamentals import load_earnings_yield_map
from .statusinvest import fetch_fundamentals_map


MAX_VENCIMENTOS = 8
PROCESSING_OVERLAY = "#tblListaOpc_processing"


async def scrape_all(
    *,
    symbols: Optional[Sequence[str]] = None,
    output_csv: Path,
    max_symbols: Optional[int] = None,
    headless: bool = True,
    throttle_sec: float = 1.0,
    goto_timeout_ms: int = 60000,
    proxy_settings: Optional[Dict[str, str]] = None,
    fundamentals_csv: Optional[Path] = None,
    use_status_invest: bool = False,
) -> None:
    output_csv = Path(output_csv)
    existing_tickers = load_existing_tickers(output_csv)
    fundamentals_map: Dict[str, tuple] = {}
    async with async_playwright() as p:
        launch_kwargs = {"headless": headless}
        if proxy_settings:
            launch_kwargs["proxy"] = proxy_settings
        browser = await p.chromium.launch(**launch_kwargs)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(
            BASE_URL,
            wait_until="domcontentloaded",
            timeout=max(goto_timeout_ms, 1000),
        )
        await _wait_idle(page)

        available = await _collect_symbols(page)
        target_symbols = _filter_symbols(available, symbols)
        if max_symbols is not None:
            target_symbols = target_symbols[:max_symbols]

        if not target_symbols:
            print("Nenhum papel selecionado.")
            await browser.close()
            return

        total_written = 0
        total_symbols = len(target_symbols)

        # Carrega fundamentos por fonte escolhida (uma vez, antes do loop)
        if use_status_invest:
            try:
                unique_symbols = list(dict.fromkeys(target_symbols))
                fundamentals_map = fetch_fundamentals_map(unique_symbols)
            except Exception as exc:  # noqa: BLE001
                print(f"Aviso: falhou Status Invest: {exc}")
                fundamentals_map = {}
        elif fundamentals_csv:
            fundamentals_map = load_earnings_yield_map(Path(fundamentals_csv))
        for idx, symbol in enumerate(target_symbols, start=1):
            print(f"[{idx}/{total_symbols}] Processando {symbol}…")
            try:
                rows = await _scrape_symbol(
                    page,
                    symbol,
                    throttle_sec=throttle_sec,
                    goto_timeout_ms=goto_timeout_ms,
                )
            except Exception as exc:  # noqa: BLE001 – queremos continuar
                print(f"  -> erro ao processar {symbol}: {exc}")
                continue

            if not rows:
                print("  -> sem resultados.")
                continue

            # Anota indicadores por papel subjacente se disponíveis
            if fundamentals_map:
                ey, pe = fundamentals_map.get(symbol, (None, None))
                ey_str = f"{ey:.6f}" if (ey is not None) else ""
                pe_str = f"{pe:.6f}" if (pe is not None) else ""
                for r in rows:
                    r["earnings_yield_ttm"] = ey_str
                    r["pe_ttm"] = pe_str

            written = append_rows_dedup(output_csv, rows, existing_tickers)
            total_written += written
            print(f"  -> {len(rows)} linhas coletadas (novas: {written}).")

        await browser.close()
        print(f"Concluído. Novos registros gravados: {total_written}. Arquivo: {output_csv}")


async def _scrape_symbol(
    page: Page,
    symbol: str,
    *,
    throttle_sec: float,
    goto_timeout_ms: int,
) -> List[Dict[str, str]]:
    await page.select_option(SELECT_ID_ACAO, value=symbol)
    await _wait_table_update(page, throttle_sec)

    await _ensure_calls_checked(page)
    await _wait_table_update(page, throttle_sec)

    await _select_last_vencimentos(page, MAX_VENCIMENTOS)
    await _wait_table_update(page, throttle_sec)

    await _stretch_strike_slider(page)
    await _wait_table_update(page, throttle_sec)

    await _set_modalidade_e(page)
    await _wait_table_update(page, throttle_sec)

    await _show_all_rows(page)
    await _wait_table_update(page, throttle_sec)

    rows = await _collect_table_rows(page, symbol)
    rows = [row for row in rows if (row.get("mod") or "").strip().upper() == "E"]
    return rows


async def _collect_symbols(page: Page) -> List[str]:
    return await page.eval_on_selector_all(
        f"{SELECT_ID_ACAO} option",
        "options => options.map(o => o.value).filter(v => v)",
    )


def _filter_symbols(available: Sequence[str], requested: Optional[Sequence[str]]) -> List[str]:
    if not requested:
        return list(available)
    requested_list: List[str] = []
    missing: List[str] = []
    available_set = set(available)
    for sym in requested:
        if sym in available_set:
            requested_list.append(sym)
        else:
            missing.append(sym)
    if missing:
        print(f"Aviso: papéis não encontrados e serão ignorados: {', '.join(missing)}")
    return requested_list


async def _ensure_calls_checked(page: Page) -> None:
    checkbox = page.locator(SELECT_CALLS_CHECKBOX)
    if await checkbox.count() == 0:
        # fallback: clicar no label
        await page.locator(SELECT_CALLS_LABEL).click()
        return
    if not await checkbox.is_checked():
        await checkbox.check()


async def _select_last_vencimentos(page: Page, total: int) -> None:
    container = page.locator(VENCIMENTOS_CONTAINER)
    await container.scroll_into_view_if_needed()
    checkboxes = page.locator(VENCIMENTOS_CHECKBOXES)
    count = await checkboxes.count()
    if count == 0:
        return
    for idx in range(count):
        await checkboxes.nth(idx).set_checked(False, force=True)

    indices = await checkboxes.evaluate_all(
        """
        (nodes, total) => {
            const parsed = nodes.map((node, index) => {
                const raw = node.value || node.getAttribute('value') || (node.id || '').replace(/^v/, '');
                const time = raw ? Date.parse(raw) : Number.NaN;
                return { index, time: Number.isNaN(time) ? null : time };
            });
            const withDate = parsed.filter(item => item.time !== null)
                .sort((a, b) => a.time - b.time)
                .map(item => item.index);
            const fallback = parsed.map(item => item.index);
            const order = withDate.length ? withDate : fallback;
            if (!order.length) {
                return [];
            }
            const start = Math.max(order.length - total, 0);
            return order.slice(start);
        }
        """,
        total,
    )

    for idx in indices:
        await checkboxes.nth(idx).set_checked(True, force=True)


async def _stretch_strike_slider(page: Page) -> None:
    track = page.locator(SLIDER_STRIKE_TRACK)
    handles = page.locator(SLIDER_STRIKE_HANDLES)
    if await handles.count() < 2:
        return
    box = await track.bounding_box()
    if not box:
        return
    center_y = box["y"] + box["height"] / 2

    async def drag_handle(handle: Locator, target_x: float) -> None:
        hb = await handle.bounding_box()
        if not hb:
            return
        start_x = hb["x"] + hb["width"] / 2
        start_y = hb["y"] + hb["height"] / 2
        await page.mouse.move(start_x, start_y)
        await page.mouse.down()
        await page.mouse.move(target_x, center_y, steps=6)
        await page.mouse.up()

    await drag_handle(handles.nth(0), box["x"])
    await drag_handle(handles.nth(1), box["x"] + box["width"])


async def _set_modalidade_e(page: Page) -> None:
    select = page.locator(SELECT_MOD_FILTER)
    count = await select.count()
    if not count:
        return
    for idx in range(count):
        await select.nth(idx).select_option(value="E")


async def _show_all_rows(page: Page) -> None:
    select = page.locator(TABELA_LENGTH)
    if not await select.count():
        return

    value = await select.evaluate("el => el.options.length ? el.options[el.options.length - 1].value : null")
    if value:
        await select.select_option(value)


async def _collect_table_rows(page: Page, underlying: str) -> List[Dict[str, str]]:
    rows_locator = page.locator(TABELA_TBODY_ROWS)
    rows_count = await rows_locator.count()
    if rows_count == 0:
        return []
    records: List[Dict[str, str]] = []
    for idx in range(rows_count):
        row = rows_locator.nth(idx)
        classes = await row.get_attribute("class") or ""
        if "dataTables_empty" in classes:
            continue
        cells = await row.locator("td").all_inner_texts()
        if len(cells) < 25:
            continue
        cells = [c.strip() for c in cells]
        record = _build_row_dict(underlying, cells)
        records.append(record)
    return records


def _build_row_dict(underlying: str, cells: Sequence[str]) -> Dict[str, str]:
    record = {
        "underlying": underlying,
        "ticker": cells[0],
        "vencimento": cells[1],
        "dias_uteis": cells[2],
        "fm": cells[3],
        "mod": cells[4],
        "strike": cells[5],
        "ai_otm": cells[6],
        "dist_perc_strike": cells[7],
        "ultimo": cells[8],
        "var_perc": cells[9],
        "data_hora": cells[10],
        "num_neg": cells[11],
        "vol_financeiro": cells[12],
        "vol_impl_perc": cells[13],
        "delta": cells[14],
        "gamma": cells[15],
        "theta_dolar": cells[16],
        "theta_perc": cells[17],
        "vega": cells[18],
        "iq": cells[19],
        "coberto": cells[20],
        "travado": cells[21],
        "descoberto": cells[22],
        "titulares": cells[23],
        "lancadores": cells[24],
    }
    _apply_status_indicators(record)
    return record


async def _wait_table_update(page: Page, throttle_sec: float) -> None:
    await _wait_processing_overlay(page)
    await page.wait_for_timeout(max(throttle_sec, 0.2) * 1000)


async def _wait_processing_overlay(page: Page) -> None:
    overlay = page.locator(PROCESSING_OVERLAY)
    try:
        await overlay.wait_for(state="visible", timeout=1500)
    except PlaywrightTimeoutError:
        pass
    try:
        await overlay.wait_for(state="hidden", timeout=10000)
    except PlaywrightTimeoutError:
        pass


async def _wait_idle(page: Page) -> None:
    with contextlib.suppress(PlaywrightTimeoutError):
        await page.wait_for_load_state("networkidle", timeout=10000)


def _apply_status_indicators(row: Dict[str, str]) -> None:
    status_m = _status_moneyness(row)
    row["Status_Moneyness"] = status_m
    pct_alta, status_2x = _double_scenario(row)
    row["%_Alta_p_2x"] = _format_decimal(pct_alta, decimals=1, signed=False)
    row["Status_2x"] = status_2x
    status_liq = _status_liquidez(row)
    row["Status_Liquidez"] = status_liq
    status_theta = _status_theta(row)
    row["Status_Theta"] = status_theta

    m_score = _score_moneyness(status_m)
    l_score = _score_liquidez(status_liq)
    d_score = _score_dobro(status_2x)
    t_score = _score_theta(status_theta)
    row["moneyness_score"] = str(m_score)
    row["liquidez_score"] = str(l_score)
    row["dobro_score"] = str(d_score)
    row["theta_score"] = str(t_score)
    row["score_total"] = str(m_score + l_score + d_score + t_score)


def _parse_float(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    cleaned = (
        value.strip()
        .replace("\xa0", "")
        .replace("\u2212", "-")
        .replace("−", "-")
        .replace("%", "")
        .replace("+", "")
    )
    if not cleaned:
        return None
    cleaned = cleaned.replace(".", "").replace(",", ".").replace(" ", "")
    if not cleaned or cleaned == "-":
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _status_moneyness(row: Dict[str, str]) -> str:
    raw_state = (row.get("ai_otm") or "").upper()
    if "ITM" in raw_state:
        return "ITM"
    dist = _parse_float(row.get("dist_perc_strike"))
    if dist is None:
        return ""
    if "ATM" in raw_state and dist <= 1.0:
        return "0-5% OTM (colada)"
    if dist < 0:
        return "ITM"
    if dist <= 5:
        return "0-5% OTM (colada)"
    if dist <= 15:
        return "5-15% OTM (aposta)"
    if dist <= 20:
        return "15-20% OTM"
    return "20%+ OTM (loteria)"


def _double_scenario(row: Dict[str, str]) -> Tuple[Optional[float], str]:
    option_price = _parse_float(row.get("ultimo"))
    delta = _parse_float(row.get("delta"))
    strike = _parse_float(row.get("strike"))
    dist = _parse_float(row.get("dist_perc_strike"))
    if (
        option_price is None
        or option_price <= 0
        or delta is None
        or abs(delta) < 1e-4
        or strike is None
        or dist is None
    ):
        return None, ""

    spot = _spot_from_strike_dist(strike, dist)
    if spot is None or spot <= 0:
        return None, ""

    move_abs = option_price / abs(delta)
    if move_abs <= 0:
        return None, ""
    pct = (move_abs / spot) * 100.0
    if not math.isfinite(pct) or pct <= 0:
        return None, ""

    if pct <= 20:
        status = "Dobra com até 20% no ativo"
    elif pct <= 40:
        status = "Dobra com 20-40% no ativo"
    else:
        status = "Precisa de 40%+ no ativo"
    return pct, status


def _spot_from_strike_dist(strike: float, dist: float) -> Optional[float]:
    denom = 1 + (dist / 100.0)
    if abs(denom) < 1e-6:
        return None
    return strike / denom


def _status_liquidez(row: Dict[str, str]) -> str:
    num_neg = _parse_float(row.get("num_neg")) or 0.0
    vol_fin = _parse_float(row.get("vol_financeiro")) or 0.0
    if num_neg >= 30 or vol_fin >= 50000:
        return "Alta"
    if num_neg >= 5 or vol_fin >= 5000:
        return "Média"
    if num_neg > 0 or vol_fin > 0:
        return "Baixa"
    return ""


def _status_theta(row: Dict[str, str]) -> str:
    theta = _parse_float(row.get("theta_perc"))
    if theta is None:
        return ""
    abs_theta = abs(theta)
    if abs_theta < 0.5:
        return "Theta baixo"
    if abs_theta < 1.0:
        return "Theta médio"
    return "Theta alto"


def _format_decimal(value: Optional[float], *, decimals: int = 2, signed: bool = False) -> str:
    if value is None or not math.isfinite(value):
        return ""
    fmt = f"{value:+.{decimals}f}" if signed else f"{value:.{decimals}f}"
    return fmt.replace(".", ",")


def _score_moneyness(label: str) -> int:
    if label == "0-5% OTM (colada)":
        return 2
    if label == "5-15% OTM (aposta)":
        return 1
    return 0


def _score_liquidez(label: str) -> int:
    if label == "Alta":
        return 2
    if label == "Média":
        return 1
    return 0


def _score_dobro(label: str) -> int:
    if label == "Dobra com até 20% no ativo":
        return 2
    if label == "Dobra com 20-40% no ativo":
        return 1
    return 0


def _score_theta(label: str) -> int:
    return 1 if label == "Theta baixo" else 0


__all__ = ["scrape_all"]
