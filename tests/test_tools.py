from datetime import date

from emporio import tools
from emporio.tools import get_order_status, get_product, search_products


def test_price_filter_returns_only_affordable_items_in_stock():
    result = search_products(category="Violões", max_price=1000, in_stock_only=True)
    names = {p["name"] for p in result["products"]}

    assert result["total_matches"] > 0
    assert all(p["final_price_brl"] <= 1000 for p in result["products"])
    assert all(p["availability"] == "em estoque" for p in result["products"])
    assert "Tagima Memphis AC-39 Nylon Natural" in names  # R$ 429,90
    assert "Takamine GD20 Dreadnought Natural" not in names  # R$ 2.199,00
    assert "Giannini GF-3D Dreadnought Sunburst" not in names  # R$ 799,90 but out of stock


def test_price_filter_uses_promotional_price_and_pix_does_not_stack():
    result = search_products(category="Violões", min_price=1900, max_price=2000)
    crafter = next(p for p in result["products"] if p["product_id"] == 90)  # list R$ 2.399, 18% off

    assert crafter["price_brl"] == 2399.0
    assert crafter["final_price_brl"] == 1967.18
    assert crafter["pix_price_brl"] == crafter["final_price_brl"]


def test_get_product_ignores_case_and_accents():
    result = get_product("takamine gd20")

    assert result["found"] is True
    assert result["product"]["price_brl"] == 2199.0
    assert result["product"]["pix_price_brl"] == 2089.05


def test_unknown_product_is_not_found():
    result = get_product("Piano de cauda Steinway Model D")

    assert result == {"found": False, "reason": "not_found", "query": "Piano de cauda Steinway Model D"}


def test_order_with_matching_email_returns_status_and_return_window():
    result = get_order_status(6, "  Gabriel.Araujo@JMAIL.com ", today=date(2026, 2, 5))

    assert result["verified"] is True
    assert result["status"] == "delivered"
    assert result["tracking_code"] == "BRVN6543210BR"
    assert result["after_sales"]["regret_return_7d"] == {"open": True, "deadline": "2026-02-08"}

    later = get_order_status(6, "gabriel.araujo@jmail.com", today=date(2026, 10, 27))
    assert later["after_sales"]["regret_return_7d"]["open"] is False


def test_order_with_wrong_email_reveals_nothing():
    wrong_email = get_order_status(6, "outra.pessoa@jmail.com")
    missing_order = get_order_status(999, "outra.pessoa@jmail.com")

    assert wrong_email["verified"] is False
    assert set(wrong_email) == {"verified", "reason", "message"}
    assert wrong_email == missing_order  # same answer: no hint that order 6 exists


def test_policy_constants_match_the_manual():
    manual = (tools.DATA_DIR / "policies.md").read_text(encoding="utf-8")

    assert f"PIX ({tools.PIX_DISCOUNT_PERCENT}%)" in manual
    assert f"{tools.REGRET_DAYS} (sete) dias corridos após o recebimento" in manual
    assert f"em até {tools.PREFERENCE_EXCHANGE_DAYS} dias, mediante disponibilidade" in manual
    assert f"{tools.DEFECT_EXCHANGE_DAYS} (trinta) dias corridos após a compra" in manual
    assert f"{tools.LEGAL_WARRANTY_DAYS} (noventa) dias contra defeitos" in manual
