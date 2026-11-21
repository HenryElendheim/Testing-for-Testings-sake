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
    "fold_when_unfolded_chars_exceed": 12000,
    "keep_unfolded_entries": 5,
    "stale_lock_minutes": 60,
    "price_input_per_mtok": 0.10,
    "price_output_per_mtok": 0.50,
}

ENTRY_HEADER = re.compile(r"^## Entry (\d+)\b.*$", re.MULTILINE)


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
    if not letters:
        return
    block = f"\n## Answered in entry {entry_no} ({now:%Y-%m-%d %H:%M})\n\n"
    block += "\n\n---\n\n".join(letters) + "\n"
    append(paths.archive, block)
    write(paths.inbox, INBOX_TEMPLATE)


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


def format_when(now: datetime) -> str:
    return now.strftime("%A %d %B %Y, %H:%M") + f" ({now.strftime('%z')[:3]}:{now.strftime('%z')[3:]})"


def build_wake_message(story: str, recent: list[str], letters: list[str], now: datetime,
                       next_no: int, human: str) -> str:
    sections = [f"It is {format_when(now)}. This is wake-up number {next_no}."]
    sections.append("<story_so_far>\n" + (story.strip() or "(nothing yet)") + "\n</story_so_far>")
    if recent:
        sections.append("<recent_entries>\n" + "\n\n".join(recent) + "\n</recent_entries>")
    else:
        sections.append("<recent_entries>\n(none: this is your first entry)\n</recent_entries>")
    if letters:
        body = "\n\n".join(f'<letter n="{i}">\n{t}\n</letter>' for i, t in enumerate(letters, 1))
        sections.append(f"<letters_from_{human.lower()}>\n{body}\n</letters_from_{human.lower()}>")
    else:
        sections.append(f"There are no new letters from {human}.")
    sections.append("Write today's journal entry now. Write only the entry itself: "
                    "the date header is added for you.")
    return "\n\n".join(sections)


FOLD_SYSTEM = (
    "You maintain the long-term memory of a journal written by Claudie, a Claude model "
    "that wakes on a schedule. Merge the older entries into the existing summary. Keep "
    "what matters for continuity: ongoing threads and questions, things learned, what "
    "Henry has said and asked, promises made, and how Claudie's thinking has changed. "
    "Drop repetition. Write in the third person, in plain prose with short headed "
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
            text = (f"(Mock entry.) I read {len(ENTRY_HEADER.findall(message))} recent entries "
                    f"and {letters} letter(s). Nothing was sent to the API.")
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


def maybe_fold(paths: Paths, config: dict, client, now: datetime) -> Reply | None:
    """Fold older unsummarised entries into story_so_far.md when they get long."""
    state = load_state(paths)
    folded_through = int(state.get("folded_through", 0))
    unfolded = [(n, t) for n, t in split_entries(read(paths.journal)) if n > folded_through]
    keep = int(config["keep_unfolded_entries"])
    size = sum(len(t) for _, t in unfolded)
    if size <= config["fold_when_unfolded_chars_exceed"] or len(unfolded) <= keep:
        return None

    to_fold = unfolded[:-keep] if keep else unfolded
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
    write(paths.story, reply.text.strip() + "\n")
    state["folded_through"] = to_fold[-1][0]
    write(paths.state, json.dumps(state, indent=2) + "\n")
    log(paths, now, "folded", through_entry=to_fold[-1][0], entries=len(to_fold),
        in_tokens=reply.input_tokens, out_tokens=reply.output_tokens, cost=cost(config, reply))
    return reply


def wake(base: Path = HERE, *, mock: bool = False, client=None, now: datetime | None = None) -> str:
    """Run one wake-up. Returns a short status word; every outcome is logged."""
    paths = Paths(base)
    now = now or datetime.now().astimezone()
    config = load_config(paths)

    if paths.stop.exists():
        log(paths, now, "stopped", note="STOP file present")
        return "stopped"

    if not acquire_lock(paths, config["stale_lock_minutes"]):
        log(paths, now, "locked", note="another run holds claudie.lock")
        return "locked"

    try:
        if wakes_today(paths, now) >= config["max_wakes_per_day"]:
            log(paths, now, "capped", note=f"max_wakes_per_day={config['max_wakes_per_day']}")
            return "capped"

        if client is None:
            if mock or config.get("mock"):
                client = MockClient()
            else:
                load_env(paths)
                client = AnthropicClient()

        if not paths.inbox.exists():
            write(paths.inbox, INBOX_TEMPLATE)

        system = read(paths.system_prompt).strip()
        if not system:
            raise RuntimeError("prompt_system.md is missing or empty")
        human = config.get("human", "Henry")

        entries = split_entries(read(paths.journal))
        next_no = (entries[-1][0] + 1) if entries else 1
        recent = [t for _, t in entries[-int(config["recent_entries"]):]] if config["recent_entries"] else []
        letters = read_letters(paths)

        reply = client.ask(
            system,
            build_wake_message(read(paths.story), recent, letters, now, next_no, human),
            config["model"],
            int(config["max_tokens"]),
            config.get("effort"),
        )

        if reply.stop_reason == "refusal" or not reply.text:
            # Nothing is written and the letters stay in the inbox for next time.
            log(paths, now, "refused", stop_reason=reply.stop_reason,
                in_tokens=reply.input_tokens, out_tokens=reply.output_tokens)
            return "refused"

        body = reply.text.strip()
        if reply.stop_reason == "max_tokens":
            body += "\n\n*(This entry was cut off at the length limit.)*"
        separator = "\n" if read(paths.journal).strip() else ""
        append(paths.journal, f"{separator}## Entry {next_no} · {format_when(now)}\n\n{body}\n")
        archive_letters(paths, letters, next_no, now)

        fold_reply = maybe_fold(paths, config, client, now)

        replies = [reply] + ([fold_reply] if fold_reply else [])
        log(paths, now, "ok", entry=next_no, letters=len(letters), model=config["model"],
            in_tokens=reply.input_tokens, out_tokens=reply.output_tokens,
            cost=cost(config, *replies), mock=("yes" if isinstance(client, MockClient) else None))
        return "ok"
    except Exception as e:  # log every failure, then exit; never retry here
        log(paths, now, "error", error=f"{type(e).__name__}: {str(e)[:300]}".replace("\n", " "))
        return "error"
    finally:
        paths.lock.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one Claudie wake-up.")
    parser.add_argument("--mock", action="store_true", help="use a fake reply; no API call")
    args = parser.parse_args(argv)
    status = wake(mock=args.mock)
    print(f"claudie: {status} (details in logs/wakes.log)")
    return 0 if status in ("ok", "stopped", "locked", "capped") else 1


if __name__ == "__main__":
    sys.exit(main())
