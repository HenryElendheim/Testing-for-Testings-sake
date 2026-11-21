// Elementary cellular automata: a row of 0/1 cells, where each cell's next
// state depends on itself and its two neighbours. A rule number 0-255 encodes
// the outcome for each of the 8 possible neighbourhoods (Wolfram numbering).
// Works in the browser (as window.Automata) and in Node (module.exports).
(function (root) {
  'use strict';

  function checkRule(rule) {
    if (!Number.isInteger(rule) || rule < 0 || rule > 255) {
      throw new RangeError('rule must be an integer from 0 to 255');
    }
  }

  /**
   * Compute the next row.
   * @param {Uint8Array|number[]} cells current row of 0s and 1s
   * @param {number} rule 0-255
   * @param {boolean} [wrap=true] edges wrap around; otherwise cells beyond
   *   the edge count as 0
   * @returns {Uint8Array}
   */
  function step(cells, rule, wrap = true) {
    checkRule(rule);
    const n = cells.length;
    const next = new Uint8Array(n);
    for (let i = 0; i < n; i++) {
      const l = i > 0 ? cells[i - 1] : (wrap ? cells[n - 1] : 0);
      const c = cells[i];
      const r = i < n - 1 ? cells[i + 1] : (wrap ? cells[0] : 0);
      next[i] = (rule >> ((l << 2) | (c << 1) | r)) & 1;
    }
    return next;
  }

  /** A row with a single live cell in the middle. */
  function singleCell(width) {
    const row = new Uint8Array(width);
    row[Math.floor(width / 2)] = 1;
    return row;
  }

  /** A random row. `random` is injectable for repeatable output. */
  function randomRow(width, density = 0.5, random = Math.random) {
    const row = new Uint8Array(width);
    for (let i = 0; i < width; i++) row[i] = random() < density ? 1 : 0;
    return row;
  }

  /** Small seeded PRNG (mulberry32) so a seed always gives the same row. */
  function seededRandom(seed) {
    let a = seed >>> 0;
    return function () {
      a = (a + 0x6d2b79f5) >>> 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  /** Run `generations` steps; returns all rows including the first. */
  function run(first, rule, generations, wrap = true) {
    const rows = [Uint8Array.from(first)];
    for (let g = 1; g < generations; g++) rows.push(step(rows[g - 1], rule, wrap));
    return rows;
  }

  /** The 8 neighbourhoods from 111 down to 000, and what the rule maps each to. */
  function ruleTable(rule) {
    checkRule(rule);
    const table = [];
    for (let p = 7; p >= 0; p--) {
      table.push({ pattern: [(p >> 2) & 1, (p >> 1) & 1, p & 1], out: (rule >> p) & 1 });
    }
    return table;
  }

  // Rules worth a look, with a one-line reason.
  const PRESETS = [
    { rule: 30, note: 'Chaotic. Its centre column is random enough to have been used as a random number generator.' },
    { rule: 90, note: 'Each cell is the XOR of its neighbours. From one cell it draws a Sierpinski triangle.' },
    { rule: 110, note: 'Proven Turing complete: it can, in principle, run any program.' },
    { rule: 184, note: 'A traffic model. Live cells are cars that move right when the space ahead is free.' },
    { rule: 73, note: 'Walls and gliders. Start from a random row.' },
    { rule: 150, note: 'XOR of all three cells. Another fractal, with a denser weave.' },
    { rule: 54, note: 'Complex, with particles that collide and interact.' },
    { rule: 45, note: 'Chaotic, with a lean to one side.' },
  ];

  const api = { step, singleCell, randomRow, seededRandom, run, ruleTable, PRESETS };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.Automata = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
