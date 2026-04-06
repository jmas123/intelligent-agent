"""Execute approved actions by dispatching to the appropriate writer."""

import json
import logging

from sqlalchemy.orm import Session

from deadline_agent.models import ProposedAction
from deadline_agent.store.action_repository import ActionRepository

logger = logging.getLogger(__name__)


async def execute_action(session: Session, action: ProposedAction) -> bool:
    """Execute an approved action. Returns True on success.

    Dispatches to calendar or gmail writer based on action type.
    Updates the action status to executed or stores the error.
    """
    repo = ActionRepository(session)
    payload = json.loads(action.payload)

    try:
        if action.type == "calendar_block":
            from deadline_agent.actions.calendar import create_calendar_event

            result = await create_calendar_event(payload)
            if result is None:
                repo.mark_executed(action.id, error="Calendar API call failed")
                return False

        elif action.type == "gmail_draft":
            from deadline_agent.actions.gmail import create_gmail_draft

            result = await create_gmail_draft(payload)
            if result is None:
                repo.mark_executed(action.id, error="Gmail API call failed")
                return False

        else:
            repo.mark_executed(action.id, error=f"Unknown action type: {action.type}")
            return False

    except Exception as e:
        repo.mark_executed(action.id, error=str(e))
        logger.exception("Action execution failed: %s", action.id)
        return False

    repo.mark_executed(action.id)
    logger.info("Action %d executed successfully", action.id)
    return True
