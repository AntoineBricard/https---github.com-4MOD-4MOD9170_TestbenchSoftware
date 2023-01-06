import enum
import logging
import serial
import serial.tools.list_ports
import time
import re
import collections


class CommTest():
    class CommTestException(Exception):
        def __init__(self, msg):
            super().__init__(msg)

    class Config(enum.Enum):
        HARDWARE_REVISION = 1
        SIM_PROVIDER = 2
        APN = 3
        APN_USER = 4
        SIM_USER_PASS = 5

    CommResult = collections.namedtuple(
        typename="CommResult", field_names=["raw", "data"])

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

        if port == None:
            ports = CommTest.list()
            if len(ports) > 0:
                port = ports[0]

            if port == None:
                raise IOError("CommTest serial port not found !")

        # self.logger.debug("CommTest port : {}".format(port))

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

    def flush_input(self):
        self.port.reset_input_buffer()

    def read_lines(self, timeout=1, end_flags=[]):
        lines = []

        now = time.time()
        timeout = (now + timeout)
        while now < timeout:
            self.port.timeout = (timeout - now)
            line = self.port.read_until(b"\r\n")
            if len(line) > 1:
                try:
                    line = line.decode()
                except UnicodeDecodeError as ex:
                    self.logger.exception(ex)
                    self.logger.error(f"Received : {line}")
                    continue
                line = line.strip()

                self.logger.debug(f"RX: {line}")

                lines.append(line)

                if any([re.match(r, line) for r in end_flags]):
                    return lines

            now = time.time()

        return lines

    def send_command(self, command, timeout=None, args=[], try_nb=None, end_flags=[]):
        if try_nb is None:
            try_nb = self.config.get("comm_test_STM_try_nb", 5)
        if timeout is None:
            timeout= self.config.get("comm_STM_timeout", 2)

        def try_send_command(self, command, timeout=1, args=[]):
            self.flush_input()

            if type(command) is str:
                command = command.format(*args)
                self.logger.debug(f"TX: {command}")
                self.port.write(f"{command}".encode())
            elif type(command) is bytes:
                self.logger.debug(f"TX: {command}")
                self.port.write(command)
            else:
                raise CommTest.CommTestException(
                    f"Invalid command type ({type(command)}) !")

            lines = []

            now = time.time()
            timeout = (now + timeout)
            while now < timeout:
                self.port.timeout = (timeout - now)
                time.sleep(0.1)
                line = self.port.read_all()
                if len(line) >= 1:
                    try:
                        line = line.decode("utf-8")
                    except UnicodeDecodeError as ex:
                        self.logger.exception(ex)
                        self.logger.error(f"Received : {line}")
                        continue
                    line = line.strip()

                    self.logger.debug(f"RX: {line}")

                    lines.append(line)

                    if any(['z' in l for l in lines]):
                        self.logger.info("Get 'z', error raise !")
                        #raise CommTest.CommTestException("fail was return")
                        return lines
                    elif any([command in l for l in lines]):
                        return lines
                    elif any([re.match(r, line) for r in end_flags]):
                        return lines
                    else:
                        self.logger.info("Get unexpected RX, retry send command !")
                        time.sleep(0.250)
                        raise CommTest.CommTestException(
                            "Unexpected RX !")

                now = time.time()
            time.sleep(0.250)
            raise CommTest.CommTestException("Test communication timeout !")

        if self.config.get("comm_test_STM_retry_on_badarg", True) or self.config.get("comm_test_STM_retry_on_fail", True):
            try:
                if type(command) is str:
                    cmd_flag = re.match(
                        fr"^\s*\w+\+(\w+)(?:\s*=\s*<.*>)?\s*$", command)
                elif type(command) is bytes:
                    cmd_flag = re.match(
                        fr"^\s*\w+\+(\w+)(?:\s*=\s*<.*>)?\s*$", command.decode())
                else:
                    cmd_flag = None

                if cmd_flag is not None:
                    cmd_flag = cmd_flag.group(1)
            except Exception as ex:
                self.logger.exception(ex)
                cmd_flag = None
        else:
            cmd_flag = None

        while try_nb > 0:
            try:
                lines = try_send_command(
                    self=self, command=command, timeout=timeout, args=args)
                if cmd_flag:
                    if self.config.get("comm_test_STM_retry_on_badarg", True):
                        if any(re.match(fr"^\s*\+{cmd_flag}\s*:\s*BADARG\s*$", l) for l in lines):
                            raise CommTest.CommTestException(
                                "Test communication error !")
                    if self.config.get("comm_test_STM_retry_on_fail", True):
                        if any(re.match(fr"^\s*\+{cmd_flag}\s*:\s*FAIL\s*$", l) for l in lines):
                            raise CommTest.CommTestException(
                                "Test communication error !")

                return lines
            except CommTest.CommTestException as ex:
                self.logger.warning(str(ex))
                # self.logger.exception(ex)

            try_nb -= 1

        raise CommTest.CommTestException("Test communication command failed !")

    def simple_check(self, base, check, **kwargs):
        lines = self.send_command(f"{base}",end_flags=check, **kwargs)
        if "z" in lines:
            return CommTest.CommResult(lines, False)
        if not check in lines:
            return CommTest.CommResult(lines, False)
        return CommTest.CommResult(lines, any(re.match(fr"^\s*{check}\s*$", l) for l in lines))

    def simple_int(self, base, check, **kwargs):
        lines = self.send_command(f"{base}+{check}", **kwargs)
        if not "OK" in lines:
            return CommTest.CommResult(lines, None)
        r = re.compile(fr"^\s*\+{check}\s*:\s*<\s*(-?\d+)\s*>\s*$")
        l = list(filter(lambda l: r.match(l), lines))
        if len(l) <= 0:
            return CommTest.CommResult(lines, None)
        return CommTest.CommResult(lines, int(r.match(l[0]).group(1)))

    def shutter_ON(self, **kwargs):
        timeout = self.config.get("shutterON_timeout_delay", 4)
        lines = self.simple_check(base="B",check="B", timeout=timeout)
        return lines

    def shutter_OFF(self, **kwargs):
        timeout = self.config.get("shutterOFF_timeout_delay", 4)
        lines = self.simple_check(base="C",check="C", timeout=timeout)
        return lines

    def DAC1(self, **kwargs):
        timeout = self.config.get("DAC1_timeout_delay", 3)
        lines = self.simple_check(base="a",check="y", timeout=timeout)
        return lines

    def DAC2(self, **kwargs):
        timeout = self.config.get("DAC2_timeout_delay", 3)
        lines = self.simple_check(base="b",check="y", timeout=timeout)
        return lines

    def radar_SquareWave(self, **kwargs):
        timeout = self.config.get("radar_SquareWave_timeout_delay", 4)
        lines = self.simple_check(base="m",check="y", timeout=timeout)
        return lines

    def radar_NoMotion(self, **kwargs):
        timeout = self.config.get("radarNoMotion_timeout_delay", 4)
        lines = self.simple_check(base="k",check="y", timeout=timeout)
        return lines

    def radar_Motion(self, **kwargs):
        timeout = self.config.get("radar_Motion_timeout_delay", 15)
        lines = self.simple_check(base="l",check="y", timeout=timeout)
        return lines

    def radar_Noise(self, **kwargs):
        timeout = self.config.get("radar_Noise_timeout_delay", 8)
        lines = self.simple_check(base="n",check="y", timeout=timeout)
        return lines

    def set_OFF_GPIOs(self, **kwargs):
        timeout = self.config.get("setGPIO_state_timeout_delay", 3)
        lines = []
        lines.append(self.simple_check(base="E",check="E", timeout=timeout))
        lines.append(self.simple_check(base="G",check="G", timeout=timeout))
        lines.append(self.simple_check(base="I",check="I", timeout=timeout))
        lines.append(self.simple_check(base="K",check="K", timeout=timeout))
        lines.append(self.simple_check(base="M",check="M", timeout=timeout))
        lines.append(self.simple_check(base="O",check="O", timeout=timeout))

        return lines

    def set_ON_GPIOs(self, **kwargs):
        timeout = self.config.get("setGPIO_state_timeout_delay", 3)
        lines = []
        lines.append(self.simple_check(base="D",check="D", timeout=timeout))
        lines.append(self.simple_check(base="F",check="F", timeout=timeout))
        lines.append(self.simple_check(base="H",check="H", timeout=timeout))
        lines.append(self.simple_check(base="J",check="J", timeout=timeout))
        lines.append(self.simple_check(base="L",check="L", timeout=timeout))
        lines.append(self.simple_check(base="N",check="N", timeout=timeout))

        return lines

    #Heat Sensors
    def heat_1(self, **kwargs):
        timeout = self.config.get("heat_sensors_timeout_delay", 4)
        lines = self.simple_check(base="g",check="y", timeout=timeout)
        return lines

    def heat_2(self, **kwargs):
        timeout = self.config.get("heat_sensors_timeout_delay", 4)
        lines = self.simple_check(base="h",check="y", timeout=timeout)
        return lines

    def heat_3(self, **kwargs):
        timeout = self.config.get("heat_sensors_timeout_delay", 4)
        lines = self.simple_check(base="i",check="y", timeout=timeout)
        return lines

    def heat_4(self, **kwargs):
        timeout = self.config.get("heat_sensors_timeout_delay", 4)
        lines = self.simple_check(base="j",check="y", timeout=timeout)
        return lines

    #ToF Sensors
    def tof_1(self, **kwargs):
        timeout = self.config.get("tof_sensors_timeout_delay", 8)
        lines = self.simple_check(base="c",check="y", timeout=timeout)
        return lines

    def tof_2(self, **kwargs):
        timeout = self.config.get("tof_sensors_timeout_delay", 8)
        lines = self.simple_check(base="d",check="y", timeout=timeout)
        return lines

    def tof_3(self, **kwargs):
        timeout = self.config.get("tof_sensors_timeout_delay", 8)
        lines = self.simple_check(base="e",check="y", timeout=timeout)
        return lines

    def tof_4(self, **kwargs):
        timeout = self.config.get("tof_sensors_timeout_delay", 8)
        lines = self.simple_check(base="f",check="y", timeout=timeout)
        return lines

    #WDG_STM test
    def wdg_stm(self, **kwargs):
        timeout = self.config.get("wdg_stm_timeout_delay", 4)
        lines = self.simple_check(base="P",check="y", timeout=timeout)
        return lines

    #WDG_SOM
    def wdg_som_on(self, **kwargs):
        timeout = self.config.get("wdg_som_timeout_delay", 7)
        lines = self.simple_check(base="w",check="w", timeout=timeout)
        return lines

    def wdg_som_off(self, **kwargs):
        timeout = self.config.get("wdg_som_timeout_delay", 7)
        lines = self.simple_check(base="A",check="A", timeout=timeout)
        return lines

    #GPIO OUTPUT TO SOM
    def GPIO_18(self, state, **kwargs):
        timeout = self.config.get("gpio_SOM_timeout_delay", 7)
        if state=="ON":
            lines = self.simple_check(base="o",check="o", timeout=timeout)
        else:
            lines = self.simple_check(base="p",check="p", timeout=timeout)
        return lines

    def GPIO_23(self, state, **kwargs):
        timeout = self.config.get("gpio_SOM_timeout_delay", 7)
        if state=="ON":
            lines = self.simple_check(base="q",check="q", timeout=timeout)
        else:
            lines = self.simple_check(base="r",check="r", timeout=timeout)
        return lines

    def GPIO_24(self, state, **kwargs):
        timeout = self.config.get("gpio_SOM_timeout_delay", 7)
        if state=="ON":
            lines = self.simple_check(base="s",check="s", timeout=timeout)
        else:
            lines = self.simple_check(base="t",check="t", timeout=timeout)
        return lines

    def GPIO_25(self, state, **kwargs):
        timeout = self.config.get("gpio_SOM_timeout_delay", 7)
        if state=="ON":
            lines = self.simple_check(base="u",check="u", timeout=timeout)
        else:
            lines = self.simple_check(base="v",check="v", timeout=timeout)
        return lines