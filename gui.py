try:
    import sys
    from sys import platform
    from tkinter import *
    from tkinter import filedialog
    from tkinter import messagebox
    from PIL import Image, ImageTk
    import tkinter.font, os, core, recognize
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

    def convert(self, mode):
        if not self.file:
            messagebox.showerror("oh no", "Open an image first")
            return
        try:
            res = self.select_grid()
            if res is None:
                return
            box, W, H, bg, counts = res
            w, h, blocks, grid, thr = core.detect_structure(self.file, box, W, H, bg=bg, block_counts=counts)
            if not blocks:
                messagebox.showerror("oh no", "Detected 0 blocks. Adjust the grid box/dimensions and try again.")
                return
            self.struct = (w, h, blocks, grid, box, W, H, mode)
            self.review_window()
        except Exception as e:
            messagebox.showerror("oh no", e)

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
        bg_pick = {"active": False}

        def to_actual(x, y):
            return int(x / scale), int(y / scale)

        def on_down(e):
            if bg_pick["active"]:
                bg_pick["active"] = False
                ix, iy = to_actual(e.x, e.y)
                pix = PILImage.open(self.file).convert("RGB").load()
                iw, ih = pix.size
                rs = gs = bs = 0; n = 0
                for dy in range(-12, 13):
                    for dx in range(-12, 13):
                        x, y = ix + dx, iy + dy
                        if 0 <= x < iw and 0 <= y < ih:
                            p = pix[x, y]; rs += p[0]; gs += p[1]; bs += p[2]; n += 1
                result["bg"] = (rs // n, gs // n, bs // n)
                status.configure(text="Background set to %s. Draw the grid box and Detect." % (result["bg"],))
                return
            rect["sx"], rect["sy"] = e.x, e.y
            if rect["id"]:
                canvas.delete(rect["id"])
            rect["id"] = canvas.create_rectangle(e.x, e.y, e.x, e.y, outline="red", width=2)

        def on_drag(e):
            if rect["id"]:
                canvas.delete(rect["id"])
            rect["id"] = canvas.create_rectangle(rect["sx"], rect["sy"], e.x, e.y, outline="red", width=2)

        def on_up(e):
            if rect["id"]:
                canvas.delete(rect["id"])
            x0, y0, x1, y1 = rect["sx"], rect["sy"], e.x, e.y
            if x1 < x0:
                x0, x1 = x1, x0
            if y1 < y0:
                y0, y1 = y1, y0
            rect["id"] = canvas.create_rectangle(x0, y0, x1, y1, outline="red", width=2)
            result["box"] = (x0, y0, x1, y1)

        canvas.bind("<ButtonPress-1>", on_down)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_up)

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
            try:
                w, h, blocks, grid, thr = core.detect_structure(
                    self.file, (ax0, ay0, ax1, ay1), W, H, bg=result["bg"], thresh=T, block_counts=counts)
            except Exception as ex:
                status.configure(text="Detection error: " + str(ex))
                return
            status.configure(text="Detected %d blocks (occupancy thr=%.2f). Adjust the box/dimensions if needed, then OK." % (len(blocks), thr))
            canvas.delete("occ")
            plist.delete(0, END)
            for (nm, x, y, rot, cfg, size) in blocks:
                plist.insert(END, "%-22s (%d,%d) r%d" % (nm, x, y, rot))
            # overlay occupied cells using the same background + threshold
            tw, th, ox, oy = grid
            bg = result["bg"] or core.tuple_array[9]
            br, bgc, bb = bg[0], bg[1], bg[2]
            pix = PILImage.open(self.file).convert("RGB").load()
            def occ(x0, y0, tw, th):
                cnt = 0; tot = 0
                for y in range(y0, y0 + th):
                    for x in range(x0, x0 + tw):
                        p = pix[x, y]
                        if abs(p[0] - br) > 40 or abs(p[1] - bgc) > 40 or abs(p[2] - bb) > 40:
                            cnt += 1
                        tot += 1
                return cnt / float(tot)
            for cy in range(h):
                for cx in range(w):
                    if occ(ox + cx * tw, oy + cy * th, tw, th) > thr:
                        x0d, y0d = result["box"][0], result["box"][1]
                        dx = x0d + int((ox + cx * tw - ax0) * scale)
                        dy = y0d + int((oy + (h - 1 - cy) * th - ay0) * scale)
                        canvas.create_rectangle(dx, dy, dx + int(tw * scale), dy + int(th * scale),
                                               outline="#39d353", width=1, tags="occ")

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
        Button(win, text="Set background", command=lambda: bg_pick.__setitem__("active", True) or status.configure(text="Click an empty area to set the background color.")).pack(side=LEFT, padx=5, pady=5)
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
            if res is not None:
                box, W2, H2, bg, counts = res
                r = core.detect_structure(self.file, box, W2, H2, bg=bg, block_counts=counts)
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
