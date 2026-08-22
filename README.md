# pix2msch
A program that converts images into Mindustry schematics

The usage should be pretty self explainatory once you open gui.py

If you're on Windows, you can go to releases, download and run pix2msch.exe. No Python installation needed.

Note: You will need `pyperclip` and `pillow` to run gui.py. You can install them by doing `pip install <package>`

Note: Mindustry won't load schematics bigger than 128x128, so images are automatically scaled down to fit that limit while keeping their aspect ratio. Smaller images are left untouched, but keep in mind that even a 100x100 image becomes a large structure in-game.

## Structure detection

Open an image and the tool tries to recognize the Mindustry blocks in it. There
are two ways it works:

- **With a reference `.msch`** (recommended): if a `.msch` file with the same
  name sits next to the image (e.g. `6xsil.png` + `6xsil.msch`), the tool reads
  the exact block layout from it and reproduces the schematic byte-for-byte.
  This is 100% reliable for any base, including running ones.
- **Box-only (heuristic)**: draw a rectangle around the block grid, enter the
  number of Columns/Rows, and press **Detect**. The tool snaps the grid to the
  blocks and lists what it found in the side panel, then shows a preview you can
  save.

### Box-only without a reference `.msch`

You do **not** need a `.msch` for box-only detection, even for running/live
bases. Two extra inputs make it work:

- **Set background**: click **Set background**, then drag a **yellow box**
  over an empty area of the screenshot (or just click one spot). The tool
  averages that region's color and uses it as the ground, so it can tell a block
  cell from an infrastructure-filled one.
- **Block counts**: enter the block types and how many of each, e.g.
  `silicon-smelter:6, unloader:5, sorter:5, bridge-conveyor:4`. Running-state
  multi-tile blocks (which look nothing like their idle exemplars) are mined
  from the screenshot itself using these counts, so they are recognized too.

Tune **Threshold** and watch the live panel if you like, but when **Block
counts** are supplied the tool auto-picks the occupancy threshold that makes the
detected counts match yours — so running/live screenshots are recognized
automatically with no manual threshold tuning. A reference `.msch` is still the
most exact option if you have one.

### Review window & training data

After detection you get a **Review window** overlaying the screenshot: each
detected block is outlined with its guessed name, the original square appears at
full strength when you hover it, and clicking a block opens a searchable picker
to correct the type or rotation (or mark it empty). **Save** writes the `.msch`
and also exports every corrected cell as a labeled training exemplar to
`examples/training/` — these are folded back into the detection corpus on the
next run, so repeated corrections keep improving accuracy.

### Automated training data (no game needed)

Hand-correcting screenshots is slow; getting ~1k labeled cells by hand is a
tall order. `gen_synth_sheets.py` produces training material fully offline:

    python gen_synth_sheets.py [--sheets 60] [--seed 0] [--out examples]

It renders random schematics from the real game sprites (auto-discovered for
every block in `sprite_train.py`), composites them onto editor-gray / striped /
floored backgrounds with drop shadows and light capture-style augmentation,
palette-quantizes the result like a real screenshot, and writes a matching
`.msch` next to each `.png`. Because pairs land in `examples/`, `build_corpus`
consumes them unchanged — grid fitting, cell-crop exemplars, occupancy data
and the classifier all train on them exactly like real screenshots.

The sprite list is no longer hand-maintained: `sprite_train` fetches the game's
raw-asset tree once (cached) and resolves any catalog block by name, so every
placeable block gets coverage. Run `gen_synth_sheets.py --start N --sheets M`
to append more batches without clobbering earlier ones.

Here's a screenshot of the gui:

![GUI](https://i.ibb.co/TPfc2MJ/Screenshot-203.png)

The GUI is completely made with `tkinter`, and supports a lot of platforms

![WOMM](https://cdn.discordapp.com/attachments/676843444274069504/677566642888376320/WOMM.png)
