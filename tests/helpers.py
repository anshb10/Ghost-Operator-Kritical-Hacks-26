"""Build offers and pages by hand for adversarial cases that no real recording covers."""
from backend.models import Offer, Requirements, Warranty
from backend.ml.normalize import parse_warranty


def offer(url="https://shop.test/p", price=1000.0, currency="INR", **attrs) -> Offer:
    w = attrs.pop("warranty", "")
    stock = attrs.pop("stock", "In stock")
    sku = attrs.pop("sku", "")
    name = attrs.pop("name", "Test Laptop 14")
    o = Offer(id=f"t{abs(hash((url, price, name))) % 10**8}", vendor=attrs.pop("vendor", "Test Vendor"), name=name,
              sku=sku, unit_price=price, currency=currency, url=url, warranty=parse_warranty(w) if w else Warranty(),
              stock=stock, seller=attrs.pop("seller", "Test Vendor"), condition="new", attributes=attrs)
    o.attributes.setdefault("warranty_years", o.warranty.years or 0.0)
    o.attributes.setdefault("warranty_onsite", 1.0 if o.warranty.service == "onsite" else 0.0)
    return o


def req(currency="INR", quantity=10, budget=None, deadline=None, domain="laptops_in", **kw) -> Requirements:
    from backend.models import Constraint
    cons = kw.pop("constraints", [Constraint(attribute="ram_gb", op=">=", value=16, hard=True, weight=2)])
    return Requirements(domain=domain, item="laptops", quantity=quantity, currency=currency, budget_total=budget,
                        deadline_days=deadline, constraints=cons, **kw)


def verify_one(o: Offer, page_text: str | None, quantity: int = 10) -> Offer:
    from backend import verify
    pages = {o.url: {"title": "", "text": page_text, "fetched_at": "2026-10-20T00:00:00+00:00",
                     "provenance": {"kind": "synthetic", "source": "unit test"}}} if page_text is not None else {}
    verify.verify_with_pages([o], pages, quantity)
    return o
