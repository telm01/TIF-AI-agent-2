def test_128_64_0(gpiodevice, gpiod, spidev, numpy, st7735):
    display = st7735.ST7735(port=0, cs=0, dc=24, width=128, height=64, rotation=0)
    assert display.width == 128
    assert display.height == 64


def test_128_64_90(gpiodevice, gpiod, spidev, numpy, st7735):
    display = st7735.ST7735(port=0, cs=0, dc=24, width=128, height=64, rotation=90)
    assert display.width == 64
    assert display.height == 128
