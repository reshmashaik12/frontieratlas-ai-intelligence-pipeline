from pydantic import BaseModel, Field
from datetime import datetime


class Source(BaseModel):
    name: str
    url: str


class Startup(BaseModel):
    schemaVersion: str = "1.0"
    recordType: str = "STARTUP"
    source: Source
    content: dict
    collectedAt: datetime = Field(default_factory=datetime.utcnow)


class Product(BaseModel):
    schemaVersion: str = "1.0"
    recordType: str = "PRODUCT"
    source: Source
    content: dict
    collectedAt: datetime = Field(default_factory=datetime.utcnow)


class ResearchPaper(BaseModel):
    schemaVersion: str = "1.0"
    recordType: str = "RESEARCH_PAPER"
    content: dict


class Job(BaseModel):
    schemaVersion: str = "1.0"
    recordType: str = "JOB"
    content: dict


class News(BaseModel):
    schemaVersion: str = "1.0"
    recordType: str = "NEWS"
    content: dict
