import subprocess
import time
import sys

from PIL import Image

import st7735

disp = st7735.ST7735(
    port=0,
    cs=st7735.BG_SPI_CS_BACK,
    dc="GPIO24",
    backlight="GPIO22",
    rst="GPIO25",
    rotation=90,
    invert=False,
    spi_speed_hz=4000000
)

disp.begin()

while True:
    # Take screenshot of the main display
    subprocess.run(
        ["scrot", "/tmp/screen.png"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    # Open screenshot
    img = Image.open("/tmp/screen.png").convert("RGB")

    # Resize for ST7735
    img = img.resize((124, 160), Image.Resampling.LANCZOS)

    # Send to display
    disp.display(img)

    time.sleep(0.1)