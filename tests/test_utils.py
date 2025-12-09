from opcoes.utils import infer_option_type


def test_infer_option_type_calls_and_puts() -> None:
    assert infer_option_type("PETRA30") == "CALL"
    assert infer_option_type("PETRM30") == "PUT"
    assert infer_option_type("ABEVA105W1") == "CALL"
    assert infer_option_type("VALEV") is None

