"""Import every model module so Base.metadata and the mapper registry are

fully populated as soon as `app.models` is imported — required for Alembic
autogenerate and for string-based relationship() forward refs to resolve.
"""

from app.models.base import Base
from app.models.client import Client
from app.models.market import Market
from app.models.prompt import Prompt, PromptSet
from app.models.provider import AIModel, Provider
from app.models.run import Citation, RawResponse, Run, SearchQuery
from app.models.settings import SystemInstructionTemplate

__all__ = [
    "Base",
    "Client",
    "Market",
    "PromptSet",
    "Prompt",
    "Provider",
    "AIModel",
    "Run",
    "RawResponse",
    "Citation",
    "SearchQuery",
    "SystemInstructionTemplate",
]
