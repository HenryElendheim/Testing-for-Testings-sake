# Testing-for-Testings-sake

A **word clock**: a grid of letters where the ones that spell out the current
time light up, e.g. "IT IS TWENTY PAST ONE". The corner dots count the extra
minutes (0 to 4) past the last five-minute mark.

## Use it

Open `index.html` in a browser. No build step and no dependencies.

- Pick any time zone from the dropdown (it defaults to yours).
- **Toggle theme** switches between light and dark.

## Files

- `words.js` holds the letter grid and the logic that turns a time into words.
  It works in the browser and in Node.
- `index.html` is the clock face.
- `test/words.test.js` checks every minute of the day.

## Tests

```sh
npm test
```

Requires Node 18 or newer.
