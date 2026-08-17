# pix2msch
A program that converts images into Mindustry schematics

The usage should be pretty self explainatory once you open gui.py

If you're on Windows, you can go to releases, download and run pix2msch.exe. No Python installation needed.

Note: You will need `pyperclip` and `pillow` to run gui.py. You can install them by doing `pip install <package>`

Note: Mindustry won't load schematics bigger than 128x128, so images are automatically scaled down to fit that limit while keeping their aspect ratio. Smaller images are left untouched, but keep in mind that even a 100x100 image becomes a large structure in-game.

The GUI now enables **Detect Mindustry blocks** by default. This mode is for a screenshot of a Mindustry schematic/base layout: it recognizes the confirmed 3x6 layout, writes real block types and configurations, and produces one schematic block per structure. Uncheck it to use the original pixel-art conversion.

Here's a screenshot of the gui:

![GUI](https://i.ibb.co/TPfc2MJ/Screenshot-203.png)

The GUI is completely made with `tkinter`, and supports a lot of platforms

![WOMM](https://cdn.discordapp.com/attachments/676843444274069504/677566642888376320/WOMM.png)
