const test = require('node:test');
const assert = require('node:assert/strict');
const {
  step, singleCell, randomRow, seededRandom, run, ruleTable,
} = require('../automata/rules.js');

const ones = (row) => row.reduce((n, c) => n + c, 0);
const popcount = (n) => n.toString(2).split('').filter((b) => b === '1').length;

test('rule 90 from one cell has 2^popcount(n) live cells in row n', () => {
  // Gould's sequence: a known property of the Sierpinski pattern.
  const rows = run(singleCell(301), 90, 128, false);
  rows.forEach((row, n) => assert.equal(ones(row), 2 ** popcount(n), `row ${n}`));
});

test('rule 30 centre column starts with the known sequence', () => {
  // OEIS A051023
  const expected = [1, 1, 0, 1, 1, 1, 0, 0, 1, 1, 0, 0, 0, 1, 0, 1];
  const width = 101;
  const rows = run(singleCell(width), 30, expected.length, false);
  assert.deepEqual(rows.map((r) => r[(width - 1) / 2]), expected);
});

test('rule 184 conserves the number of live cells when wrapping', () => {
  const rows = run(randomRow(200, 0.4, seededRandom(7)), 184, 100, true);
  const count = ones(rows[0]);
  for (const row of rows) assert.equal(ones(row), count);
});

test('rule 204 is the identity and rule 0 kills everything', () => {
  const row = randomRow(64, 0.5, seededRandom(1));
  assert.deepEqual(step(row, 204), row);
  assert.equal(ones(step(row, 0)), 0);
});

test('rule 170 shifts every cell one place to the left', () => {
  const row = Uint8Array.from([1, 0, 0, 1, 1, 0, 1, 0]);
  assert.deepEqual(Array.from(step(row, 170, true)), [0, 0, 1, 1, 0, 1, 0, 1]);
});

test('wrap controls the edges', () => {
  const row = Uint8Array.from([1, 0, 0, 0, 0]);
  // Rule 2: a cell becomes live only if its right neighbour alone is live (001).
  assert.deepEqual(Array.from(step(row, 2, true)), [0, 0, 0, 0, 1]);
  assert.deepEqual(Array.from(step(row, 2, false)), [0, 0, 0, 0, 0]);
});

test('ruleTable lists neighbourhoods 111 to 000 with the rule bits', () => {
  const t = ruleTable(30); // 30 = 00011110
  assert.deepEqual(t.map((e) => e.pattern.join('')),
    ['111', '110', '101', '100', '011', '010', '001', '000']);
  assert.deepEqual(t.map((e) => e.out), [0, 0, 0, 1, 1, 1, 1, 0]);
});

test('seeded random rows are repeatable', () => {
  assert.deepEqual(randomRow(50, 0.5, seededRandom(42)), randomRow(50, 0.5, seededRandom(42)));
});

test('rejects invalid rules', () => {
  assert.throws(() => step([0, 1, 0], 256), RangeError);
  assert.throws(() => step([0, 1, 0], -1), RangeError);
  assert.throws(() => ruleTable(3.5), RangeError);
});
