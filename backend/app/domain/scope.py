"""Explicit negative scope for every Mass Production Quality Validation release.

These capabilities are intentionally absent from the product.  Keeping the
requirement IDs in executable code makes the release guard precise without
blocking legitimate future ports such as a provider-neutral Scheduler
contract or supplier-quality analytics.
"""

from enum import StrEnum


class ExcludedCapability(StrEnum):
    PHOTO_AI_ANALYSIS = "PHOTO_AI_ANALYSIS"
    MEASUREMENT_DEVICE_CALIBRATION = "MEASUREMENT_DEVICE_CALIBRATION"
    SUPPLIER_RESPONSE_SPEED_SCORING = "SUPPLIER_RESPONSE_SPEED_SCORING"
    SUPPLIER_EMAIL_AUTO_SEND = "SUPPLIER_EMAIL_AUTO_SEND"
    AUTOMATIC_SHIPMENT_HOLD = "AUTOMATIC_SHIPMENT_HOLD"
    AUTOMATIC_MASTER_SPEC_CHANGE = "AUTOMATIC_MASTER_SPEC_CHANGE"
    AUTOMATIC_SUPPLY_RATIO_DECISION = "AUTOMATIC_SUPPLY_RATIO_DECISION"
    CLAIM_BASED_MARKET_NO_ISSUE_SCORING = "CLAIM_BASED_MARKET_NO_ISSUE_SCORING"


EXCLUDED_SCOPE_REQUIREMENTS: dict[str, ExcludedCapability] = {
    "EXC-001": ExcludedCapability.PHOTO_AI_ANALYSIS,
    "EXC-002": ExcludedCapability.MEASUREMENT_DEVICE_CALIBRATION,
    "EXC-003": ExcludedCapability.SUPPLIER_RESPONSE_SPEED_SCORING,
    "EXC-004": ExcludedCapability.SUPPLIER_EMAIL_AUTO_SEND,
    "EXC-005": ExcludedCapability.AUTOMATIC_SHIPMENT_HOLD,
    "EXC-006": ExcludedCapability.AUTOMATIC_MASTER_SPEC_CHANGE,
    "EXC-007": ExcludedCapability.AUTOMATIC_SUPPLY_RATIO_DECISION,
    "EXC-008": ExcludedCapability.CLAIM_BASED_MARKET_NO_ISSUE_SCORING,
}
