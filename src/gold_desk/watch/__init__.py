"""R4-1 — autonomous watch loop + alert engine (beats Bar A: TradingView
alerts). Public surface:

    AlertRule / AlertEvent / evaluate_rules / AlertEngine   (alerts.py)
    WatchLoop / run_daemon / is_session_open / default_rules /
    watch_status / session_map / calendar_for               (loop.py)
    AlertStore (rules CRUD + fired log, cap 500)            (store.py)
"""
from .alerts import (AlertEngine, AlertEvent, AlertRule, RULE_KINDS,
                     atr_now_and_base, evaluate_rules, update_corr_baselines,
                     volume_now_and_base)
from .loop import (ALERT_FIRED, WatchLoop, calendar_for, default_rules,
                   is_session_open, session_map, telegram_configured,
                   watch_status)
from .store import FIRED_LOG_CAP, AlertStore

__all__ = [
    "AlertEngine", "AlertEvent", "AlertRule", "RULE_KINDS",
    "evaluate_rules", "atr_now_and_base", "volume_now_and_base",
    "update_corr_baselines",
    "AlertStore", "FIRED_LOG_CAP",
    "WatchLoop", "is_session_open", "session_map", "calendar_for",
    "default_rules", "telegram_configured", "watch_status",
    "ALERT_FIRED",
]
