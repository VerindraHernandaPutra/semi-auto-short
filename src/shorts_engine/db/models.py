import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)

from .base import Base


def utc_now():
    return datetime.datetime.now(datetime.UTC)


class Topic(Base):
    __tablename__ = "topics"

    id = Column(String, primary_key=True, index=True)
    text = Column(Text, nullable=False)
    category = Column(String, nullable=False)
    series = Column(String, nullable=True)
    score = Column(Float, nullable=True)
    score_reason = Column(Text, nullable=True)
    footage_check = Column(JSON, nullable=True)
    status = Column(String, nullable=False, default="candidate", index=True)
    reason = Column(Text, nullable=True)


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True, index=True)
    topic_id = Column(String, ForeignKey("topics.id"), nullable=False)
    content_language = Column(String, default="en")
    series = Column(String, nullable=True)
    episode_number = Column(Integer, nullable=True)
    status = Column(String, nullable=False, index=True)
    last_valid_state = Column(String, nullable=True)
    config_snapshot = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, index=True)
    error = Column(Text, nullable=True)


class JobEvent(Base):
    __tablename__ = "job_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String, ForeignKey("jobs.id"), nullable=False, index=True)
    from_state = Column(String, nullable=True)
    to_state = Column(String, nullable=False)
    actor = Column(String, nullable=False)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)


class Source(Base):
    __tablename__ = "sources"

    id = Column(String, primary_key=True, index=True)
    job_id = Column(String, ForeignKey("jobs.id"), nullable=False)
    url = Column(Text, nullable=False)
    domain = Column(String, nullable=False)
    publisher = Column(String, nullable=True)
    title = Column(Text, nullable=True)
    tier = Column(String, nullable=False)  # Tier A, Tier B, Tier C
    accessed_at = Column(DateTime(timezone=True), default=utc_now)
    extracted_text_path = Column(Text, nullable=True)


class Claim(Base):
    __tablename__ = "claims"

    id = Column(String, primary_key=True, index=True)
    job_id = Column(String, ForeignKey("jobs.id"), nullable=False)
    source_ids = Column(JSON, nullable=False)  # List of source IDs
    text = Column(Text, nullable=False)
    supporting_quote = Column(Text, nullable=False)
    quote_match_score = Column(Float, nullable=False)
    support_check = Column(String, nullable=False)  # supports / partial / no
    is_hook = Column(Boolean, default=False)
    status = Column(String, nullable=False)  # verified / rejected
    reject_reason = Column(Text, nullable=True)


class Script(Base):
    __tablename__ = "scripts"

    id = Column(String, primary_key=True, index=True)
    job_id = Column(String, ForeignKey("jobs.id"), nullable=False)
    version = Column(Integer, default=1)
    hook = Column(Text, nullable=False)
    segments = Column(JSON, nullable=False)  # List of segment dicts
    ending = Column(Text, nullable=False)
    structure = Column(String, nullable=False)
    check_results = Column(JSON, nullable=True)
    status = Column(String, nullable=False)  # passed / failed


class CostEntry(Base):
    __tablename__ = "cost_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String, ForeignKey("jobs.id"), nullable=True, index=True)
    provider = Column(String, nullable=False)  # anthropic, tavily, pexels
    model = Column(String, nullable=True)
    operation = Column(String, nullable=False)  # e.g., script_generation, claim_verification
    input_units = Column(Integer, default=0)
    output_units = Column(Integer, default=0)
    cost_usd = Column(Float, default=0.0)
    cost_kind = Column(String, default="actual")  # actual / estimated
    created_at = Column(DateTime(timezone=True), default=utc_now, index=True)


class Asset(Base):
    __tablename__ = "assets"

    id = Column(String, primary_key=True, index=True)
    provider = Column(String, nullable=False)
    provider_asset_id = Column(String, nullable=False)
    page_url = Column(Text, nullable=False)
    download_url = Column(Text, nullable=False)
    contributor = Column(String, nullable=True)
    contributor_url = Column(Text, nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    duration = Column(Float, nullable=True)
    size = Column(Integer, nullable=True)
    sha256 = Column(String(64), nullable=True)
    attribution = Column(Text, nullable=True)
    fetched_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)


class SegmentAsset(Base):
    __tablename__ = "segment_assets"

    job_id = Column(String, ForeignKey("jobs.id"), primary_key=True)
    script_version = Column(Integer, primary_key=True)
    segment_index = Column(Integer, primary_key=True)
    asset_id = Column(String, ForeignKey("assets.id"), nullable=False, index=True)
    start_s = Column(Float, nullable=True)
    end_s = Column(Float, nullable=True)
    relevance_score = Column(Float, nullable=True)
    relevance_reason = Column(Text, nullable=True)
    flags = Column(JSON, nullable=False, default=list)


class Artifact(Base):
    __tablename__ = "artifacts"

    job_id = Column(String, ForeignKey("jobs.id"), primary_key=True)
    kind = Column(String, primary_key=True)
    version = Column(Integer, primary_key=True)
    path = Column(Text, nullable=False)
    sha256 = Column(String(64), nullable=True)
    valid = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)


class Approval(Base):
    __tablename__ = "approvals"

    job_id = Column(String, ForeignKey("jobs.id"), primary_key=True)
    script_version = Column(Integer, primary_key=True)
    final_sha256 = Column(String(64), primary_key=True)
    decision = Column(String, nullable=False)
    answers = Column(JSON, nullable=True)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    invalidated_at = Column(DateTime(timezone=True), nullable=True)


class Publication(Base):
    __tablename__ = "publications"

    job_id = Column(String, ForeignKey("jobs.id"), primary_key=True)
    youtube_url = Column(Text, nullable=False, unique=True)
    published_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
