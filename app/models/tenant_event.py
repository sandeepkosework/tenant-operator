from sqlalchemy import Column, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class TenantEvent(Base):
    """One line of a tenant's creation history, shown as the step-by-step
    progress in the web UI. Append-only; written best-effort by
    services/progress.record() and never read by the provisioning logic."""

    __tablename__ = "tenant_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    step = Column(String(32), nullable=False)    # key from progress.CREATE_STEPS
    state = Column(String(8), nullable=False)    # start | info | done | warn | skip
    message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
