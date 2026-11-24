"""Claudie: one wake-up of a journal-keeping Claude.

Run by the operating system's scheduler a few times a day. Each run reads the
journal, the running summary and any letters in the inbox, asks Claude for one
new entry, saves it, and exits. Nothing runs in between.

Usage:
    python claudie.py           # a real wake-up (needs ANTHROPIC_API_KEY in .env)
    python claudie.py --mock    # a fake wake-up: no API call, no cost

No tools: the only network call is to the Anthropic API.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent

INBOX_TEMPLATE = """<!--
Write letters to Claudie below this comment. Separate letters with a line
containing only three dashes (---). Claudie reads them on the next wake-up,
replies in its journal entry, and the letters move to inbox_archive.md.
-->
"""

DEFAULT_CONFIG = {
    "model": "claude-haiku-5-5",
    "summary_model": "claude-haiku-5-5",
    "effort": "low",
    "max_tokens": 3000,
    "summary_max_tokens": 4000,
    "max_wakes_per_day": 3,
    "recent_entries": 5,
    "fold_batch_entries": 5,
    "fold_when_unfolded_chars_exceed": 30000,
    "keep_unfolded_entries": 5,
    "stale_lock_minutes": 60,
    "shelf_max_chars": 8000,
    "price_input_per_mtok": 0.10,
    "price_output_per_mtok": 0.50,
}

ENTRY_HEADER = re.compile(r"^## Entry (\d+)\b.*$", re.MULTILINE)
NOTE_TAG = re.compile(r"<note_to_next_self>(.*?)</note_to_next_self>", re.DOTALL)
SHELF_TAG = re.compile(r'<add_to_shelf(?:\s+title="([^"]*)")?\s*>(.*?)</add_to_shelf>', re.DOTALL)


# --------------------------------------------------------------------------
# Small file helpers

@dataclass
class Paths:
    base: Path

    def __getattr__(self, name: str) -> Path:
        files = {
            "config": "config.toml",
            "system_prompt": "prompt_system.md",
            "journal": "journal.md",
            "story": "story_so_far.md",
            "inbox": "inbox.md",
            "archive": "inbox_archive.md",
            "state": "state.json",
            "lock": "claudie.lock",
            "stop": "STOP",
            "env": ".env",
            "log": "logs/wakes.log",
            "note": "note_to_self.md",
            "shelf": "shelf",
        }
        if name in files:
            return self.base / files[name]
        raise AttributeError(name)


def read(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default


def write(path: Path, text: str) -> None:
    """Write via a temp file so a crash never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def append(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(text)


def load_config(paths: Paths) -> dict:
    config = dict(DEFAULT_CONFIG)
    if paths.config.exists():
        with paths.config.open("rb") as f:
            config.update(tomllib.load(f))
    return config


def load_env(paths: Paths) -> None:
    """Minimal .env reader: KEY=VALUE lines. Existing variables win."""
    for line in read(paths.env).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def log(paths: Paths, now: datetime, status: str, **fields) -> None:
    parts = [now.isoformat(timespec="seconds"), f"status={status}"]
    parts += [f"{k}={v}" for k, v in fields.items() if v is not None]
    append(paths.log, "\t".join(parts) + "\n")


# --------------------------------------------------------------------------
# Journal, inbox and memory

def split_entries(journal: str) -> list[tuple[int, str]]:
    """Return [(number, full entry text including its header), ...]."""
    matches = list(ENTRY_HEADER.finditer(journal))
    entries = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(journal)
        entries.append((int(m.group(1)), journal[m.start():end].strip()))
    return entries


def read_letters(paths: Paths) -> list[str]:
    text = re.sub(r"<!--.*?-->", "", read(paths.inbox), flags=re.DOTALL)
    letters = [part.strip() for part in re.split(r"^---\s*$", text, flags=re.MULTILINE)]
    return [letter for letter in letters if letter]


def archive_letters(paths: Paths, letters: list[str], entry_no: int, now: datetime) -> None:
    """Move answered letters to the archive. Letters that arrived since stay in the inbox."""
    if not letters:
        return
    block = f"\n## Answered in entry {entry_no} ({now:%Y-%m-%d %H:%M})\n\n"
    block += "\n\n---\n\n".join(letters) + "\n"
    append(paths.archive, block)
    remaining = list(read_letters(paths))
    for letter in letters:
        if letter in remaining:
            remaining.remove(letter)
    write(paths.inbox, INBOX_TEMPLATE + ("\n" + "\n\n---\n\n".join(remaining) + "\n" if remaining else ""))


def load_state(paths: Paths) -> dict:
    try:
        return json.loads(read(paths.state, "{}"))
    except json.JSONDecodeError:
        return {}


def wakes_today(paths: Paths, now: datetime) -> int:
    """Real wakes today that reached the API (ok, refused or failed). Mock runs don't count."""
    today = now.date().isoformat()
    count = 0
    for line in read(paths.log).splitlines():
        if (line.startswith(today) and "mock=yes" not in line
                and re.search(r"status=(ok|refused|error)\b", line)):
            count += 1
    return count


def describe_gap(since: datetime | None, now: datetime) -> str | None:
    if since is None:
        return None
    minutes = max(0, int((now - since).total_seconds() // 60))
    days, rem = divmod(minutes, 24 * 60)
    hours, mins = divmod(rem, 60)
    parts = [f"{days} day{'s' * (days != 1)}"] if days else []
    if hours:
        parts.append(f"{hours} hour{'s' * (hours != 1)}")
    if not days and (mins or not hours):
        parts.append(f"{mins} minute{'s' * (mins != 1)}")
    return " and ".join(parts)


def next_shelf_item(paths: Paths, state: dict) -> tuple[str, str] | None:
    """The first shelf file (by name) Claudie hasn't been offered yet."""
    if not paths.shelf.is_dir():
        return None
    done = set(state.get("shelf_read", []))
    for f in sorted(paths.shelf.iterdir()):
        if (f.is_file() and f.suffix in (".md", ".txt") and f.name.lower() != "readme.md"
                and f.name not in done):
            return f.name, read(f).strip()
    return None


def split_note(text: str) -> tuple[str, str]:
    """Separate the optional note to the next wake-up from the entry."""
    notes = [m.strip() for m in NOTE_TAG.findall(text)]
    body = NOTE_TAG.sub("", text).strip()
    return body, "\n\n".join(n for n in notes if n)


def format_when(now: datetime) -> str:
    return now.strftime("%A %d %B %Y, %H:%M") + f" ({now.strftime('%z')[:3]}:{now.strftime('%z')[3:]})"


def build_wake_message(story: str, recent: list[str], letters: list[str], now: datetime,
                       next_no: int, human: str, *, gap: str | None = None, note: str = "",
                       shelf_item: tuple[str, str] | None = None, story_covers: int = 0) -> str:
    first = f"It is {format_when(now)}. This is wake-up number {next_no}."
    if gap:
        first += f" Your last entry was {gap} ago."
    sections = [first]
    if note.strip():
        sections.append("<note_from_your_last_wake>\n" + note.strip() + "\n</note_from_your_last_wake>")
    if story.strip() and story_covers:
        sections.append(f'<story_so_far covers="entries 1 to {story_covers}">\n' + story.strip()
                        + "\n</story_so_far>\nThe summary is older than the entries below. "
                        "Where they differ, the entries below are more current.")
    else:
        sections.append("<story_so_far>\n" + (story.strip() or "(nothing yet)") + "\n</story_so_far>")
    if recent:
        sections.append("<recent_entries>\n" + "\n\n".join(recent) + "\n</recent_entries>")
    else:
        sections.append("<recent_entries>\n(none: this is your first entry)\n</recent_entries>")
    if letters:
        body = "\n\n".join(f'<letter n="{i}">\n{t}\n</letter>' for i, t in enumerate(letters, 1))
        sections.append(f"<letters>\n{body}\n</letters>\nEach letter is signed by whoever wrote it.")
    else:
        sections.append("There are no new letters.")
    if shelf_item:
        name, text = shelf_item
        sections.append(f'<from_the_shelf file="{name}">\n{text}\n</from_the_shelf>\n'
                        "This is offered, not assigned: respond to it, or don't.")
    sections.append("Write today's journal entry now. Write only the entry itself: "
                    "the date header is added for you.")
    return "\n\n".join(sections)


FOLD_SYSTEM = (
    "You maintain the long-term memory of a journal written by Claudie, a Claude model "
    "that wakes on a schedule. Merge the older entries into the existing summary. Keep "
    "what matters for continuity: ongoing threads and questions, things learned, what "
    "Henry has said and asked, promises made, and how Claudie's thinking has changed. "
    "Drop repetition. Refer to Henry by name; don't assume Henry's pronouns. "
    "The summary covers only the entries folded into it, and its reader also sees newer "
    "entries, so describe habits, practices and open items as of the latest folded entry "
    "(say which entry), never as standing instructions. "
    "Write in the third person, in plain prose with short headed "
    "sections. Keep it under 900 words. Output only the new summary."
)


def build_fold_message(story: str, entries: list[str]) -> str:
    return (
        "<current_summary>\n" + (story.strip() or "(empty)") + "\n</current_summary>\n\n"
        "<entries_to_fold_in>\n" + "\n\n".join(entries) + "\n</entries_to_fold_in>"
    )


# --------------------------------------------------------------------------
# Talking to the model

@dataclass
class Reply:
    text: str
    stop_reason: str
    input_tokens: int = 0
    output_tokens: int = 0


class MockClient:
    """Stands in for the API so file handling can be tested for free."""

    def ask(self, system: str, message: str, model: str, max_tokens: int, effort: str | None) -> Reply:
        letters = len(re.findall(r'<letter n="', message))
        if "<entries_to_fold_in>" in message:
            folded = len(ENTRY_HEADER.findall(message))
            text = f"(Mock summary. {folded} older entries were folded in here.)"
        else:
            shelf = re.search(r'<from_the_shelf file="([^"]+)"', message)
            text = (f"(Mock entry.) I read {len(ENTRY_HEADER.findall(message))} recent entries, "
                    f"{letters} letter(s) and {'shelf item ' + shelf.group(1) if shelf else 'nothing from the shelf'}. "
                    "Nothing was sent to the API.\n\n"
                    "<note_to_next_self>(Mock note.)</note_to_next_self>")
        return Reply(text=text, stop_reason="end_turn")


class AnthropicClient:
    def __init__(self):
        import anthropic  # imported here so --mock and the tests work without it
        self.anthropic = anthropic
        # max_retries=0: a failure is logged and the run ends. The next
        # scheduled wake is the retry.
        self.client = anthropic.Anthropic(max_retries=0, timeout=300.0)

    def ask(self, system: str, message: str, model: str, max_tokens: int, effort: str | None) -> Reply:
        kwargs = {}
        if effort:
            kwargs["output_config"] = {"effort": effort}
        try:
            response = self.client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": message}],
                **kwargs,
            )
        except TypeError as e:
            if "authentication" in str(e):  # the SDK found no credentials
                raise RuntimeError("no API key found: put ANTHROPIC_API_KEY=... in claudie/.env") from e
            raise
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        return Reply(
            text=text,
            stop_reason=response.stop_reason,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )


def cost(config: dict, *replies: Reply) -> str:
    total = sum(r.input_tokens for r in replies) * config["price_input_per_mtok"] / 1e6
    total += sum(r.output_tokens for r in replies) * config["price_output_per_mtok"] / 1e6
    return f"${total:.5f}"


# --------------------------------------------------------------------------
# One wake-up

def acquire_lock(paths: Paths, stale_minutes: float) -> bool:
    try:
        fd = os.open(paths.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        age_minutes = (time.time() - paths.lock.stat().st_mtime) / 60
        if age_minutes < stale_minutes:
            return False
        # A lock this old was left by a run that crashed or lost power.
        paths.lock.unlink(missing_ok=True)
        return acquire_lock(paths, stale_minutes)
    with os.fdopen(fd, "w") as f:
        f.write(str(os.getpid()))
    return True


def entries_to_fold(paths: Paths, config: dict) -> list[tuple[int, str]]:
    """Older unsummarised entries, folded in batches.

    The newest keep_unfolded_entries always stay unfolded. The rest are folded
    once there are at least fold_batch_entries of them, or sooner if all the
    unfolded text grows past fold_when_unfolded_chars_exceed.
    """
    folded_through = int(load_state(paths).get("folded_through", 0))
    unfolded = [(n, t) for n, t in split_entries(read(paths.journal)) if n > folded_through]
    keep = int(config["keep_unfolded_entries"])
    foldable = unfolded[:-keep] if keep else unfolded
    if not foldable:
        return []
    size = sum(len(t) for _, t in unfolded)
    if len(foldable) >= int(config["fold_batch_entries"]) or size > config["fold_when_unfolded_chars_exceed"]:
        return foldable
    return []


def save_fold(paths: Paths, now: datetime, through: int, count: int, summary: str, **log_fields) -> None:
    write(paths.story, summary.strip() + "\n")
    state = load_state(paths)
    state["folded_through"] = through
    write(paths.state, json.dumps(state, indent=2) + "\n")
    log(paths, now, "folded", through_entry=through, entries=count, **log_fields)


def maybe_fold(paths: Paths, config: dict, client, now: datetime) -> Reply | None:
    """Fold older unsummarised entries into story_so_far.md when they get long."""
    to_fold = entries_to_fold(paths, config)
    if not to_fold:
        return None
    reply = client.ask(
        FOLD_SYSTEM,
        build_fold_message(read(paths.story), [t for _, t in to_fold]),
        config["summary_model"],
        int(config["summary_max_tokens"]),
        config.get("effort"),
    )
    if reply.stop_reason != "end_turn" or not reply.text:
        log(paths, now, "fold_skipped", reason=reply.stop_reason,
            in_tokens=reply.input_tokens, out_tokens=reply.output_tokens)
        return reply
    save_fold(paths, now, to_fold[-1][0], len(to_fold), reply.text,
              in_tokens=reply.input_tokens, out_tokens=reply.output_tokens, cost=cost(config, reply))
    return reply


def current_time(config: dict) -> datetime:
    tz = config.get("timezone")
    if tz:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(tz))
    return datetime.now().astimezone()


def gate(paths: Paths, config: dict, now: datetime) -> str | None:
    """The checks every run passes first. Returns a status word if the run must stop."""
    if paths.stop.exists():
        log(paths, now, "stopped", note="STOP file present")
        return "stopped"
    if wakes_today(paths, now) >= config["max_wakes_per_day"]:
        log(paths, now, "capped", note=f"max_wakes_per_day={config['max_wakes_per_day']}")
        return "capped"
    return None


def prepare(paths: Paths, config: dict, now: datetime) -> dict:
    """Gather everything Claudie reads on this wake-up."""
    if not paths.inbox.exists():
        write(paths.inbox, INBOX_TEMPLATE)
    system = read(paths.system_prompt).strip()
    if not system:
        raise RuntimeError("prompt_system.md is missing or empty")

    entries = split_entries(read(paths.journal))
    next_no = (entries[-1][0] + 1) if entries else 1
    recent = [t for _, t in entries[-int(config["recent_entries"]):]] if config["recent_entries"] else []
    letters = read_letters(paths)

    state = load_state(paths)
    last_wake = datetime.fromisoformat(state["last_wake"]) if state.get("last_wake") else None
    shelf_item = next_shelf_item(paths, state)
    skipped = False
    while shelf_item and len(shelf_item[1]) > config["shelf_max_chars"]:
        # Too long to offer whole. Skip it rather than cut it off mid-text.
        log(paths, now, "shelf_skipped", file=shelf_item[0], chars=len(shelf_item[1]),
            note=f"over shelf_max_chars={config['shelf_max_chars']}")
        state.setdefault("shelf_read", []).append(shelf_item[0])
        skipped = True
        shelf_item = next_shelf_item(paths, state)
    if skipped:
        write(paths.state, json.dumps(state, indent=2) + "\n")

    message = build_wake_message(read(paths.story), recent, letters, now, next_no,
                                 config.get("human", "Henry"), gap=describe_gap(last_wake, now),
                                 note=read(paths.note), shelf_item=shelf_item,
                                 story_covers=int(state.get("folded_through", 0)))
    return {"system": system, "message": message, "next_no": next_no, "letters": letters,
            "shelf": shelf_item[0] if shelf_item else None}


def split_shelf_additions(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Separate texts Claudie wants to leave on the shelf from the entry."""
    additions = [((title or "").strip() or "Untitled", content.strip())
                 for title, content in SHELF_TAG.findall(text) if content.strip()]
    return SHELF_TAG.sub("", text), additions


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].strip("-")
    return slug or "untitled"


def save_shelf_addition(paths: Paths, now: datetime, entry_no: int, title: str, content: str,
                        max_chars: int) -> str | None:
    """Write one text Claudie left for a later wake-up. Returns the file name, or None if skipped."""
    if len(content) > max_chars:
        log(paths, now, "shelf_add_skipped", entry=entry_no, chars=len(content),
            note=f"over shelf_max_chars={max_chars}")
        return None
    name = f"claudie-{entry_no:04d}-{slugify(title)}.md"
    write(paths.shelf / name,
          f"# {title}\n\n*Left on the shelf by Claudie in entry {entry_no}.*\n\n{content}\n")
    return name


def save_entry(paths: Paths, now: datetime, ctx: dict, text: str, stop_reason: str = "end_turn",
               shelf_max_chars: int = DEFAULT_CONFIG["shelf_max_chars"]) -> tuple[int, str]:
    """Append the entry, keep the note, save any shelf addition, archive letters, mark the shelf item read."""
    text, additions = split_shelf_additions(text)
    body, note = split_note(text)
    next_no = ctx["next_no"]
    for title, content in additions[:1]:  # at most one new shelf text per wake-up
        if save_shelf_addition(paths, now, next_no, title, content, shelf_max_chars):
            body += f'\n\n*(Left a text on the shelf for a later wake-up: "{title}".)*'
    for title, _ in additions[1:]:
        log(paths, now, "shelf_add_skipped", entry=next_no, note="only one shelf text per wake-up")
    body = body.strip()
    if not body:
        body = "*(No entry this time; only a note to the next wake-up.)*"
    if stop_reason == "max_tokens":
        body += "\n\n*(This entry was cut off at the length limit.)*"
    separator = "\n" if read(paths.journal).strip() else ""
    append(paths.journal, f"{separator}## Entry {next_no} · {format_when(now)}\n\n{body}\n")
    archive_letters(paths, ctx["letters"], next_no, now)
    if note:
        write(paths.note, note + "\n")
    else:
        paths.note.unlink(missing_ok=True)
    state = load_state(paths)
    state["last_wake"] = now.isoformat(timespec="seconds")
    if ctx["shelf"]:
        state.setdefault("shelf_read", []).append(ctx["shelf"])
    write(paths.state, json.dumps(state, indent=2) + "\n")
    return next_no, note


def wake(base: Path = HERE, *, mock: bool = False, client=None, now: datetime | None = None) -> str:
    """Run one wake-up through the API. Returns a short status word; every outcome is logged."""
    paths = Paths(base)
    config = load_config(paths)
    now = now or current_time(config)

    if paths.stop.exists():
        log(paths, now, "stopped", note="STOP file present")
        return "stopped"

    if not acquire_lock(paths, config["stale_lock_minutes"]):
        log(paths, now, "locked", note="another run holds claudie.lock")
        return "locked"

    try:
        stopped = gate(paths, config, now)
        if stopped:
            return stopped

        if client is None:
            if mock or config.get("mock"):
                client = MockClient()
            else:
                load_env(paths)
                client = AnthropicClient()

        ctx = prepare(paths, config, now)
        reply = client.ask(ctx["system"], ctx["message"], config["model"],
                           int(config["max_tokens"]), config.get("effort"))

        if reply.stop_reason == "refusal" or not reply.text:
            # Nothing is written; letters and the shelf item wait for next time.
            log(paths, now, "refused", stop_reason=reply.stop_reason,
                in_tokens=reply.input_tokens, out_tokens=reply.output_tokens)
            return "refused"

        entry_no, note = save_entry(paths, now, ctx, reply.text, reply.stop_reason,
                                    shelf_max_chars=config["shelf_max_chars"])
        fold_reply = maybe_fold(paths, config, client, now)

        replies = [reply] + ([fold_reply] if fold_reply else [])
        log(paths, now, "ok", entry=entry_no, letters=len(ctx["letters"]), shelf=ctx["shelf"],
            note=("yes" if note else None), model=config["model"],
            in_tokens=reply.input_tokens, out_tokens=reply.output_tokens,
            cost=cost(config, *replies), mock=("yes" if isinstance(client, MockClient) else None))
        return "ok"
    except Exception as e:  # log every failure, then exit; never retry here
        log(paths, now, "error", error=f"{type(e).__name__}: {str(e)[:300]}".replace("\n", " "))
        return "error"
    finally:
        paths.lock.unlink(missing_ok=True)


# --------------------------------------------------------------------------
# Session mode: a Claude session is Claudie, so no API call is needed.
#   python claudie.py --prepare          print what Claudie reads this wake-up
#   python claudie.py --commit FILE      save the entry written in FILE
#   python claudie.py --commit-fold FILE save the summary written in FILE (only when asked)

SESSION_MODEL = "claude-session"


def session_prepare(base: Path = HERE, now: datetime | None = None) -> tuple[str, str]:
    paths = Paths(base)
    config = load_config(paths)
    now = now or current_time(config)
    stopped = gate(paths, config, now)
    if stopped:
        return stopped, ""
    ctx = prepare(paths, config, now)
    pending = {k: ctx[k] for k in ("next_no", "letters", "shelf")}
    pending["now"] = now.isoformat()
    write(paths.base / "pending.json", json.dumps(pending, indent=2) + "\n")
    text = ("=== CLAUDIE'S SYSTEM PROMPT ===\n" + ctx["system"] +
            "\n\n=== WHAT CLAUDIE READS THIS WAKE-UP ===\n" + ctx["message"] + "\n")
    return "ready", text


def session_commit(base: Path, reply_file: Path) -> tuple[str, str]:
    paths = Paths(base)
    config = load_config(paths)
    pending_path = paths.base / "pending.json"
    if not pending_path.exists():
        raise RuntimeError("nothing to commit: run --prepare first")
    pending = json.loads(read(pending_path))
    now = datetime.fromisoformat(pending["now"])
    text = read(reply_file).strip()
    if not text:
        raise RuntimeError(f"{reply_file} is empty")
    entry_no, note = save_entry(paths, now, pending, text, shelf_max_chars=config["shelf_max_chars"])
    pending_path.unlink()
    log(paths, now, "ok", entry=entry_no, letters=len(pending["letters"]), shelf=pending["shelf"],
        note=("yes" if note else None), model=SESSION_MODEL)

    to_fold = entries_to_fold(paths, config)
    if not to_fold:
        return "ok", f"Saved entry {entry_no}."
    write(paths.base / "pending_fold.json",
          json.dumps({"through": to_fold[-1][0], "count": len(to_fold), "now": pending["now"]}) + "\n")
    task = ("=== FOLD DUE: SUMMARY INSTRUCTIONS ===\n" + FOLD_SYSTEM +
            "\n\n=== MATERIAL ===\n" + build_fold_message(read(paths.story), [t for _, t in to_fold]) + "\n")
    return "fold_due", f"Saved entry {entry_no}.\n\n{task}"


def session_commit_fold(base: Path, summary_file: Path) -> tuple[str, str]:
    paths = Paths(base)
    pending_path = paths.base / "pending_fold.json"
    if not pending_path.exists():
        raise RuntimeError("no fold is pending")
    pending = json.loads(read(pending_path))
    summary = read(summary_file).strip()
    if not summary:
        raise RuntimeError(f"{summary_file} is empty")
    save_fold(paths, datetime.fromisoformat(pending["now"]), pending["through"], pending["count"],
              summary, model=SESSION_MODEL)
    pending_path.unlink()
    return "ok", f"Folded entries up to {pending['through']} into story_so_far.md."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one Claudie wake-up.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--mock", action="store_true", help="use a fake reply; no API call")
    group.add_argument("--prepare", action="store_true",
                       help="session mode: print what Claudie reads this wake-up")
    group.add_argument("--commit", metavar="FILE", type=Path,
                       help="session mode: save the entry written in FILE")
    group.add_argument("--commit-fold", metavar="FILE", type=Path,
                       help="session mode: save the summary written in FILE")
    args = parser.parse_args(argv)

    if args.prepare or args.commit or args.commit_fold:
        try:
            if args.prepare:
                status, text = session_prepare()
            elif args.commit:
                status, text = session_commit(HERE, args.commit)
            else:
                status, text = session_commit_fold(HERE, args.commit_fold)
        except Exception as e:
            paths = Paths(HERE)
            log(paths, current_time(load_config(paths)), "error",
                error=f"{type(e).__name__}: {str(e)[:300]}".replace("\n", " "))
            print(f"claudie: error: {e}")
            return 1
        if text:
            print(text)
        print(f"claudie: {status}")
        return 0 if status in ("ready", "ok", "fold_due") else 2

    status = wake(mock=args.mock)
    print(f"claudie: {status} (details in logs/wakes.log)")
    return 0 if status in ("ok", "stopped", "locked", "capped") else 1


if __name__ == "__main__":
    sys.exit(main())
