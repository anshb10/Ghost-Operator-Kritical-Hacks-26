"""Landed cost: every expected value below is computed by hand in the comment next to it."""
from backend.landed_cost import landed_cost

from .helpers import offer, req, verify_one

GST = {"kind": "gst", "rate_pct": 18, "hsn": "8471", "source": "test"}


def lines(lc):
    return {l["key"]: l for l in lc["lines"]}


def test_tax_inclusive_is_not_added_again():
    o = verify_one(offer(price=63900), "Price: ₹63,900 (inclusive of all taxes)\nFree delivery.", 25)
    lc = landed_cost(o, req(quantity=25), GST)
    assert lc["total"] == 1_597_500.00  # 25 × 63,900, GST already inside
    tax = lines(lc)["tax"]
    assert tax["included_in_price"] and tax["status"] == "confirmed"
    assert tax["amount"] == round(1_597_500 - 1_597_500 / 1.18, 2)  # the GST portion, shown for the invoice
    assert lc["confidence"] == "confirmed" and lc["unknowns"] == []


def test_gst_extra_applies_to_goods_delivery_and_fees():
    page = "Price: ₹54,500 + GST\nDelivery charges: ₹1,200 per unit\nInstallation fee: ₹500 per unit"
    o = verify_one(offer(price=54500), page, 25)
    lc = landed_cost(o, req(quantity=25), GST)
    # goods 1,362,500 + delivery 30,000 + installation 12,500 = 1,405,000; × 1.18 = 1,657,900
    assert lines(lc)["delivery"]["amount"] == 30_000
    assert lines(lc)["fee"]["amount"] == 12_500
    assert lines(lc)["tax"]["amount"] == 252_900
    assert lc["total"] == 1_657_900
    assert lc["confidence"] == "estimated"  # the GST rate is configured, not stated by this vendor


def test_promo_cap_costs_the_rest_at_list_price():
    page = "Deal price: ₹49,999 ~~₹66,999~~ (inclusive of all taxes)\nDeal price: Max 2 units per order\nFree delivery"
    o = verify_one(offer(price=49999), page, 25)
    lc = landed_cost(o, req(quantity=25), GST)
    # 2 × 49,999 + 23 × 66,999 = 99,998 + 1,540,977 = 1,640,975
    assert lines(lc)["goods_promo"]["amount"] == 99_998
    assert lines(lc)["goods_list"]["amount"] == 1_540_977
    assert lines(lc)["goods_list"]["status"] == "confirmed"
    assert lc["total"] == 1_640_975


def test_cap_without_a_list_price_is_unknown_not_assumed():
    o = verify_one(offer(price=50000), "Price ₹50,000 incl. GST. Max 5 units per order.", 20)
    lc = landed_cost(o, req(quantity=20), GST)
    assert lines(lc)["goods_list"]["amount"] is None
    assert lc["confidence"] == "incomplete"
    assert lc["total"] == 250_000  # only the 5 promo units are known; the other 15 are listed as unknown


def test_volume_discount_only_when_quantity_qualifies():
    page = "Price: ₹55,800 + GST\n5% off on 20+ units\nFree delivery"
    o = verify_one(offer(price=55800), page, 25)
    lc = landed_cost(o, req(quantity=25), GST)
    # 1,395,000 − 5% (69,750) = 1,325,250; × 1.18 = 1,563,795
    assert lines(lc)["discount"]["amount"] == -69_750
    assert lc["total"] == 1_563_795
    small = landed_cost(o, req(quantity=10), GST)
    assert lines(small)["discount"]["amount"] == 0  # 10 < 20: shown as not qualifying, nothing deducted
    assert small["total"] == round(558_000 * 1.18, 2)


def test_us_sales_tax_unknown_unless_buyer_gives_rate():
    o = verify_one(offer(price=1299.0, currency="USD"), "Price $1,299.00\nFREE Storewide Shipping", 50)
    lc = landed_cost(o, req(currency="USD", quantity=50, domain="laptops"), None)
    assert lc["total"] == 64_950  # tax unknown → excluded and listed, not guessed
    assert "Sales tax" in lc["unknowns"] and lc["confidence"] == "incomplete"
    lc2 = landed_cost(o, req(currency="USD", quantity=50, domain="laptops", tax_rate_pct=8.25), None)
    assert lc2["total"] == round(64_950 * 1.0825, 2)
    assert lines(lc2)["tax"]["status"] == "estimated"


def test_unstated_tax_in_india_is_an_upper_bound_estimate():
    o = verify_one(offer(price=10000), "Price ₹10,000. Free delivery.", 10)
    lc = landed_cost(o, req(quantity=10), GST)
    assert lc["total"] == 118_000
    assert "upper bound" in lines(lc)["tax"]["basis"]


def test_agent_only_price_is_an_estimate():
    o = verify_one(offer(price=10000), None, 10)  # page never fetched
    lc = landed_cost(o, req(quantity=10), GST)
    assert lines(lc)["goods"]["status"] == "estimated"
    assert "unknown" == lines(lc)["delivery"]["status"]
