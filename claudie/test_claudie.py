"""Tests for claudie.py. Run with:  python -m unittest -v   (no API key needed)"""

import os
import shutil
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import claudie
from claudie import Reply, wake

HERE = Path(__file__).resolve().parent
NOW = datetime(2026, 11, 21, 9, 0, tzinfo=timezone(timedelta(hours=1)))


class FakeClient:
    """Records every request and returns scripted replies."""

    def __init__(self, *replies, delay=0.0):
        self.replies = list(replies) or [Reply("A fine entry.", "end_turn", 100, 50)]
        self.calls = []
        self.delay = delay

    def ask(self, system, message, model, max_tokens, effort):
        self.calls.append({"system": system, "message": message, "model": model,
                           "max_tokens": max_tokens, "effort": effort})
        if self.delay:
            time.sleep(self.delay)
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


class ClaudieTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        shutil.copy(HERE / "prompt_system.md", self.dir)
        shutil.copy(HERE / "config.toml", self.dir)
        self.p = claudie.Paths(self.dir)

    def tearDown(self):
        shutil.rmtree(self.dir)

    def log_lines(self):
        return claudie.read(self.p.log).splitlines()

    def set_config(self, **values):
        lines = [f"{k} = {v!r}".replace("'", '"') for k, v in values.items()]
        self.p.config.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # --- milestone 1 -------------------------------------------------------

    def test_first_wake_writes_entry_one_and_logs(self):
        client = FakeClient()
        self.assertEqual(wake(self.dir, client=client, now=NOW), "ok")
        journal = claudie.read(self.p.journal)
        self.assertTrue(journal.startswith("## Entry 1 · Saturday 21 November 2026, 09:00 (+01:00)"))
        self.assertIn("A fine entry.", journal)
        self.assertIn("first entry", client.calls[0]["message"])
        self.assertEqual(client.calls[0]["model"], "claude-haiku-5-5")
        self.assertEqual(client.calls[0]["effort"], "low")
        self.assertIn("Henry", client.calls[0]["system"])
        (line,) = self.log_lines()
        self.assertIn("status=ok", line)
        self.assertIn("entry=1", line)
        self.assertIn("in_tokens=100", line)
        self.assertFalse(self.p.lock.exists())

    def test_second_wake_rereads_the_first(self):
        wake(self.dir, client=FakeClient(Reply("First thoughts.", "end_turn")), now=NOW)
        client = FakeClient(Reply("Second thoughts.", "end_turn"))
        wake(self.dir, client=client, now=NOW + timedelta(hours=6))
        self.assertIn("First thoughts.", client.calls[0]["message"])
        numbers = [n for n, _ in claudie.split_entries(claudie.read(self.p.journal))]
        self.assertEqual(numbers, [1, 2])

    def test_mock_mode_needs_no_api(self):
        self.assertEqual(wake(self.dir, mock=True, now=NOW), "ok")
        self.assertIn("Mock entry", claudie.read(self.p.journal))
        self.assertIn("mock=yes", self.log_lines()[0])

    def test_kill_switch(self):
        self.p.stop.touch()
        client = FakeClient()
        self.assertEqual(wake(self.dir, client=client, now=NOW), "stopped")
        self.assertEqual(client.calls, [])
        self.assertFalse(self.p.journal.exists())

    def test_existing_lock_blocks_a_run(self):
        self.p.lock.touch()
        client = FakeClient()
        self.assertEqual(wake(self.dir, client=client, now=NOW), "locked")
        self.assertEqual(client.calls, [])
        self.assertTrue(self.p.lock.exists(), "must not delete another run's lock")

    def test_stale_lock_is_cleared(self):
        self.p.lock.touch()
        old = time.time() - 2 * 3600
        os.utime(self.p.lock, (old, old))
        self.assertEqual(wake(self.dir, client=FakeClient(), now=NOW), "ok")

    def test_two_runs_at_once_only_one_writes(self):
        slow = FakeClient(delay=0.5)
        results = {}
        t = threading.Thread(target=lambda: results.setdefault("a", wake(self.dir, client=slow, now=NOW)))
        t.start()
        time.sleep(0.1)
        results["b"] = wake(self.dir, client=FakeClient(), now=NOW)
        t.join()
        self.assertEqual(sorted(results.values()), ["locked", "ok"])
        self.assertEqual(len(claudie.split_entries(claudie.read(self.p.journal))), 1)

    def test_daily_cap(self):
        self.set_config(max_wakes_per_day=2)
        client = FakeClient()
        self.assertEqual(wake(self.dir, client=client, now=NOW), "ok")
        self.assertEqual(wake(self.dir, client=client, now=NOW + timedelta(hours=1)), "ok")
        self.assertEqual(wake(self.dir, client=client, now=NOW + timedelta(hours=2)), "capped")
        self.assertEqual(len(client.calls), 2)
        # A new day resets the count.
        self.assertEqual(wake(self.dir, client=client, now=NOW + timedelta(days=1)), "ok")

    def test_mock_runs_do_not_use_up_the_daily_cap(self):
        self.set_config(max_wakes_per_day=1)
        for i in range(3):
            self.assertEqual(wake(self.dir, mock=True, now=NOW + timedelta(minutes=i)), "ok")
        self.assertEqual(wake(self.dir, client=FakeClient(), now=NOW + timedelta(hours=1)), "ok")

    def test_entries_are_separated_by_one_blank_line(self):
        wake(self.dir, mock=True, now=NOW)
        wake(self.dir, mock=True, now=NOW)
        journal = claudie.read(self.p.journal)
        self.assertIn("API.\n\n## Entry 2", journal)
        self.assertNotIn("\n\n\n", journal)

    def test_api_error_is_logged_not_retried(self):
        class Boom:
            calls = 0

            def ask(self, *a):
                Boom.calls += 1
                raise ConnectionError("network down")

        self.assertEqual(wake(self.dir, client=Boom(), now=NOW), "error")
        self.assertEqual(Boom.calls, 1)
        self.assertIn("ConnectionError: network down", self.log_lines()[0])
        self.assertFalse(self.p.lock.exists())

    def test_truncated_entry_is_kept_and_marked(self):
        wake(self.dir, client=FakeClient(Reply("Half a thou", "max_tokens")), now=NOW)
        self.assertIn("cut off at the length limit", claudie.read(self.p.journal))

    # --- milestone 2: mailbox ---------------------------------------------

    def test_letters_are_read_answered_and_archived(self):
        wake(self.dir, mock=True, now=NOW)  # creates the inbox template
        self.p.inbox.write_text(claudie.INBOX_TEMPLATE + "Hello Claudie!\n\n---\n\nSecond letter.\n",
                                encoding="utf-8")
        client = FakeClient()
        wake(self.dir, client=client, now=NOW + timedelta(hours=6))
        message = client.calls[0]["message"]
        self.assertIn('<letter n="1">\nHello Claudie!', message)
        self.assertIn('<letter n="2">\nSecond letter.', message)
        self.assertEqual(claudie.read_letters(self.p), [])
        archive = claudie.read(self.p.archive)
        self.assertIn("## Answered in entry 2", archive)
        self.assertIn("Hello Claudie!", archive)
        self.assertIn("letters=2", self.log_lines()[-1])

    def test_refusal_writes_nothing_and_keeps_letters(self):
        self.p.inbox.write_text("Please keep me.\n", encoding="utf-8")
        status = wake(self.dir, client=FakeClient(Reply("", "refusal")), now=NOW)
        self.assertEqual(status, "refused")
        self.assertFalse(self.p.journal.exists())
        self.assertEqual(claudie.read_letters(self.p), ["Please keep me."])

    def test_no_letters_says_so(self):
        client = FakeClient()
        wake(self.dir, client=client, now=NOW)
        self.assertIn("There are no new letters from Henry.", client.calls[0]["message"])

    # --- milestone 4: memory upkeep ---------------------------------------

    def test_old_entries_fold_into_story(self):
        self.set_config(fold_when_unfolded_chars_exceed=2000, keep_unfolded_entries=3, recent_entries=3)
        padding = "\n\n".join(f"## Entry {n} · day {n}\n\n" + ("words " * 80) for n in range(1, 9))
        self.p.journal.write_text(padding + "\n", encoding="utf-8")

        client = FakeClient(Reply("Entry nine.", "end_turn"), Reply("The story so far.", "end_turn", 900, 120))
        self.assertEqual(wake(self.dir, client=client, now=NOW), "ok")

        self.assertEqual(len(client.calls), 2)
        fold_msg = client.calls[1]["message"]
        for n in range(1, 7):
            self.assertIn(f"## Entry {n} ·", fold_msg)
        for n in (7, 8, 9):
            self.assertNotIn(f"## Entry {n} ·", fold_msg)
        self.assertEqual(claudie.read(self.p.story).strip(), "The story so far.")
        self.assertEqual(claudie.load_state(self.p)["folded_through"], 6)
        self.assertIn("status=folded", "\n".join(self.log_lines()))

        # The journal itself is append-only: nothing was removed.
        self.assertEqual(len(claudie.split_entries(claudie.read(self.p.journal))), 9)

        # Next wake sees the summary and only the recent entries.
        client2 = FakeClient()
        wake(self.dir, client=client2, now=NOW + timedelta(hours=6))
        msg = client2.calls[0]["message"]
        self.assertIn("The story so far.", msg)
        self.assertNotIn("## Entry 6 ·", msg)
        self.assertIn("## Entry 9 ·", msg)

    def test_no_fold_while_journal_is_short(self):
        client = FakeClient()
        wake(self.dir, client=client, now=NOW)
        self.assertEqual(len(client.calls), 1)
        self.assertFalse(self.p.story.exists())

    def test_failed_fold_keeps_state(self):
        self.set_config(fold_when_unfolded_chars_exceed=100, keep_unfolded_entries=1)
        self.p.journal.write_text("## Entry 1 · a\n\n" + "x" * 200 + "\n", encoding="utf-8")
        client = FakeClient(Reply("Entry two.", "end_turn"), Reply("", "refusal"))
        self.assertEqual(wake(self.dir, client=client, now=NOW), "ok")
        self.assertEqual(claudie.load_state(self.p), {})
        self.assertFalse(self.p.story.exists())


class AnthropicClientTest(unittest.TestCase):
    def test_request_shape(self):
        """The real client sends the expected request and reads text blocks only."""
        from types import SimpleNamespace as NS

        captured = {}

        class FakeMessages:
            def create(self, **kwargs):
                captured.update(kwargs)
                return NS(
                    content=[NS(type="thinking", thinking=""), NS(type="text", text=" Hi. ")],
                    stop_reason="end_turn",
                    usage=NS(input_tokens=10, output_tokens=5),
                )

        c = claudie.AnthropicClient.__new__(claudie.AnthropicClient)
        c.client = NS(messages=FakeMessages())
        reply = c.ask("sys", "msg", "claude-haiku-5-5", 3000, "low")
        self.assertEqual(reply, Reply("Hi.", "end_turn", 10, 5))
        self.assertEqual(captured["output_config"], {"effort": "low"})
        self.assertEqual(captured["system"], "sys")
        self.assertEqual(captured["messages"], [{"role": "user", "content": "msg"}])


if __name__ == "__main__":
    unittest.main()
