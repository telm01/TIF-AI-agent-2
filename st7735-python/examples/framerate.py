# Copyright (c) 2014 Adafruit Industries
# Author: Tony DiCola
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
import math
import sys
import time

from PIL import Image, ImageDraw

import st7735

SPI_SPEED_MHZ = 4  # Higher speed = higher framerate

if len(sys.argv) > 1:
    SPI_SPEED_MHZ = int(sys.argv[1])

print(f"""
framerate.py - Test LCD framerate.

If you're using Breakout Garden, plug the 0.96" LCD (SPI)
breakout into the rear slot.

Running at: {SPI_SPEED_MHZ}MHz
""")

# Create ST7735 LCD display class.
disp = st7735.ST7735(
    port=0,
    cs=st7735.BG_SPI_CS_FRONT,  # BG_SPI_CS_BACK or BG_SPI_CS_FRONT. BG_SPI_CS_FRONT (eg: CE1) for Enviro Plus
    dc="PIN21",                 # "GPIO9" / "PIN21". "PIN21" for a Pi 5 with Enviro Plus
    backlight="PIN32",          # "PIN18" for back BG slot, "PIN19" for front BG slot. "PIN32" for a Pi 5 with Enviro Plus
    rotation=90,
    spi_speed_hz=4000000
)

WIDTH = disp.width
HEIGHT = disp.height
STEPS = WIDTH * 2
images = []

for step in range(STEPS):
    image = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 128))
    draw = ImageDraw.Draw(image)

    if step % 2 == 0:
        draw.rectangle((79, 0, 159, 79), (0, 128, 0))
    else:
        draw.rectangle((0, 0, 79, 79), (0, 128, 0))

    f = math.sin((float(step) / STEPS) * math.pi)
    offset_left = int(f * WIDTH)
    draw.ellipse((offset_left, 35, offset_left + 10, 45), (255, 0, 0))

    images.append(image)

count = 0
time_start = time.time()

while True:
    disp.display(images[count % len(images)])
    count += 1
    time_current = time.time() - time_start
    if count % 120 == 0:
        print(f"Time: {time_current:8.3f},      Frames: {count:6d},      FPS: {count / time_current:8.3f}")
