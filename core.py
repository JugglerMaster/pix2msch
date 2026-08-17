try:
    import struct, zlib, os, base64, sys
    from PIL import Image
except Exception as e:
    print("You're missing a package!")
    print()
    print(e)
    input()
    
colorarray = [
    217, 157, 115,
    140, 127, 169,
    235, 238, 245,
    178, 198, 210,
    247, 203, 164,
    39, 39, 39,
    141, 161, 227,
    249, 163, 199,
    119, 119, 119,
    83, 86, 92,
    203,217, 127,
    244,186, 110,
    243, 233, 121,
    116, 87, 206,
    255, 121, 94,
    255, 170, 95
    ]

#convert array of ints into a list of tuples, then into a palette
tuple_array = [tuple(colorarray[t*3:t*3+3]) for t in range(len(colorarray)//3)]
palette = Image.new("P", (16, 16))
palette.putpalette(colorarray*16)
palette.load()

#Mindustry refuses to load schematics larger than this (see Schematics.java)
max_size = 128


class ByteBuffer():
    """Small big-endian writer for Mindustry's schematic format."""
    def __init__(self):
        self.data = bytearray()

    def writeShort(self, value):
        self.data += struct.pack(">H", value & 0xffff)

    def writeUTF(self, value):
        encoded = value.encode("utf-8")
        self.writeShort(len(encoded))
        self.data += encoded

    def writeByte(self, value):
        self.data += struct.pack("B", value & 0xff)

    def writeInt(self, value):
        self.data += struct.pack(">i", value)


def _write_object(data, value):
    """Write the TypeIO values needed by detected blocks."""
    if value is None:
        data.writeByte(0)
    elif value[0] == "content":
        data.writeByte(5)
        data.writeByte(value[1])  # ContentType
        data.writeShort(value[2])
    elif value[0] == "points":
        data.writeByte(8)
        data.writeByte(len(value[1]))
        for point in value[1]:
            data.writeInt(point)
    else:
        raise ValueError("Unsupported schematic config: {0}".format(value[0]))


def _write_schematic(width, height, tags, blocks, path=None, mode="path"):
    """Serialize blocks as a current Mindustry .msch file."""
    data = ByteBuffer()
    data.writeShort(width)
    data.writeShort(height)

    data.writeByte(len(tags))
    for key, value in tags.items():
        data.writeUTF(key)
        data.writeUTF(value)

    dictionary = []
    for block in blocks:
        if block[0] not in dictionary:
            dictionary.append(block[0])
    data.writeByte(len(dictionary))
    for block_name in dictionary:
        data.writeUTF(block_name)

    data.writeInt(len(blocks))
    for block_name, x, y, config, rotation in blocks:
        data.writeByte(dictionary.index(block_name))
        data.writeInt((x << 16) | (y & 0xffff))
        _write_object(data, config)
        data.writeByte(rotation)

    schematic = b"msch" + bytes([1]) + zlib.compress(data.data)
    if mode == "path":
        with open(path, "wb") as file:
            file.write(schematic)
        print("Successfully saved {0} ".format(path))
    else:
        try:
            import pyperclip
        except ImportError:
            raise Exception("To use this feature, you need to have the pyperclip module")
        pyperclip.copy(base64.standard_b64encode(schematic).decode())
        print("Schematic converted to base64, and put into clipboard")


def _palette_image(imgfile):
    """Load an image without the 128px pixel-art resize."""
    image = Image.open(imgfile).convert("RGB")
    return image._new(image.im.convert("P", 0, palette.im)).convert("RGB")


def quantize(img, dither, transparency_treshold):
    #invalid input checking
    try:
        img = Image.open(img)
        transparency_treshold = int(transparency_treshold)
    except AttributeError:
        raise Exception("No image selected")
    except ValueError:
        raise Exception("Transparency Treshold must be a number")
    
    if transparency_treshold > 255:
        raise Exception("Transparency Treshold must not exceed 255")
    elif transparency_treshold < 0:
        raise Exception("Transparency Treshold most not be negative")
    
    #scale down to fit Mindustry's schematic size limit, keeping the aspect ratio
    #this must happen before quantization so every pixel still matches the palette exactly
    img = img.convert("RGBA") # image
    if max(img.size) > max_size:
        scale = max_size / max(img.size)
        img = img.resize((int(img.size[0] * scale), int(img.size[1] * scale)), Image.LANCZOS)
        print("Scaled image down to {0}x{1} to fit Mindustry's 128x128 schematic limit".format(*img.size))
    imgq = img.convert("RGB") # fully opaque image
    imgq = imgq._new(imgq.im.convert("P", 1 if dither else 0, palette.im)) #where the actual quantization happens

    imgA = Image.new("RGBA", img.size)
    pixels = imgA.load()
    imgq = imgq.convert("RGB")
    
    for y in range(img.size[1]):
        for x in range(img.size[0]):
            if img.getpixel((x, y))[3] >= transparency_treshold: #transparency treshold
                pixels[x, y] = imgq.getpixel((x, y))
            else:
                pixels[x, y] = (0, 0, 0, 0)

    print("Quantization complete")

    return imgA


# imgfile - Path to the image
# name - Name of the schematic
# save_location - Save location, i guess
# dither - Whether to use dithering (True or False, 1 or 0)
# transparency_treshold - Below which alpha level to stop displaying (0-255), where 0 is show everything and 255 is show only fully opaque
# mode - Either "path" or "clipboard". Whether to save the schematic as .msch or to copy it into clipboard

def pix2msch(imgfile               = None,
             name                  = "schematic",
             save_location         = None,
             dither                = True,
             transparency_treshold = 127,
             mode                  = "path",
             structure_mode        = True,
             reference             = None
             ): #sad face
    
    #input checking
    if mode == "path":
        if not(os.path.isdir(os.path.expandvars(save_location))):
            raise Exception("Invalid path")
        
    if name == "":
        raise Exception("Please enter a name")
    
    import recognize
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(getattr(sys, "_MEIPASS", ""))
        candidates.append(os.path.dirname(sys.executable))
    candidates.append(os.path.dirname(os.path.abspath(__file__)))
    corpus_dir = None
    for base in candidates:
        if base and os.path.isdir(os.path.join(base, "examples")):
            corpus_dir = os.path.join(base, "examples")
            break
    if corpus_dir is None:
        raise FileNotFoundError("Could not locate the examples/ directory needed for block detection")
    corpus = recognize.build_corpus(corpus_dir)
    if reference:
        w, h, refblocks = recognize.parse_msch(reference)
        occ = recognize.occ_from_blocks(refblocks, w, h)
        W, H, blocks = recognize.recognize(imgfile, corpus, dims=(w, h), occ=occ)
    else:
        W, H, blocks = recognize.recognize(imgfile, corpus)
    tags = {
        "contentMap": "{0:{sand:4,coal:5}}",
        "labels": "[]",
        "name": name,
        "description": "",
    }
    output = os.path.join(os.path.expandvars(save_location), name + ".msch") if mode == "path" else None
    wb = [(n, x, y, cfg, rot) for (n, x, y, rot, cfg, size) in blocks]
    _write_schematic(W, H, tags, wb, output, mode)
    print("Detected and wrote {0} structures".format(len(blocks)))
        
