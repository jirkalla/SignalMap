"""Import every model module so Base.metadata and the mapper registry are

fully populated as soon as `app.models` is imported — required for Alembic
autogenerate and for string-based relationship() forward refs to resolve.
"""

from app.models.analysis import AnalysisResult, AnalysisSkill
from app.models.base import Base
from app.models.client import Client
from app.models.client_alias import ClientAlias
from app.models.domain_classification import DomainClassification
from app.models.market import Market
from app.models.persona import Persona
from app.models.prompt import Prompt, PromptSet
from app.models.provider import AIModel, AIModelPriceComponent, AIModelPriceHistory, Provider
from app.models.run import Citation, RawResponse, Run, SearchQuery
from app.models.settings import SystemInstructionTemplate
from app.models.tracked_entity import TrackedEntity, TrackedEntityAlias
from app.models.user import User

__all__ = [
    "Base",
    "Client",
    "ClientAlias",
    "DomainClassification",
    "Market",
    "Persona",
    "PromptSet",
    "Prompt",
    "Provider",
    "AIModel",
    "AIModelPriceHistory",
    "AIModelPriceComponent",
    "Run",
    "RawResponse",
    "Citation",
    "SearchQuery",
    "SystemInstructionTemplate",
    "AnalysisSkill",
    "AnalysisResult",
    "TrackedEntity",
    "TrackedEntityAlias",
    "User",
]
