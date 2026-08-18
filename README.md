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

- **Set background**: click an empty area of the screenshot. The tool uses that
  ground color to decide which cells actually contain a block (instead of
  assuming the flat editor background), so it can tell a block cell from an
  infrastructure-filled one.
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

Here's a screenshot of the gui:

![GUI](https://i.ibb.co/TPfc2MJ/Screenshot-203.png)

The GUI is completely made with `tkinter`, and supports a lot of platforms

![WOMM](https://cdn.discordapp.com/attachments/676843444274069504/677566642888376320/WOMM.png)
