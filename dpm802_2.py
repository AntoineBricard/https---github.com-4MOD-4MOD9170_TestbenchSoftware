import logging
import serial
import serial.tools.list_ports
import time
import enum
import time
import collections


class DPM802_2():
    class DPM802Exception(Exception):
        def __init__(self, msg):
            super().__init__(msg)

    class Function(enum.Enum):
        VOLTAGE = 0b0111011
        CURRENT_UA = 0b0111101
        CURRENT_MA = 0b0111001
        CURRENT_A = 0b0111111
        ADP0 = 0b0111110
        ADP1 = 0b0111100
        ADP2 = 0b0111000
        ADP3 = 0b0111010

    Measure = collections.namedtuple(
        typename="Measure", field_names=["function", "value"])

    @staticmethod
    def list():
        DEFAULT_USB_IDS = [
            {"VID": 0x0403, "PID": 0x6001},
        ]

        def list_serial_ports(vid, pid):
            return [port.device for port in serial.tools.list_ports.comports()
                    if ((vid == port.vid) and (pid == port.pid))]

        for usb_ids in DEFAULT_USB_IDS:
            ports = list_serial_ports(usb_ids["VID"], usb_ids["PID"])
            if len(ports) > 0:
                return ports

        return []

    def __init__(self, port=None, config={}, logger=None):
        self.config = config

        if logger is None:
            self.logger = logging.getLogger(__name__)
        else:
            self.logger = logger.getChild(__name__)

        self.logger.setLevel(self.config.get("log_level", "INFO"))

        if port == None:
            ports = DPM802_2.list()
            if len(ports) > 0:
                port = ports[0]

            if port == None:
                raise DPM802_2.DPM802Exception("DPM802_2 serial port not found !")

        # self.logger.debug(f"DPM802 port : {port}")

        # This already opens the port
        self.port = serial.Serial(
            port, 2400, bytesize=serial.SEVENBITS, parity=serial.PARITY_ODD)

    def __del__(self):
        try:
            if self.port.is_open:
                self.port.close()
        except AttributeError as ex:
            pass

    def port_name(self):
        return self.port.port

    def read_measure(self, timeout=2.5):
        self.port.reset_input_buffer()

        time.sleep(0.05)

        now = time.time()
        timeout = (now + timeout)
        while now < timeout:
            self.port.timeout = (timeout - now)
            buff = (self.port.read_until(serial.to_bytes(b"\r\n")))
            now = time.time()

            if len(buff) == 11:
                buff = buff[:-2]  # Remove trailing "\r\n"

                # self.logger.debug(buff)

                function = DPM802_2.Function(buff[5])

                DIGITS = {
                    0b0110000: 0,
                    0b0110001: 1,
                    0b0110010: 2,
                    0b0110011: 3,
                    0b0110100: 4,
                    0b0110101: 5,
                    0b0110110: 6,
                    0b0110111: 7,
                    0b0111000: 8,
                    0b0111001: 9,
                }

                value = 0
                for i in range(1, 5):
                    value *= 10
                    value += DIGITS[buff[i]]

                if buff[6] & (1 << 2):  # Check sign
                    value = -value

                FACTORS = {
                    0b0110000: {DPM802_2.Function.VOLTAGE: 0.0001, DPM802_2.Function.CURRENT_UA: 0.1, DPM802_2.Function.CURRENT_MA: 0.01, DPM802_2.Function.CURRENT_A: 0.01},
                    0b0110001: {DPM802_2.Function.VOLTAGE: 0.001,  DPM802_2.Function.CURRENT_UA: 1,  DPM802_2.Function.CURRENT_MA: 0.1},
                    0b0110010: {DPM802_2.Function.VOLTAGE: 0.01},
                    0b0110011: {DPM802_2.Function.VOLTAGE: 0.1},
                    0b0110100: {DPM802_2.Function.VOLTAGE: 1},
                    0b0110101: {},
                }

                value *= FACTORS[buff[0]][function]

                # Convert all function to the same unit...
                if function == DPM802_2.Function.CURRENT_UA:
                    value *= 0.001
                    function = DPM802_2.Function.CURRENT_MA
                elif function == DPM802_2.Function.CURRENT_A:
                    value *= 1000
                    function = DPM802_2.Function.CURRENT_MA

                self.logger.debug(f"DPM802 : {function} --> {value}")

                return DPM802_2.Measure(function, value)

        raise TimeoutError("DPM802 timeout !")
