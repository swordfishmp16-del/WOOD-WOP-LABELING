# Wood WOP Labeling

Draws each piece's size (depth x width, in inches) and its unit number on
woodWOP part files (`.mpr`), so they show on the part and travel with it into
woodNest and onto the sheet. Special shapes (L shelves, angled shelves) get
every side numbered. **No machining changes**, and a label is **never routed**.

![Wood WOP Labeling window](docs/app.png)

## What gets labeled

The app reads the outlines the router cuts, so it labels the **finished
pieces**, not just the board:

| Part | Label |
|---|---|
| Regular rectangle (end panel, shelf) | `12 x 95 1/4` with `U12` under it, centered |
| Double shelf (two shelves from one board) | each shelf gets its own size, e.g. `13 x 35` on each |
| L shelf, angled shelf, any other shape | every straight side numbered with its length, just inside that side, plus the overall size and unit in the middle |
| Curved side | no number (not needed); labels stay clear of the curve |
| Cutout or edge notch in a piece | not a piece; labels stay clear of it |
| Closed pocket or shallow routed outline | not a piece; labels stay clear of it |

- **Sizes** are depth x width: the piece's front-to-back size (Y in woodWOP)
  first, then side to side (X). Rounded to the nearest 1/16".
- **Unit** comes from the file name: the last `U12`, `UNIT12` or `UNIT_12`
  after the job number, shown as `U12`. If there's none, only the size is shown.
- **Text size** shrinks with the piece, from 50 mm (about 2") on big pieces down
  to 3 mm. If there is no room to draw something, the app says so; the sizes are
  still written to the variable list.
- If the routing can't be followed for sure (a router cutting along an open
  line or only part of an outline, a depth or outline that can't be worked out,
  several curved pieces), the app labels the **board size** instead of guessing.
  The status in the app says why, and the hidden label is marked
  `(BOARD - PIECES UNCLEAR)`. A single piece with curves gets the board's
  overall size.
- A part where an operation points at a contour that isn't in the file is
  reported as a problem and left alone.

The label is written in two places, neither of which the machine runs:

1. **Drawn on the pieces as plain lines.** Each character is a woodWOP
   **contour**, which is just geometry. The machine only cuts a contour when a
   routing operation points at it, and **nothing ever points at these**. The
   label contours are numbered above every contour any operation points at.
   Each character is one open line that never closes, so woodNest can't mistake
   a letter for a cutout.
2. **A `LABEL` entry at the end of the part's variable list**, with the label as
   its comment, for example `KM="SIZE 12 x 95 1/4 | U12"` or
   `KM="SIZE 31 7/8 x 25 7/8 (SIDES 31 7/8, 16, 15 7/8, 9 7/8, 16, 25 7/8) | U7"`.
   Its value is the number of drawn characters. That's how the app finds the
   drawing again to update or remove it, and your checking AI can read every
   size here.

In the file (end panel):

```
bfb="57"
KM="bore from back"
LABEL="11"                   <- added (11 drawn characters)
KM="SIZE 12 x 95 1/4 | U12"  <- added

]1                           <- the part's own outline (unchanged)
...
]2                           <- added: drawn "1"
$E0
KP
X=...
...
]12                          <- added: drawn "2" of U12
...
<100 \WerkStck\              <- everything from here down is unchanged
...
<105 \Konturfraesen\
EA="1:0"                     <- routing still cuts only contour 1, the outline
```

## Router safety: a label is never routed

A routed label ruins the part. Before any file is saved, the app checks:

- No operation is added, and every existing operation is byte-for-byte
  unchanged and still cuts exactly the same contour as before.
- Nothing points at a label contour.

If any check fails, the file is not written. **Remove Labels** also refuses to
touch a file where something points at its label.

**Sheet files are checked too.** Any woodNest sheet file in the folder has every
operation checked. If any operation is set to cut a label (or a contour that's
missing), the sheet shows in red as **DANGER - DO NOT RUN**. Otherwise it shows
`sheet OK`, with how many labeled parts and label lines it found. Sheet files
are never changed.

## Using it

1. Download `WoodWopLabeling.exe` from the **latest** release on this repo
   (Releases, on the right side of the repo page). It's one file with no install.
   Copy it to any Windows computer.
   - Windows may warn about an unrecognized app the first time. Click
     **More info**, then **Run anyway**.
2. Open it and click **Choose Folder...** to pick the folder with the part
   files, or **Choose Files...** to pick part files. You can also drop a folder
   onto the exe's icon.
3. The list shows each file's label and status. Click a row to see the label
   drawn on the part.
4. Click **Add Labels**. Each file is saved back **in its own folder under the
   same name**.

**Auto-label (drop files in, they come out labeled):** after choosing a folder,
tick **Auto-label new files in this folder**. While the window stays open:

- Every part file in that folder that isn't labeled yet gets labeled and saved.
  That covers files already there and any new ones copied in.
- A file is only touched once it has stopped changing for 3 seconds and is
  complete, so files are never labeled mid-save.
- Sheet files are only checked.

**Safety**

- Before a file is replaced, the original is copied to
  `Documents\WoodWOP Label Backups\<date and time>\`.
- A file that already has the right label is left alone. Running it twice is safe.
- If a part's size, shape or file name (unit) changes, its label is updated.
- If the size in the file header doesn't match the part's own size settings,
  the file is skipped and marked as a problem. A stale header could otherwise
  give a wrong label.
- **Remove Labels** takes the label back out. The file returns to exactly its
  original bytes.
- Files labeled by any earlier version are upgraded to the current label
  automatically.

## For developers

- `woodwop_label.py`: labeling logic, plus a command line
  (`python woodwop_label.py --dry-run <folder>`).
- `stroke_font.py`: the single-line font and rectangle label layout.
- `shapes.py`: works out the pieces from the routed outlines and lays out side labels.
- `app.py`: the window app.
- `tests/`: run `python -m pytest`. Tests use the real files in `samples/`, and
  files from the earlier test versions in `tests/fixtures/`.
- `.github/workflows/build.yml`: builds the exe on Windows, runs the tests and
  an end-to-end self-test of the exe (button and auto-label), then publishes it
  to the `latest` release.
