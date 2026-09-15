"""
Notification sink for events that need a human's attention but don't (yet)
have automation behind them -- e.g. stage hitting spoke capacity with no
autoscaling in place. POC implementation: log only. Swap the body of
notify() for a real Slack/email/webhook call when one is available; callers
don't need to change.
"""
import logging

logger = logging.getLogger("tenant-operator.notifications")


def notify(event: str, message: str, **context) -> None:
    logger.warning("NOTIFY event=%s message=%s context=%s", event, message, context)
    print(f"\U0001F514 NOTIFY [{event}]: {message} {context}")
