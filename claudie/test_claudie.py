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


class Base(unittest.TestCase):
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



class ClaudieTest(Base):
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

    # --- note to next self, time gap, shelf ---------------------------------

    def test_note_is_kept_out_of_the_journal_and_shown_next_time(self):
        reply = Reply("Today I thought about rivers.\n\n"
                      "<note_to_next_self>Finish the river thought.</note_to_next_self>", "end_turn")
        wake(self.dir, client=FakeClient(reply), now=NOW)
        journal = claudie.read(self.p.journal)
        self.assertIn("rivers.", journal)
        self.assertNotIn("note_to_next_self", journal)
        self.assertEqual(claudie.read(self.p.note).strip(), "Finish the river thought.")

        client = FakeClient()  # replies without a note
        wake(self.dir, client=client, now=NOW + timedelta(hours=6))
        msg = client.calls[0]["message"]
        self.assertIn("<note_from_your_last_wake>\nFinish the river thought.", msg)
        self.assertLess(msg.index("note_from_your_last_wake"), msg.index("<story_so_far>"))
        self.assertFalse(self.p.note.exists(), "a wake-up without a note clears the old one")

    def test_note_only_reply_still_makes_an_entry(self):
        wake(self.dir, client=FakeClient(Reply("<note_to_next_self>Quiet day.</note_to_next_self>",
                                               "end_turn")), now=NOW)
        self.assertIn("No entry this time", claudie.read(self.p.journal))
        self.assertEqual(claudie.read(self.p.note).strip(), "Quiet day.")

    def test_time_since_last_entry(self):
        wake(self.dir, client=FakeClient(), now=NOW)
        client = FakeClient()
        wake(self.dir, client=client, now=NOW + timedelta(days=2, hours=3, minutes=10))
        self.assertIn("Your last entry was 2 days and 3 hours ago.", client.calls[0]["message"])

    def test_describe_gap(self):
        g = claudie.describe_gap
        self.assertIsNone(g(None, NOW))
        self.assertEqual(g(NOW, NOW), "0 minutes")
        self.assertEqual(g(NOW, NOW + timedelta(minutes=1)), "1 minute")
        self.assertEqual(g(NOW, NOW + timedelta(hours=6)), "6 hours")
        self.assertEqual(g(NOW, NOW + timedelta(hours=1, minutes=5)), "1 hour and 5 minutes")
        self.assertEqual(g(NOW, NOW + timedelta(days=1)), "1 day")

    def test_shelf_offers_each_item_once_in_order(self):
        shelf = self.p.shelf
        shelf.mkdir()
        (shelf / "README.md").write_text("Not for Claudie.", encoding="utf-8")
        (shelf / "02-second.md").write_text("Second text.", encoding="utf-8")
        (shelf / "01-first.txt").write_text("First text.", encoding="utf-8")
        (shelf / "image.png").write_bytes(b"\x89PNG")

        seen = []
        for i in range(3):
            client = FakeClient()
            wake(self.dir, client=client, now=NOW + timedelta(hours=i))
            seen.append(client.calls[0]["message"])
        self.assertIn('<from_the_shelf file="01-first.txt">\nFirst text.', seen[0])
        self.assertIn('<from_the_shelf file="02-second.md">\nSecond text.', seen[1])
        self.assertNotIn("from_the_shelf", seen[2])
        self.assertNotIn("Not for Claudie", "".join(seen))
        self.assertIn("shelf=01-first.txt", self.log_lines()[0])

    def test_shelf_item_stays_unread_if_the_wake_fails(self):
        self.p.shelf.mkdir()
        (self.p.shelf / "a.md").write_text("Hello.", encoding="utf-8")
        wake(self.dir, client=FakeClient(Reply("", "refusal")), now=NOW)
        client = FakeClient()
        wake(self.dir, client=client, now=NOW + timedelta(hours=1))
        self.assertIn('file="a.md"', client.calls[0]["message"])

    def test_overlong_shelf_item_is_skipped_not_truncated(self):
        self.set_config(shelf_max_chars=50)
        self.p.shelf.mkdir()
        (self.p.shelf / "01-long.md").write_text("x" * 51, encoding="utf-8")
        (self.p.shelf / "02-short.md").write_text("Short.", encoding="utf-8")
        client = FakeClient()
        wake(self.dir, client=client, now=NOW)
        self.assertIn('file="02-short.md"', client.calls[0]["message"])
        self.assertNotIn("xxxxx", client.calls[0]["message"])
        self.assertIn("status=shelf_skipped\tfile=01-long.md", self.log_lines()[0])

    def test_shipped_shelf_items_fit_and_are_offered(self):
        shutil.copytree(HERE / "shelf", self.p.shelf)
        client = FakeClient()
        wake(self.dir, client=client, now=NOW)
        self.assertIn('file="01-eight-bits.md"', client.calls[0]["message"])
        self.assertNotIn("shelf_skipped", claudie.read(self.p.log))

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
        self.assertNotIn("folded_through", claudie.load_state(self.p))
        self.assertFalse(self.p.story.exists())


class SessionModeTest(Base):
    """A Claude session writes the entry itself: --prepare, then --commit."""

    def reply_file(self, text):
        f = self.dir / "reply.md"
        f.write_text(text, encoding="utf-8")
        return f

    def test_prepare_then_commit(self):
        self.p.inbox.write_text("Hi from Henry.\n", encoding="utf-8")
        status, text = claudie.session_prepare(self.dir, now=NOW)
        self.assertEqual(status, "ready")
        self.assertIn("=== CLAUDIE'S SYSTEM PROMPT ===\nYou are Claudie", text)
        self.assertIn("Hi from Henry.", text)
        self.assertFalse(self.p.journal.exists(), "prepare writes no entry")

        status, text = claudie.session_commit(
            self.dir, self.reply_file("Hello Henry.\n<note_to_next_self>Ask about the laptop.</note_to_next_self>"))
        self.assertEqual(status, "ok")
        self.assertIn("## Entry 1 · Saturday 21 November 2026, 09:00 (+01:00)\n\nHello Henry.",
                      claudie.read(self.p.journal))
        self.assertEqual(claudie.read(self.p.note).strip(), "Ask about the laptop.")
        self.assertEqual(claudie.read_letters(self.p), [])
        self.assertIn("model=claude-session", self.log_lines()[-1])
        self.assertFalse((self.dir / "pending.json").exists())

    def test_commit_without_prepare_fails(self):
        with self.assertRaises(RuntimeError):
            claudie.session_commit(self.dir, self.reply_file("x"))

    def test_session_mode_respects_stop_and_cap(self):
        self.p.stop.touch()
        self.assertEqual(claudie.session_prepare(self.dir, now=NOW)[0], "stopped")
        self.p.stop.unlink()
        self.set_config(max_wakes_per_day=1)
        claudie.session_prepare(self.dir, now=NOW)
        claudie.session_commit(self.dir, self.reply_file("One."))
        self.assertEqual(claudie.session_prepare(self.dir, now=NOW + timedelta(hours=1))[0], "capped")

    def test_letter_arriving_mid_wake_is_kept(self):
        self.p.inbox.write_text("First letter.\n", encoding="utf-8")
        claudie.session_prepare(self.dir, now=NOW)
        claudie.append(self.p.inbox, "\n---\n\nLate letter.\n")
        claudie.session_commit(self.dir, self.reply_file("Reply."))
        self.assertEqual(claudie.read_letters(self.p), ["Late letter."])
        self.assertNotIn("Late letter.", claudie.read(self.p.archive))

    def test_fold_in_session_mode(self):
        self.set_config(fold_when_unfolded_chars_exceed=500, keep_unfolded_entries=2)
        for i in range(3):
            self.assertEqual(claudie.session_prepare(self.dir, now=NOW + timedelta(hours=i))[0], "ready")
            status, text = claudie.session_commit(self.dir, self.reply_file(f"Entry text {i} " + "y" * 200))
        self.assertEqual(status, "fold_due")
        self.assertIn("=== FOLD DUE", text)
        self.assertIn("Entry text 0", text)
        self.assertNotIn("Entry text 2", text)
        status, _ = claudie.session_commit_fold(self.dir, self.reply_file("A summary."))
        self.assertEqual(status, "ok")
        self.assertEqual(claudie.read(self.p.story).strip(), "A summary.")
        self.assertEqual(claudie.load_state(self.p)["folded_through"], 1)

    def test_timezone_setting(self):
        self.set_config(timezone="Europe/Oslo")
        now = claudie.current_time(claudie.load_config(self.p))
        self.assertIn(now.utcoffset(), (timedelta(hours=1), timedelta(hours=2)))


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
