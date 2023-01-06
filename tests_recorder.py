import enum
import logging
import json
import queue
import threading
import datetime
import os
import requests
import collections


class TestsRecorder():
    class TestsRecorderException(Exception):
        def __init__(self, msg, data=None):
            super().__init__(msg)
            self.data = data

    class Database(enum.Enum):
        LOCAL = (1 << 0)
        ONLINE = (1 << 1)

    RecordQueueItem = collections.namedtuple(
        typename="RecordQueueItem", field_names=["record", "callback"])

    def __init__(self, recording_file_path="automatic", recording_server_url=None, config={}, logger=None):
        self.config = config

        if logger is None:
            self.logger = logging.getLogger(__name__)
        else:
            self.logger = logger.getChild(__name__)

        self.logger.setLevel(self.config.get("log_level", "DEBUG"))

        if recording_file_path == "automatic":
            automatic_records_folder = self.config.get(
                "automatic_records_folder", "./tests_results")
            if not os.path.isdir(automatic_records_folder):
                os.makedirs(automatic_records_folder)
            recording_file_path = datetime.datetime.now().strftime(
                automatic_records_folder + "/%Y%m%d_%H%M%S.json")

        self.recording_file_path = recording_file_path

        self.recording_server_url = recording_server_url

        self.local_records_queue = queue.SimpleQueue()
        self.online_records_queue = queue.SimpleQueue()

    def __del__(self):
        pass

    def start(self, exit_signal=lambda: False):
        def local_recording_thread(self, exit_signal):
            while True:
                try:
                    record_item = self.local_records_queue.get(timeout=0.25)
                    if record_item is None or record_item.record is None:
                        continue

                    self.logger.debug(f"Local record : {record_item.record}")

                    if isinstance(self.recording_file_path, str) and (len(self.recording_file_path) > 0):
                        with open(self.recording_file_path, "a+") as recording_file:
                            if self.config.get("pretty_print_local_records", True):
                                recording_file.write(json.dumps(record_item.record, indent=4, default=str))
                            else:
                                recording_file.write(json.dumps(record_item.record, default=str))

                            recording_file.write(",\n")

                    if record_item.callback is not None:
                        record_item.callback(
                            TestsRecorder.Database.LOCAL, record_item.record, None)
                except queue.Empty as ex:
                    if exit_signal():
                        return
                except Exception as ex:
                    if (record_item is not None) and (record_item.callback is not None):
                        record_item.callback(
                            TestsRecorder.Database.LOCAL, record_item.record, ex)
                    else:
                        self.logger.exception(ex)

        def online_recording_thread(self, exit_signal):
            while True:
                try:
                    record_item = self.online_records_queue.get(timeout=0.25)
                    if (record_item is None) or (record_item.record is None):
                        continue

                    self.logger.debug(f"Online record : {record_item.record}")

                    if isinstance(self.recording_server_url, str) and (len(self.recording_server_url) > 0):
                        resp = requests.post(self.recording_server_url, json=record_item.record, headers={"XApiKey": f"{self.config.get('recording_server_token')}", "Content-Type": "application/json",})
                        try:
                            self.logger.debug(resp.json())
                        except ValueError as ex:
                            raise TestsRecorder.TestsRecorderException(
                                f"Recording server returned invalid content : {resp.status_code}", data=resp)

                        if resp.status_code != requests.codes.ok:
                            raise TestsRecorder.TestsRecorderException(
                                f"Recording server returned status code : {resp.status_code}", data=resp)

                        if record_item.callback is not None:
                            record_item.callback(
                                TestsRecorder.Database.ONLINE, record_item.record, None)
                except queue.Empty as ex:
                    if exit_signal():
                        return
                except Exception as ex:
                    if (record_item is not None) and (record_item.callback is not None):
                        record_item.callback(
                            TestsRecorder.Database.ONLINE, record_item.record, ex)
                    else:
                        self.logger.exception(ex)

        t = threading.Thread(name="TestsRecorderLocalThread", target=local_recording_thread, args=(self, exit_signal))
        t.start()

        t = threading.Thread(name="TestsRecorderOnlineThread", target=online_recording_thread, args=(self, exit_signal))
        t.start()

    def record(self, record, callback=None, database=(Database.ONLINE.value)):
        if database & TestsRecorder.Database.LOCAL.value:
            self.local_records_queue.put(TestsRecorder.RecordQueueItem(record, callback), timeout=0.5)

        if database & TestsRecorder.Database.ONLINE.value:
            self.online_records_queue.put(TestsRecorder.RecordQueueItem(record, callback), timeout=0.5)
