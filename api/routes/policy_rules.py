from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from api.dependencies import DatabaseDependency
from api.schemas import PolicyRuleCreateRequest, PolicyRuleResponse


LOGGER = logging.getLogger("api.policy_rules")
router = APIRouter(prefix="/policy-rules", tags=["policy-rules"])

_VALID_ACTION_TYPES = {"publish_tiktok", "publish_youtube", "publish_facebook"}


class PolicyRuleListResponse(BaseModel):
    items: list[PolicyRuleResponse]


@router.get("", response_model=PolicyRuleListResponse)
async def list_policy_rules(
    database: DatabaseDependency,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> PolicyRuleListResponse:
    rows = await database.list_policy_rules_all(limit=limit, offset=offset)
    LOGGER.info("policy_rules_listed", extra={"event": "policy_rules_listed", "count": len(rows)})
    return PolicyRuleListResponse(items=[PolicyRuleResponse.from_row(row) for row in rows])


@router.post("", response_model=PolicyRuleResponse, status_code=status.HTTP_201_CREATED)
async def create_policy_rule(
    request: PolicyRuleCreateRequest,
    database: DatabaseDependency,
) -> PolicyRuleResponse:
    if request.action_type not in _VALID_ACTION_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid action_type '{request.action_type}'. Must be one of: {sorted(_VALID_ACTION_TYPES)}",
        )
    row = await database.create_policy_rule(
        action_type=request.action_type,
        rule_name=request.rule_name,
        max_actions=request.max_actions,
        window_seconds=request.window_seconds,
        account_id=request.account_id,
        platform=request.platform,
        cooldown_seconds=request.cooldown_seconds,
    )
    LOGGER.info(
        "policy_rule_created",
        extra={"event": "policy_rule_created", "rule_id": row["id"], "action_type": row["action_type"]},
    )
    return PolicyRuleResponse.from_row(row)


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_policy_rule(rule_id: str, database: DatabaseDependency) -> None:
    deleted = await database.delete_policy_rule(rule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Policy rule not found")
    LOGGER.info("policy_rule_deleted", extra={"event": "policy_rule_deleted", "rule_id": rule_id})
