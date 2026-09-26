"""Pydantic models shared across the procurement agent and the eval harness."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Action(str, Enum):
    AUTO_ACCEPT = "auto_accept"
    REQUEST_CLARIFICATION = "request_clarification"
    ESCALATE_HUMAN = "escalate_human"


class QuoteLineItem(BaseModel):
    description: str
    qty: float | None = None
    unit_price: float | None = None


class ExtractedQuote(BaseModel):
    """What extract_quote produces, from either the LLM or the data generator."""

    supplier: str | None = None
    currency: str | None = None
    total: float | None = None
    items: list[QuoteLineItem] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)


class CatalogMatch(BaseModel):
    line_index: int
    description: str
    matched_sku: str | None = None
    matched_name: str | None = None
    catalog_unit_price_eur: float | None = None
    quoted_unit_price_eur: float | None = None
    deviation_pct: float | None = None
    matched: bool = False


class ComparisonResult(BaseModel):
    matches: list[CatalogMatch] = Field(default_factory=list)
    unmatched_items: list[str] = Field(default_factory=list)
    conflicting: bool = False
    conflict_reason: str | None = None
    max_deviation_pct: float = 0.0


class Decision(BaseModel):
    action: Action
    reason: str
