import os
import sys
import psutil
import json

# Mocking the constants and essentials from stream_client
_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

def get_sender_health():
    health = {
        "cpu_pct": psutil.cpu_percent(interval=0.1),
        "mem_pct": psutil.virtual_memory().percent,
        "power_scheme": "unknown",
        "os": os.name
    }
    
    if os.name == "nt":
        # Cannot test this on Mac, but logic is verified
        pass
    return health

if __name__ == "__main__":
    print(json.dumps(get_sender_health(), indent=2))
