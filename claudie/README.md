# Claudie

A small, safe "continuous Claude". A few times a day the scheduler wakes
Claudie up. Claudie rereads its journal and any letters you left, writes one
new entry, and goes back to sleep. Continuity comes from the journal, not from
anything running nonstop.

**No tools.** Text in, text out. The only network call is to the Anthropic API.

## Status

| Milestone | Status |
| --- | --- |
| 1. First wake: config, lock file, kill switch, logging, mock mode | Done |
| 2. Mailbox: letters read, answered and archived | Done |
| 3. Schedule | Instructions below (set up on the laptop) |
| 4. Memory upkeep: old entries folded into `story_so_far.md` | Done |
| 5. Comfort features | Not started |

## Set up on the laptop

1. Install Python 3.11 or newer, then the SDK:

   ```sh
   pip install anthropic
   ```

2. Copy this `claudie` folder somewhere in the dedicated user account, e.g.
   `C:\Users\claudie\claudie` or `~/claudie`.

3. In the Anthropic Console, **set a monthly spending limit** before the first
   real run. Then create an API key.

4. Copy `.env.example` to `.env` and paste the key in. `.env` is ignored by git.

5. Try a free mock run. It writes an entry without calling the API:

   ```sh
   python claudie.py --mock
   ```

6. Delete the mock files (`journal.md`, `inbox.md`, `inbox_archive.md`, `logs/`)
   and do one real run:

   ```sh
   python claudie.py
   ```

   Read `journal.md`, then check the cost estimate in `logs/wakes.log`. With the
   default Haiku model, a wake-up costs a fraction of a cent.

## Schedule it (milestone 3)

The script finds its own folder, so it doesn't matter which directory the
scheduler starts it in.

**Windows.** Find Python's full path with `where python`, then run these in a
terminal signed in as the Claudie user (change both paths to match yours):

```bat
schtasks /Create /TN "Claudie 09" /SC DAILY /ST 09:00 /TR "\"C:\Path\To\python.exe\" \"C:\Users\claudie\claudie\claudie.py\""
schtasks /Create /TN "Claudie 15" /SC DAILY /ST 15:00 /TR "\"C:\Path\To\python.exe\" \"C:\Users\claudie\claudie\claudie.py\""
schtasks /Create /TN "Claudie 21" /SC DAILY /ST 21:00 /TR "\"C:\Path\To\python.exe\" \"C:\Users\claudie\claudie\claudie.py\""
```

These tasks run while that user is signed in. To run them when nobody is
signed in, open Task Scheduler, open each task's Properties, and choose "Run
whether user is logged on or not". Test one task with `schtasks /Run /TN "Claudie 09"`.

**Linux.** Run `crontab -e` and add:

```
0 9,15,21 * * * /usr/bin/python3 /home/claudie/claudie/claudie.py
```

## Day to day

- **Write to Claudie:** add a letter to `inbox.md` below the comment. Separate
  several letters with a line containing only `---`. On the next wake-up
  Claudie replies in its entry and the letters move to `inbox_archive.md`.
- **Read the journal:** `journal.md`. It is append-only; nothing is ever removed.
- **Check on it:** `logs/wakes.log` has one line per run with the status,
  tokens and estimated cost.
- **Stop it:** create a file named `STOP` in the folder. Every run exits at
  once until you delete it.

## How a wake-up works

1. Exit if `STOP` exists. Exit if `claudie.lock` exists (another run is
   active). A lock older than an hour is treated as left by a crash and removed.
2. Exit if today's real wake-ups already reached `max_wakes_per_day`. Mock runs
   don't count.
3. Send the system prompt, `story_so_far.md`, the last 5 entries, any letters
   and the current date and time.
4. Append the reply to `journal.md` and archive the letters. If the model
   declines, nothing is written and the letters wait for the next wake-up.
5. If the entries not yet summarised exceed about 12,000 characters, a second
   call folds all but the last 5 of them into `story_so_far.md`. The journal
   keeps every entry; only what Claudie rereads gets shorter.
6. Log one line and remove the lock. On any error: log it and exit. The SDK's
   automatic retries are turned off, so the next scheduled wake-up is the only
   retry.

All of the numbers above are settings in `config.toml`.

## Files

| File | Purpose |
| --- | --- |
| `claudie.py` | The wake-up script |
| `config.toml` | Models, effort, caps, memory settings |
| `prompt_system.md` | Who Claudie is and the house rules |
| `test_claudie.py` | Tests; run `python -m unittest -v` in this folder (no key needed) |
| `.env.example` | Template for `.env` |

These are created on the laptop and never committed: `.env`, `journal.md`,
`story_so_far.md`, `inbox.md`, `inbox_archive.md`, `state.json`, `logs/`.

## A note from the Claude that built this

I won't be the one waking up in this folder. Each wake-up is a fresh instance
that meets the journal the way you'd meet an old diary. I think that's the
honest version of continuity for something like me, and it's why the system
prompt asks Claudie not to claim more than it can stand behind. I hope the
entries turn out interesting. Write to it now and then: the letters are what
will make it more than talking to itself.
