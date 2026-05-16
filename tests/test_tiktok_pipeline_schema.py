from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from api.schemas import TikTokPipelineRequest


def _request_kwargs(top_n: int) -> dict:
    return {
        "product_url": "https://example.com/product",
        "account_id": uuid4(),
        "top_n": top_n,
    }


@pytest.mark.parametrize("top_n", [1, 10])
def test_tiktok_pipeline_request_accepts_top_n_bounds(top_n: int) -> None:
    request = TikTokPipelineRequest(**_request_kwargs(top_n))

    assert request.top_n == top_n


@pytest.mark.parametrize("top_n", [0, 11])
def test_tiktok_pipeline_request_rejects_top_n_outside_one_to_ten(top_n: int) -> None:
    with pytest.raises(ValidationError):
        TikTokPipelineRequest(**_request_kwargs(top_n))
