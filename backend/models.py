from typing import Literal, Optional

from pydantic import AliasChoices, BaseModel, Field, field_validator


class Constraint(BaseModel):
    attribute: str = Field(description="Normalized attribute key, e.g. ram_gb, storage_gb, os, warranty_years")
    op: Literal[">=", "<=", "==", "contains"]
    value: str | float
    hard: bool = Field(description="True = must-have (filters out offers). False = preference (affects score).")
    weight: float = Field(default=1.0, description="Importance for soft constraints, 0-3")

    @field_validator("value", mode="before")
    @classmethod
    def _ints_are_floats(cls, v):
        return float(v) if isinstance(v, int) and not isinstance(v, bool) else v


class Requirements(BaseModel):
    domain: str = Field(description="One of the configured domain keys, e.g. laptops, lab_supplies, laptops_in")
    item: str = Field(description="Short noun phrase for what is being bought")
    quantity: int
    currency: Literal["USD", "INR"] = Field(default="USD", description="Currency of the budget and of all prices")
    # Older missions and clients send budget_total_usd / max_unit_price_usd; both names are accepted.
    budget_total: Optional[float] = Field(default=None, validation_alias=AliasChoices("budget_total", "budget_total_usd"),
                                          description="Total budget in `currency`, including tax and delivery")
    max_unit_price: Optional[float] = Field(default=None, validation_alias=AliasChoices("max_unit_price", "max_unit_price_usd"),
                                            description="Per-unit price ceiling in `currency`")
    constraints: list[Constraint]
    deadline_days: Optional[int] = Field(default=None, description="Goods must arrive within this many days")
    tax_rate_pct: Optional[float] = Field(default=None, description="Buyer-supplied sales-tax/GST rate override, percent")
    ship_to_state: str = Field(default="", description="Delivery state (India: decides CGST+SGST vs IGST)")
    notes: str = ""


class Warranty(BaseModel):
    raw: str = ""
    years: Optional[float] = None
    service: Literal["onsite", "depot", "mail_in", "carry_in", "unknown", "none"] = "unknown"


class Offer(BaseModel):
    id: str
    vendor: str
    name: str
    sku: str = ""
    unit_price: Optional[float] = None
    currency: str = "USD"
    url: str = ""
    attributes: dict = {}
    warranty: Warranty = Warranty()
    seller: str = ""
    condition: Literal["new", "refurbished", "used", "unknown"] = "unknown"
    stock: str = ""
    evidence: dict = {}  # raw extracted fields from TinyFish, kept for the audit trail
    provenance: dict = {}  # {"kind": live|recorded|synthetic, "source": ...}: where this offer's data came from
    verification: dict = {}  # Fetch API second-source check (see verify.py)
    effective_unit_price: Optional[float] = None  # set when a promo price only covers part of the quantity

    # Evidence-first layer (evidence.py, adversarial.py)
    claims: list[dict] = []  # one entry per important claim: status verified|contradicted|inferred|unknown + evidence
    terms: dict = {}  # commercial terms read from the product page: shipping, tax inclusion, MOQ, lead time, fees
    flags: list[dict] = []  # adversarial findings: misleading unit basis, hidden charges, conflicts, ...

    # Filled by the analysis engine
    group_id: Optional[str] = None
    passes_hard: bool = True
    failed_constraints: list[str] = []
    fit_score: float = 0.0
    warranty_risk: float = 0.0
    warranty_risk_reasons: list[str] = []
    fair_price: Optional[float] = None
    price_delta_pct: Optional[float] = None
    anomaly: Optional[str] = None
    price_flag: Optional[Literal["deal", "suspicious", "overpriced", "outlier"]] = None
    landed: dict = {}  # landed-cost breakdown (landed_cost.py)
    risk: dict = {}  # supplier risk radar (risk.py)
    history: dict = {}  # prior observations of a comparable configuration (memory.py)
    tco: Optional[float] = None
    tco_breakdown: dict = {}
    score: float = 0.0
    score_parts: dict = {}  # weighted contribution of each scoring term, for explanations
    rank: Optional[int] = None
