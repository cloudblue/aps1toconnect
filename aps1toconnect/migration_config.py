import json
import os
import sys

CFG_FILE_PATH = os.path.expanduser('~/.connect/migration.json')
NULL_CFG_INFO = (None, None)


def get_config():
    try:
        with open(CFG_FILE_PATH) as f:
            cfg = json.load(f)
    except IOError as e:
        if e.errno == 2:
            print("Could not find migration configuration file")
        else:
            print("Could not open configuration file:\n{}".format(e))
        sys.exit(1)
    except ValueError:
        print("Could not parse the configuration file")
        sys.exit(1)
    except Exception as e:
        print("Failed to read connected hub configuration. Error message:\n{}".format(e))
        sys.exit(1)
    else:
        _validate_config(cfg)
        return cfg


def _validate_config(cfg):
    REQUIRED = [
        "APP_APP_ID",
        "APP_SOURCE_VERSION",
        "APP_SAFE_DELETE_VERSION",
        "SUBSCRIPTION_ID_SETTING",
        "RESOURCE_MAPPING",
        "PARAMS_MAPPING",
        "CONNECT_PRODUCT_ID",
        "CONNECT_API_KEY",
        "CONNECT_API_ENDPOINT",
    ]
    for req in REQUIRED:
        if req not in cfg or cfg[req] == "":
            print(f"Migration Configuration file misses key {req} or is empty")
            sys.exit(1)
