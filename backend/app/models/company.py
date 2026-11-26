"""Companies, directors, promoters and shareholding structure."""
from __future__ import annotations

from datetime import date

from sqlalchemy import Boolean, Date, Float, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, TimestampMixin


class Company(Base, TimestampMixin):
    __tablename__ = "companies"
    __table_args__ = (
        UniqueConstraint("organization_id", "cin", name="uq_company_org_cin"),
        UniqueConstraint("organization_id", "pan", name="uq_company_org_pan"),
        UniqueConstraint("organization_id", "gstin", name="uq_company_org_gstin"),
        Index("ix_company_org_name", "organization_id", "name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    cin: Mapped[str | None] = mapped_column(String(21))
    pan: Mapped[str | None] = mapped_column(String(10))
    gstin: Mapped[str | None] = mapped_column(String(15))
    industry: Mapped[str] = mapped_column(String(120), nullable=False)
    business_type: Mapped[str] = mapped_column(String(80), nullable=False)
    incorporation_date: Mapped[date | None] = mapped_column(Date)
    registered_address: Mapped[str | None] = mapped_column(Text)
    headquarters: Mapped[str | None] = mapped_column(String(200))
    annual_turnover: Mapped[float | None] = mapped_column(Numeric(20, 2, asdecimal=False))
    website: Mapped[str | None] = mapped_column(String(255))
    contact_person: Mapped[str | None] = mapped_column(String(200))
    contact_email: Mapped[str | None] = mapped_column(String(255))
    contact_phone: Mapped[str | None] = mapped_column(String(40))
    business_description: Mapped[str | None] = mapped_column(Text)
    subsidiaries: Mapped[str | None] = mapped_column(Text)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    demo_scenario: Mapped[str | None] = mapped_column(String(60))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    directors: Mapped[list["Director"]] = relationship(back_populates="company", cascade="all, delete-orphan")
    promoters: Mapped[list["Promoter"]] = relationship(back_populates="company", cascade="all, delete-orphan")
    shareholdings: Mapped[list["Shareholding"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    applications: Mapped[list["CreditApplication"]] = relationship(  # noqa: F821
        back_populates="company", cascade="all, delete-orphan"
    )


class Director(Base, TimestampMixin):
    __tablename__ = "directors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    din: Mapped[str | None] = mapped_column(String(8))
    designation: Mapped[str | None] = mapped_column(String(120))
    appointment_date: Mapped[date | None] = mapped_column(Date)
    is_independent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    company: Mapped[Company] = relationship(back_populates="directors")


class Promoter(Base, TimestampMixin):
    __tablename__ = "promoters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    pan: Mapped[str | None] = mapped_column(String(10))
    shareholding_pct: Mapped[float | None] = mapped_column(Float)
    experience_years: Mapped[int | None] = mapped_column(Integer)
    background: Mapped[str | None] = mapped_column(Text)
    net_worth: Mapped[float | None] = mapped_column(Numeric(20, 2, asdecimal=False))

    company: Mapped[Company] = relationship(back_populates="promoters")


class Shareholding(Base, TimestampMixin):
    __tablename__ = "shareholdings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    holder_name: Mapped[str] = mapped_column(String(200), nullable=False)
    holder_type: Mapped[str] = mapped_column(String(40), nullable=False, default="other")
    percentage: Mapped[float] = mapped_column(Float, nullable=False)
    shares: Mapped[int | None] = mapped_column(Integer)
    as_of_date: Mapped[date | None] = mapped_column(Date)
    pledged_pct: Mapped[float | None] = mapped_column(Float)

    company: Mapped[Company] = relationship(back_populates="shareholdings")
