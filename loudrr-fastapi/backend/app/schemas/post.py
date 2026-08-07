from pydantic import BaseModel


class PostSubmitRequest(BaseModel):
    x_link: str
    karma_amount: int | None = None  # defaults to POST_COST_MIN in the service


class PostSubmitResponse(BaseModel):
    success: bool
    message: str
    post_id: str
    new_balance: float
    escrow: int
