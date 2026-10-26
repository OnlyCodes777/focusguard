"""Local smoke test for rule transitions with a fake persistent state store.

Run from the repository root with: python -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from rules import advance_state, evaluate


class FakeStateStore:
    def __init__(self):
        self.state = {}
        self.reads = 0
        self.writes = 0

    def read(self):
        self.reads += 1
        return self.state

    def write(self, value):
        self.writes += 1
        self.state = value


class RuleSmokeTest(unittest.TestCase):
    def test_evaluate_and_state_transitions(self):
        store = FakeStateStore()
        frame_with_phone = [{"cls": 0, "conf": 0.91}, {"cls": 67, "conf": 0.83}]
        events = evaluate(frame_with_phone)
        self.assertEqual(events, {"PHONE_DETECTED": 0.83})

        # One fake-store read and write per frame, with all event types in one item.
        for now in (0, 1):
            incidents, next_state = advance_state(events, store.read(), now, 2, 30)
            store.write(next_state)
        self.assertEqual(store.reads, 2)
        self.assertEqual(store.writes, 2)
        self.assertEqual(incidents, {"PHONE_DETECTED": 0.83})
        self.assertEqual(store.state["PHONE_DETECTED"], {"streak": 0, "cooldown_until": 31})

        # A missing event resets its streak; an active cooldown suppresses alerts.
        clear_events = evaluate([{"cls": 0, "conf": 0.9}])
        incidents, state = advance_state(clear_events, store.read(), 2, 2, 30)
        store.write(state)
        self.assertNotIn("PHONE_DETECTED", incidents)
        self.assertEqual(store.state["PHONE_DETECTED"]["streak"], 0)

        # Independent event streaks share the same persisted candidate state.
        multiple_people = evaluate([
            {"cls": 0, "conf": 0.95}, {"cls": 0, "conf": 0.88}, {"cls": 67, "conf": 0.8}
        ])
        self.assertEqual(multiple_people["MULTIPLE_PEOPLE"], 0.88)
        self.assertIn("PHONE_DETECTED", multiple_people)
        self.assertEqual(set(store.state), {"PHONE_DETECTED", "MULTIPLE_PEOPLE", "NO_FACE_DETECTED"})


if __name__ == "__main__":
    unittest.main()
