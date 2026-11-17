from datetime import datetime, timezone
from service.infrastructure.mt5.calendar import CalendarObservation
from service.infrastructure.mt5.calendar_composer import CalendarEvidenceComposer
from service.infrastructure.mt5.mt5_session_calendar import MT5SessionCalendarAdapter

def _obs(state, source, reason="test"):
    return CalendarObservation(state=state, effective_from=datetime(2026,1,1,tzinfo=timezone.utc), effective_until=datetime(2026,1,2,tzinfo=timezone.utc), source=source, revision="r1", reason=reason)

def test_mt5_adapter_maps_logical_symbol_before_provider_call():
    seen=[]
    def provider(symbol, start, end, timeframe): seen.append(symbol); return _obs("closed", "mt5", "weekend")
    resolver=type("Resolver", (), {"resolve": lambda self, value: "EURUSD"})()
    result=MT5SessionCalendarAdapter(symbol_resolver=resolver, session_provider=provider).classify_gap("EURUSDm",datetime(2026,1,1,tzinfo=timezone.utc),datetime(2026,1,2,tzinfo=timezone.utc),"M5")
    assert result.state == "closed" and seen == ["EURUSD"]

def test_composer_is_fail_closed_when_any_source_is_unknown():
    def known(self,*args): return _obs("closed","mt5","weekend")
    def unknown(self,symbol,start,end,timeframe): return CalendarObservation(state="unknown",effective_from=start,effective_until=end,source="unavailable",revision="unknown",reason="missing evidence")
    make=lambda fn:type("Source",(),{"classify_gap":fn})()
    result=CalendarEvidenceComposer(make(known),make(unknown)).classify_gap("EURUSDm",datetime(2026,1,1,tzinfo=timezone.utc),datetime(2026,1,2,tzinfo=timezone.utc),"M5")
    assert result.state == "unknown"

def test_composer_is_fail_closed_on_source_conflict():
    start=datetime(2026,1,1,tzinfo=timezone.utc); end=datetime(2026,1,2,tzinfo=timezone.utc)
    def make(state,source): return type("Source",(),{"classify_gap":lambda self,*args:_obs(state,source)})()
    result=CalendarEvidenceComposer(make("closed","mt5"),make("open","broker")).classify_gap("EURUSDm",start,end,"M5")
    assert result.state == "unknown" and result.reason == "calendar evidence conflict"
