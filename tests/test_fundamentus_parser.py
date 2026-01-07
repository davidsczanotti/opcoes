from opcoes.fundamentus import normalize_rows, parse_result_table


def _build_html_row(cells: list[str]) -> str:
    tds = "".join(f"<td>{cell}</td>" for cell in cells)
    return f"<tr>{tds}</tr>"


def test_parse_result_table_and_normalize() -> None:
    cells = [
        '<span class="tips"><a href="detalhes.php?papel=MNPR3">MNPR3</a></span>',
        "4,56",
        "0,62",
        "1,61",
        "0,757",
        "0,00%",
        "0,734",
        "2,08",
        "4,18",
        "-52,25",
        "2,64",
        "2,33",
        "18,12%",
        "121,81%",
        "3,01",
        "25,67%",
        "258,34%",
        "169.282,00",
        "201.583.000,00",
        "0,00",
        "6,23%",
    ]
    html = f"""
    <html>
      <body>
        <table id="resultado">
          <tbody>
            {_build_html_row(cells)}
          </tbody>
        </table>
      </body>
    </html>
    """

    rows = parse_result_table(html)
    assert len(rows) == 1
    assert rows[0]["papel"] == "MNPR3"
    assert rows[0]["pl"] == "0,62"

    normalized = normalize_rows(rows)
    assert len(normalized) == 1
    row = normalized[0]
    assert row["papel"] == "MNPR3"
    assert row["cotacao"] == 4.56
    assert row["div_yield"] == 0.0
    assert row["patrimonio_liq"] == 201583000.0
