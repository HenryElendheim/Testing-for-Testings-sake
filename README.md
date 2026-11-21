# Testing-for-Testings-sake

Two small browser toys with no dependencies:

- [Word clock](#word-clock): spells out the time in a grid of letters.
- [Rule Explorer](#rule-explorer): all 256 elementary cellular automata.

Run every test with `npm test` (Node 18 or newer).

## Word clock

A **word clock**: a grid of letters where the ones that spell out the current
time light up, e.g. "IT IS TWENTY PAST ONE". The corner dots count the extra
minutes (0 to 4) past the last five-minute mark.

Open `index.html` in a browser. No build step and no dependencies.

- Pick any time zone from the dropdown (it defaults to yours).
- **Toggle theme** switches between light and dark.

Files:

- `words.js` holds the letter grid and the logic that turns a time into words.
  It works in the browser and in Node.
- `index.html` is the clock face.
- `test/words.test.js` checks every minute of the day.

## Rule Explorer

Open `automata/index.html`. Each row is computed from the row above: a cell's
next state depends only on itself and its two neighbours. That gives 8 possible
neighbourhoods, so a rule is just 8 bits, a number from 0 to 255.

- Type a rule number, or click any of the 8 tiles to flip that bit.
- Use the presets for the famous ones: 30 (chaos), 90 (Sierpinski triangle),
  110 (Turing complete), 184 (traffic flow).
- Start from one live cell or a random row, and change the zoom.
- Keyboard: arrow keys step through rules, Space picks a random rule, and R
  rerolls the random start.
- The URL keeps your settings, so you can share a pattern you find.

Files:

- `automata/rules.js` holds the automaton logic. It works in the browser and in Node.
- `automata/index.html` is the explorer.
- `test/automata.test.js` checks the logic against known properties, such as
  Rule 90 matching Gould's sequence and Rule 30's centre column (OEIS A051023).
