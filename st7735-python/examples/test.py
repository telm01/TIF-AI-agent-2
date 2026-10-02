import io
import os
import time
import subprocess
import board
import digitalio
from PIL import Image
from adafruit_rgb_display import ili9341
from PIL import Image, ImageEnhance


# Needed if you run from SSH or a non-desktop terminal
env = os.environ.copy()
env.setdefault("XDG_RUNTIME_DIR", "/run/user/1000")   # 1000 = your user id (check with: id -u)
env.setdefault("WAYLAND_DISPLAY", "wayland-0")        # check with: ls /run/user/1000

def grab():
    data = subprocess.check_output(["grim", "-t", "ppm", "-s", "0.25", "-"], env=env)
    return Image.open(io.BytesIO(data)).convert("RGB")

spi = board.SPI()
cs  = digitalio.DigitalInOut(board.CE0)
dc  = digitalio.DigitalInOut(board.D24)
rst = digitalio.DigitalInOut(board.D25)

backlight = digitalio.DigitalInOut(board.D22)
backlight.switch_to_output(value=True)

disp = ili9341.ILI9341(
    spi, cs=cs, dc=dc, rst=rst,
    baudrate=32000000,
    width=240, height=320,
    rotation=90,
)
W, H = 320, 240

try:
    while True:
        frame = grab().resize((W, H))
        disp.image(frame)
        time.sleep(0.03)
except KeyboardInterrupt:
    backlight.value = False