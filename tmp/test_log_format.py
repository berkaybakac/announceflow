import os
import sys
import json
import time

# Mocking the logger context
from logger import log_system, log_play, log_error, log_trigger, EVENT_LOG_FILE

def test_logs():
    print(f"Testing logs in {EVENT_LOG_FILE}...")
    
    # Trigger different types of logs
    log_system("test_boot", {"msg": "System is booting"})
    log_play("test_track", {"file": "song.mp3"})
    log_trigger("test_announcement", {"id": 123})
    log_error("test_fallback", {"reason": "network timeout"})
    
    # Read the last 4 lines of the log file
    time.sleep(0.1) # Ensure write
    if os.path.exists(EVENT_LOG_FILE):
        with open(EVENT_LOG_FILE, "r") as f:
            lines = f.readlines()
            for line in lines[-4:]:
                print(json.dumps(json.loads(line), indent=2))
    else:
        print("Log file not found!")

if __name__ == "__main__":
    test_logs()
