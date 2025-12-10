from playwright.async_api import Page
from .selectors import (
    SELECT_ID_ACAO,
    SELECT_ID_LISTA,
    VENCIMENTOS_CONTAINER,
    TABELA_ID,
    SLIDER_STRIKE_TRACK,
)

async def check_health(page: Page) -> None:
    """
    Verifica se os seletores críticos ainda existem na página.
    Levanta RuntimeError se algo crítico mudou.
    """
    print("Executando verificação de integridade da página (Health Check)...")

    checks = [
        ("Seletor de Ação", SELECT_ID_ACAO),
        ("Container de Vencimentos", VENCIMENTOS_CONTAINER),
        ("Tabela de Opções", TABELA_ID),
        ("Slider de Strike", SLIDER_STRIKE_TRACK),
    ]

    missing = []
    for name, selector in checks:
        count = await page.locator(selector).count()
        if count == 0:
            missing.append(f"{name} ({selector})")
    
    # O seletor de lista (IdLista) às vezes carrega dinamicamente ou pode não estar presente
    # dependendo do estado, mas é bom avisar se não achar.
    if await page.locator(SELECT_ID_LISTA).count() == 0:
        print(f"Aviso: Seletor de Lista ({SELECT_ID_LISTA}) não encontrado (pode ser não crítico).")

    if missing:
        error_msg = (
            "FATAL: A estrutura do site parece ter mudado. "
            "Os seguintes elementos não foram encontrados:\n" +
            "\n".join(f"  - {m}" for m in missing)
        )
        raise RuntimeError(error_msg)
    
    print("Health Check: OK. Estrutura da página parece consistente.")
