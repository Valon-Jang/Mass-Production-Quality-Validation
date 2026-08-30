"""HTTP adapters for Mass Production Quality Validation."""

from app.api.bulk import create_bulk_router
from app.api.bulk_finalization import create_bulk_finalization_router
from app.api.configuration import create_configuration_router
from app.api.data_review import create_data_review_router
from app.api.historical_comparison import create_historical_comparison_router
from app.api.intake import create_intake_router
from app.api.long import create_long_router
from app.api.mapping import create_mapping_registration_router, create_mapping_router
from app.api.result_replacement import create_result_replacement_router

__all__ = [
    "create_bulk_finalization_router",
    "create_bulk_router",
    "create_configuration_router",
    "create_data_review_router",
    "create_historical_comparison_router",
    "create_intake_router",
    "create_long_router",
    "create_mapping_registration_router",
    "create_mapping_router",
    "create_result_replacement_router",
]
