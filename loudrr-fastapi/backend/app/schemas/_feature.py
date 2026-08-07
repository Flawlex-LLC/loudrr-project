from pydantic import BaseModel, Field


class FeatureInterestRequest(BaseModel):
    feature: str
    interests: list[str] = Field(default_factory=list)
