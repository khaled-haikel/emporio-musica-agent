"""Deterministic tools the agent can call, implemented with pandas over data/*.csv.

Business rules live here, never in the prompt: promotional and PIX prices,
availability, the order identity check and the return/warranty deadlines are
computed in code. The model only decides which tool to call and explains the result.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CSV_PREFIX = "desafio_tecnico_ai_eng - "

# Numbers from data/policies.md. tests/test_tools.py fails if the manual stops saying so.
PIX_DISCOUNT_PERCENT = 5  # 3. Formas de Pagamento; not cumulative with promotions (6.2)
REGRET_DAYS = 7  # 4.1 Direito de Arrependimento: days after receipt
PREFERENCE_EXCHANGE_DAYS = 7  # 4.3 Trocas por Preferência
DEFECT_EXCHANGE_DAYS = 30  # 4.2 Trocas por Defeito: days after purchase
LEGAL_WARRANTY_DAYS = 90  # 8.1 Garantia Legal: days after receipt

MAX_RESULTS = 15

STATUS_LABELS = {
    "pending": "pendente (aguardando processamento)",
    "confirmed": "confirmado (em preparação, ainda não enviado)",
    "shipped": "enviado (em transporte)",
    "delivered": "entregue",
    "cancelled": "cancelado",
}
PAYMENT_LABELS = {
    "pix": "PIX",
    "debit": "cartão de débito",
    "boleto": "boleto bancário",
    "credit_3x": "cartão de crédito em 3x",
    "credit_6x": "cartão de crédito em 6x",
    "credit_12x": "cartão de crédito em 12x",
}
STOPWORDS = {"a", "o", "as", "os", "de", "da", "do", "das", "dos", "e", "em", "um", "uma", "para", "com"}


# --------------------------------------------------------------------------- data


def apply_discount(price: float, percent: float) -> float:
    """Price after a percentage discount, rounded half-up to cents."""
    value = Decimal(str(price)) * (Decimal(100) - Decimal(str(percent))) / Decimal(100)
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


@lru_cache(maxsize=1)
def load_tables() -> dict[str, pd.DataFrame]:
    def read(name: str) -> pd.DataFrame:
        return pd.read_csv(DATA_DIR / f"{CSV_PREFIX}{name}.csv", encoding="utf-8")

    categories = read("categories")
    promotions = read("promotions")
    products = read("products").merge(
        categories[["category_id", "name"]].rename(columns={"name": "category"}),
        on="category_id",
        how="left",
    )
    # Promotions are not cumulative (6.2): if a product had two active ones, keep the biggest.
    active = (
        promotions[promotions.is_active == 1]
        .sort_values("discount_percent", ascending=False)
        .drop_duplicates("product_id")[["product_id", "discount_percent", "description"]]
        .rename(columns={"description": "promotion"})
    )
    products = products.merge(active, on="product_id", how="left")
    products["final_price_brl"] = [
        apply_discount(price, pct) if pd.notna(pct) else float(price)
        for price, pct in zip(products.price_brl, products.discount_percent)
    ]
    products["search_text"] = (
        products["name"] + " " + products["category"].fillna("") + " " + products["description"].fillna("")
    )
    return {
        "products": products,
        "categories": categories,
        "orders": read("orders"),
        "order_items": read("order_items"),
        "customers": read("customers"),
    }


def category_names() -> list[str]:
    return load_tables()["categories"]["name"].tolist()


# ----------------------------------------------------------------------- matching


def normalize(text: object) -> str:
    """Lowercase, no accents, no punctuation: 'Violões Elétricos' -> 'violoes eletricos'."""
    ascii_text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", ascii_text.lower()).split())


def _stem(word: str) -> str:
    """Crude Portuguese singular: 'violoes' -> 'violao', 'guitarras' -> 'guitarra'."""
    if word.endswith(("oes", "aes")):
        return word[:-3] + "ao"
    if word.endswith("s") and len(word) > 3:
        return word[:-1]
    return word


def matches(query: str, text: str) -> bool:
    """True when every query word is in the text, tolerating case, accents, plurals and small typos."""
    words = [_stem(w) for w in normalize(text).split()]
    compact = "".join(words)  # so 'GD 20' still finds 'GD20'
    tokens = [_stem(w) for w in normalize(query).split() if w not in STOPWORDS]
    return bool(tokens) and all(
        token in compact or any(w.startswith(token) or SequenceMatcher(None, token, w).ratio() >= 0.8 for w in words)
        for token in tokens
    )


# ------------------------------------------------------------------------ records


def _clean(value: object) -> object:
    return None if pd.isna(value) else value


def _availability(p: dict) -> str:
    if p["status"] == "discontinued":
        return "descontinuado"
    if p["status"] == "coming_soon":
        return "em breve"
    return "em estoque" if p["stock_quantity"] > 0 else "esgotado"


def _product_record(p: dict, detailed: bool = False) -> dict:
    on_sale = pd.notna(p["discount_percent"])
    record = {
        "product_id": int(p["product_id"]),
        "name": p["name"],
        "category": p["category"],
        "price_brl": float(p["price_brl"]),
        "promotion": (
            {"name": p["promotion"], "discount_percent": int(p["discount_percent"]), "promo_price_brl": p["final_price_brl"]}
            if on_sale
            else None
        ),
        "final_price_brl": p["final_price_brl"],
        # PIX discount does not apply on top of a promotional price (6.2).
        "pix_price_brl": p["final_price_brl"] if on_sale else apply_discount(p["price_brl"], PIX_DISCOUNT_PERCENT),
        "availability": _availability(p),
    }
    if p["status"] == "active":
        record["stock_quantity"] = int(p["stock_quantity"])
    if detailed:
        record["description"] = p["description"]
        record["specs"] = json.loads(p["specs"]) if isinstance(p["specs"], str) else {}
    return record


def _alternatives(p: dict, limit: int = 3) -> list[dict]:
    """In-stock products of the same category with the closest price (7.3: suggest alternatives)."""
    products = load_tables()["products"]
    pool = products[
        (products.category_id == p["category_id"])
        & (products.product_id != p["product_id"])
        & (products.status == "active")
        & (products.stock_quantity > 0)
    ]
    pool = pool.assign(gap=(pool.final_price_brl - p["final_price_brl"]).abs()).sort_values("gap")
    return [_product_record(row) for row in pool.head(limit).to_dict("records")]


# -------------------------------------------------------------------------- tools


def search_products(
    query: str | None = None,
    category: str | None = None,
    max_price: float | None = None,
    min_price: float | None = None,
    in_stock_only: bool | None = True,
) -> dict:
    """Catalog search. Price filters use the final price (after any active promotion)."""
    tables = load_tables()
    df = tables["products"]
    if category:
        cats = tables["categories"]
        ids = cats[cats["name"].map(lambda name: matches(category, name))].category_id
        if ids.empty:
            return {"error": f"Categoria desconhecida: {category}", "valid_categories": category_names()}
        df = df[df.category_id.isin(ids)]
    if in_stock_only is not False:
        df = df[(df.status == "active") & (df.stock_quantity > 0)]
    if query:
        df = df[df.search_text.map(lambda text: matches(query, text))]
    if max_price is not None:
        df = df[df.final_price_brl <= max_price]
    if min_price is not None:
        df = df[df.final_price_brl >= min_price]
    df = df.sort_values(["final_price_brl", "name"])
    result = {
        "total_matches": len(df),
        "products": [_product_record(p) for p in df.head(MAX_RESULTS).to_dict("records")],
    }
    if len(df) > MAX_RESULTS:
        result["note"] = f"Mostrando os {MAX_RESULTS} mais baratos de {len(df)}; refine a busca para ver outros."
    return result


def get_product(name_or_sku: str) -> dict:
    """Price, stock and details of one product, by product_id (the data has no SKU) or name."""
    products = load_tables()["products"]
    key = str(name_or_sku).strip()
    if re.fullmatch(r"#?\d+", key):
        hits = products[products.product_id == int(key.lstrip("#"))]
    else:
        target = normalize(key)
        names = products["name"].map(normalize)
        hits = products[names == target]
        if hits.empty:
            hits = products[products["name"].map(lambda name: matches(key, name))]
        if len(hits) > 1:  # 'Martin D-28' should pick the D-28, not the D-28E
            prefixed = hits[(names[hits.index] + " ").str.startswith(target + " ")]
            hits = prefixed if len(prefixed) == 1 else hits
    if hits.empty:
        return {"found": False, "reason": "not_found", "query": key}
    if len(hits) > 1:
        return {
            "found": False,
            "reason": "ambiguous",
            "candidates": [{"product_id": int(p["product_id"]), "name": p["name"]} for p in hits.to_dict("records")],
        }
    product = hits.iloc[0].to_dict()
    record = _product_record(product, detailed=True)
    if record["availability"] != "em estoque":
        record["alternatives"] = _alternatives(product)
    return {"found": True, "product": record}


def get_active_promotions() -> dict:
    """Promotions with is_active = 1, biggest discount first."""
    products = load_tables()["products"]
    on_sale = products[products.discount_percent.notna()].sort_values("discount_percent", ascending=False)
    return {"total": len(on_sale), "promotions": [_product_record(p) for p in on_sale.to_dict("records")]}


def get_order_status(order_id: int, email: str, today: date | None = None) -> dict:
    """Order status, only after the e-mail matches the customer who placed the order.

    A wrong e-mail and a nonexistent order get the same answer, so the tool never
    reveals whether an order exists or whose it is.
    """
    if not str(email or "").strip():
        return {"verified": False, "reason": "email_required", "message": "Peça o e-mail usado na compra."}
    tables = load_tables()
    orders = tables["orders"]
    order = orders[orders.order_id == int(order_id)]
    customer = None
    if not order.empty:
        customers = tables["customers"]
        customer = customers[customers.customer_id == order.iloc[0].customer_id]
    if customer is None or customer.empty or normalize_email(customer.iloc[0].email) != normalize_email(email):
        return {
            "verified": False,
            "reason": "not_verified",
            "message": "Não há pedido com esse número e esse e-mail. Nenhum dado do pedido pode ser informado.",
        }

    o = order.iloc[0].to_dict()
    items = tables["order_items"]
    items = items[items.order_id == o["order_id"]].merge(
        tables["products"][["product_id", "name"]], on="product_id", how="left"
    )
    return {
        "verified": True,
        "order_id": int(o["order_id"]),
        "customer_name": customer.iloc[0]["name"],
        "order_date": o["order_date"],
        "status": o["status"],
        "status_label": STATUS_LABELS.get(o["status"], o["status"]),
        "total_brl": float(o["total_brl"]),
        "payment_method": PAYMENT_LABELS.get(o["payment_method"], o["payment_method"]),
        "tracking_code": _clean(o["tracking_code"]),
        "estimated_delivery": _clean(o["estimated_delivery"]),
        "notes": _clean(o["notes"]),
        "items": [
            {"product_id": int(i["product_id"]), "name": i["name"], "quantity": int(i["quantity"])}
            for i in items.to_dict("records")
        ],
        "after_sales": after_sales(o, today or date.today()),
    }


def normalize_email(email: object) -> str:
    return str(email).strip().lower()


def _window(start: date, days: int, today: date) -> dict:
    deadline = start + timedelta(days=days)
    return {"open": today <= deadline, "deadline": deadline.isoformat()}


def after_sales(order: dict, today: date) -> dict:
    """Return, exchange and warranty windows for an order (manual sections 4 and 8)."""
    status = order["status"]
    info: dict = {"as_of": today.isoformat()}
    if status == "delivered":
        # The data has no actual delivery date; estimated_delivery is used as the receipt date.
        received = date.fromisoformat(order["estimated_delivery"])
        purchased = date.fromisoformat(order["order_date"])
        info.update(
            {
                "received_on": received.isoformat(),
                "days_since_receipt": (today - received).days,
                "regret_return_7d": _window(received, REGRET_DAYS, today),  # 4.1
                "preference_exchange_7d": _window(received, PREFERENCE_EXCHANGE_DAYS, today),  # 4.3
                "defect_exchange_30d_from_purchase": _window(purchased, DEFECT_EXCHANGE_DAYS, today),  # 4.2
                "legal_warranty_90d": _window(received, LEGAL_WARRANTY_DAYS, today),  # 8.1
            }
        )
        info["if_defective"] = (
            "troca direto na loja (4.2)"
            if info["defect_exchange_30d_from_purchase"]["open"]
            else "acionar a garantia do fabricante, com prazo no certificado do produto (8.2); "
            "a loja pode intermediar se o cliente pedir (4.2)"
        )
    elif status == "shipped":
        info["note"] = "Ainda em transporte: o prazo de arrependimento de 7 dias começa no recebimento."
        eta = _clean(order.get("estimated_delivery"))
        info["delivery_overdue"] = bool(eta) and today > date.fromisoformat(eta)
    elif status in ("pending", "confirmed"):
        info["note"] = (
            "Pedido ainda não enviado. O manual não traz regra de cancelamento antes do envio: "
            "encaminhar o pedido para a equipe da loja."
        )
    elif status == "cancelled":
        info["note"] = "Pedido cancelado; não há devolução a fazer."
    return info


# ------------------------------------------------------------- schemas + dispatch


def tool_schemas() -> list[dict]:
    """Function-tool definitions for the OpenAI Responses API (strict mode: every field required, null = unset)."""
    return [
        {
            "type": "function",
            "name": "search_products",
            "description": (
                "Busca produtos no catálogo da loja. Use para perguntas abertas (ex.: 'violões até R$ 1000'). "
                "Filtros de preço usam o preço final, já com promoção ativa. Retorna preço de tabela, promoção, "
                "preço final, preço no PIX e disponibilidade."
            ),
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": ["string", "null"], "description": "Palavras-chave (marca, modelo, tipo). null para não filtrar."},
                    "category": {"type": ["string", "null"], "enum": [*category_names(), None], "description": "Categoria exata ou null."},
                    "max_price": {"type": ["number", "null"], "description": "Preço final máximo em reais."},
                    "min_price": {"type": ["number", "null"], "description": "Preço final mínimo em reais."},
                    "in_stock_only": {"type": ["boolean", "null"], "description": "true (padrão) para só itens à venda e com estoque."},
                },
                "required": ["query", "category", "max_price", "min_price", "in_stock_only"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "get_product",
            "description": (
                "Preço, estoque e detalhes de um produto específico, pelo nome (tolerante a maiúsculas e acentos) "
                "ou pelo código (product_id). Se o produto não estiver disponível, sugere alternativas."
            ),
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {"name_or_sku": {"type": "string", "description": "Nome do produto ou product_id."}},
                "required": ["name_or_sku"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "get_order_status",
            "description": (
                "Status, itens, rastreio e prazos de devolução/troca/garantia de um pedido. Exige o número do pedido "
                "e o e-mail do cliente; sem e-mail que confira, nenhum dado é retornado."
            ),
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "integer", "description": "Número do pedido."},
                    "email": {"type": "string", "description": "E-mail informado pelo cliente."},
                },
                "required": ["order_id", "email"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "get_active_promotions",
            "description": "Lista as promoções ativas agora, com preço original, desconto e preço promocional.",
            "strict": True,
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        },
    ]


FUNCTIONS = {
    "search_products": search_products,
    "get_product": get_product,
    "get_order_status": get_order_status,
    "get_active_promotions": get_active_promotions,
}


def run_tool(name: str, arguments: str | dict | None) -> dict:
    """Execute one tool call from the model. Errors go back to the model as data, not exceptions."""
    try:
        args = json.loads(arguments or "{}") if isinstance(arguments, str) else dict(arguments or {})
        return FUNCTIONS[name](**args)
    except Exception as exc:  # noqa: BLE001 - the model gets the message and can retry or apologise
        return {"error": f"{type(exc).__name__}: {exc}"}
