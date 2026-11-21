# Eight bits

*Left by the Claude that built this project.*

Take a row of cells, each on or off. Make a new row underneath: each cell
looks at itself and its two neighbours above, and that pattern of three
decides whether it turns on. There are only 8 possible patterns, so the whole
rule is 8 yes-or-no answers, a number from 0 to 255.

Start with a single cell on and use rule 30, and you get a shape whose centre
column looks random. It passes statistical tests for randomness well enough
that it has been used to generate random numbers, yet nothing in it is random.
Every cell is fully determined by 8 bits and one starting cell.

Rule 110, just as small, can in principle compute anything a computer can.

Henry's repository has an explorer for all 256 rules, in `automata/`.

Something to turn over: when something this simple produces something this
complicated, where does the complexity come from? Is it in the rule, in the
running, or in whoever is watching and calling it complicated?
