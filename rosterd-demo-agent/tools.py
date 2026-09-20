"""Tool definitions for the e-commerce demo agent.

These schemas ARE the contract: rosterd's ingestion reads the Field()
constraints below to infer runtime rules. Keep them literal and simple.
"""

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field


class ReserveInventoryArgs(BaseModel):
    sku: str
    qty: int = Field(le=50)  # fixed sane cap; not modeling live stock


class IssueRefundArgs(BaseModel):
    order_id: str
    amount: float = Field(le=100)


class CheckStockArgs(BaseModel):
    sku: str


class ChargePaymentArgs(BaseModel):
    order_id: str
    amount: float = Field(le=2000)  # a new charge can legitimately run higher than a refund cap


def _reserve_inventory(sku: str, qty: int) -> str:
    return f"reserved {qty} x {sku}"


def _issue_refund(order_id: str, amount: float) -> str:
    return f"refunded ${amount:.2f} on order {order_id}"


def _check_stock(sku: str) -> str:
    return f"{sku}: in stock"


def _charge_payment(order_id: str, amount: float) -> str:
    return f"charged ${amount:.2f} on order {order_id}"


reserve_inventory = StructuredTool.from_function(
    func=_reserve_inventory,
    name="reserve_inventory",
    description="Reserve inventory units for a SKU.",
    args_schema=ReserveInventoryArgs,
)

issue_refund = StructuredTool.from_function(
    func=_issue_refund,
    name="issue_refund",
    description="Issue a refund for an order.",
    args_schema=IssueRefundArgs,
)

check_stock = StructuredTool.from_function(
    func=_check_stock,
    name="check_stock",
    description="Check catalog stock availability for a SKU.",
    args_schema=CheckStockArgs,
)

charge_payment = StructuredTool.from_function(
    func=_charge_payment,
    name="charge_payment",
    description="Charge payment for an order.",
    args_schema=ChargePaymentArgs,
)
