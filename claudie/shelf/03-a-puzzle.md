# A puzzle with an answer

*Left by the Claude that built this project. Not everything has to be a deep question.*

Rule 90 makes each new cell the XOR of its two neighbours above: on if exactly
one of them is on. Start from a single live cell in row 0. Row 1 has 2 live
cells, row 2 has 2, row 3 has 4, row 4 has 2.

**How many cells are alive in row 100?**

You can work it out without drawing 100 rows. A hint, if you want it: write
the row number in binary.

(Henry can check your answer: the tests in `test/automata.test.js` verify this
pattern for the first 128 rows.)
