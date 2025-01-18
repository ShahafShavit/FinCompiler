import os
import threading
import time
from datetime import datetime

import config


class FolderTracker:
    def __init__(self, folder_path):
        """
        Initialize the tracker with the folder path and store the initial state.
        """
        self.folder_path = folder_path
        self.previous_state = self._get_folder_state()
        self.history = []
        self.total_files_added = 0  # Track total files added since initialization
        self.running = False  # To control the while loop in the thread
        self.success = None  # To store the result of monitoring

    def _get_folder_state(self):
        """
        Get the current state of the folder (files and metadata).
        """
        folder_state = {}
        for file_name in os.listdir(self.folder_path):
            file_path = os.path.join(self.folder_path, file_name)
            if os.path.isfile(file_path):
                metadata = os.stat(file_path)
                folder_state[file_name] = {
                    "size": metadata.st_size,
                    "last_modified": datetime.fromtimestamp(metadata.st_mtime)
                }
        return folder_state

    def check_changes(self):
        """
        Compare the current folder state with the previous state and track changes.
        """
        current_state = self._get_folder_state()
        added = {
            file_name: current_state[file_name]
            for file_name in current_state
            if file_name not in self.previous_state
        }
        removed = {
            file_name: self.previous_state[file_name]
            for file_name in self.previous_state
            if file_name not in current_state
        }

        # Update the total files added count
        self.total_files_added += len(added)

        changes = {
            "added": added,
            "removed": removed
        }

        if added or removed:
            self.history.append(changes)

        # Update the previous state
        self.previous_state = current_state

        return changes

    def get_history(self):
        """
        Return the history of changes between scans.
        """
        return self.history

    def get_total_files_added(self):
        """
        Return the total number of files added since initialization.
        """
        return self.total_files_added

    def monitor_folder(self, expected_files, timeout):
        """
        Monitor the folder in a separate thread until the expected number of files
        has been added or the timeout is reached. Returns True if successful, False otherwise.
        """
        def monitor():
            start_time = time.time()
            self.running = True
            self.success = False  # Default to failure

            while self.running:
                self.check_changes()
                if self.total_files_added >= expected_files:
                    self.success = True  # Success: expected files added
                    print(f"Expected files added: {self.total_files_added}")
                    break
                if time.time() - start_time >= timeout:
                    self.success = False  # Failure: timeout reached
                    print(f"Timeout reached: {timeout} seconds")
                    break
                time.sleep(1)  # Sleep for a second before checking again

            self.running = False

        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()
        thread.join()  # Wait for the thread to complete
        return self.success

    def stop_monitoring(self):
        """
        Stop the monitoring thread gracefully.
        """
        self.running = False

if __name__ == '__main__':
    tracker = FolderTracker(config.input_dir)

    expected_files = 5
    timeout = 60
    result = tracker.monitor_folder(expected_files, timeout)

    if result:
        print("Monitoring successful: All expected files were added.")
    else:
        print("Monitoring failed: Timeout reached before all expected files were added.")

    print("History of changes:", tracker.get_history())
    print("Total files added:", tracker.get_total_files_added())
