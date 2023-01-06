from typing import IO
import serial
import serial.tools.list_ports
import time
import enum
import time
import struct
import logging
import collections


class IOBoard():
    SOF = 0xFF
    ESC = 0x33
    EOF = 0xCC

    class IOBoardException(Exception):
        def __init__(self, msg):
            super().__init__(msg)

    class CommandCode(enum.Enum):
        RETURN = 0
        VERSION = enum.auto()
        ID = enum.auto()
        ECHO = enum.auto()
        RESET = enum.auto()

        GPIO = enum.auto()
        DPM_AMMETER1 = enum.auto()
        DPM_AMMETER2 = enum.auto()
        DPM_VOLTMETER = enum.auto()
        CURRENT = enum.auto() #Read current with SN0291 module

    class CommandRetCode(enum.Enum):
        OK = 0xFF  # -1

        NONE = 0

        GENERIC = 1
        CHKSM = enum.auto()
        CMD_UNKNOW = enum.auto()
        CMD_NOT_IMPLEMENTED = enum.auto()
        BUSY = enum.auto()
        INVALID_ARGUMENT = enum.auto()

    class GPIO(enum.Enum):
        # Expanders
        GPIO_SEL8_DMM1=0
        GPIO_SEL7_DMM1=enum.auto()
        GPIO_SEL6_DMM1=enum.auto()
        GPIO_SEL5_DMM1=enum.auto()
        GPIO_SEL4_DMM1=enum.auto()
        GPIO_SEL3_DMM1=enum.auto()
        GPIO_SEL2_DMM1=enum.auto()
        GPIO_SEL1_DMM1=enum.auto()
        GPIO_SEL9_DMM1=enum.auto()
        GPIO_MAX_MIN_DMM1=enum.auto()
        GPIO_TRIG_RS_DMM1=enum.auto()

        GPIO_SEL8_DMM2=enum.auto()
        GPIO_SEL7_DMM2=enum.auto()
        GPIO_SEL6_DMM2=enum.auto()
        GPIO_SEL5_DMM2=enum.auto()
        GPIO_SEL4_DMM2=enum.auto()
        GPIO_SEL3_DMM2=enum.auto()
        GPIO_SEL2_DMM2=enum.auto()
        GPIO_SEL1_DMM2=enum.auto()
        GPIO_SEL9_DMM2=enum.auto()
        GPIO_MAX_MIN_DMM2=enum.auto()
        GPIO_TRIG_RS_DMM2=enum.auto()

        GPIO_MAX_MIN_DMM3=enum.auto()
        GPIO_TRIG_RS_DMM3=enum.auto()

        GP0=enum.auto()
        GP1=enum.auto()
        GP2=enum.auto()
        GP3=enum.auto()
        GP4=enum.auto()
        GP5=enum.auto()
        EN_5V_RJ45IO=enum.auto()
        GP7=enum.auto()

        GPIO_TP35=enum.auto()
        GPIO_TP36=enum.auto()
        GPIO_TP38=enum.auto()
        GPIO_TP39=enum.auto()
        GPIO_TP40=enum.auto()
        GPIO_TP41=enum.auto()
        GPIO_TP43=enum.auto()
        GPIO_TP44=enum.auto()

        # Builtins
        EN_PROG_STM=enum.auto()
        EN_UART_STM32=enum.auto()
        A2_SWA=enum.auto()
        HW_Range_DMM_AMP=enum.auto()
        SELECT_PAIR_VOLT_MEAS=enum.auto()
        SELECT_TARGET_VOLT_MEAS=enum.auto()
        EN_SHUNT_AMMETER=enum.auto()
        SW_JIG_2=enum.auto()
        EN_UART_CM4=enum.auto()
        EN_BOOT_CM4=enum.auto()
        EN_POWER_POE=enum.auto()
        LED=enum.auto()
        SELECT_PAIR_AMP_MEAS=enum.auto()
        CMD_EL=enum.auto()
        A0_SWA = enum.auto()
        A1_SWA = enum.auto()
        EN_SWA = enum.auto()

    SILENCED_COMMANDS = []

    @staticmethod
    def byte_is_special(byte):
        return byte in [IOBoard.SOF, IOBoard.ESC, IOBoard.EOF]

    @staticmethod
    def list():
        DEFAULT_USB_IDS = [
            {"VID": 0x0483, "PID": 0x374B},
            {"VID": 0x1366, "PID": 0x0105},
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

    def __init__(self, port="COM27", config={}, logger=None):
        self.config = config

        if logger is None:
            self.logger = logging.getLogger(__name__)
        else:
            self.logger = logger.getChild(__name__)
        self.logger.setLevel(self.config.get("log_level", "INFO"))
        if port == None:
            ports = IOBoard.list()
            if len(ports) > 0:
                port = ports[0]

            if port == None:
                raise IOBoard.IOBoardException(
                    "IO Board serial port not found !")

        # self.logger.debug(f"IO Board port : {port}")

        # This already opens the port
        self.port = serial.Serial(port, 115200)

    def __del__(self):
        try:
            if self.port.is_open:
                self.port.close()
        except AttributeError as ex:
            pass

    def port_name(self):
        return self.port.port

    def send(self, cmd_code, data=None):
        if data is None:
            data = []

        # if not cmd_code in IOBoard.SILENCED_COMMANDS:
        if not ((cmd_code == IOBoard.CommandCode.GPIO) and (data[0] == IOBoard.GPIO.SW_JIG_2.value)):
            self.logger.debug(f"TX {cmd_code} : {data}")

        cmd_code = cmd_code.value

        buff = [IOBoard.SOF]

        if IOBoard.byte_is_special(cmd_code):
            buff.append(IOBoard.ESC)

        buff.append(cmd_code)

        chksm = cmd_code
        for b in data:
            chksm += b
            if IOBoard.byte_is_special(b):
                buff.append(IOBoard.ESC)
            buff.append(b)
        chksm = chksm & 0xFF

        if IOBoard.byte_is_special(chksm):
            buff.append(IOBoard.ESC)
        buff.append(chksm)

        buff.append(IOBoard.EOF)

        self.port.write(buff)

    def receive(self, timeout=1):
        buff = []

        now = time.time()
        timeout = (now + timeout)
        while now < timeout:
            self.port.timeout = (timeout - now)
            buff.extend(self.port.read_until(serial.to_bytes([IOBoard.EOF])))
            now = time.time()

            try:
                buff = buff[buff.index(IOBoard.SOF):]
            except:
                pass

            if len(buff) >= 4:
                if (buff[0] == IOBoard.SOF) and (buff[-1] == IOBoard.EOF):
                    i = 2
                    while (i < len(buff)) and (buff[-i] == IOBoard.ESC):
                        i += 1
                    if i % 2:
                        continue

                    buff = buff[1:-1]  # Keep only payload and CRC

                    # Unescape
                    i = 0
                    while i < len(buff):
                        if buff[i] == IOBoard.ESC:
                            buff.pop(i)
                        i += 1

                    crc = buff.pop(-1)

                    # Check CRC and format result
                    if crc == (sum(buff) & 0xFF):
                        cmd_code = IOBoard.CommandCode(buff.pop(0))
                        data = buff

                        # if not cmd_code in IOBoard.SILENCED_COMMANDS:
                        if not (cmd_code == IOBoard.CommandCode.GPIO):
                            self.logger.debug(f"RX {cmd_code} : {data}")

                        return (cmd_code, data)

                    raise IOBoard.IOBoardException("Invalid CRC !")

        raise TimeoutError("IO Board timeout !")

    @staticmethod
    def check_return_ok(code, data):
        if (code == IOBoard.CommandCode.RETURN) and (len(data) == 1):
            if (data[0] == IOBoard.CommandRetCode.OK.value):
                return True

        raise IOBoard.IOBoardException(f"Invalid response ({code}:{data}) !")

    def send_and_receive(self, cmd_code, data=None, timeout=1):
        self.port.reset_output_buffer()
        self.port.reset_input_buffer()

        self.send(cmd_code, data)

        time.sleep(0.01)  # Don't know why it must be here...

        return self.receive(timeout)

    def read_id(self):
        (code, data) = self.send_and_receive(IOBoard.CommandCode.ID)
        if code == IOBoard.CommandCode.ID:
            return "".join([f"{b:02X}" for b in data])

        raise IOBoard.IOBoardException(f"Invalid response ({code}:{data}) !")

    def echo(self, buff=[]):
        (code, data) = self.send_and_receive(IOBoard.CommandCode.ECHO, buff)
        if code == IOBoard.CommandCode.ECHO:
            return data

        raise IOBoard.IOBoardException(f"Invalid response ({code}:{data}) !")

    def read_version(self):
        (code, data) = self.send_and_receive(IOBoard.CommandCode.VERSION)
        if code == IOBoard.CommandCode.VERSION:
            return "".join([chr(b) for b in data])

        raise IOBoard.IOBoardException(f"Invalid response ({code}:{data}) !")

    def reset(self):
        (code, data) = self.send_and_receive(IOBoard.CommandCode.RESET, [])
        return IOBoard.check_return_ok(code, data)

    def read_rgb(self, idx):
        RGBValues = collections.namedtuple(
            typename="RGBValues", field_names=["c", "r", "g", "b"])

        (code, data) = self.send_and_receive(IOBoard.CommandCode.RGB, [idx])
        if (code == IOBoard.CommandCode.RGB) and (len(data) == 8):
            (c, r, g, b) = struct.unpack("4H", bytes(data))
            return RGBValues(c=c, r=r, g=g, b=b)

        raise IOBoard.IOBoardException(f"Invalid response ({code}:{data}) !")

    def read_adc(self, type):
        (code, data) = self.send_and_receive(
            IOBoard.CommandCode.ADC, [type.value], timeout=3)
        if (code == IOBoard.CommandCode.ADC) and (len(data) == 2):
            raw = struct.unpack("H", bytes(data))[0]
            raw_mv = ((raw * 1200) / 4096)
            R1 = 33000
            R2 = 16500
            return (raw_mv * ((R1 + R2) / R2))

        raise IOBoard.IOBoardException(f"Invalid response ({code}:{data}) !")

    def toggle_dpm802_1(self):
        (code, data) = self.send_and_receive(IOBoard.CommandCode.DPM_AMMETER1, [])
        return IOBoard.check_return_ok(code, data)

    def toggle_dpm802_2(self):
        (code, data) = self.send_and_receive(IOBoard.CommandCode.DPM_AMMETER2, [])
        return IOBoard.check_return_ok(code, data)

    def toggle_dpm802_voltmeter_rs232(self):
        (code, data) = self.send_and_receive(IOBoard.CommandCode.DPM_VOLTMETER, [])
        return IOBoard.check_return_ok(code, data)

    def set_dpm802_function(self, function):
        (code, data) = self.send_and_receive(
            IOBoard.CommandCode.DPM, [function])
        return IOBoard.check_return_ok(code, data)

    def read_gpio(self, gpio):
        (code, data) = self.send_and_receive(
            IOBoard.CommandCode.GPIO, [gpio.value])
        if (code == IOBoard.CommandCode.GPIO) and (len(data) == 1):
            return (data[0] != 0)

        raise IOBoard.IOBoardException(f"Invalid response ({code}:{data}) !")

    def write_gpio(self, gpio, value):
        (code, data) = self.send_and_receive(
            IOBoard.CommandCode.GPIO, [gpio.value, value])
        return IOBoard.check_return_ok(code, data)

    def write_gpios(self, gpio_values):
        for (gpio, value) in gpio_values:
            self.write_gpio(gpio, value)

    def wait_jig(self, closed, exit_signal=lambda: False, interval_check=0.05):
        while not exit_signal():
            if self.read_gpio(IOBoard.GPIO.SW_JIG_2) == closed:
                return True
            time.sleep(interval_check)
