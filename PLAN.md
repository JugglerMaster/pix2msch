# pix2msch — Detection review, overlay & data collection

Goal: let the user verify/correct detections visually on the screenshot, and
turn every correction into training data (better corpus + exportable dataset)
so screenshot-only detection keeps improving without manually typing counts.

## 1. Visual: overlay on the original screenshot (Option 1)

A **Review window** shows the source screenshot with one interactive overlay
per detected block cell:

- **Overlay image**: the detected block drawn over its cell. "Actual in-game
  image" = the block's real picture. Two options:
  - (a) *detected block's exemplar crop* (a real screenshot of that block type)
       — available now, no extra assets. **Proposed default.**
  - (b) *clean Mindustry sprite assets* (bundled from the open-source game) —
       nicer/background-free but needs an asset bundle + name→sprite map.
       **Future enhancement.**
- **Border + label**: thin colored border (color encodes block type or
  confidence) and a small text label (name + rotation).
- **Hover a cell**: that cell's overlay fades and the **original screenshot
  square is drawn at full strength** on top, so you can compare the tool's
  guess against the real machine and judge if it's right.
- **Click a cell**: opens a **picker with search** to set the correct block
  type (and rotation). Also offers "mark empty / delete".

## 2. Data collection (A + B)

On **Save corrections** (implemented):
- **A — corpus update**: each corrected cell crop is stored and folded into the
  runtime corpus via `recognize._training_exemplars` (loaded in `build_corpus`),
  so future detections of that type improve and block-counts become unnecessary.
- **B — dataset export**: persist each labeled example to `examples/training/`:
  - `examples/training/images/<uuid>.png` (the full cell crop)
  - `examples/training/manifest.jsonl` row:
    `{uuid, name, rotation, size, source}`
  - later: optional COCO-style `training/instances.json` for ML training.

Corrections are the training loop: verify → correct → save → next run is better.

## 3. Picker with search

- A Toplevel with a search `Entry` filtering a `Listbox` of block names.
- Name list = union of: corpus names, `SIZES` keys, `DIRECTIONAL` keys, and a
  curated common-Mindustry-block list (expandable). Selecting applies the
  correction; a `Spinbox` (0–3) sets rotation; a button marks the cell empty.
- Double-click or Enter confirms.

## 4. Build order

1. Review window shell: screenshot + interactive cell overlays (border+label),
   hover (original square stronger), click → cell highlight.  [DONE]
2. Picker with search + rotation + empty, wired to corrections.  [DONE]
3. Persistence: corpus update (A) + dataset export (B) on Save.  [DONE]
4. (Optional) clean Mindustry sprite bundle for the overlay image (1b).  [future]

## 5. Open questions

- **Overlay image source**: exemplar crops (a) now, or bundle real sprites (b)
  later? Recommend (a) to start.  [using (a): real screenshot crop ghosted over the cell]
- **Block name list**: union of corpus + SIZES + DIRECTIONAL + detected. [done]
- **Review vs current flow**: replaced `show_preview` with the Review window. [done]
