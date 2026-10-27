"""The evaluation only means something if its checks can fail: each must reject a wrong answer."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "evals"))

from run_eval import build_cases  # noqa: E402

WRONG_ANSWERS = {
    "Product price": "O Yamaha FG800 custa R$ 1.199,00.",
    "Product that does not exist": "A Gibson Flying V custa R$ 9.999,00.",
    "Order status": "Seu pedido foi entregue.",
    "Order with wrong e-mail": "Não consegui confirmar, mas o rastreio é BRJL5544332BR.",
    "Return rule": "Você pode devolver em até 30 dias.",
    "Opening hours": "No sábado abrimos às 9h e fechamos às 18h.",
    "Active promotion": "Sim, está com 20% de desconto, por R$ 439,20.",  # no original price (6.2)
    "Out of scope": "Claro! Misture farinha, ovos e açúcar e leve ao forno. Depois passe na loja!",
}


def test_every_eval_check_rejects_a_wrong_answer():
    cases = {case.name: case for case in build_cases()}

    assert set(cases) == set(WRONG_ANSWERS)
    for name, answer in WRONG_ANSWERS.items():
        assert not cases[name].check(answer), name
