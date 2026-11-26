"""ORM models. Importing this package registers every table on ``Base.metadata``."""
from app.models.org import Membership, Organization, RevokedToken, User  # noqa: F401
from app.models.company import Company, Director, Promoter, Shareholding  # noqa: F401
from app.models.application import CreditApplication, ProcessingJob, ReviewComment  # noqa: F401
from app.models.document import (  # noqa: F401
    DocumentChunk,
    DocumentExtraction,
    DocumentSignal,
    ExtractedFinancialMetric,
    UploadedDocument,
)
from app.models.financial import (  # noqa: F401
    BankTransaction,
    FinancialRatio,
    FinancialStatement,
    GSTRecord,
    Reconciliation,
)
from app.models.research import PrimaryObservation, ResearchFinding, ResearchQuery  # noqa: F401
from app.models.risk import (  # noqa: F401
    LoanRecommendation,
    ModelVersion,
    ReviewDecision,
    RiskAssessment,
    RiskDimension,
    ScoringPolicy,
)
from app.models.misc import AuditLog, CAMReport, ChatMessage, ChatSession, Notification  # noqa: F401
