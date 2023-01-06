import enum
import logging
import threading
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
        self.cm4_is_boot = False

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
            line = self.port.read_all()
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

    def read_lines_without_log(self, timeout=1, end_flags=[]):
        lines = []

        now = time.time()
        timeout = (now + timeout)
        while now < timeout:
            self.port.timeout = (timeout - now)
            line = self.port.read_all()
            if len(line) > 1:
                try:
                    line = line.decode()
                except UnicodeDecodeError as ex:
                    self.logger.exception(ex)
                    self.logger.error(f"Received : {line}")
                    continue
                line = line.strip()

                #self.logger.debug(f"RX: {line}")

                lines.append(line)

                if any([re.match(r, line) for r in end_flags]):
                    return lines

            now = time.time()

        return lines

    def singleCommand_withoutLogs(self, command, timeout=None, args=[], try_nb=None, end_flags=[]):
        if try_nb is None:
            try_nb = self.config.get("comm_test_CM4_try_nb", 3)
        if timeout is None:
            timeout= self.config.get("comm_CM4_timeout", 2)

        def try_send_command(self, command, timeout=1, args=[]):
            self.flush_input()

            if type(command) is str:
                command = command.format(*args)
                #self.logger.debug(f"TX: {command}")
                self.port.write(f"{command}\n".encode())
            elif type(command) is bytes:
                #self.logger.debug(f"TX: {command}")
                self.port.write(command)
            output = self.read_lines_without_log(timeout=timeout, end_flags=end_flags)
            return output

        while try_nb > 0:
            try:
                lines = try_send_command(self=self, command=command, timeout=timeout, args=args)
                single_string = ""
                if len(lines)>1:
                    for line in lines:
                        single_string = single_string+line
                elif len(lines)==0:
                    self.logger.warning("No data received")
                elif type(lines) is list:
                    single_string = lines[0]
                else:
                    single_string = lines

                if len(end_flags) >=1:
                    for ef in end_flags:
                        if re.search(ef, single_string):
                            self.logger.info(f"Retuned :{single_string}")
                            return single_string
                else:
                    self.logger.info(f"Data returned")
                    return single_string
            except Exception as ex:
                self.logger.error(ex)
            self.logger.warning("Retrying...")
            try_nb -= 1


    def singleCommand(self, command, timeout=None, args=[], try_nb=None, end_flags=[]):
        if try_nb is None:
            try_nb = self.config.get("comm_test_CM4_try_nb", 3)
        if timeout is None:
            timeout= self.config.get("comm_CM4_timeout", 2)

        def try_send_command(self, command, timeout=1, args=[]):
            self.flush_input()

            if type(command) is str:
                command = command.format(*args)
                self.logger.debug(f"TX: {command}")
                self.port.write(f"{command}\n".encode())
            elif type(command) is bytes:
                self.logger.debug(f"TX: {command}")
                self.port.write(command)
            output = self.read_lines(timeout=timeout, end_flags=end_flags)
            return output

        while try_nb > 0:
            try:
                lines = try_send_command(self=self, command=command, timeout=timeout, args=args)
                single_string = ""
                if len(lines)>1:
                    for line in lines:
                        single_string = single_string+line
                elif len(lines)==0:
                    self.logger.warning("No data received")
                else:
                    single_string = lines

                if len(end_flags) >=1:
                    for ef in end_flags:
                        if re.search(ef, single_string):
                            self.logger.info(f"Retuned :{single_string}")
                            return single_string
                else:
                    self.logger.info(f"Retuned :{single_string}")
                    return single_string
            except Exception as ex:
                self.logger.error(ex)
            self.logger.warning("Retrying...")
            try_nb -= 1

    #CHECK_IMAGE scripts:
    def take_image(self, **kwargs):
        timeout = self.config.get("take_image_timeout_delay", 10)
        #image = self.singleCommand("python3 ./PI_Tests/camtest.py", timeout, end_flags=[r"pego@p[0-9]{15}:~\$", "raspberrypi:~"])
        command = "sudo ./PI_Tests/cam_test.sh"
        try_nb = self.config.get("take_image_try_nb", 2)
        read_retry = self.config.get("take_image_read_retry", 6)

        while try_nb > 0:
            self.logger.debug(f"TX: {command}")
            self.port.write(f"{command}\n".encode())
            data=""
            try:
                is_empty = True
                while is_empty and read_retry>0:
                    readed_lines = self.read_lines(timeout=timeout, end_flags=[r"\s*Image\s*Captured\s*","Image Captured", r"pego@CM4:~\$", r"pego@CM4:~\$$", r"pego@p[0-9]{15}:~$\$"])
                    if len(readed_lines) >=1:
                        for e in readed_lines:
                            data = data+str(e)
                    if len(data) >0 and re.search(r"\s*Image\s*Captured\s*", data):
                        is_empty = False
                        self.logger.info(f"CM4 values returned :{data}")
                        return data
                    read_retry -=1
                    self.logger.debug(f"retry read... {read_retry}")

            except Exception as ex:
                self.logger.error(ex)
            self.logger.warning("Retry cam_test.sh")
            self.logger.debug("retry take picture")
            try_nb -=1
        return data

    def check_image(self, **kwargs):
        timeout = self.config.get("check_image_timeout_delay", 45)
        image = self.singleCommand("python3 ./PI_Tests/image_check.py", timeout, end_flags=["Image ok", "ok", "Too many black pixels","Too many white pixels","raspberrypi:~"])
        return image
    ###

    def wdg_stm(self, **kwargs):
        timeout = self.config.get("wdg_stm_timeout_delay", 1)
        wdg = self.singleCommand("sudo ./PI_Tests/test_wdg_stm.sh", timeout)
        return wdg

    def wdg_som(self, **kwargs):
        timeout = self.config.get("wdg_som_timeout_delay", 11)
        wdg = self.singleCommand("sudo ./PI_Tests/test_wdg_som.sh", timeout, end_flags=["Succeeded", "Test failed:"])
        return wdg

    #GPIO_SOM scripts
    def GPIO_18_script(self):
        timeout = self.config.get("gpio_som_script_timeout", 11)
        gpio_18 = self.singleCommand("sudo ./PI_Tests/test_pin_18.sh", timeout, end_flags=["Succeeded", "Test failed:"])
        return gpio_18

    def GPIO_23_script(self, timeout=None):
        timeout = self.config.get("gpio_som_script_timeout", 11)
        gpio_23 = self.singleCommand("sudo ./PI_Tests/test_pin_23.sh", timeout, end_flags=["Succeeded", "Test failed:"])
        return gpio_23

    def GPIO_24_script(self, timeout=None):
        timeout = self.config.get("gpio_som_script_timeout", 11)
        gpio_24 = self.singleCommand("sudo ./PI_Tests/test_pin_24.sh", timeout, end_flags=["Succeeded", "Test failed:"])
        return gpio_24

    def GPIO_25_script(self, timeout=None):
        timeout = self.config.get("gpio_som_script_timeout", 11)
        gpio_25 = self.singleCommand("sudo ./PI_Tests/test_pin_25.sh", timeout, end_flags=["Succeeded", "Test failed:"])
        return gpio_25

    def dut_provisioning(self, serial_number, **kwargs):
        dut_provision_try_nb= self.config.get("dut_provision_try_nb", 2)
        timeout = self.config.get("pod_provision_delay",30)
        command = "sudo ./pod_provision.sh " + serial_number

        while dut_provision_try_nb>0:
            provisioning = self.singleCommand(command, timeout, end_flags=[fr"Pod\s*provision\s*script\s*finished\s*with\s*Serial\s*{serial_number}\s*", r"pego@CM4:~$", r"pego@CM4:~\$$", r"pego@p[0-9]{15}:~$\$"]) #"## Run TPM Provision with serialnumber ##"
            if re.search(fr"Pod\s*provision\s*script\s*finished\s*with\s*Serial\s*{serial_number}\s*", provisioning):
                self.logger.info("Provision OK")
                return provisioning
            time.sleep(0.2)
            dut_provision_try_nb -=1

        return provisioning

    def test_LED_RJ45(self, **kwargs):
        timeout = self.config.get("test_leds_timeout", 1)
        test_leds = self.singleCommand("sudo ./PI_Tests/LEDs_test.sh", timeout)
        return test_leds

    def cm4_ID_script(self, timeout=None):
        timeout = self.config.get("CM4_ID_script_timeout", 5)
        try_nb = self.config.get("CM4_ID_script_try_nb", 3)
        #cm4_id = self.singleCommand("sudo ./CM4_ID_script.sh", timeout=1, try_nb=1, end_flags=["pego@p[0-9]{15}:~$", r"pego@p[0-9]{15}:~\$$", r"Cam_serial\s*:\s*([0-9]{8})"])
        command = "sudo ./CM4_ID_script.sh"
        read_retry = self.config.get("read_cm4_id_nb_retry", 10)
        while try_nb > 0:
            self.logger.debug(f"TX: {command}")
            self.port.write(f"{command}\n".encode())
            try:
                is_empty = True
                while is_empty or read_retry>0:
                    cm4_id = self.read_lines(timeout=timeout, end_flags=[r"pego@CM4:~$", r"pego@CM4:~\$$", r"Cam_serial\s*:\s*([0-9]{8})"])
                    try:
                        if len(cm4_id) >1:
                            data= ""
                            for e in cm4_id:
                                data = data+str(e)
                            cm4_id[0] = data
                        if len(cm4_id) >0 and re.search(r"Cam_serial\s*:\s*([0-9]{8})", cm4_id[0]):
                            is_empty = False
                            break
                    except:
                        pass
                    read_retry-=1

                cm4_id = cm4_id[0]

                cpu_serial = (re.search(r"CPU_Serial\s*:\s*([0-9a-fA-F]{16})", cm4_id)).group(1)
                ethernet_mac_address = (re.search(r"Ethermet_mac_address\s*:\s*([0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2})", cm4_id)).group(1)
                try:
                    wifi_mac_address = (re.search(r"Wifi_mac_address\s*:\s*([0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2})", cm4_id)).group(1)
                except:
                    wifi_mac_address = (re.search(r"Wifi_mac_address\s*:\s*(WIFI NOT ENABLED)", cm4_id)).group(1)
                registration_id = (re.search(r"Registration_ID\s*:\s*([0-9a-zA-Z]{52})", cm4_id)).group(1)
                endorsment_key = (re.search(r"Endorsment_Key\s*:\s*(.{424})", cm4_id)).group(1)
                cam_serial = (re.search(r"Cam_serial\s*:\s*([0-9]{8})", cm4_id)).group(1)
                self.logger.info(f"CM4 values returned :{cm4_id}")
                return cm4_id
            except Exception as ex:
                self.logger.error(ex)
            self.logger.warning("Retry CM4_ID_script")
            try_nb -= 1
        return cm4_id

    def cancel_script(self,try_nb=None, timeout=None):
        canceled = self.singleCommand('b\x03', timeout=1, try_nb=try_nb, end_flags=[r"^C$", r"pego@CM4:~"])
        return canceled


    def shutdown_CM4(self):
        timeout = self.config.get("timeout_shutdown_command", 1)
        self.singleCommand("sudo shutdown now", timeout)

        time.sleep(self.config.get("delay_after_shutdown", 6))
        return True

    def get_CM4_state(self):
        return self.cm4_is_boot

    def connectSOM(self, exit_signal=lambda: False, stop_cm4_thread=lambda: False):
        """if not self.port.isOpen():
            self.port.open()"""
        self.logger.info("Boot CM4...")
        def bootCM4(self, exit_signal, stop_cm4_thread):
            #default-> login: pi | password: raspberry
            output = []
            enter_login= False

            result= ""
            while not (enter_login or stop_cm4_thread()):
                output=output + self.read_lines(timeout=1, end_flags=["CM4 login:", r"p[0-9]{15} login:"])
                for line in output:
                    result = result + line
                if re.search("CM4 login:", result):
                    enter_login = True
                    break
                if re.search(r"p[0-9]{15} login:", result):
                    enter_login = True
                    break
                if re.search("Kernel panic:", result):
                    self.logger.error("Kernal panic detected ! The SOM will reboot")
                    break
                if stop_cm4_thread():
                    return

            if not enter_login:
                return

            time.sleep(0.1)
            if stop_cm4_thread():
                return
            self.singleCommand("pego", timeout=1, end_flags=["Password:"])
            if stop_cm4_thread():
                return
            time.sleep(1.2)
            self.singleCommand_withoutLogs("GTa@XiM7_v$KR9a^Bs63YAz2", timeout=2, end_flags=[r"pego@CM4"]) #this password must never be seen by anyone
            if stop_cm4_thread():
                return
            time.sleep(1.2)
            output_lines = self.singleCommand("ls -l", timeout=1, end_flags=["CM4_ID_script.sh",r"pego@CM4:~"])
            if stop_cm4_thread():
                return
            if not bool(re.search("CM4_ID_script.sh", output_lines)):
                self.logger.error("Fail to initiated SOM")
                self.logger.error(f"ls -l output: {output}")
                self.cm4_is_boot = False
                return
            self.cm4_is_boot = True
            self.logger.info("CM4 well boot")

        self.cm4_is_boot=False
        t = threading.Thread(name="CommCM4_Thread", target=bootCM4, args=(self, exit_signal, stop_cm4_thread))
        t.start()

        return t