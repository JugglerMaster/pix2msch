try:
    import sys
    from sys import platform
    from tkinter import *
    from tkinter import filedialog
    from tkinter import messagebox
    from PIL import Image, ImageTk
    import tkinter.font, os, core, recognize, threading
except Exception as e:
    print("You're missing a package!")
    print()
    print(e)
    input()


def resource_path(relative_path):
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        #running as a plain Python script: look next to gui.py
        base_path = os.path.dirname(os.path.abspath(__file__))

    return os.path.join(base_path, relative_path)


class GUI():
    def __init__(self, root):
        self.root = root
        root.title("pix2msch")
        root.resizable(False, False)
        root.geometry("600x500")

        photo = PhotoImage(file = resource_path("background.png"))
        background = Label(root, image = photo)
        background.image = photo
        background.place(x = -2, y = -2)

        font = tkinter.font.Font(family="Consolas", size=12)

        self.dither = IntVar(value=1)
        self.dither_c = Checkbutton(
        root, font = font, text="Dithering",
        bg = "#35373C", fg = "#B7BBCE",
        activebackground="#515359", activeforeground="#cccccc", bd = 0,
        selectcolor="#515359",
        var = self.dither
        )
        self.dither_c.place(x = 300, y = 375)

        self.file = None
        self.name = StringVar()
        self.path = StringVar()
        self.transparency = StringVar()

        name_entry = Entry(root, font = font, width = 25, bg = "#35373C", fg = "#B7BBCE", textvariable = self.name)
        name_entry.place(x = 300, y = 278)

        path_entry = Entry(root, font = font, width = 25, bg = "#35373C", fg = "#B7BBCE", textvariable = self.path)
        path_entry.place(x = 300, y = 308)

        transparency_entry = Entry(root, font = font, width = 25, bg = "#35373C", fg = "#B7BBCE", textvariable = self.transparency)
        transparency_entry.place(x = 300, y = 338)

        name_entry.insert(0, "schematic")
        if platform == "win32":
            path_entry.insert(0, "%appdata%\\Mindustry\\schematics")
        elif platform == "linux" or platform == "linux2":
            path_entry.insert(0, "~/.local/share/Mindustry/schematics/")
        else:
            path_entry.insert(0, "Enter Mindustry schematic path...")

        transparency_entry.insert(0, "127")

        self.open_image_b = Button(root, font = font, command=self.open_image, text = "Open Image...", bg = "#35373C", fg = "#B7BBCE", activebackground="#515359", activeforeground="#cccccc", bd = 0)
        self.open_image_b.place(x = 300, y = 240, anchor = CENTER)

        convert_b = Button(root, command = lambda : self.convert("path"), font = font, text = "Convert to msch...", bg = "#35373C", fg = "#B7BBCE", activebackground="#515359", activeforeground="#cccccc", bd = 0)
        convert_b.place(x = 300, y = 450, anchor = CENTER)

        preview_b = Button(root, command=self.preview, font = font, text = "Preview", bg = "#35373C", fg = "#B7BBCE", activebackground="#515359", activeforeground="#cccccc", bd = 0)
        preview_b.place(x = 150, y = 450, anchor = CENTER)

        clipboard_b = Button(root, command= lambda : self.convert("clipboard"), font = font, text = "Copy", bg = "#35373C", fg = "#B7BBCE", activebackground="#515359", activeforeground="#cccccc", bd = 0)
        clipboard_b.place(x = 435, y = 450, anchor = CENTER)

        # shared state for the structure workflow
        self.struct = None

    def open_image(self):
        root.update()
        self.file = filedialog.askopenfilename()
        self.open_image_b.configure(text = self.file)
        try:
            Image.open(self.file)
        except:
            self.open_image_b.configure(text = "Invalid Image file!")
            self.file = None
        root.update()

    # ---- async detection (keeps the UI responsive, offers Cancel) ----
    def _detect_async(self, file, box, W, H, bg, thresh, counts, on_done, parent=None):
        """Run detection in a worker thread so the window never locks up.

        Shows a Cancel dialog; on_done is called on the main thread with either
        the (w, h, blocks, grid, thr) result, ("err", exception), or ("cancel",).
        """
        cancel = [False]
        prog = Toplevel(parent or root)
        prog.title("Detecting...")
        prog.resizable(False, False)
        Label(prog, text="Detecting blocks...\nPress Cancel to stop.", justify=LEFT).pack(padx=12, pady=10)
        Button(prog, text="Cancel", command=lambda: _cancel()) .pack(pady=5)

        def _cancel():
            cancel[0] = True
            if prog.winfo_exists():
                prog.destroy()

        def work():
            try:
                res = core.detect_structure(file, box, W, H, bg=bg, thresh=thresh, block_counts=counts)
            except Exception as e:
                res = ("err", e)
            (parent or root).after(0, lambda: _done(res))

        threading.Thread(target=work, daemon=True).start()

        def _done(res):
            if prog.winfo_exists():
                prog.destroy()
            if cancel[0]:
                on_done(("cancel",))
                return
            on_done(res)

    def convert(self, mode):
        if not self.file:
            messagebox.showerror("oh no", "Open an image first")
            return
        res = self.select_grid()
        if res is None:
            return
        box, W, H, bg, counts = res
        ld = getattr(self, "_last_detect", None)
        if ld and ld[0] == self.file and ld[1] == box and ld[2] == W and ld[3] == H \
                and ld[4] == bg and ld[5] == counts:
            w, h, blocks, grid, thr = ld[6]
            self.struct = (w, h, blocks, grid, box, W, H, mode)
            self.review_window()
            return
        self._detect_async(self.file, box, W, H, bg, 0.2, counts,
                           lambda r: self._after_convert(r, mode, box, W, H))

    def _after_convert(self, r, mode, box, W, H):
        if isinstance(r, tuple) and r and r[0] in ("err", "cancel"):
            if r[0] == "err":
                messagebox.showerror("oh no", r[1])
            return
        w, h, blocks, grid, thr = r
        if not blocks:
            messagebox.showerror("oh no", "Detected 0 blocks. Adjust the grid box/dimensions and try again.")
            return
        self.struct = (w, h, blocks, grid, box, W, H, mode)
        self.review_window()

    # ---- grid selection window ----
    def select_grid(self):
        from PIL import Image as PILImage
        img = PILImage.open(self.file).convert("RGB")
        iw, ih = img.size
        maxdim = 820
        scale = maxdim / max(iw, ih)
        dw, dh = int(iw * scale), int(ih * scale)
        disp = img.resize((dw, dh))

        win = Toplevel(root)
        win.title("Place the grid over the schematic")
        win.resizable(False, False)

        body = Frame(win)
        body.pack()

        canvas = Canvas(body, width=dw, height=dh, cursor="cross")
        canvas.pack(side=LEFT)
        photo = ImageTk.PhotoImage(disp)
        canvas.create_image(0, 0, anchor=NW, image=photo)
        canvas.image = photo

        panel = Frame(body)
        panel.pack(side=LEFT, fill=Y, padx=5)
        Label(panel, text="Detected blocks").pack()
        pscroll = Scrollbar(panel, orient=VERTICAL)
        plist = Listbox(panel, yscrollcommand=pscroll.set, width=34, height=34)
        pscroll.config(command=plist.yview)
        plist.pack(side=LEFT, fill=Y)
        pscroll.pack(side=RIGHT, fill=Y)

        status = Label(win, text="Drag a box around the block grid, then set Columns/Rows and press Detect.")
        status.pack()

        frm = Frame(win)
        frm.pack()
        Label(frm, text="Columns:").grid(row=0, column=0)
        cols = Spinbox(frm, from_=1, to=64, width=5)
        cols.grid(row=0, column=1)
        Label(frm, text="Rows:").grid(row=0, column=2)
        rows = Spinbox(frm, from_=1, to=64, width=5)
        rows.grid(row=0, column=3)
        Label(frm, text="Threshold:").grid(row=1, column=0)
        thresh_var = Spinbox(frm, from_=0.0, to=1.0, increment=0.05, width=5)
        thresh_var.delete(0, "end"); thresh_var.insert(0, "0.2")
        thresh_var.grid(row=1, column=1)
        Label(frm, text="Block counts:").grid(row=2, column=0)
        counts_var = Entry(frm, width=42)
        counts_var.insert(0, "silicon-smelter:6, unloader:5, sorter:5, bridge-conveyor:4, titanium-conveyor:1, item-source:3, item-void:1")
        counts_var.grid(row=2, column=1, columnspan=3)

        result = {"box": None, "ok": False, "bg": None, "counts": None}

        rect = {"id": None, "sx": 0, "sy": 0}
        bg_pick = {"active": False, "drawing": False, "id": None}
        sel = {"idx": None}

        def to_actual(x, y):
            return int(x / scale), int(y / scale)

        def block_rect(b):
            tw, th, ox, oy = result["grid"]
            x, y, size = b[1], b[2], b[5]
            cy_top = result["H"] - 1 - (y + size - 1)
            ax = ox + x * tw
            ay = oy + cy_top * th
            return ax * scale, ay * scale, tw * size * scale, th * size * scale

        def redraw_overlay():
            canvas.delete("occ")
            blocks = result.get("blocks")
            if not blocks or not result.get("grid"):
                return
            for i, b in enumerate(blocks):
                dx, dy, dww, dhh = block_rect(b)
                if i == sel["idx"]:
                    canvas.create_rectangle(int(dx), int(dy), int(dx + dww), int(dy + dhh),
                                           outline="#ffd400", width=3, tags="occ")
                else:
                    canvas.create_rectangle(int(dx), int(dy), int(dx + dww), int(dy + dhh),
                                           outline="#39d353", width=1, tags="occ")

        def select_block(i):
            select_block._busy = True
            sel["idx"] = i
            plist.selection_clear(0, END)
            if i is not None:
                plist.selection_set(i)
                plist.see(i)
            redraw_overlay()
            if i is not None:
                status.configure(text="Selected: %s (%d,%d) r%d" % (blocks[i][0], blocks[i][1], blocks[i][2], blocks[i][3]))
            select_block._busy = False

        def on_canvas_click(e):
            blocks = result.get("blocks")
            if not blocks or not result.get("grid"):
                select_block(None)
                return
            fx, fy = e.x, e.y
            for i, b in enumerate(blocks):
                dx, dy, dww, dhh = block_rect(b)
                if dx <= fx < dx + dww and dy <= fy < dy + dhh:
                    select_block(i)
                    return
            select_block(None)

        def on_list_select(ev):
            if getattr(select_block, "_busy", False):
                return
            cur = plist.curselection()
            if cur:
                select_block(cur[0])

        def on_down(e):
            if bg_pick["active"]:
                bg_pick["drawing"] = True
                bg_pick["sx"], bg_pick["sy"] = e.x, e.y
                if bg_pick["id"]:
                    canvas.delete(bg_pick["id"])
                bg_pick["id"] = canvas.create_rectangle(e.x, e.y, e.x, e.y, outline="yellow", width=2)
                return
            rect["sx"], rect["sy"] = e.x, e.y
            if rect["id"]:
                canvas.delete(rect["id"])
            rect["id"] = canvas.create_rectangle(e.x, e.y, e.x, e.y, outline="red", width=2)

        def on_drag(e):
            if bg_pick.get("drawing"):
                if bg_pick["id"]:
                    canvas.delete(bg_pick["id"])
                bg_pick["id"] = canvas.create_rectangle(bg_pick["sx"], bg_pick["sy"], e.x, e.y, outline="yellow", width=2)
                return
            if rect["id"]:
                canvas.delete(rect["id"])
            rect["id"] = canvas.create_rectangle(rect["sx"], rect["sy"], e.x, e.y, outline="red", width=2)

        def on_up(e):
            if bg_pick.get("drawing"):
                bg_pick["drawing"] = False
                x0, y0, x1, y1 = bg_pick["sx"], bg_pick["sy"], e.x, e.y
                if x1 < x0:
                    x0, x1 = x1, x0
                if y1 < y0:
                    y0, y1 = y1, y0
                # A bare click samples a small region around the point.
                if x1 - x0 < 4 and y1 - y0 < 4:
                    m = 10
                    x0 = max(0, x0 - m); y0 = max(0, y0 - m)
                    x1 = min(dw, x1 + m); y1 = min(dh, y1 + m)
                ax0, ay0 = to_actual(x0, y0)
                ax1, ay1 = to_actual(x1, y1)
                pix = PILImage.open(self.file).convert("RGB").load()
                rs = gs = bs = 0; n = 0
                for y in range(ay0, ay1 + 1):
                    for x in range(ax0, ax1 + 1):
                        p = pix[x, y]; rs += p[0]; gs += p[1]; bs += p[2]; n += 1
                if n:
                    result["bg"] = (rs // n, gs // n, bs // n)
                bg_pick["active"] = False
                if bg_pick["id"]:
                    canvas.delete(bg_pick["id"]); bg_pick["id"] = None
                win.config(cursor="")
                status.configure(text="Background set to %s. Draw the grid box and Detect." % (result["bg"],))
                return
            if rect["id"]:
                canvas.delete(rect["id"])
            x0, y0, x1, y1 = rect["sx"], rect["sy"], e.x, e.y
            # A click (negligible drag) selects a block instead of drawing a box.
            if max(abs(x1 - x0), abs(y1 - y0)) < 4:
                on_canvas_click(e)
                return
            if x1 < x0:
                x0, x1 = x1, x0
            if y1 < y0:
                y0, y1 = y1, y0
            rect["id"] = canvas.create_rectangle(x0, y0, x1, y1, outline="red", width=2)
            result["box"] = (x0, y0, x1, y1)

        canvas.bind("<ButtonPress-1>", on_down)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_up)
        plist.bind("<<ListboxSelect>>", on_list_select)

        def do_detect():
            if not result["box"]:
                status.configure(text="Draw a box first.")
                return
            x0, y0, x1, y1 = result["box"]
            ax0, ay0 = to_actual(x0, y0)
            ax1, ay1 = to_actual(x1, y1)
            try:
                W = int(cols.get()); H = int(rows.get())
                T = float(thresh_var.get())
            except:
                status.configure(text="Columns/Rows/Threshold must be numbers.")
                return
            counts = None
            cs = counts_var.get().strip()
            if cs:
                counts = {}
                for part in cs.split(","):
                    if ":" in part:
                        nm, cv = part.split(":", 1)
                        nm = nm.strip(); cv = cv.strip()
                        if nm:
                            try:
                                counts[nm] = int(cv)
                            except ValueError:
                                pass
            result["counts"] = counts
            if ax1 <= ax0 or ay1 <= ay0 or W < 1 or H < 1:
                status.configure(text="Invalid box or dimensions.")
                return
            status.configure(text="Detecting...")
            self._detect_async(self.file, (ax0, ay0, ax1, ay1), W, H, result["bg"], T, counts,
                               lambda r: finish_detect(r, (ax0, ay0, ax1, ay1), W, H), parent=win)

        def finish_detect(r, box_actual, W, H):
            if isinstance(r, tuple) and r and r[0] in ("err", "cancel"):
                if r[0] == "err":
                    status.configure(text="Detection error: " + str(r[1]))
                else:
                    status.configure(text="Detection cancelled.")
                return
            w, h, blocks, grid, thr = r
            result["blocks"] = blocks
            result["grid"] = grid
            result["W"] = W
            result["H"] = H
            sel["idx"] = None
            self._last_detect = (self.file, box_actual, W, H, result["bg"], counts, (w, h, blocks, grid, thr))
            plist.delete(0, END)
            for (nm, x, y, rot, cfg, size) in blocks:
                plist.insert(END, "%-22s (%d,%d) r%d" % (nm, x, y, rot))
            redraw_overlay()
            status.configure(text="Detected %d blocks (occupancy thr=%.2f). Click a block to highlight it; OK when done." % (len(blocks), thr))

        def do_ok():
            if not result["box"]:
                status.configure(text="Draw a box first.")
                return
            try:
                W = int(cols.get()); H = int(rows.get())
            except:
                status.configure(text="Columns/Rows must be numbers.")
                return
            ax0, ay0 = to_actual(*result["box"][:2])
            ax1, ay1 = to_actual(*result["box"][2:])
            result["box"] = (ax0, ay0, ax1, ay1)
            result["W"] = W
            result["H"] = H
            result["ok"] = True
            win.destroy()

        def do_cancel():
            win.destroy()

        Button(win, text="Detect", command=do_detect).pack(side=LEFT, padx=5, pady=5)
        def enter_bg():
            bg_pick["active"] = True
            win.config(cursor="tcross")
            status.configure(text="Set background: drag a YELLOW box over an empty area (or just click one).")

        Button(win, text="Set background", command=enter_bg).pack(side=LEFT, padx=5, pady=5)
        Button(win, text="OK", command=do_ok).pack(side=LEFT, padx=5, pady=5)
        Button(win, text="Cancel", command=do_cancel).pack(side=LEFT, padx=5, pady=5)

        note = Label(win,
                     text="No .msch needed: click 'Set background' on an empty area, enter the "
                          "block counts (e.g. silicon-smelter:6, unloader:5), tune Threshold, then "
                          "Detect. The right-hand panel shows results live.",
                     fg="#bbbbbb", wraplength=820, justify=LEFT)
        note.pack(side=BOTTOM, padx=8, pady=(0, 6))

        win.wait_window(win)
        if not result["ok"]:
            return None
        return (result["box"], result["W"], result["H"], result["bg"], result["counts"])

    # ---- review window: overlay + hover inspection + click-to-correct ----
    def review_window(self):
        w, h, blocks, grid, box, W, H, mode = self.struct
        if not blocks:
            messagebox.showerror("oh no", "No blocks to review. Go back and adjust the grid.")
            return
        tw, th, ox, oy = grid
        img = Image.open(self.file).convert("RGB")
        iw, ih = img.size
        maxdim = 820
        scale = maxdim / max(iw, ih)
        dw, dh = int(iw * scale), int(ih * scale)
        disp = img.resize((dw, dh))

        rblocks = [[n, x, y, rot, cfg, sz] for (n, x, y, rot, cfg, sz) in blocks]
        names = sorted(set(list(recognize.SIZES.keys()) + list(recognize.DIRECTIONAL.keys()) + [b[0] for b in rblocks]))

        win = Toplevel(root)
        win.title("Review detections - click a block to correct, hover to inspect")
        win.resizable(False, False)
        canvas = Canvas(win, width=dw, height=dh, cursor="hand2")
        canvas.pack()
        photo = ImageTk.PhotoImage(disp)
        canvas.create_image(0, 0, anchor=NW, image=photo)
        canvas.image = photo

        ov_imgs = []

        def cell_rect(b):
            n, x, y, rot, cfg, sz = b
            cy_top = h - 1 - (y + sz - 1)
            ix0 = ox + x * tw
            iy0 = oy + cy_top * th
            return ix0, iy0, tw * sz, th * sz

        def draw_all():
            canvas.delete("ov")
            del ov_imgs[:]
            for b in rblocks:
                ix0, iy0, ww, hh = cell_rect(b)
                dx, dy = ix0 * scale, iy0 * scale
                dww, dhh = ww * scale, hh * scale
                region = disp.crop((int(dx), int(dy), int(dx + dww), int(dy + dhh)))
                crop = img.crop((int(ix0), int(iy0), int(ix0 + ww), int(iy0 + hh))).resize((int(dww), int(dhh)))
                ghost = Image.blend(crop, region, 0.5)
                gimg = ImageTk.PhotoImage(ghost)
                ov_imgs.append(gimg)
                canvas.create_image(int(dx), int(dy), anchor=NW, image=gimg, tags="ov")
                canvas.create_rectangle(int(dx), int(dy), int(dx + dww), int(dy + dhh),
                                       outline="#39d353", width=2, tags="ov")
                canvas.create_text(int(dx) + 3, int(dy) + 3, text=b[0], fill="#ffe66d",
                                  font=("Consolas", 10), anchor=NW, tags="ov")

        def block_at(ev):
            ix, iy = int(ev.x / scale), int(ev.y / scale)
            for b in rblocks:
                ix0, iy0, ww, hh = cell_rect(b)
                if ix0 <= ix < ix0 + ww and iy0 <= iy < iy0 + hh:
                    return b
            return None

        hover_img = [None]
        def on_motion(ev):
            if hover_img[0] is not None:
                canvas.delete(hover_img[0]); hover_img[0] = None
            b = block_at(ev)
            if b is None:
                return
            ix0, iy0, ww, hh = cell_rect(b)
            dx, dy = ix0 * scale, iy0 * scale
            dww, dhh = ww * scale, hh * scale
            crop = img.crop((int(ix0), int(iy0), int(ix0 + ww), int(iy0 + hh))).resize((int(dww), int(dhh)))
            himg = ImageTk.PhotoImage(crop)
            hover_img[0] = canvas.create_image(int(dx), int(dy), anchor=NW, image=himg)
            canvas._hover_ref = himg

        def on_click(ev):
            b = block_at(ev)
            if b is not None:
                open_picker(b)

        canvas.bind("<Motion>", on_motion)
        canvas.bind("<Button-1>", on_click)
        draw_all()

        def open_picker(b):
            pk = Toplevel(win)
            pk.title("Pick block type")
            pk.resizable(False, False)
            Label(pk, text="Search:").grid(row=0, column=0)
            q = StringVar(); e = Entry(pk, textvariable=q, width=30); e.grid(row=0, column=1); e.focus()
            lb = Listbox(pk, width=40, height=12); lb.grid(row=1, column=0, columnspan=2)
            rot = IntVar(value=b[3])
            Label(pk, text="Rotation:").grid(row=2, column=0)
            rs = Spinbox(pk, from_=0, to=3, width=4, textvariable=rot); rs.grid(row=2, column=1)

            def refresh(*_):
                lb.delete(0, END)
                qv = q.get().lower()
                for nm in names:
                    if qv in nm.lower():
                        lb.insert(END, nm)
            q.trace("w", refresh); refresh()
            if b[0] in names:
                try:
                    lb.selection_set(names.index(b[0]))
                except Exception:
                    pass

            def apply():
                sel = lb.curselection()
                if sel:
                    b[0] = lb.get(sel[0])
                b[3] = int(rot.get())
                pk.destroy(); draw_all()
            def mark_empty():
                if b in rblocks:
                    rblocks.remove(b)
                pk.destroy(); draw_all()

            Button(pk, text="OK", command=apply).grid(row=3, column=0)
            Button(pk, text="Mark empty", command=mark_empty).grid(row=3, column=1)
            Button(pk, text="Cancel", command=pk.destroy).grid(row=3, column=2)

        def do_save():
            try:
                out_dir = os.path.expandvars(self.path.get())
                os.makedirs(out_dir, exist_ok=True)
                out = os.path.join(out_dir, self.name.get() + ".msch")
                tags = {"contentMap": "{0:{sand:4,coal:5}}", "labels": "[]",
                        "name": self.name.get(), "description": ""}
                wb = [(b[0], b[1], b[2], b[4], b[3]) for b in rblocks]
                core._write_schematic(w, h, tags, wb, out, "path")
                n = self._export_training(rblocks, img, cell_rect)
                messagebox.showinfo("Saved", "Wrote %s\n(%d cells exported for training)" % (out, n))
            except Exception as e:
                messagebox.showerror("oh no", e)

        def do_back():
            win.destroy()
            res = self.select_grid()
            if res is None:
                return
            box, W2, H2, bg, counts = res
            ld = getattr(self, "_last_detect", None)
            if ld and ld[0] == self.file and ld[1] == box and ld[2] == W2 and ld[3] == H2 \
                    and ld[4] == bg and ld[5] == counts:
                nw, nh, nblocks, ngrid, nthr = ld[6]
                self.struct = (nw, nh, nblocks, ngrid, box, W2, H2, mode)
                self.review_window()
                return
            self._detect_async(self.file, box, W2, H2, bg, 0.2, counts,
                               lambda r: self._after_back(r, box, W2, H2, mode))

    def _after_back(self, r, box, W2, H2, mode):
        if isinstance(r, tuple) and r and r[0] in ("err", "cancel"):
            if r[0] == "err":
                messagebox.showerror("oh no", r[1])
            return
        nw, nh, nblocks, ngrid, nthr = r
        self.struct = (nw, nh, nblocks, ngrid, box, W2, H2, mode)
        self.review_window()

        Button(win, text="Save", command=do_save).pack(side=LEFT, padx=5, pady=5)
        Button(win, text="Back", command=do_back).pack(side=LEFT, padx=5, pady=5)
        win.wait_window(win)

    def _export_training(self, blocks, img, cell_rect):
        """Persist each corrected cell crop as a labeled training exemplar.

        Written to examples/training/ (images + manifest.jsonl) so the corpus
        can be improved by folding them in on the next run.
        """
        import uuid, json
        base = os.path.dirname(os.path.abspath(__file__))
        tdir = os.path.join(base, "examples", "training")
        idir = os.path.join(tdir, "images")
        os.makedirs(idir, exist_ok=True)
        mpath = os.path.join(tdir, "manifest.jsonl")
        n = 0
        with open(mpath, "a") as mf:
            for b in blocks:
                ix0, iy0, ww, hh = cell_rect(b)
                crop = img.crop((int(ix0), int(iy0), int(ix0 + ww), int(iy0 + hh)))
                uid = uuid.uuid4().hex
                crop.save(os.path.join(idir, uid + ".png"))
                rec = {"uuid": uid, "name": b[0], "rotation": int(b[3]),
                       "size": int(b[5]), "source": os.path.basename(self.file)}
                mf.write(json.dumps(rec) + "\n")
                n += 1
        return n

    def preview(self):
        try:
            targetsize = 700
            qimg = core.quantize(self.file, self.dither.get(), 127)

            sizemultiplier = targetsize/max(qimg.size)
            self.window = Toplevel(root)
            self.window.geometry(str(int(qimg.size[0]*sizemultiplier)) + "x" + str(int(qimg.size[1]*sizemultiplier)))

            image = ImageTk.PhotoImage(qimg.resize((int(qimg.size[0]*sizemultiplier), int(qimg.size[1]*sizemultiplier))))
            background = Label(self.window, image = image)
            self.window.image = image
            self.window.resizable(False, False)
            background.place(x = -2, y = -2)
        except Exception as e:
            messagebox.showerror("oh no", e)

root = Tk()
try:
    GUI(root)
except Exception as e:
    messagebox.showerror("oh no", e)
root.mainloop()
