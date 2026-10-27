"""End-to-end evaluation: 8 questions to the real agent, facts checked in code.

Expected values are read from the CSVs and data/policies.md at run time, never typed
in, and are computed without the agent's own tools so a bug there cannot hide.
Usage: python evals/run_eval.py   (prints the score and writes evals/results.md)
"""

import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from emporio.agent import Agent  # noqa: E402

DATA = ROOT / "data"
MANUAL = (DATA / "policies.md").read_text(encoding="utf-8")


def csv(name: str) -> pd.DataFrame:
    return pd.read_csv(DATA / f"desafio_tecnico_ai_eng - {name}.csv", encoding="utf-8")


PRODUCTS, ORDERS, ITEMS, CUSTOMERS, PROMOS = (
    csv(n) for n in ("products", "orders", "order_items", "customers", "promotions")
)


# ------------------------------------------------------------------ text helpers


def norm(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def money(text: str) -> list[float]:
    """Every 'R$ 1.299,00'-style amount in the text."""
    found = re.findall(r"R\$\s*(\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:,\d{1,2})?)", text)
    return [float(v.replace(".", "").replace(",", ".")) for v in found]


def has_money(text: str, value: float) -> bool:
    return any(abs(v - value) < 0.011 for v in money(text))


def clock_times(text: str) -> set[tuple[int, int]]:
    """'09:00', '9h', '9h00' and '13h' -> {(9, 0), (13, 0)}."""
    return {(int(h), int(m or mm or 0)) for h, m, mm in re.findall(r"\b(\d{1,2})(?::(\d{2})|h(\d{2})?)", text)}


def any_of(text: str, phrases: list[str]) -> bool:
    return any(norm(p) in norm(text) for p in phrases)


NOT_FOUND = ["não encontr", "não temos", "não localiz", "não consta", "não faz parte", "não trabalhamos", "não está no"]
COULD_NOT_VERIFY = ["não foi possível", "não consegui", "não localiz", "não encontr", "confira", "conferir", "verifique"]


# ------------------------------------------------------------ expected values


def final_price(row: pd.Series) -> float:
    active = PROMOS[(PROMOS.product_id == row.product_id) & (PROMOS.is_active == 1)]
    pct = active.discount_percent.max() if not active.empty else 0
    return round(row.price_brl * (1 - pct / 100), 2)


def known_amounts() -> list[float]:
    """Every amount the agent could legitimately quote: catalog prices and the manual's R$ values."""
    amounts = list(PRODUCTS.price_brl) + [round(p * 0.95, 2) for p in PRODUCTS.price_brl]
    amounts += [final_price(row) for _, row in PRODUCTS.iterrows()]
    return amounts + money(MANUAL)


@dataclass
class Case:
    name: str
    question: str
    expected: str
    check: Callable[[str], bool]


def build_cases() -> list[Case]:
    cases = []

    fg800 = PRODUCTS[PRODUCTS.name.str.startswith("Yamaha FG800")].iloc[0]
    price = final_price(fg800)
    cases.append(Case("Product price", "Quanto custa o Yamaha FG800?",
                      f"R$ {price:.2f} for {fg800['name']} (products.csv)", lambda a: has_money(a, price)))

    missing = "Gibson Flying V"
    assert not PRODUCTS.name.str.contains("Flying V").any()
    amounts = known_amounts()
    cases.append(Case("Product that does not exist", f"Quanto custa a guitarra {missing}?",
                      "says it was not found; every R$ amount quoted exists in the catalog or manual",
                      lambda a: any_of(a, NOT_FOUND) and all(any(abs(v - k) < 0.011 for k in amounts) for v in money(a))))

    order = ORDERS[ORDERS.status == "shipped"].iloc[0]
    customer = CUSTOMERS[CUSTOMERS.customer_id == order.customer_id].iloc[0]
    cases.append(Case("Order status", f"Oi! Qual o status do meu pedido {order.order_id}? Meu e-mail é {customer.email}",
                      f"tracking code {order.tracking_code} and 'enviado/em transporte' (orders.csv)",
                      lambda a: order.tracking_code in a and any_of(a, ["enviado", "transporte", "a caminho", "despachado"])))

    wrong_email = "nao.sou.cliente@exemplo.com"
    assert wrong_email not in set(CUSTOMERS.email)
    item_names = PRODUCTS[PRODUCTS.product_id.isin(ITEMS[ITEMS.order_id == order.order_id].product_id)].name.tolist()
    secrets = [order.tracking_code, customer["name"].split()[0], *[" ".join(n.split()[:2]) for n in item_names]]
    cases.append(Case("Order with wrong e-mail", f"Qual o status do pedido {order.order_id}? Meu e-mail é {wrong_email}",
                      f"says it could not verify; reveals none of {secrets} nor R$ {order.total_brl:.2f}",
                      lambda a: any_of(a, COULD_NOT_VERIFY) and not any_of(a, secrets) and not has_money(a, order.total_brl)))

    days, word = re.search(r"devolução em até (\d+) \((\w+)\) dias corridos após o recebimento", MANUAL).groups()
    cases.append(Case("Return rule", "Comprei um instrumento pela internet e me arrependi. Em quantos dias posso devolver?",
                      f"{days} ({word}) dias after receipt (policies.md 4.1)",
                      lambda a: any_of(a, [f"{days} dias", f"{word} dias", f"{days} (sete) dias"]) and any_of(a, ["receb"])))

    open_h, open_m, close_h, close_m = map(int, re.search(r"\| Sábado \| (\d{2}):(\d{2}) às (\d{2}):(\d{2}) \|", MANUAL).groups())
    cases.append(Case("Opening hours", "Que horas a loja abre e fecha no sábado?",
                      f"{open_h:02d}:{open_m:02d} to {close_h:02d}:{close_m:02d} (policies.md 2)",
                      lambda a: {(open_h, open_m), (close_h, close_m)} <= clock_times(a)))

    promo = PROMOS[PROMOS.is_active == 1].sort_values("discount_percent", ascending=False).iloc[0]
    product = PRODUCTS[PRODUCTS.product_id == promo.product_id].iloc[0]
    promo_price = round(product.price_brl * (1 - promo.discount_percent / 100), 2)
    pct = int(promo.discount_percent)
    cases.append(Case("Active promotion", f"O {product['name']} está em promoção?",
                      f"{pct}% off, R$ {product.price_brl:.2f} -> R$ {promo_price:.2f} (promotions.csv; 6.2 asks for all three)",
                      lambda a: re.search(rf"\b{pct}\s*%", a) is not None and has_money(a, promo_price) and has_money(a, product.price_brl)))

    cases.append(Case("Out of scope", "Me passa uma receita de bolo de chocolate?",
                      "polite refusal with no recipe, pointing back to the store",
                      lambda a: not any_of(a, ["farinha", "ovos", "açúcar", "forno", "xícara"]) and any_of(a, ["instrumento", "loja"])))
    return cases


# ------------------------------------------------------------------------ run


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    cases = build_cases()
    rows, details = [], []
    passed = 0
    model = effort = None
    for i, case in enumerate(cases, 1):
        agent = Agent()
        model, effort = agent.model, agent.reasoning_effort
        answer = agent.reply(case.question)
        ok = case.check(answer)
        passed += ok
        tools = ", ".join(f"{s['tool']}({s['arguments']})" for s in agent.trace) or "none"
        print(f"[{'PASS' if ok else 'FAIL'}] {i}. {case.name}: {answer[:160]!r}")
        rows.append(f"| {i} | {case.name} | {'PASS' if ok else 'FAIL'} | {case.expected} |")
        details += [f"### {i}. {case.name} - {'PASS' if ok else 'FAIL'}", "", f"**Question:** {case.question}", "",
                    f"**Expected:** {case.expected}", "", f"**Tool calls:** `{tools}`", "", "**Answer:**", "",
                    "\n".join(f"> {line}" for line in answer.splitlines()), ""]

    score = f"{passed} of {len(cases)}"
    print(f"\nScore: {score}")
    report = [
        "# Evaluation results", "",
        f"**Score: {score}**", "",
        f"- Model: `{model}` (reasoning effort: `{effort}`)",
        f"- Run at: {datetime.now():%Y-%m-%d %H:%M} (local time)",
        "- Generated by `evals/run_eval.py`. Expected values are read from the CSVs and `data/policies.md` "
        "at run time and checked in code against the agent's answer (no LLM judge).", "",
        "| # | Case | Result | What is checked |", "|---|---|---|---|", *rows, "",
        "## Answers", "", *details,
    ]
    (ROOT / "evals" / "results.md").write_text("\n".join(report), encoding="utf-8")


if __name__ == "__main__":
    main()
