import time

class VEML7700:
    def __init__(self, i2c, addr=0x10):
        self.i2c = i2c
        self.addr = addr
        self.write_reg(0x00, 0x0000)
        self.gain_to_bits = {1: 0b00, 2: 0b01, 1/8: 0b10, 1/4: 0b11}
        self.it_to_bits = {25: 0b1100, 50: 0b1000, 100: 0b0000, 200: 0b0001, 400: 0b0010, 800: 0b0011}
        self.bits_to_gain = {v: k for k, v in self.gain_to_bits.items()}
        self.bits_to_it = {v: k for k, v in self.it_to_bits.items()}
        time.sleep(0.1)
        print("VEML7700 initialized")

    def write_reg(self, reg, value):
        data = value.to_bytes(2, 'little')
        self.i2c.writeto_mem(self.addr, reg, data)

    def read_reg(self, reg):
        data = self.i2c.readfrom_mem(self.addr, reg, 2)
        return int.from_bytes(data, 'little')

    def get_conf(self):
        return self.read_reg(0x00)

    # it (Integration Time) - Higher is better for low light, lower is better for bright environments
    # Gain - Amplification of incoming light, same tuning as Integration time

    def set_config(self, gain=1, it_ms=100):
        gain_bits = self.gain_to_bits.get(gain, 0b00)
        it_bits = self.it_to_bits.get(it_ms, 0b0000)

        conf = (gain_bits << 11) | (it_bits << 6)
        self.write_reg(0x00, conf)
        
    def correction_pol(self, lux):
        if lux <= 0:
            return 0.0

        return (
            6.0135e-13 * (lux ** 4)
            - 9.3924e-9 * (lux ** 3)
            + 8.1488e-5 * (lux ** 2)
            + 1.0023 * lux
        )

    

    def get_gain(self):
        conf = self.get_conf()
        gain_bits = (conf >> 11) & 0b11
        return self.bits_to_gain.get(gain_bits, 1)

    def get_it_ms(self):
        conf = self.get_conf()
        it_bits = (conf >> 6) & 0b1111
        return self.bits_to_it.get(it_bits, 100)

    def read_lux(self):
        conf = self.get_conf()

        gain_bits = (conf >> 11) & 0b11
        it_bits = (conf >> 6) & 0b1111

        gain = self.bits_to_gain.get(gain_bits, 1)
        it_ms = self.bits_to_it.get(it_bits, 100)
        
        raw = self.read_reg(0x04)
        lux = raw / (it_ms * gain)
        
        if lux > 1000 and self.get_gain() in (0.25, 0.125):
            return self.correction_pol(lux)
        else:
            return lux
        