# Wood WOP Labeling

Adds a size label to woodWOP part files (`.mpr`) so the size travels with each
part into woodNest and onto the sheet. **No machining changes.** Only the label
is added, and the app checks that before it saves anything.

![Wood WOP Labeling window](docs/app.png)

## What the label looks like

For `WHE_TABBY_1071128_ROSENBAUM_SHARI_L_CEP_U12_X1.mpr`:

```
SIZE 95-1/4 x 12 x 3/4 IN | JOB 1071128 | PART CEP U12 X1
```

- **Size**: length x width x thickness in inches, rounded to the nearest 1/16".
  Read from the part's rough exterior size in the file.
- **Job**: the first 5+ digit number in the file name.
- **Part**: from the part code before the unit number (`CEP U12`) to the end of
  the file name. If there's no unit number, it's everything after the job number.

The label goes in two places woodWOP never machines:

1. **A `LABEL` entry at the end of the part's variable list**, with the label
   as its comment. woodNest copies each part's variables and comments onto
   the sheet, so the label goes with the part.
2. **A woodWOP comment** right after the workpiece entry, shown in the
   operations list.

In the file it's 5 added lines:

```
bfb="57"
KM="bore from back"
LABEL="0"                                                        <- added
KM="SIZE 95-1/4 x 12 x 3/4 IN | JOB 1071128 | PART CEP U12 X1"   <- added
...
<100 \WerkStck\
...
AY="0"

<101 \Kommentar\                                                 <- added
KM="SIZE 95-1/4 x 12 x 3/4 IN | JOB 1071128 | PART CEP U12 X1"   <- added
                                                                 <- added
<102 \BohrVert\
```

## Using it

1. Download `WoodWopLabeling.exe` from the **latest** release on this repo
   (Releases, on the right side of the repo page). It's one file with no install.
   Copy it to any Windows computer.
   - Windows may warn about an unrecognized app the first time. Click
     **More info**, then **Run anyway**.
2. Open it and click **Choose Folder...** to pick the job folder, or
   **Choose Files...** to pick part files. You can also drop a folder onto the
   exe's icon.
3. Check the list: size, job, part, and status for each file. Click a row to see
   its exact label text.
4. Click **Add Labels**. Each file is saved back **in its own folder under the
   same name**.

**Safety**

- Before a file is replaced, the original is copied to
  `Documents\WoodWOP Label Backups\<date and time>\`.
- Sheet (board) files are detected and skipped.
- A file that already has the right label is left alone. Running it twice is safe.
- If the size in the file header doesn't match the part's own size settings, the
  file is skipped and marked as a problem. A stale header could otherwise give a
  wrong label.
- **Remove Labels** takes the label back out. The file returns to exactly its
  original bytes.
- Every save is checked: the new file minus the label must match the original
  byte for byte, or nothing is written.

## For developers

- `woodwop_label.py`: labeling logic, plus a command line
  (`python woodwop_label.py --dry-run <folder>`).
- `app.py`: the window app.
- `tests/`: run `python -m pytest`. Tests use the real files in `samples/`.
- `.github/workflows/build.yml`: builds the exe on Windows, runs the tests and
  an end-to-end self-test of the exe, then publishes it to the `latest` release.
