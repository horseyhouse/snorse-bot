import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import sqlite_state as state
from app.admission import support_due


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path, state.DB_PATH = state.DB_PATH, Path(self.tmp.name) / "state.db"
        self.old_mode, state.MODE = state.MODE, "auto"
        self.old_groups, state.MAX_GROUPS = state.MAX_GROUPS, 1

    def tearDown(self):
        state.DB_PATH, state.MODE, state.MAX_GROUPS = self.old_path, self.old_mode, self.old_groups
        self.tmp.cleanup()

    def report(self, minutes=31):
        now = datetime.now(timezone.utc)
        return {"timestamp": now.isoformat(), "healthySince": (now-timedelta(minutes=minutes)).isoformat(), "ramPercent":20, "diskPercent":20, "signalApiHealthy":True, "listenerHealthy":True}

    def test_health_hysteresis_and_threshold(self):
        self.assertFalse(state.healthy(self.report(29))[0])
        self.assertTrue(state.healthy(self.report())[0])
        self.assertEqual(state.healthy({**self.report(), "ramPercent":70}), (False, "RAM is at least 70%"))

    def test_cap_and_fifo_reactivation(self):
        with state.database() as db:
            db.execute("BEGIN IMMEDIATE")
            one,_=state.admit_scope(db,{"groupRecipient":"group.one","groupName":"one","scopeType":"group"},self.report())
            two,_=state.admit_scope(db,{"groupRecipient":"group.two","groupName":"two","scopeType":"group"},self.report())
            self.assertEqual((one["admissionStatus"],two["admissionStatus"]),("active","waitlisted"))
            db.execute("UPDATE scopes SET active=0,admission_status='inactive' WHERE recipient='group.one'")
            self.assertEqual([x["groupRecipient"] for x in state.activate_waitlist(db,self.report())],["group.two"])

    def test_support_excludes_personal_and_missing_url(self):
        now=datetime.now(timezone.utc); old=(now-timedelta(days=400)).isoformat()
        scope={"groupRecipient":"group.one","scopeType":"group","admissionStatus":"active","active":True,"admittedAt":old}
        self.assertFalse(support_due({**scope,"scopeType":"personal"},now,"https://example/support"))
        self.assertFalse(support_due(scope,now,""))


if __name__ == "__main__": unittest.main()
