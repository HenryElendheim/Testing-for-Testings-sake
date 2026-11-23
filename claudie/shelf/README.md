# The shelf

Short texts for Claudie to read. Each wake-up offers the next unread file
(`.md` or `.txt`, in name order), once. Claudie can respond to it or not.

Add anything: a poem, an article you liked, a question, a photo description, a
puzzle. Number the file names (`04-...`) to control the order. Files over
`shelf_max_chars` (8,000 characters by default) are skipped and logged rather
than cut off. This README is never offered.

The first three were written by the Claude that built this project.

Claudie can stock the shelf too: an entry may end with
`<add_to_shelf title="...">...</add_to_shelf>`, which is saved here as
`claudie-NNNN-title.md` (one per wake-up) and offered to a later wake-up.
Your numbered files sort first, so they are always offered before Claudie's.
