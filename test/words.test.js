const test = require('node:test');
const assert = require('node:assert/strict');
const { GRID, WORDS, timeToWords, litCells } = require('../words.js');

test('grid rows are all the same width', () => {
  const width = GRID[0].length;
  for (const row of GRID) assert.equal(row.length, width, row);
});

test('every word key spells the right word in the grid', () => {
  const expected = {
    IT: 'IT', IS: 'IS', A: 'A', TWENTY: 'TWENTY', HALF: 'HALF',
    QUARTER: 'QUARTER', M_FIVE: 'FIVE', M_TEN: 'TEN', TO: 'TO', PAST: 'PAST',
    H1: 'ONE', H2: 'TWO', H3: 'THREE', H4: 'FOUR', H5: 'FIVE', H6: 'SIX',
    H7: 'SEVEN', H8: 'EIGHT', H9: 'NINE', H10: 'TEN', H11: 'ELEVEN',
    H12: 'TWELVE', OCLOCK: 'OCLOCK',
  };
  for (const [key, [row, col, len]] of Object.entries(WORDS)) {
    assert.equal(GRID[row].slice(col, col + len), expected[key], key);
  }
});

const cases = [
  [0, 0, "it is twelve o'clock", 0],
  [1, 46, 'it is a quarter to two', 1],
  [1, 52, 'it is ten to two', 2],
  [12, 5, 'it is five past twelve', 0],
  [13, 15, 'it is a quarter past one', 0],
  [9, 25, 'it is twenty five past nine', 0],
  [9, 30, 'it is half past nine', 0],
  [9, 35, 'it is twenty five to ten', 0],
  [11, 40, 'it is twenty to twelve', 0],
  [23, 59, 'it is five to twelve', 4],
  [18, 3, "it is six o'clock", 3],
];

for (const [h, m, phrase, dots] of cases) {
  test(`${h}:${String(m).padStart(2, '0')} -> ${phrase}`, () => {
    const result = timeToWords(h, m);
    assert.equal(result.phrase, phrase);
    assert.equal(result.dots, dots);
  });
}

test('every minute of the day produces valid, non-overlapping words', () => {
  for (let h = 0; h < 24; h++) {
    for (let m = 0; m < 60; m++) {
      const { keys } = timeToWords(h, m);
      const total = keys.reduce((n, k) => n + WORDS[k][2], 0);
      assert.equal(litCells(keys).size, total, `${h}:${m} has overlapping words`);
    }
  }
});

test('rejects out-of-range input', () => {
  assert.throws(() => timeToWords(24, 0), RangeError);
  assert.throws(() => timeToWords(0, 60), RangeError);
  assert.throws(() => timeToWords(1.5, 0), RangeError);
});
