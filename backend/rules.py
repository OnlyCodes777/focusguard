"""Pure event evaluation and per-event streak/cooldown state transitions."""
PERSON, PHONE = 0, 67
EVENT_TYPES = ("PHONE_DETECTED", "MULTIPLE_PEOPLE", "NO_FACE_DETECTED")


def evaluate(dets):
    persons = sorted((d["conf"] for d in dets if d["cls"] == PERSON), reverse=True)
    phones = [d["conf"] for d in dets if d["cls"] == PHONE]
    events = {}
    if phones:
        events["PHONE_DETECTED"] = max(phones)
    if len(persons) > 1:
        events["MULTIPLE_PEOPLE"] = persons[1]
    if not persons:
        events["NO_FACE_DETECTED"] = 1.0
    return events


def advance_state(events, state, now, streak_n=2, cooldown_s=30):
    """Apply one frame to the exam/candidate state; return incidents and new state."""
    state = {name: dict(value) for name, value in state.items()}
    incidents = {}
    for event_type in EVENT_TYPES:
        entry = state.get(event_type, {"streak": 0, "cooldown_until": 0})
        if event_type not in events:
            entry["streak"] = 0
        else:
            entry["streak"] = int(entry.get("streak", 0)) + 1
            if entry["streak"] >= streak_n and float(entry.get("cooldown_until", 0)) <= now:
                entry["streak"] = 0
                entry["cooldown_until"] = now + cooldown_s
                incidents[event_type] = events[event_type]
        state[event_type] = entry
    return incidents, state
