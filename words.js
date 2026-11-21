// Word clock logic: turns a time into the words that should light up.
// Works in the browser (as window.WordClock) and in Node (module.exports).
(function (root) {
  'use strict';

  // 10 rows x 12 columns. Letters not part of a word are filler.
  const GRID = [
    'ITQISMAZYTBK',
    'TWENTYXHALFP',
    'QUARTERFIVEW',
    'TENWTOPPASTD',
    'ONETWOSIXKEG',
    'THREEFOURNIV',
    'FIVESEVENRZU',
    'EIGHTNINEXJL',
    'TENELEVENQYM',
    'TWELVEOCLOCK',
  ];

  // word key -> [row, startCol, length]
  const WORDS = {
    IT: [0, 0, 2],
    IS: [0, 3, 2],
    A: [0, 6, 1],
    TWENTY: [1, 0, 6],
    HALF: [1, 7, 4],
    QUARTER: [2, 0, 7],
    M_FIVE: [2, 7, 4],
    M_TEN: [3, 0, 3],
    TO: [3, 4, 2],
    PAST: [3, 7, 4],
    H1: [4, 0, 3],
    H2: [4, 3, 3],
    H6: [4, 6, 3],
    H3: [5, 0, 5],
    H4: [5, 5, 4],
    H5: [6, 0, 4],
    H7: [6, 4, 5],
    H8: [7, 0, 5],
    H9: [7, 5, 4],
    H10: [8, 0, 3],
    H11: [8, 3, 6],
    H12: [9, 0, 6],
    OCLOCK: [9, 6, 6],
  };

  const HOUR_NAMES = ['twelve', 'one', 'two', 'three', 'four', 'five', 'six',
    'seven', 'eight', 'nine', 'ten', 'eleven'];

  // Minute words for each 5-minute step, and whether it's "past" or "to".
  const MINUTE_STEPS = [
    { keys: [], rel: null },                      // :00  o'clock
    { keys: ['M_FIVE'], rel: 'PAST' },            // :05
    { keys: ['M_TEN'], rel: 'PAST' },             // :10
    { keys: ['A', 'QUARTER'], rel: 'PAST' },      // :15
    { keys: ['TWENTY'], rel: 'PAST' },            // :20
    { keys: ['TWENTY', 'M_FIVE'], rel: 'PAST' },  // :25
    { keys: ['HALF'], rel: 'PAST' },              // :30
    { keys: ['TWENTY', 'M_FIVE'], rel: 'TO' },    // :35
    { keys: ['TWENTY'], rel: 'TO' },              // :40
    { keys: ['A', 'QUARTER'], rel: 'TO' },        // :45
    { keys: ['M_TEN'], rel: 'TO' },               // :50
    { keys: ['M_FIVE'], rel: 'TO' },              // :55
  ];

  const SPOKEN = {
    IT: 'it', IS: 'is', A: 'a', TWENTY: 'twenty', HALF: 'half',
    QUARTER: 'quarter', M_FIVE: 'five', M_TEN: 'ten', TO: 'to',
    PAST: 'past', OCLOCK: "o'clock",
  };

  /**
   * @param {number} hours   0-23
   * @param {number} minutes 0-59
   * @returns {{keys: string[], dots: number, phrase: string}}
   *   keys: words to light, dots: extra minutes past the 5-minute mark (0-4),
   *   phrase: the time as a sentence.
   */
  function timeToWords(hours, minutes) {
    if (!Number.isInteger(hours) || hours < 0 || hours > 23) {
      throw new RangeError('hours must be an integer from 0 to 23');
    }
    if (!Number.isInteger(minutes) || minutes < 0 || minutes > 59) {
      throw new RangeError('minutes must be an integer from 0 to 59');
    }

    const step = MINUTE_STEPS[Math.floor(minutes / 5)];
    const dots = minutes % 5;
    const hour12 = ((step.rel === 'TO' ? hours + 1 : hours) % 12);
    const hourKey = 'H' + (hour12 === 0 ? 12 : hour12);

    const keys = ['IT', 'IS', ...step.keys];
    if (step.rel) keys.push(step.rel);
    keys.push(hourKey);
    if (!step.rel) keys.push('OCLOCK');

    const phrase = keys
      .map((k) => (k === hourKey ? HOUR_NAMES[hour12] : SPOKEN[k]))
      .join(' ');

    return { keys, dots, phrase };
  }

  /** Set of "row,col" strings for every letter that should be lit. */
  function litCells(keys) {
    const cells = new Set();
    for (const key of keys) {
      const [row, col, len] = WORDS[key];
      for (let i = 0; i < len; i++) cells.add(row + ',' + (col + i));
    }
    return cells;
  }

  const api = { GRID, WORDS, timeToWords, litCells };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.WordClock = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
