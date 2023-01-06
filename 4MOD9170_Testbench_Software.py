import json
import logging
import sys
import threading
import traceback
import tkinter as tk
import tkinter.ttk as ttk
import os
import datetime
import tkinter.scrolledtext
import tkinter.simpledialog
import queue
import requests
import time
import enum
import collections
import subprocess
import pyautogui
from tkinter import messagebox
from blabel import LabelWriter
import psutil
import re

import io_board
import comm_test_CM4
import comm_test_STM
import dpm802_1
import dpm802_2
import dpm802_3_voltmeter
import tests_recorder

TEST_SW_VERSION = "0.0.7_f"


def get_sub_config(name, config={}):
    sub_config = config.get(name, {})
    cfg = config.copy()
    if cfg.get(name, {}):
        del cfg[name]
    return (cfg | sub_config)


class ScrollableFrame(tk.Frame):
    def __init__(self, container, *args, **kwargs):
        super().__init__(container, *args, **kwargs)
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        canvas = tk.Canvas(self)
        scrollbar = tk.Scrollbar(
            self, orient="vertical", command=canvas.yview)
        self.scrollable_frame = tk.Frame(canvas)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(
                scrollregion=canvas.bbox("all"),
                width=self.scrollable_frame.bbox()[2]
            )
        )

        canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.grid(row=0, column=0, sticky=(tk.N + tk.E + tk.W + tk.S))
        scrollbar.grid(row=0, column=1, sticky=(tk.N + tk.E + tk.W + tk.S))

        canvas.bind("<Enter>", lambda e: canvas.bind_all(
            "<MouseWheel>", lambda e: canvas.yview_scroll(-int(e.delta / 120), "units")))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))


class ScrolledTextLoggingQueueHandler(logging.Handler):
    # https://docs.python.org/3/library/logging.html#logging-levels
    LOGGING_LEVEL_BIGGER = 24
    LOGGING_LEVEL_TEST_RESULT_SUCCESS = 25
    LOGGING_LEVEL_TEST_RESULT_FAILURE = 26
    logging.addLevelName(LOGGING_LEVEL_BIGGER, "BIGGER")
    logging.addLevelName(LOGGING_LEVEL_TEST_RESULT_SUCCESS,
                         "TEST_RESULT_SUCCESS")
    logging.addLevelName(LOGGING_LEVEL_TEST_RESULT_FAILURE,
                         "TEST_RESULT_FAILURE")

    def __init__(self, st, config={}):
        super().__init__()

        self.config = config
        self.st = st
        self.log_queue = queue.Queue()

        self.window = st
        while self.window.master is not None:
            self.window = self.window.master

        self.window.after(50, self.poll_log_queue)

    def emit(self, record):
        self.log_queue.put(record)

    def poll_log_queue(self):
        try:
            record = self.log_queue.get(block=False)
            scroll = self.st.bbox("end-1c") is not None
            self.st.configure(state=tk.NORMAL)
            try:
                while True:
                    self.st.insert(tk.END, self.format(
                        record) + "\n", record.levelname)
                    record = self.log_queue.get(block=False)
            except queue.Empty:
                pass
            finally:
                max_lines = self.config.get("gui_logs_max_lines", 2500)
                if max_lines:
                    current_lines = self.st.count(
                        "1.0", self.st.index(tk.END), "lines")[0]
                    if current_lines > max_lines:
                        self.st.delete(
                            "1.0", f"{current_lines - max_lines}.end")

                self.st.update_idletasks()
                self.st.configure(state=tk.DISABLED)
                if scroll or self.config.get("force_logs_autoscroll", False):
                    self.st.yview(tk.END)
                    self.st.update_idletasks()
        except queue.Empty:
            pass
        finally:
            self.window.after(50, self.poll_log_queue)

    def clear_logs(self):
        self.st.configure(state=tk.NORMAL)
        self.st.delete("1.0", tk.END)
        self.st.configure(state=tk.DISABLED)

class LinuxCommands():
    class LinuxCommandException(Exception):
        def __init__(self, msg):
            super().__init__(msg)

    def __init__(self, config, logger= None):
        self.config = config
        self.logger = logger

    def kill_process(self,pid):
        password = self.config.get("user_password", "TestTest")
        cmd1 = subprocess.Popen(['echo',password], stdout=subprocess.PIPE)
        cmd2 = subprocess.Popen(['sudo','-S'] + f"kill -9 {pid}".split(), stdin=cmd1.stdout, stdout=subprocess.PIPE)


        out = []
        while cmd2.poll() is None:
            l = cmd2.stdout.readlines()
            if l:
                for sd in l:
                    sd = sd.strip().decode("utf-8")
                    self.logger.debug(sd)
                    out.append(sd)
        self.logger.debug(out)
        return True

    def kill_oldest(self, pid_list):
        own_pid = os.getpid()
        self.logger.info(f"Own PID: {own_pid}")
        self.logger.info(pid_list)

        if not len(pid_list)>2:
            return True

        pid_list.remove(own_pid)
        pid_list.remove(min(pid_list, key=lambda x:abs(x-own_pid))) #To remove the closest pid from OWN_PID. 
                                                                    #As pyinstaller use 2 process to runn software, it's require to keep alive the own_pid process and the second process : the closest to the own_pid

        for pid in pid_list:
            if pid == own_pid:
                continue
            self.logger.info(f"Kill process: {pid}")
            self.kill_process(pid)
            time.sleep(0.1)

        return True

    def check_process(self):
        cmd2 = subprocess.Popen(["ps -aux | grep 4MOD9170"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, shell=True)

        out = []
        while cmd2.poll() is None:
            l = cmd2.stdout.readlines()
            if l:
                for sd in l:
                    sd = sd.strip().decode("utf-8")
                    self.logger.debug(sd)
                    out.append(sd)

        testbench_process = []
        pattern = re.compile(r'^\S+ +(\d+) +\S+ +\S+ +\S+ +\S+ +\S+ +\S+ +\S+ +\S+ +(\S+)')
        for p in out:
            m = pattern.search(p)
            if m is None:
                continue
            if "4MOD9170_Testbench_Software_V" in m.group(2):
                testbench_process.append(int(m.group(1)))
                continue

        return testbench_process

class STM32_Flasher():
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger

    def flash(self, hex_file):
        timeout = self.config.get("flash_timeout_delay", 25)

        stlink_log = datetime.datetime.now().strftime("./logs/STLink_logs/%Y%m%d_%H%M%S_STLink_Flash.log")
        p = subprocess.Popen([self.config.get("stm_cube_programmer_path", r"/home/test4mod9170/STMicroelectronics/STM32Cube/STM32CubeProgrammer/bin/STM32_Programmer_CLI"),
                              *(f"-log {stlink_log} -q -c port=SWD mode=UR  freq=4000 -rdu -e all -d {hex_file} 0x08000000 -v".split())],
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE,
                             stdin=subprocess.DEVNULL,
                             text=True)

        def set_timeout(self, p):
            for _ in range(timeout):
                if p.poll() is not None:
                    break
                time.sleep(1)
            else:
                self.logger.error("Timeout raised !")
                pr = psutil.Process(p.pid)
                for child in pr.children(recursive=True):
                    child.kill()
                pr.kill()

        out = []
        th = threading.Thread(name="Timeout_ti_thread", target=set_timeout, args=(self, p))
        th.start()

        out = []
        while p.poll() is None:
            l = p.stdout.readline()
            if l:
                l = l.strip()
                if l:
                    self.logger.debug(l)
                    out.append(l)
        self.logger.info(p.returncode)

        return ("Download verified successfully" in "\n".join(out))
    
    def read_memory_protection(self):
        stlink_log = datetime.datetime.now().strftime("./logs/STLink_logs/%Y%m%d_%H%M%S_STLink_RMP.log")
        p = subprocess.Popen([self.config.get("stm_cube_programmer_path", r"/home/test4mod9170/STMicroelectronics/STM32Cube/STM32CubeProgrammer/bin/STM32_Programmer_CLI"),
                              *(f"-log {stlink_log} -q -c port=SWD mode=UR freq=4000 -ob rdp=0x1".split())],
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE,
                             stdin=subprocess.DEVNULL,
                             text=True)

        out = []
        while p.poll() is None:
            l = p.stdout.readline()
            if l:
                l = l.strip()
                if l:
                    self.logger.debug(l)
                    out.append(l)

        return ("Option Bytes successfully programmed" in "\n".join(out))

    def readID(self):
        stlink_log = datetime.datetime.now().strftime(
            "./logs/ID_Logs/%Y%m%d_%H%M%S_STLink_readID.log")
        p = subprocess.Popen([self.config.get("stm_cube_programmer_path", r"/home/test4mod9170/STMicroelectronics/STM32Cube/STM32CubeProgrammer/bin/STM32_Programmer_CLI"),
                              *(f"-log {stlink_log} -c port=SWD mode=UR freq=4000 -rdu -r32 0x1FFF7A10 0x10".split())],
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE,
                             stdin=subprocess.DEVNULL,
                             text=True)

        out = []
        while p.poll() is None:
            l = p.stdout.readline()
            if l:
                l = l.strip()
                if l:
                    self.logger.debug(l)
                    out.append(l)
        time.sleep(0.2)
        with open(f"{stlink_log}", "r", encoding='utf-8', errors='ignore') as fd:
            for line in fd:
                #match = re.search(r"""(?m)^[0-9]{2}:[0-9]{2}:[0-9]{2}:[0-9]{3}\s0x1FFF7A10\s*:\s*([A-F0-9]{8})\s*([A-F0-9]{8})\s([A-F0-9]{8})\s*([A-F0-9]{8})\s*
                 #                         ^[0-9]{2}:[0-9]{2}:[0-9]{2}:[0-9]{3}\s0x1FFF7A20\s*:\s*([A-F0-9]{8})\s*([A-F0-9]{8})\s([A-F0-9]{8})\s*([A-F0-9]{8})\s*
                #                          ^[0-9]{2}:[0-9]{2}:[0-9]{2}:[0-9]{3}\s0x1FFF7A30\s*:\s*([A-F0-9]{8})\s*([A-F0-9]{8})\s([A-F0-9]{8})\s*([A-F0-9]{8})\s*$""", line)

                match = re.search(r"^[0-9]{2}:[0-9]{2}:[0-9]{2}:[0-9]{3}\s0x1FFF7A10\s:\s([A-F0-9]{8})\s([A-F0-9]{8})\s([A-F0-9]{8})\s*([A-F0-9]{8})\s*$", line)

                if match:
                    uid1 = match.group(1)
                    uid2 = match.group(2)
                    uid3 = match.group(3)
                    uid4 = match.group(4)
                    self.logger.info(f"UID : {uid1} {uid2} {uid3} {uid4}")
                    fd.close()
                    return (uid1 + uid2 + uid3 + uid4)
        fd.close()
        self.logger.error("Couldn't find UID-96 !")
        return False


class Tester():
    class TesterException(Exception):
        def __init__(self, msg):
            super().__init__(msg)

    class ProductModel(enum.Enum):
        BASE = 0

    class Test(enum.Enum):
        POWER_SUPPLY = 0
        READ_ID = enum.auto()
        FLASH_ST = enum.auto()
        INIT_SOM = enum.auto()
        HEAT_SENSOR = enum.auto()
        TOF_SENSOR = enum.auto()
        DAC_I2C = enum.auto()
        CHECK_WINDOW = enum.auto()
        RADAR_STATUS_CLEAR = enum.auto()
        RADAR_STATUS_MOVEMENT = enum.auto()
        EXTERNAL_IO = enum.auto()
        SOM_TO_ST_CONNEXION = enum.auto()
        ST_TO_SOM_CONNEXION = enum.auto()
        FLASH_FINAL_FIRMWARE = enum.auto()
        TAKE_PICTURE = enum.auto()
        GET_SECURITY_KEYS = enum.auto()
        POD_PROVISION = enum.auto()
        SEND_KEYS = enum.auto()
        PRINT_LABEL = enum.auto()

    TEST_PER_MODEL = {
        ProductModel.BASE: [
            Test.POWER_SUPPLY,
            Test.READ_ID,
            Test.FLASH_ST,
            Test.INIT_SOM,
            Test.HEAT_SENSOR,
            Test.TOF_SENSOR,
            Test.DAC_I2C,
            Test.CHECK_WINDOW,
            Test.RADAR_STATUS_CLEAR,
            Test.RADAR_STATUS_MOVEMENT,
            Test.EXTERNAL_IO,
            Test.SOM_TO_ST_CONNEXION,
            Test.ST_TO_SOM_CONNEXION,
            Test.FLASH_FINAL_FIRMWARE,
            Test.TAKE_PICTURE,
            Test.GET_SECURITY_KEYS,
            Test.POD_PROVISION,
            Test.SEND_KEYS,
            Test.PRINT_LABEL,
        ]
    }


    def __init__(self, testbench, config, logger=None, cm4logger = None):
        self.config = config
        self.logger = logger
        self.cm4logger = cm4logger
        self.testbench = testbench
        if self.logger is None:
            self.logger = logging.getLogger(__name__)
        self.exit = False
        self.stop_cm4Thread = False

    def initialize(self):
        self.io_board = io_board.IOBoard(port=self.config.get("io_board_port", None), config=self.config, logger=self.logger)
        self.logger.debug("IO Board Init done !")

        self.stm32_flasher = STM32_Flasher(config=self.config, logger=self.logger)
        self.logger.debug("STM32_Flasher Init done !")

        self.comm_test_CM4 = comm_test_CM4.CommTest(port=self.config.get("comm_test_CM4_port", None), config=self.config, logger=self.cm4logger)
        self.comm_test_STM = comm_test_STM.CommTest(port=self.config.get("comm_test_STM_port", None), config=self.config, logger=self.logger)

        if not self.config.get("tests_recorder_disabled", False):
            self.tests_recorder = tests_recorder.TestsRecorder(recording_file_path=self.config.get("recording_file_path", "automatic"), recording_server_url=self.config.get(
                "recording_server_url", "https://pego-pod-api.azurewebsites.net/Pod/AddPods"), config=self.config, logger=self.logger)

        """self.dpm802_ampermeter_1 = dpm802_1.DPM802_1(port=self.config.get("dpm802_1_port", None), config=self.config, logger=self.logger)
        self.dpm802_ampermeter_2 = dpm802_2.DPM802_2(port=self.config.get("dpm802_2_port", None), config=self.config, logger=self.logger)"""
        self.dpm802_voltmeter = dpm802_3_voltmeter.DPM802_VOLTMETER(port=self.config.get("dpm802_3_voltmeter_port", None), config=self.config, logger=self.logger)


        self.logger.debug(f"IOBoard port : {self.io_board.port_name()}")
        self.logger.debug(f"IOBoard version : {self.io_board.read_version()}")
        self.logger.debug(f"IOBoard ID : {self.io_board.read_id()}")

        self.io_board.reset()
        time.sleep(0.5)

        if not self.reset_jig():
            raise Tester.TesterException("Coudn't reset jig !")   # it make stop the dpm802

        """try:
            self.dpm802_1.read_measure()
        except TimeoutError as ex:
            self.io_board.toggle_dpm802_1()
            time.sleep(0.5)
            self.dpm802_1.read_measure()

        try:
            self.dpm802_2.read_measure()
        except TimeoutError as ex:
            self.io_board.toggle_dpm802_2()
            time.sleep(0.5)
            self.dpm802_2.read_measure()"""

        try:
            self.dpm802_voltmeter.read_measure()
        except TimeoutError as ex:
            self.io_board.toggle_dpm802_voltmeter_rs232()
            time.sleep(0.5)
            self.dpm802_voltmeter.read_measure()

        #self.set_dpm802_1_function(dpm802_1.DPM802_1.Function.CURRENT_MA)
        #self.set_dpm802_2_function(dpm802_2.DPM802_2.Function.CURRENT_MA)
        return True

    #start test region
    def POWER_SUPPLY_perform(self, context, exit_signal):
        #Apply power supply on the PoE connector and check the 5V, 3V3 and 3V3_RADAR
        measures = []
        self.logger.info("Enable POE...")
        self.io_board.write_gpio(io_board.IOBoard.GPIO.EN_POWER_POE, 1)
        time.sleep(4)

        self.io_board.write_gpios([
            (io_board.IOBoard.GPIO.SELECT_PAIR_VOLT_MEAS, 0),
            (io_board.IOBoard.GPIO.SELECT_TARGET_VOLT_MEAS, 0), #set to 0 in order to measure the voltages
            (io_board.IOBoard.GPIO.EN_SWA, 1)
        ])

        #Check 5V : TP18
        self.logger.info("Measure 5V TP18")
        self.io_board.write_gpios([
            (io_board.IOBoard.GPIO.A0_SWA, 1),
            (io_board.IOBoard.GPIO.A1_SWA, 0),
            (io_board.IOBoard.GPIO.A2_SWA, 0)
        ])
        time.sleep(0.1)
        measures.append(self.voltage_measure_average(self.config.get("voltage_measure_duration", 1.5)))

        #Check 3V3 : TP11
        self.logger.info("Measure 3V3 TP11")
        self.io_board.write_gpios([
            (io_board.IOBoard.GPIO.A0_SWA, 1),
            (io_board.IOBoard.GPIO.A1_SWA, 1),
            (io_board.IOBoard.GPIO.A2_SWA, 0)
        ])
        time.sleep(0.1)
        measures.append(self.voltage_measure_average(self.config.get("voltage_measure_duration", 1)))

        #Check 3V3_RADAR : TP17
        self.logger.info("Measure 3V3_RADAR TP17")
        self.io_board.write_gpios([
            (io_board.IOBoard.GPIO.A0_SWA, 0),
            (io_board.IOBoard.GPIO.A1_SWA, 1),
            (io_board.IOBoard.GPIO.A2_SWA, 0)
        ])
        time.sleep(0.1)
        measures.append(self.voltage_measure_average(self.config.get("voltage_measure_duration", 1)))


        self.io_board.write_gpios([
            (io_board.IOBoard.GPIO.SELECT_TARGET_VOLT_MEAS, 0),
            (io_board.IOBoard.GPIO.EN_SWA, 0),
            (io_board.IOBoard.GPIO.EN_POWER_POE, 0)
        ])

        voltage = {"5V": measures[0], "3V3": measures[1], "3V3_RADAR": measures[2]}
        return voltage

    def POWER_SUPPLY_check(self, online_test_results, context,  exit_signal, result=None):
        power_supplyConfig = self.config.get("POWER_SUPPLY",{})
        correct_voltage = True

        if result is None:
            self.logger.error("no measure received")
            return False

        self.logger.info(f'5V measured :{result.get("5V")}')
        if not (power_supplyConfig.get("5V_min", 4.75) < result.get("5V") < power_supplyConfig.get("5V_max", 5.25)):
            self.logger.error("Wrong voltage for 5V !")
            correct_voltage = False

        self.logger.info(f'3V3 measured : {result.get("3V3")}')
        if not (power_supplyConfig.get("3V3_min", 3.1) < result.get("3V3") < power_supplyConfig.get("3V3_max", 3.5)):
            self.logger.error("Wrong voltage for 3V3 !")
            correct_voltage = False

        self.logger.info(f'3V3_RADAR measured :{result.get("3V3_RADAR")}')
        if not (power_supplyConfig.get("3V3_RADAR_min", 3.135) < result.get("3V3_RADAR") < power_supplyConfig.get("3V3_RADAR_max", 3.465)):
            self.logger.error("Wrong voltage for 3V3 !")
            correct_voltage = False

        return correct_voltage


    def READ_ID_perform(self, context, exit_signal):
        self.io_board.write_gpio(io_board.IOBoard.GPIO.EN_POWER_POE, 1)
        self.io_board.write_gpio(io_board.IOBoard.GPIO.EN_PROG_STM, 1)
        time.sleep(2)

        uid = self.stm32_flasher.readID()
        self.logger.info(f"UID : {uid}")
        context["UID"] = uid

        return uid if len(uid)==32 else False


    def FLASH_ST_perform(self, context, exit_signal):
        #Program the STM32
        time.sleep(1)
        flash_stm = self.stm32_flasher.flash(self.config.get("stm32_firmware", "09_4DMOD_v2.hex"))
        self.io_board.write_gpio(io_board.IOBoard.GPIO.EN_PROG_STM, 0)

        self.io_board.write_gpio(io_board.IOBoard.GPIO.EN_POWER_POE, 0)
        time.sleep(4)
        self.io_board.write_gpio(io_board.IOBoard.GPIO.EN_POWER_POE, 1)
        time.sleep(3)

        return flash_stm

    def INIT_SOM_perform(self, context, exit_signal):
        self.io_board.write_gpio(io_board.IOBoard.GPIO.EN_POWER_POE, 1)

        self.io_board.write_gpios([
            (io_board.IOBoard.GPIO.EN_BOOT_CM4, 0),
            (io_board.IOBoard.GPIO.EN_UART_CM4, 1)
        ])

        t = self.comm_test_CM4.connectSOM(self.exit_signal, self.stop_cm4_thread)
        context["cm4_thread"] = t
        def thread_timeout(self, t, timeout):
                now = time.time()
                timeout = (now+timeout)
                while now < timeout:
                    if not t.is_alive():
                        return
                    time.sleep(0.1)
                    now = time.time()
                self.stop_cm4Thread = True
                return False

        timeout = self.config.get("cm4_thread_timeout", 70)
        to = threading.Thread(name="cm4_thread_timeout", target=thread_timeout, args=(self, t, timeout))
        to.start()
        context["timeout_cm4_thread"]=to
        return True

    def HEAT_SENSOR_perform(self, context, exit_signal):
        #Soft ask the GRID EYE satus - Answer is YES or a number of BAD GRID EYE
        self.io_board.write_gpio(io_board.IOBoard.GPIO.EN_UART_STM32, 1)
        time.sleep(0.1)

        heat1 = self.comm_test_STM.heat_1()
        heat3 = self.comm_test_STM.heat_3()

        if self.config.get("pego_pod_version", "MAX") == "MAX":
            heat2 = self.comm_test_STM.heat_2()
            heat4 = self.comm_test_STM.heat_4()
            result = {"HEAT_1":heat1, "HEAT_2":heat2, "HEAT_3":heat3,"HEAT_4":heat4}
        else:
            self.logger.info("MINI version, skipping Heat 2 & 4")
            result = {"HEAT_1":heat1, "HEAT_3":heat3}

        self.logger.info(result)
        self.io_board.write_gpio(io_board.IOBoard.GPIO.EN_UART_STM32, 0)
        return result

    def HEAT_SENSOR_check(self, online_test_results, context, exit_signal, result=None):
        if result is None:
            self.logger.error("no data received")
            return False

        sensorsOK = True

        if not result.get("HEAT_1").data:
            self.logger.error(f"HEAT_1 fail : {result.get('HEAT_1')}")
            self.testbench.gui.sensor_result_set("heat1", False)
            sensorsOK = False
        else:
            self.logger.info("HEAT_1 OK")
            self.testbench.gui.sensor_result_set("heat1", True)

        if self.config.get("pego_pod_version", "MAX") == "MAX":
            if not result.get("HEAT_2").data:
                self.logger.error(f"HEAT_2 fail : {result.get('HEAT_2')}")
                self.testbench.gui.sensor_result_set("heat2", False)
                sensorsOK = False
            else:
                self.logger.info("HEAT_2 OK")
                self.testbench.gui.sensor_result_set("heat2", True)
        else:
            self.logger.info("Pod MINI, skipping check HEAT_2")

        if not result.get("HEAT_3").data:
            self.logger.error(f"HEAT_3 fail : {result.get('HEAT_3')}")
            self.testbench.gui.sensor_result_set("heat3", False)
            sensorsOK = False
        else:
            self.logger.info("HEAT_3 OK")
            self.testbench.gui.sensor_result_set("heat3", True)

        if self.config.get("pego_pod_version", "MAX") == "MAX":
            if not result.get("HEAT_4").data:
                self.logger.error(f"HEAT_4 fail : {result.get('HEAT_4')}")
                self.testbench.gui.sensor_result_set("heat4", False)
                sensorsOK = False
            else:
                self.logger.info("HEAT_4 OK")
                self.testbench.gui.sensor_result_set("heat4", True)
        else:
            self.logger.info("Pod MINI, skipping check Heat_4")

        return sensorsOK

    def TOF_SENSOR_perform(self, context, exit_signal):
        #Soft ask ST sensor status
        self.io_board.write_gpio(io_board.IOBoard.GPIO.EN_UART_STM32, 1)
        time.sleep(0.2)

        tof1 = self.comm_test_STM.tof_1()
        tof2 = self.comm_test_STM.tof_2()
        tof3 = self.comm_test_STM.tof_3()
        tof4 = self.comm_test_STM.tof_4()

        result = {"TOF_1":tof1, "TOF_2":tof2, "TOF_3":tof3,"TOF_4":tof4}
        self.logger.info(result)
        self.io_board.write_gpio(io_board.IOBoard.GPIO.EN_UART_STM32, 0)
        return result

    def TOF_SENSOR_check(self, online_test_results, context, exit_signal, result=None):
        if result is None:
            self.logger.error("no data received")
            return False

        sensorsOK = True

        if not result.get("TOF_1").data:
            self.logger.error(f"TOF_1 fail : {result.get('TOF_1')}")
            self.testbench.gui.sensor_result_set("tof1", False)
            sensorsOK = False
        else:
            self.logger.info("TOF_1 OK")
            self.testbench.gui.sensor_result_set("tof1", True)

        if not result.get("TOF_2").data:
            self.logger.error(f"TOF_2 fail : {result.get('TOF_2')}")
            self.testbench.gui.sensor_result_set("tof2", False)
            sensorsOK = False
        else:
            self.logger.info("TOF_2 OK")
            self.testbench.gui.sensor_result_set("tof2", True)

        if not result.get("TOF_3").data:
            self.logger.error(f"TOF_3 fail : {result.get('TOF_3')}")
            self.testbench.gui.sensor_result_set("tof3", False)
            sensorsOK = False
        else:
            self.logger.info("TOF_3 OK")
            self.testbench.gui.sensor_result_set("tof3", True)

        if not result.get("TOF_4").data:
            self.logger.error(f"TOF_4 fail : {result.get('TOF_4')}")
            self.testbench.gui.sensor_result_set("tof4", False)
            sensorsOK = False
        else:
            self.logger.info("TOF_4 OK")
            self.testbench.gui.sensor_result_set("tof4", True)

        return sensorsOK

    def DAC_I2C_perform(self, context, exit_signal):
        #DACs check if I2C module are OK
        self.io_board.write_gpio(io_board.IOBoard.GPIO.EN_UART_STM32, 1)
        time.sleep(0.1)

        dac1 = self.comm_test_STM.DAC1()
        self.logger.info(f"DAC1 : {dac1}")

        dac2 = self.comm_test_STM.DAC2()
        self.logger.info(f"DAC2 : {dac2}")

        self.io_board.write_gpio(io_board.IOBoard.GPIO.EN_UART_STM32, 0)
        return [dac1, dac2]

    def DAC_I2C_check(self, online_test_results, context, exit_signal, result = None):
        if result is None:
            self.logger.error("no data received")
            return False

        test_pass = True
        for comm_result in result:
            self.logger.debug(comm_result)
            if not comm_result.data:
                self.logger.error(f"{comm_result} KO")
                test_pass = False
            self.logger.info(f"{comm_result} OK")
        return test_pass

    def CHECK_WINDOW_perform(self, context, exit_signal):
        #Operator check that the windows is clear(we can see through)
        self.io_board.write_gpio(io_board.IOBoard.GPIO.EN_UART_STM32, 1)
        time.sleep(0.1)

        self.comm_test_STM.shutter_ON()
        time.sleep(0.2)

        msg = messagebox.askyesno(f"Check shutter", "Is the shutter toggle between white and transparent ?")
        self.testbench.gui.window_gui_result(msg)
        self.comm_test_STM.shutter_OFF()
        self.io_board.write_gpio(io_board.IOBoard.GPIO.EN_UART_STM32, 0)
        return msg

    def RADAR_STATUS_CLEAR_perform(self, context, exit_signal):
        #Soft ask RADAR status while a metallic plate cover it
        self.logger.info("Setting plate in front of Radar...")
        self.io_board.write_gpio(io_board.IOBoard.GPIO.EN_UART_STM32, 1)
        self.io_board.write_gpio(io_board.IOBoard.GPIO.CMD_EL, 1)
        time.sleep(0.2)

        self.logger.info("Perform Square Wave test")
        square_wave = self.comm_test_STM.radar_SquareWave()

        self.logger.info("Performing Noise Test")
        noise_test = self.comm_test_STM.radar_Noise()

        self.logger.info("Performing No Motion Test")
        noMotion = self.comm_test_STM.radar_NoMotion()

        test_result = {"SquareWave":square_wave, "Noise":noise_test, "NoMotion":noMotion}

        self.io_board.write_gpio(io_board.IOBoard.GPIO.CMD_EL, 0)
        self.io_board.write_gpio(io_board.IOBoard.GPIO.EN_UART_STM32, 0)
        return test_result

    def RADAR_STATUS_CLEAR_check(self, online_test_results, context, exit_signal, result=None):
        if result is None:
            self.logger.error("no data received")
            return False

        self.logger.info(f"Radar result: {result}")
        #if result.get("SquareWave").data and result.get("Noise").data and result.get("NoMotion").data:
        if result.get("NoMotion").data:
            return True
        else:
            self.testbench.gui.radar_gui_result(False)
            return False

    def RADAR_STATUS_MOVEMENT_perform(self, context, exit_signal):
        #Soft ask RADAR status while operator wave his hand
        self.logger.info("Setting plate in front of Radar...")
        self.io_board.write_gpio(io_board.IOBoard.GPIO.EN_UART_STM32, 1)
        time.sleep(0.2)
        timeout = self.config.get("radar_Motion_timeout_delay", 15)
        test_onGoing = True

        def ev_on_off(self, bool):
            state = 1
            toggle = 1
            for _ in range(timeout):
                if not test_onGoing:
                    break
                time.sleep(0.5)
                toggle = state - toggle
                self.io_board.write_gpio(io_board.IOBoard.GPIO.CMD_EL, toggle)
            else:
                self.logger.error("Timeout raised for radar motion !")

        if self.config.get("use_handwave_for_radar_movement", False):
            messagebox.showinfo("Radar Motion", "Wave a hand in front of the radar")
        else:
            th = threading.Thread(name="toggle_EV_thread", target=ev_on_off, args=(self, True))
            th.start()
        result = self.comm_test_STM.radar_Motion()
        test_onGoing = False
        if not self.config.get("use_handwave_for_radar_movement", False):
            if th.is_alive():
                th.join()

        self.io_board.write_gpio(io_board.IOBoard.GPIO.EN_UART_STM32, 0)
        return result

    def RADAR_STATUS_MOVEMENT_check(self, online_test_results, context, exit_signal, result=None):
        if result is None:
            self.logger.error("no data received")
            return False

        if result.data:
            self.testbench.gui.radar_gui_result(True)
            self.logger.debug("Radar Motion OK")
            return True

        self.testbench.gui.radar_gui_result(False)
        self.logger.error(f"Radar Motion KO : {result}")
        return False

    def EXTERNAL_IO_perform(self, context, exit_signal):
        #Check external IO connector -The 6 values should be 5V by default = State OFF | ask STM to Light UP LED -> read led state, must be 0V = State ON
        self.logger.info("Enable 5V RJ45...")
        self.io_board.write_gpios([
            (io_board.IOBoard.GPIO.EN_5V_RJ45IO, 1),
            (io_board.IOBoard.GPIO.EN_UART_STM32, 1)
        ])
        time.sleep(0.1)
        self.logger.info("Set GPIOs OFF")
        self.comm_test_STM.set_OFF_GPIOs()
        time.sleep(0.1)

        measures = []
        measures.append(self.io_board.read_gpio(io_board.IOBoard.GPIO.GP0))
        measures.append(self.io_board.read_gpio(io_board.IOBoard.GPIO.GP1))
        measures.append(self.io_board.read_gpio(io_board.IOBoard.GPIO.GP2))
        measures.append(self.io_board.read_gpio(io_board.IOBoard.GPIO.GP3))
        measures.append(self.io_board.read_gpio(io_board.IOBoard.GPIO.GP4))
        measures.append(self.io_board.read_gpio(io_board.IOBoard.GPIO.GP5))
        self.logger.debug(f"measures GPIOs OFF : {measures}")

        self.logger.info("Set GPIOs ON")
        self.comm_test_STM.set_ON_GPIOs()
        time.sleep(0.1)

        measuresON = []
        measuresON.append(self.io_board.read_gpio(io_board.IOBoard.GPIO.GP0))
        measuresON.append(self.io_board.read_gpio(io_board.IOBoard.GPIO.GP1))
        measuresON.append(self.io_board.read_gpio(io_board.IOBoard.GPIO.GP2))
        measuresON.append(self.io_board.read_gpio(io_board.IOBoard.GPIO.GP3))
        measuresON.append(self.io_board.read_gpio(io_board.IOBoard.GPIO.GP4))
        measuresON.append(self.io_board.read_gpio(io_board.IOBoard.GPIO.GP5))
        self.logger.debug(f"measures GPIOs ON : {measuresON}")

        self.comm_test_STM.set_OFF_GPIOs()

        self.io_board.write_gpios([
            (io_board.IOBoard.GPIO.EN_5V_RJ45IO, 0),
            (io_board.IOBoard.GPIO.EN_UART_STM32, 0)
        ])

        result = {"OFF":[measures], "ON":[measuresON]}
        self.logger.info(result)
        return result

    def EXTERNAL_IO_check(self, online_test_results, context, exit_signal, result=None):
        if result is None:
            self.logger.error("No data received !")
            return False

        self.logger.debug("Check GPIOs OFF :")
        for gpioOff in (result.get("OFF")[0]):
            self.logger.debug(f"Check measure OFFF : {gpioOff}")
            if not gpioOff:
                self.logger.error("Get 0 on a gpio OFF")
                return False

        self.logger.debug("Check GPIOs ON :")
        for gpioOff in (result.get("ON")[0]):
            self.logger.debug(f"Check measure ON : {gpioOff}")
            if gpioOff:
                self.logger.error("Get 1 on a gpio ON")
                return False

        return True

    def SOM_TO_ST_CONNEXION_perform(self, context, exit_signal):
        #Soft ask CM4 if connection with the ST is good
        self.io_board.write_gpio(io_board.IOBoard.GPIO.EN_UART_STM32, 1)

        self.boot_SOM(context)
        if self.config.get("test_led_RJ45", False):
            test = self.comm_test_CM4.test_LED_RJ45()
            msg = messagebox.askyesno(f"LEDs Test", "Stop LEDs test ?")
            self.comm_test_CM4.cancel_script()

        self.comm_test_CM4.wdg_stm()
        time.sleep(0.8)
        wdg_STM = self.comm_test_STM.wdg_stm()

        self.comm_test_CM4.cancel_script()
        self.logger.info(f"WDG_STM : {wdg_STM}")
        time.sleep(0.1)


        self.io_board.write_gpio(io_board.IOBoard.GPIO.EN_UART_STM32, 0)
        return wdg_STM

    def SOM_TO_ST_CONNEXION_check(self, online_test_results, context, exit_signal, result=None):
        if result is None:
            self.logger.error("No data provided")
            return False

        self.logger.info(result)
        return result.data

    def ST_TO_SOM_CONNEXION_perform(self, context, exit_signal):
        #Soft ask STM if connection with the SOM is good
        self.io_board.write_gpio(io_board.IOBoard.GPIO.EN_UART_STM32, 1)
        #Soft ask CM4 if connection with the ST is good
        self.boot_SOM(context)

        gpios = {}
        self.logger.info("Check WDG_SOM...")
        self.comm_test_STM.wdg_som_on()
        time.sleep(0.1)
        wdg_som = self.comm_test_CM4.wdg_som()
        gpios["WDG_SOM"] = wdg_som
        self.comm_test_STM.wdg_som_off()

        self.logger.info("Check GPIO_18...")
        self.comm_test_STM.GPIO_18("ON")
        time.sleep(0.1)
        gpio_18 = self.comm_test_CM4.GPIO_18_script()
        gpios["GPIO_18"] = gpio_18
        self.comm_test_STM.GPIO_18("OFF")

        self.logger.info("Check GPIO_23...")
        self.comm_test_STM.GPIO_23("ON")
        time.sleep(0.1)
        gpio_23 = self.comm_test_CM4.GPIO_23_script()
        gpios["GPIO_23"] = gpio_23
        self.comm_test_STM.GPIO_23("OFF")

        self.logger.info("Check GPIO_24...")
        self.comm_test_STM.GPIO_24("ON")
        time.sleep(0.1)
        gpio_24 = self.comm_test_CM4.GPIO_24_script()
        gpios["GPIO_24"] = gpio_24
        self.comm_test_STM.GPIO_24("OFF")

        self.logger.info("Check GPIO_25...")
        self.comm_test_STM.GPIO_25("ON")
        time.sleep(0.1)
        gpio_25 = self.comm_test_CM4.GPIO_25_script()
        gpios["GPIO_25"] = gpio_25
        self.comm_test_STM.GPIO_25("OFF")

        self.io_board.write_gpio(io_board.IOBoard.GPIO.EN_UART_STM32, 0)
        return gpios

    def ST_TO_SOM_CONNEXION_check(self, online_test_results, context, exit_signal, result=None):
        if result is None:
            self.logger.error("No data provided")
            return False

        if not bool(re.search("Succeeded",result.get("WDG_SOM"))):
            self.logger.debug("WDG_SOM failed!")
            return False
        if not bool(re.search("Succeeded", result.get("GPIO_18"))):
            self.logger.debug("GPIO_18 failed!")
            return False
        if not bool(re.search("Succeeded", result.get("GPIO_23"))):
            self.logger.debug("GPIO_23 failed!")
            return False
        if not bool(re.search("Succeeded", result.get("GPIO_24"))):
            self.logger.debug("GPIO_24 failed!")
            return False
        if not bool(re.search("Succeeded", result.get("GPIO_25"))):
            self.logger.debug("GPIO_25 failed!")
            return False
        self.logger.info("All gpio correct")
        return True

    def FLASH_FINAL_FIRMWARE_perform(self, context, exit_signal):
        #Flash the final firmware on ST
        self.io_board.write_gpio(io_board.IOBoard.GPIO.EN_PROG_STM, 1)
        time.sleep(0.1)

        self.logger.info("Flashing final firmware...")
        flash_stm = self.stm32_flasher.flash(self.config.get("stm32_final_firmware", "Final_STM_Software.hex"))
        time.sleep(0.3)
        self.logger.info("Activating ReadMemoryProtection...")
        rmp = self.stm32_flasher.read_memory_protection()

        self.io_board.write_gpio(io_board.IOBoard.GPIO.EN_PROG_STM, 0)

        return True if flash_stm and rmp else False

    def TAKE_PICTURE_perform(self, context, exit_signal):
        #Soft ask CM4 to take picture and it will give feedback through UART
        self.io_board.write_gpio(io_board.IOBoard.GPIO.EN_UART_STM32, 1)
        self.boot_SOM(context)
        time.sleep(0.075)

        result=[None]*2

        output = self.comm_test_CM4.take_image()
        result[0] = output

        img = self.comm_test_CM4.check_image()
        result[1] = img

        return result

    def TAKE_PICTURE_check(self, online_test_results, context, exit_signal, result=None):
        if result is None:
            self.logger.error("No data provided")
            return False

        if not bool(re.search(r"Image\s*ok", result[1])):
            return False
        return True

    def GET_SECURITY_KEYS_perform(self, context, exit_signal):
        #Soft ask CM4 security keys from secure element
        self.boot_SOM(context)
        self.logger.info("CM4 correctly boot")
        self.logger.info("Start CM4 ID script...")
        data = self.comm_test_CM4.cm4_ID_script()

        return data

    def GET_SECURITY_KEYS_check(self, online_test_results, context, exit_signal, result=None):
        if result is None:
            self.logger.error("No data provided")
            return False

        online_test_results["pods"][0]["PodType"] = self.config.get("pego_pod_version")

        if self.config.get("retry_existing_serial", None) is None:
            self.logger.info("Generate new serial")
            pod_serial = self.generate_PodSerial(context)
        else:
            self.logger.warning("Use of existing serial number...")
            pod_serial = self.config.get("retry_existing_serial")

        if not pod_serial:
            return False
        online_test_results["pods"][0]["PodSerial"] = pod_serial
        context["pod_serial"] = pod_serial

        try:
            self.logger.info("CPU_Serial...")
            cpu_serial = (re.search(r"CPU_Serial\s*:\s*([0-9a-fA-F]{16})", result)).group(1)
            online_test_results["pods"][0]["SOM"]["CPUSerial"] = cpu_serial
            context["cpu_serial"] = cpu_serial

            self.logger.info("Ethernet_mac_address...")
            ethernet_mac_address = (re.search(r"Ethermet_mac_address\s*:\s*([0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2})", result)).group(1)
            online_test_results["pods"][0]["SOM"]["EthernetMACAddress"] = ethernet_mac_address
            context["ethernet_mac_address"] = ethernet_mac_address

            self.logger.info("Wifi_mac_address...")
            try:
                wifi_mac_address = (re.search(r"Wifi_mac_address\s*:\s*([0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2})", result)).group(1)
                online_test_results["pods"][0]["WifiEnabled"] = True
                online_test_results["pods"][0]["SOM"]["WifiMACAddress"] = wifi_mac_address
            except:
                wifi_mac_address = (re.search(r"Wifi_mac_address\s*:\s*(WIFI NOT ENABLED)", result)).group(1)
                online_test_results["pods"][0]["SOM"]["WifiMACAddress"] = wifi_mac_address
                online_test_results["pods"][0]["WifiEnabled"] = False
            context["wifi_mac_address"] = wifi_mac_address

            self.logger.info("registration_id...")
            registration_id = (re.search(r"Registration_ID\s*:\s*([0-9a-zA-Z]{52})", result)).group(1)
            online_test_results["pods"][0]["TPM"]["RegistrationID"] = registration_id
            context["registration_id"]=registration_id

            self.logger.info("endorsment_key")
            endorsment_key = (re.search(r"Endorsment_Key\s*:\s*(.{424})", result)).group(1)
            online_test_results["pods"][0]["TPM"]["EndorsementKey"] = endorsment_key
            context["endorsment_key"] = endorsment_key

            self.logger.info("cam_serial")
            cam_serial = (re.search(r"Cam_serial\s*:\s*([0-9]{8})", result)).group(1)
            online_test_results["pods"][0]["CAMERA"]["CamSerial"] = cam_serial
            context["cam_serial"] = cam_serial
        except Exception as ex:
            self.logger.error(ex)
            return False
        return True

    def POD_PROVISION_perform(self, context, exit_signal):
        sn = context.get("pod_serial")
        provision = self.comm_test_CM4.dut_provisioning(sn)

        self.comm_test_CM4.shutdown_CM4()
        return provision

    def POD_PROVISION_check(self, online_test_results, context, exit_signal, result=None):
        if result is None:
            self.logger.error("No data provided")
            return False

        test_result = True
        pod_serial = context.get("pod_serial")
        if not re.search(fr"Pod\s*provision\s*script\s*finished\s*with\s*Serial\s*{pod_serial}\s*", result):
            test_result=False
        return test_result


    def SEND_KEYS_perform(self, context, exit_signal):
        #Soft send KEYs+Unique ID of ST + Unique ID of the SOM + Serial number on the plastic to PEGO Cloud
        self.logger.info("Send data to Pego Cloud...")

        result = None
        evt = threading.Event()

        def recorder_callback(database, record, exception):
            nonlocal result

            result = exception

            if exception is not None:
                self.logger.exception(exception)
                if database == tests_recorder.TestsRecorder.Database.ONLINE:
                    if type(exception) is tests_recorder.TestsRecorder.TestsRecorderException:
                        if exception.data is not None:
                            self.logger.error(
                                f"Response : {exception.data.text}")
                self.logger.error(
                    f"Couldn't record {record} in {database.name} database !")
            else:
                self.logger.debug(
                    f"{database.name} database record saved")

            evt.set()

        online_test_results = context.get("online_test_results")
        if online_test_results is not None:
            self.tests_recorder.record(record=online_test_results, callback=recorder_callback, database=tests_recorder.TestsRecorder.Database.ONLINE.value)

            if evt.wait(self.config.get("tests_recorder_database_timeout", 30)):
                if result is None:
                    result = True

                return result

        return None

    def SEND_KEYS_check(self, online_test_results, context, exit_signal, result=None):
        if type(result) is bool:
            return result

        return False
    #end test region

    def PRINT_LABEL_perform(self, context, exit_signal):
        if self.config.get("retry_existing_serial", None) is not None:
            self.logger.warning("Skipping printing because of existing POD serial...")
            return True

        device_id = context.get("pod_serial")
        num_version = self.config.get("hardware_version", "None")
        barcode = device_id
        if context.get("wifi_mac_address")=="WIFI NOT ENABLED":
            wifi_option = 'False'
        else:
            wifi_option='True'
        qr_code = f"https://activatePods.pego.io/{device_id}"
        label_writer = LabelWriter("item_template.html", default_stylesheets=("sticker_style.css",))
        records = [dict(sample_id = qr_code, sample_name=f"PEGO POD {num_version}", fcc_id="XXXX XXXX", ic="XXXXX-XXXXXX", barcode_content=barcode, wifi=wifi_option, pod_version=self.config.get("pego_pod_version", "None"))]

        LABELS_FOLDER = "./Labels"
        if not os.path.isdir(LABELS_FOLDER):
            os.makedirs(LABELS_FOLDER)
        label_writer.write_labels(records, target=f"./Labels/{device_id}.pdf", base_url=".")

        label_path = self.config.get("label_path", r"/home/test4mod9170/Desktop/4MOD9170_PEGO_Testbench_Software/4MOD9170_TestbenchSoftware/Labels")+f"/{device_id}.pdf"

        if not self.config.get("launch_printing", True):
            self.logger.warning("Skip printing...")
            return True
        p = subprocess.run(["lp", "-o fit-to-page", label_path], capture_output=True)
        self.logger.debug(f"STDOUT : {p.stdout}")
        self.logger.error(f"STDERR : {p.stderr}")

        if p.returncode == 0:
            self.increase_sequential_number()
            return True
        return False

    def boot_SOM(self, context):
        #function to boot som and wait until we're log in or until timeout
        cm4_thread = context.get("cm4_thread")
        timeout_cm4_thread = context.get("timeout_cm4_thread")

        if timeout_cm4_thread.is_alive() and cm4_thread.is_alive():
            self.logger.info("SOM doesn't end the init yet...Wait for him...")
            cm4_thread.join()

        if not self.comm_test_CM4.get_CM4_state():#Check if SOM is not already correctly boot
            self.logger.error("SOM not initialized, rebooting...")
            self.io_board.write_gpio(io_board.IOBoard.GPIO.EN_POWER_POE, 0)
            time.sleep(2)
            self.io_board.write_gpio(io_board.IOBoard.GPIO.EN_POWER_POE, 1)
            time.sleep(3)
            self.stop_cm4Thread = False
            t = self.comm_test_CM4.connectSOM(self.exit_signal, self.stop_cm4_thread)

            def thread_timeout(self, t, timeout):
                now = time.time()
                timeout = (now+timeout)
                while now < timeout:
                    if not t.is_alive():
                        return
                    time.sleep(0.1)
                    now = time.time()
                self.stop_cm4Thread = True
                return
            timeout = self.config.get("cm4_thread_timeout", 70)

            to = threading.Thread(name="cm4_thread_timeout", target=thread_timeout, args=(self, t, timeout))
            to.start()
            if to.is_alive() and t.is_alive():
                self.logger.warning("cm4 rebooting...")
                t.join()
            return

    def generate_PodSerial(self, context):
        pod_serial = [0]*15
        max_mini = self.config.get("pego_pod_version")
        hw_version = self.config.get("hardware_version")
        week_number = str(datetime.date.today().isocalendar().week).zfill(2)
        current_year = datetime.date.today().year
        year_number = current_year - 2020 +1 #In PEGO's spec, year start at 03. So 03=2022
        year_number = str('%02d' % year_number)
        sequential_number = self.get_sequential_number()

        if max_mini =="MAX":
            pod_serial[0] = "2"
        else:
            pod_serial[0] = "1"

        pod_serial[1] = week_number[0]
        pod_serial[2] = week_number[1]
        pod_serial[3] = year_number[0]
        pod_serial[4] = year_number[1]
        pod_serial[5] = hw_version[0] #As the HW version format is "X.X.X", skip the dot to have only the number...
        pod_serial[6] = hw_version[2] #...(skipping dot)...
        pod_serial[7] = hw_version[4] #...(skipping dot)...

        i = 8
        for character in sequential_number:
            pod_serial[i]=character
            i = i+1

        chkDig = str(self.get_chkDig(pod_serial))
        pod_serial[14] = chkDig

        sn_string = ""
        for _ in pod_serial:
            sn_string = sn_string + _

        if len(sn_string)==15:
            return sn_string
        return False

    def get_chkDig(self, list):
        number_serial = []
        list = list[:-1]
        for e in list:
            number_serial.append(int(e))

        even = sum(number_serial[0::2])
        odd = sum(number_serial[1::2])

        if ((even*3+odd)%10)==0:
            return 0
        return(10-(even*3+odd)%10)

    def get_sequential_number(self):
        with open(self.config.get("sequential_number_path", "./sequential_number/sequential_number.txt")) as f:
            lines = f.readline()
        number = int(lines)
        return str("%06d" % number)

    def increase_sequential_number(self):
        self.logger.debug("incrementing sequential_number...")
        with open(self.config.get("sequential_number_path", "./sequential_number/sequential_number.txt")) as f:
            lines = f.readline()
        number = int(lines)
        self.logger.debug(f"increment {number} to {number+1}")
        with open(self.config.get("sequential_number_path", "./sequential_number/sequential_number.txt"), "w") as f:
            f.write(str(number+1))

    def voltage_measure_average(self, measure_duration):
        measure = []
        now = time.time()
        timeout = (now + measure_duration)
        while now < timeout:
            measure.append(self.dpm802_voltmeter.read_measure().value)
            self.logger.debug(f"voltage measured : {measure}")
            now = time.time()

        if len(measure) <= 0:
            self.logger.error("No measure !")
            return False
        if len(measure)==1:
            return measure[0]
        if len(measure) > 1:
            # Skip first value because it doesn't seem reliable
            measure = measure[1:]
            average = (sum(measure) / len(measure))
            self.logger.info(measure)
            self.logger.debug(f"sum measure {sum(measure)}; len = {len(measure)}")
            self.logger.info(f"Average over {measure_duration}seconds : {average} ")
            return average
        return False


    # TODO Add a field retry_nb to tell how many times a test should be retried
    TestDescription = collections.namedtuple(typename="TestDescription", field_names=["perform", "check"], defaults=(
        (lambda self, context, exit_signal: False), (lambda self, context, online_test_results, exit_signal, result: result)))

    TestResult = collections.namedtuple(
        typename="TestResult", field_names=["value", "success"])

    TEST_DESCRIPTIONS={
        Test.POWER_SUPPLY          : TestDescription(perform=POWER_SUPPLY_perform, check= POWER_SUPPLY_check),
        Test.READ_ID               : TestDescription(perform=READ_ID_perform),
        Test.FLASH_ST              : TestDescription(perform=FLASH_ST_perform),
        Test.INIT_SOM              : TestDescription(perform=INIT_SOM_perform),
        Test.HEAT_SENSOR           : TestDescription(perform=HEAT_SENSOR_perform, check= HEAT_SENSOR_check),
        Test.TOF_SENSOR            : TestDescription(perform=TOF_SENSOR_perform, check= TOF_SENSOR_check),
        Test.DAC_I2C               : TestDescription(perform=DAC_I2C_perform, check=DAC_I2C_check),
        Test.CHECK_WINDOW          : TestDescription(perform=CHECK_WINDOW_perform),
        Test.RADAR_STATUS_CLEAR    : TestDescription(perform=RADAR_STATUS_CLEAR_perform, check= RADAR_STATUS_CLEAR_check),
        Test.RADAR_STATUS_MOVEMENT : TestDescription(perform=RADAR_STATUS_MOVEMENT_perform, check= RADAR_STATUS_MOVEMENT_check),
        Test.EXTERNAL_IO           : TestDescription(perform=EXTERNAL_IO_perform, check= EXTERNAL_IO_check),
        Test.SOM_TO_ST_CONNEXION   : TestDescription(perform=SOM_TO_ST_CONNEXION_perform, check= SOM_TO_ST_CONNEXION_check),
        Test.ST_TO_SOM_CONNEXION   : TestDescription(perform=ST_TO_SOM_CONNEXION_perform, check=ST_TO_SOM_CONNEXION_check),
        Test.FLASH_FINAL_FIRMWARE  : TestDescription(perform=FLASH_FINAL_FIRMWARE_perform),
        Test.TAKE_PICTURE          : TestDescription(perform=TAKE_PICTURE_perform, check= TAKE_PICTURE_check),
        Test.GET_SECURITY_KEYS     : TestDescription(perform=GET_SECURITY_KEYS_perform, check= GET_SECURITY_KEYS_check),
        Test.POD_PROVISION         : TestDescription(perform=POD_PROVISION_perform, check=POD_PROVISION_check),
        Test.SEND_KEYS             : TestDescription(perform=SEND_KEYS_perform, check=SEND_KEYS_check),
        Test.PRINT_LABEL           : TestDescription(perform=PRINT_LABEL_perform),
    }

    def run_test(self, exit_signal):
        def prepare_online_test_results(self):
            online_test_results={"pods": [{"SOM":{},"TPM":{},"CAMERA":{}}]}

            return online_test_results

        online_test_results = prepare_online_test_results(self)
        try:
            self.stop_cm4Thread = False
            test_results = {}

            test_results["start_time"] = datetime.datetime.now().strftime("%Y-%m-%d_%H:%M:%S:%f")

            #Use this to exchange data between tests
            context ={"online_test_results": online_test_results}

            for (test, test_description) in [(test, Tester.TEST_DESCRIPTIONS[test])for test in Tester.TEST_PER_MODEL[Tester.ProductModel.BASE]]:
                if test.name in self.config.get("skipped_tests", []):
                    self.logger.info(f"Skipping test {test.name}")
                    self.testbench.gui.check_checklist(test, "grey")
                    continue

                self.logger.info(f"Performing test {test.name}")
                self.testbench.gui.set_status(f"Performing test {test.name}")

                value = test_description.perform(self=self, context=context, exit_signal=exit_signal)
                self.logger.debug(f"{test.name} result : {value}")

                if exit_signal():
                    raise Tester.TesterException(f"Exit requested during test {test.name} !")

                success = True if test_description.check(self=self, result=value, online_test_results=online_test_results, context=context, exit_signal=exit_signal) else False
                test_results[test.name] = Tester.TestResult(value, success)

                if not test_results[test.name].success:
                    self.stop_cm4Thread = True #to stop the commCM4 thread if a test failed
                    time.sleep(0.2)
                    self.logger.error(f"{test.name} failed !")
                    self.testbench.gui.check_checklist(test, "red")

                    test_results["pass"] = False
                    test_results["end_time"] = datetime.datetime.now().strftime("%Y-%m-%d_%H:%M:%S:%f")
                    self.logger.error(f"Test aborted : {test.name} failed !")
                    break
                else:
                    self.logger.info(f"{test.name} passed")
                    self.testbench.gui.check_checklist(test, "green")

                if self.config.get("delay_between_tests", 1):
                    time.sleep(self.config.get("delay_between_tests", 1))

            test_results["pass"] = test_results.get("pass", True)
            test_results["end_time"] = datetime.datetime.now().strftime("%Y-%m-%d_%H:%M:%S:%f")
        except Exception as ex:
            test_results["pass"] = False
            test_results["end_time"] = datetime.datetime.now().strftime("%Y-%m-%d_%H:%M:%S:%f")
            self.logger.exception(ex)
            # test_results["exception"] = ex # This is not JSON serializable...
            test_results["exception"] = {
                "test": test.name,
                "trace": traceback.format_exc(),
            }
            self.testbench.gui.check_checklist(test, "red")

        if test_results.get("pass", False):
            # self.logger.info("Test success !")
            self.logger.log(ScrolledTextLoggingQueueHandler.LOGGING_LEVEL_TEST_RESULT_SUCCESS, "Test success !")
            self.testbench.gui.set_status("Test success !", "green")
            self.stop_cm4Thread = True #to stop the commCM4 thread if all tests passe
            time.sleep(0.2)
        else:
            # self.logger.error("Test failed !")
            self.logger.log(ScrolledTextLoggingQueueHandler.LOGGING_LEVEL_TEST_RESULT_FAILURE, "Test failed !")
            self.testbench.gui.set_status("Test failed !", "red")
            self.stop_cm4Thread = True #to stop the commCM4 thread if a test failed
            time.sleep(0.2)

        return (test_results, online_test_results)

    def askNewTest(self):
        msg = pyautogui.confirm("Test", "Start new test")
        if msg == 'OK':
            return True
        self.testbench.gui.window.quit()
        #self.exit = True
        return False

    def start_tests(self, exit_signal):
        try:
            while not exit_signal():

                if not self.reset_jig():
                    raise Tester.TesterException("Couldn't reset jig state !")
                self.io_board.wait_jig(closed=True, exit_signal=exit_signal)
                if exit_signal():
                    return

                """start_test = messagebox.askyesno("Run test", "Run a new test ?")#self.askNewTest()
                if start_test:
                    pass
                else:
                    return"""

                self.logger.info("Ready")
                self.testbench.gui.set_status("Ready")
                self.logger.info("Jig oppenned")
                self.io_board.wait_jig(closed=False, exit_signal=exit_signal)
                if exit_signal():
                    return

                self.logger.info("Jig Closed")

                if self.config.get("clear_gui_logs_between_tests", True):
                        self.testbench.gui.scrolled_text_logging_handler.clear_logs()
                        self.testbench.gui.scrolled_text_logging_handler_SOM.clear_logs()

                self.testbench.gui.clear_checklist()
                self.testbench.gui.sensor_result_clear()

                (test_results, online_test_results) = self.run_test(exit_signal=exit_signal)

                if self.config.get("record_single_json_per_test", True):
                    JSON_FOLDER = "./tests_results"
                    if not os.path.isdir(JSON_FOLDER):
                        os.makedirs(JSON_FOLDER)
                    recorded_time = test_results.get("start_time")

                    with open(f"./tests_results/{recorded_time}.json", "w") as json_file:
                        json_file.write(json.dumps(test_results, indent=4))
                    json_file.close()

                def testers_recorder_callback(database, record, exception):
                            if exception is not None:
                                self.logger.exception(exception)
                                if database == tests_recorder.TestsRecorder.Database.ONLINE:
                                    if type(exception) is tests_recorder.TestsRecorder.TestsRecorderException:
                                        if exception.data is not None:
                                            self.logger.error(
                                                f"Response : {exception.data.text}")
                                self.logger.error(
                                    f"Couldn't record {record} in {database.name} database !")
                            else:
                                self.logger.debug(
                                    f"{database.name} database record saved")

                if not self.config.get("tests_recorder_disabled", False):
                    if self.config.get("record_json_with_test_recorder", False):
                        self.tests_recorder.record(record=test_results, callback=testers_recorder_callback, database=tests_recorder.TestsRecorder.Database.LOCAL.value)
                    # Record only if we didn't already record it
                    if (online_test_results is not None) and (not Tester.Test.SEND_KEYS.name in test_results):
                        if online_test_results.get("pcbaNumber") is not None:
                            self.tests_recorder.record(record=online_test_results, callback=testers_recorder_callback, database=tests_recorder.TestsRecorder.Database.ONLINE.value)

        except Exception as ex:
            self.logger.exception(ex)
        finally:
            self.stop()


    def reset_jig(self):
        self.io_board.write_gpios([
            (io_board.IOBoard.GPIO.EN_POWER_POE, 0),
            (io_board.IOBoard.GPIO.EN_PROG_STM, 0),
            (io_board.IOBoard.GPIO.EN_UART_CM4, 0),
            (io_board.IOBoard.GPIO.EN_UART_STM32, 0),
            (io_board.IOBoard.GPIO.EN_SWA, 0),
            (io_board.IOBoard.GPIO.EN_SHUNT_AMMETER, 1),
            (io_board.IOBoard.GPIO.CMD_EL, 0),
            (io_board.IOBoard.GPIO.EN_BOOT_CM4, 0)
        ])
        return True

    def stop(self):
        if not self.exit:
            self.logger.info("Stopping")
            self.exit = True

    def exit_signal(self):
        return self.exit

    def stop_cm4_thread(self):
        return self.stop_cm4Thread

    def initialization(self):
        try:
            if self.initialize():
                self.logger.debug("Init test_recorder thread...")
                self.tests_recorder.start(self.exit_signal)

                self.logger.info("Init done")
                self.start_tests(self.exit_signal)
            else:
                self.logger.error("Init failed")
        except Exception as ex:
            self.logger.exception(ex)

class Testbench():

    class GUI():
        def __init__(self, config, logger, cm4logger, tester):
            self.config = config
            self.logger = logger
            self.cm4logger = cm4logger
            self.tester = tester
            self.sensors_result = []
            self.window = tk.Tk()
            self.window.title(
                f"4MOD9170 Pego Testbench Software {TEST_SW_VERSION}")

            self.window.grid_rowconfigure(0, weight=1)
            self.window.grid_columnconfigure(0, weight=1)

            self.window.wm_protocol(
                "WM_DELETE_WINDOW", lambda: (self.tester.stop(), self.window.destroy()))

            st = tkinter.scrolledtext.ScrolledText(self.window)
            st.pack(padx=10, pady=15)
            st.configure(font="TkFixedFont")
            st.tag_config("INFO", foreground="black")
            st.tag_config("DEBUG", foreground="gray")
            st.tag_config("WARNING", foreground="orange")
            st.tag_config("ERROR", foreground="red")
            st.tag_config("CRITICAL", foreground="red", underline=1)
            st.tag_config("BIGGER", background="cyan",
                          font=("TkFixedFont", 15))
            st.tag_config("TEST_RESULT_SUCCESS",
                          background="green", font=("TkFixedFont", 15))
            st.tag_config("TEST_RESULT_FAILURE", background="red",
                          font=("TkFixedFont", 15))

            st.grid(row=0, column=0, sticky=(tk.N + tk.E + tk.W + tk.S))

            self.scrolled_text_logging_handler = ScrolledTextLoggingQueueHandler(
                st)
            self.scrolled_text_logging_handler.setLevel(logging.INFO)
            self.scrolled_text_logging_handler.setFormatter(
                logging.Formatter("%(asctime)s: %(message)s"))
            self.logger.addHandler(self.scrolled_text_logging_handler)
            self.status_label = tk.Label(
                self.window, font=("MS Sans Serif", 24))


            if not self.config.get("disable_pcba_img", False):
                self.pcba_img = tk.PhotoImage(file="pcbPEGO.png")
                image = tk.Label(self.window, image=self.pcba_img)
                image.grid(row=2, column=0, sticky=(tk.N  + tk.S))

            if not self.config.get("disable_som_log", False):
                som = tkinter.scrolledtext.ScrolledText(self.window)
                som.configure(font="TkFixedFont")
                som.tag_config("INFO", foreground="black")
                som.tag_config("DEBUG", foreground="gray")
                som.tag_config("WARNING", foreground="orange")
                self.scrolled_text_logging_handler_SOM = ScrolledTextLoggingQueueHandler(som)
                self.scrolled_text_logging_handler_SOM.setLevel(logging.DEBUG)
                self.scrolled_text_logging_handler_SOM.setFormatter(logging.Formatter("%(asctime)s: %(message)s"))
                self.cm4logger.addHandler(self.scrolled_text_logging_handler_SOM)
                som.grid(row=2, column=1, sticky=(tk.N + tk.E + tk.W + tk.S))


            self.checklist_panel = ScrollableFrame(self.window)
            self.checklist_panel.grid(
                row=0, column=1, sticky=(tk.N + tk.E + tk.W + tk.S))
            self.checklist_panel = self.checklist_panel.scrollable_frame

            for test in Tester.Test:
                check = tk.Label(self.checklist_panel,text=test.name, font=("MS Sans Serif", 10))
                NB_ROW = 50
                check.grid(row=(test.value % NB_ROW), column=(0), sticky=(tk.N + tk.W + tk.S))

            name_label = tk.Label(
                self.window, font=("MS Sans Serif", 24))
            name_label.grid(
                row=1, column=1, sticky=(tk.N + tk.E + tk.W + tk.S))

            self.status_label = tk.Label(
                self.window, font=("MS Sans Serif", 24))
            self.status_label.grid(
                row=1, column=0, sticky=(tk.N + tk.E + tk.W + tk.S))

            self.set_status("Initializing...")

        def radar_gui_result(self, success):
            if success:
                radar = tk.Label(self.window, text="Radar OK", fg="green", font=("MS Sans Serif", 15))
                radar.place(x=270, y=565)
            else:
                radar = tk.Label(self.window, text="Radar KO", fg="red", font=("MS Sans Serif", 15))
                radar.place(x=270, y=565)
            self.sensors_result.append(radar)

        def window_gui_result(self, success):
            if success:
                window = tk.Label(self.window, text="Window OK", fg="green", font=("MS Sans Serif", 15))
                window.place(x=260, y=650)
            else:
                window = tk.Label(self.window, text="Window KO", fg="red", font=("MS Sans Serif", 15))
                window.place(x=260, y=650)
            self.sensors_result.append(window)

        def sensor_result_set(self, sensors, success):
            if success:
                #Heat sensors
                if sensors=="heat3":
                    heat1 = tk.Label(self.window, text="Heat 3 OK", fg="green", font=("MS Sans Serif", 18))
                    self.sensors_result.append(heat1)
                    heat1.place(x=120, y=650)
                elif sensors=="heat4":
                    heat2 = tk.Label(self.window, text="Heat 4 OK", fg="green", font=("MS Sans Serif", 18))
                    self.sensors_result.append(heat2)
                    heat2.place(x=260, y=520)
                elif sensors=="heat1":
                    heat3 = tk.Label(self.window, text="Heat 1 OK", fg="green", font=("MS Sans Serif", 18))
                    self.sensors_result.append(heat3)
                    heat3.place(x=400, y=650)
                elif sensors=="heat2":
                    heat4 = tk.Label(self.window, text="Heat 2 OK", fg="green", font=("MS Sans Serif", 18))
                    self.sensors_result.append(heat4)
                    heat4.place(x=260, y=790)
                #ToF sensors
                elif sensors=="tof3":
                    tof1 = tk.Label(self.window, text="ToF 3 OK", fg="green", font=("MS Sans Serif", 18))
                    self.sensors_result.append(tof1)
                    tof1.place(x=140, y=550)
                elif sensors=="tof4":
                    tof2 = tk.Label(self.window, text="ToF 4 OK", fg="green", font=("MS Sans Serif", 18))
                    self.sensors_result.append(tof2)
                    tof2.place(x=400, y=550)
                elif sensors=="tof1":
                    tof3 = tk.Label(self.window, text="ToF 1 OK", fg="green", font=("MS Sans Serif", 18))
                    self.sensors_result.append(tof3)
                    tof3.place(x=400, y=780)
                elif sensors=="tof2":
                    tof4 = tk.Label(self.window, text="ToF 2 OK", fg="green", font=("MS Sans Serif", 18))
                    self.sensors_result.append(tof4)
                    tof4.place(x=140, y=780)

            else:
                #Heat sensors
                if sensors=="heat3":
                    heat1 = tk.Label(self.window, text="Heat 3 KO", fg="red", font=("MS Sans Serif", 18))
                    self.sensors_result.append(heat1)
                    heat1.place(x=120, y=650)
                elif sensors=="heat4":
                    heat2 = tk.Label(self.window, text="Heat 4 KO", fg="red", font=("MS Sans Serif", 18))
                    self.sensors_result.append(heat2)
                    heat2.place(x=260, y=520)
                elif sensors=="heat1":
                    heat3 = tk.Label(self.window, text="Heat 1 KO", fg="red", font=("MS Sans Serif", 18))
                    self.sensors_result.append(heat3)
                    heat3.place(x=400, y=650)
                elif sensors=="heat2":
                    heat4 = tk.Label(self.window, text="Heat 2 KO", fg="red", font=("MS Sans Serif", 18))
                    self.sensors_result.append(heat4)
                    heat4.place(x=260, y=790)
                #ToF sensors
                elif sensors=="tof3":
                    tof1 = tk.Label(self.window, text="ToF 3 KO", fg="red", font=("MS Sans Serif", 18))
                    self.sensors_result.append(tof1)
                    tof1.place(x=140, y=550)
                elif sensors=="tof4":
                    tof2 = tk.Label(self.window, text="ToF 4 KO", fg="red", font=("MS Sans Serif", 18))
                    self.sensors_result.append(tof2)
                    tof2.place(x=400, y=550)
                elif sensors=="tof1":
                    tof3 = tk.Label(self.window, text="ToF 1 KO", fg="red", font=("MS Sans Serif", 18))
                    self.sensors_result.append(tof3)
                    tof3.place(x=400, y=780)
                elif sensors=="tof2":
                    tof4 = tk.Label(self.window, text="ToF 2 KO", fg="red", font=("MS Sans Serif", 18))
                    self.sensors_result.append(tof4)
                    tof4.place(x=140, y=780)

        def sensor_result_clear(self):
            i=0
            for sensors in self.sensors_result:
                self.sensors_result[i].destroy()
                i=i+1
            self.sensors_result = []

        def set_status(self, text, bg="white", fg="black"):
            if self.status_label is not None:
                self.status_label["text"] = text
                self.status_label["fg"] = fg
                self.status_label["bg"] = bg

        def clear_checklist(self):
            for check in self.checklist_panel.winfo_children():
                check["bg"] = "white"
                check["fg"] = "black"

        def check_checklist(self, test, bg="white", fg="black"):
            for check in self.checklist_panel.winfo_children():
                if check["text"] == test.name:
                    check["bg"] = bg
                    check["fg"] = fg
                    self.checklist_panel.update_idletasks()
                    return

        def mainloop(self):
            return self.window.mainloop()

    def __init__(self, config, mainlogger, cm4logger):
        self.tester = Tester(testbench=self, config=config, logger=mainlogger, cm4logger = cm4logger)
        self.config = config
        self.logger = mainlogger
        self.cm4logger = cm4logger
        if self.logger is None:
            self.logger = logging.getLogger(__name__)
        self.gui = Testbench.GUI(
            config=config, logger=mainlogger, cm4logger=cm4logger, tester=self.tester)

    def mainloop(self):
        t = threading.Thread(name="Tests_Thread",
                             target=self.tester.initialization)
        t.start()
        self.gui.mainloop()
        self.tester.stop()


def main(config, logger=None):
    def setup_logging(config={}, logger=None):
        if logger is None:
            logger = logging.getLogger(__name__)

        if config.get("handle_uncatched_exceptions", True):
            def uncatched_exception_handler(exc_type, exc_value, exc_traceback):
                filename, line, _, _ = traceback.extract_tb(
                    exc_traceback).pop()
                filename = os.path.basename(filename)

                logger.error(f"{exc_type.__name__}: {exc_value}")
                logger.error(f"in {filename}:{line}")
                logger.error(traceback.format_exception(exc_type, exc_value, exc_traceback))

            # Install handler for uncatched exceptions
            sys.excepthook = uncatched_exception_handler
            threading.excepthook = lambda args: uncatched_exception_handler(
                args.exc_type, args.exc_value, args.exc_traceback)

        logging.basicConfig(
            level=config.get("log_level", "DEBUG"),
            format=config.get(
                "log_format_long", "%(asctime)s [%(threadName)s] %(levelname)s : %(message)s")  # "%(asctime)s %(name)s [%(thread)d/%(threadName)s] %(filename)s:%(lineno)d (%(funcName)s) %(levelname)s : %(message)s"
        )
        log_file = config.get("log_file", "automatic")
        if log_file == "automatic":
            LOGS_FOLDER = "./logs"
            LOGS_FILES = LOGS_FOLDER + "/Logs_files"
            if not os.path.isdir(LOGS_FOLDER):
                os.makedirs(LOGS_FOLDER)
                os.makedirs(LOGS_FILES)
                os.makedirs(LOGS_FOLDER+"/STLink_logs")
                os.makedirs(LOGS_FOLDER+"/ID_Logs")
                os.makedirs(LOGS_FOLDER+"/CM4_logs")
            mainlog_file = datetime.datetime.now().strftime(LOGS_FILES + "/%Y%m%d_%H%M%S.log")
            cm4_log_file=datetime.datetime.now().strftime(LOGS_FOLDER + "/CM4_logs" +"/%Y%m%d_%H%M%S_CM4.log")

        def setup_logger(name, log_file, log_format, level=logging.DEBUG):
            handler = logging.FileHandler(log_file)
            handler.setFormatter(log_format)

            logger = logging.getLogger(name)
            logger.setLevel(level)
            logger.addHandler(handler)
            return logger

        mainlogger = setup_logger("mainlogger", mainlog_file, logging.Formatter(config.get("log_format_long", "%(asctime)s [%(threadName)s] %(levelname)s : %(message)s")))
        cm4logger = setup_logger("cm4logger", cm4_log_file, logging.Formatter(config.get("log_format_long", "%(asctime)s [%(threadName)s] %(levelname)s -CM4: %(message)s")))

        return [mainlogger, cm4logger]

    (mainlogger, cm4logger) = setup_logging(config, logger)

    if config.get("kill_process", True):
        linux_commands = LinuxCommands(config=config, logger=mainlogger)
        pid_list = linux_commands.check_process()
        linux_commands.kill_oldest(pid_list)

    if config.get("require_factory_operator_id", True):
        if config.get("factory_operator_id") is None:
            w = tk.Tk()
            w.withdraw()

            factory_operator_id = tkinter.simpledialog.askstring(
                title="OPERATOR ID", prompt="Enter your operator ID", parent=w)
            if (factory_operator_id is None) or (len(factory_operator_id.strip()) <= 0):
                w.destroy()
                return
            config["factory_operator_id"] = factory_operator_id.strip()

            w.destroy()

        mainlogger.debug(f"Operator ID : {config.get('factory_operator_id')}")
    mainlogger.debug(f"Configuration : {config}")

    if config.get("combobox_for_options", False):
        settings = tk.Tk()
        settings.title("Select product options")
        pegoPodOption = tk.Label(settings, text="PEGO POD version")

        pegoPodOption.grid(row=0, column=1)

        max_mini = ["", "MAX", "MINI"]

        cbbMaxMini = ttk.Combobox(settings, state="readonly", values=max_mini)
        cbbMaxMini.current(0)
        cbbMaxMini.grid(row=1, column=1)


        def max_mini_selected(e):
            mainlogger.debug(f"Option selected : {cbbMaxMini.get()}")
            config["pego_pod_version"] = cbbMaxMini.get()
            if not (len(cbbMaxMini.get())<=0):
                settings.destroy()

        cbbMaxMini.bind("<<ComboboxSelected>>", max_mini_selected)

        settings.mainloop()
        pod_version = config.get("pego_pod_version")
        global TEST_SW_VERSION
        TEST_SW_VERSION = TEST_SW_VERSION + f" || Pod version : {pod_version}"
    try:
        Testbench(config, mainlogger=mainlogger, cm4logger=cm4logger).mainloop()
    except Exception as ex:
        mainlogger.exception(ex)



if __name__ == "__main__":
    def main_wrapper():
        logger = logging.getLogger(__name__)

        config = {}
        try:
            with open("config.json") as config_file:
                config = json.load(config_file)
        except Exception as ex:
            logger.exception(ex)

        main(config, logger)
    main_wrapper()