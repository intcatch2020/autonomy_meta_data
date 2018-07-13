import sys
import datetime
import re as regex
import json
import six
import numpy as np
import matplotlib.pyplot as plt


_REGEX_FLOAT = regex.compile(r"[-+]?[0-9]*\.?[0-9]+")

_REGEX_FILENAME = regex.compile(
    r".*platypus"
    r"_(?P<year>\d{4})(?P<month>\d{2})(?P<day>\d{2})"
    r"_(?P<hour>\d{2})(?P<minute>\d{2})(?P<second>\d{2})"
    r".txt$")

_TIME_STEP = 1  # in seconds
_EC_IN_WATER_CUTOFF = 100  # EC below this will be treated as if the boat is out of water
_DANGER_VOLTAGE = 14  # show battery voltage above this value


def printNestedDict(dict_to_print, indent_level=0):
    """
    Indented, vertical print.
    :param dict_to_print: duh
    :param indent_level: value must be >= 0. The minimum indent level.
    :return: nothing
    """
    for key in dict_to_print:
        if isinstance(dict_to_print[key], dict):
            string_to_print = ""
            string_to_print += "\t"*indent_level
            string_to_print += str(key)
            string_to_print += ":"
            print(string_to_print)
            printNestedDict(dict_to_print[key], indent_level+1)
        else:
            string_to_print = ""
            string_to_print += "\t"*indent_level
            string_to_print += str(key)
            string_to_print += ": "
            string_to_print += str(dict_to_print[key])

            print(string_to_print)


def dist(a, b):
    if len(a) != len(b):
        raise ValueError("collections must be the same length")
    sq_dist = 0
    for i in range(len(a)):
        sq_dist += (a[i]-b[i])*(a[i]-b[i])
    return np.sqrt(sq_dist)


def datetimeFromFilename(filename):
    m = _REGEX_FILENAME.match(filename)
    if not m:
        raise ValueError("log files must be named 'platypus_<date>_<time>.txt'.")
    start = datetime.datetime(int(m.group('year')),
                              int(m.group('month')),
                              int(m.group('day')),
                              int(m.group('hour')),
                              int(m.group('minute')),
                              int(m.group('second')))
    return start


def rawLines(filename):
    return [line.strip() for line in open(filename)]


def parse(filename):
    """
    dictionary {timestamp: message}
    """
    raw_lines = rawLines(filename)
    has_first_gps = False
    in_water = False
    rc_on = False
    is_autonomous = False
    home_pose = (0.0, 0.0)  # easting, northing
    current_pose = (0.0, 0.0)
    current_time = 0.0  # seconds
    time_since_accumulation = 0.0

    meta_data = {
        "time_elapsed_total": [0.0],
        "time_elapsed_rc": [0.0],
        "time_elapsed_auto": [0.0],
        "time_elapsed_in_water": [0.0],
        "time_elapsed_out_water": [0.0],
        "distance_traveled_total": [0.0],
        "distance_traveled_rc": [0.0],
        "distance_traveled_auto": [0.0],
        "distance_from_home_location": [0.0],
        "battery_voltage": [0.0],
        "cumulative_motor_action_total": [0.0],
        "cumulative_motor_action_rc": [0.0],
        "cumulative_motor_action_auto": [0.0],
        "rc_override_switch_count": [0.0],
    }

    start_time = datetimeFromFilename(filename)

    for line in raw_lines:
        time_offset_ms, level, message = line.split('\t', 2)

        timestamp_seconds = float(time_offset_ms)/1000.
        dt = timestamp_seconds - current_time
        current_time = timestamp_seconds
        time_since_accumulation += dt
        if time_since_accumulation > _TIME_STEP:
            time_since_accumulation = 0.0
            for k in meta_data:
                meta_data[k].append(meta_data[k][-1])  # start with previous value
        distance_traveled = 0.0

        try:
            entry = json.loads(message)

            for k, v in six.viewitems(entry):
                if k == "has_first_gps":
                    has_first_gps = v == "true"
                if k == "is_autonomous":
                    is_autonomous = v == "true"
                if k == "rc_override":
                    rc_on = v == "true"
                if has_first_gps:
                    if k == "pose":
                        new_pose = (v["p"][0], v["p"][1])
                        meta_data["distance_from_home_location"][-1] = dist(new_pose, home_pose)
                        distance_traveled = dist(new_pose, current_pose)
                        current_pose = new_pose
                    if k == "home_pose":
                        m = _REGEX_FLOAT.findall(v)
                        home_pose = (float(m[0]), float(m[1]))
                        current_pose = home_pose
                if k == "sensor":
                    if v["type"] == "EC_GOSYS":
                        ec = v["data"]
                        if ec > _EC_IN_WATER_CUTOFF:
                            if not in_water: print("Boat entered water at {}".format(timestamp_seconds))
                            in_water = True

                        else:
                            if in_water: print("Boat exited water at {}".format(timestamp_seconds))
                            in_water = False
                    if v["type"] == "BATTERY":
                        meta_data["battery_voltage"][-1] = float(v["data"]) - _DANGER_VOLTAGE
                if k == "cmd":
                    # TODO: motor action
                    None

            meta_data["time_elapsed_total"][-1] += dt
            meta_data["distance_traveled_total"][-1] += distance_traveled

            if rc_on:
                meta_data["time_elapsed_rc"][-1] += dt
                meta_data["distance_traveled_rc"][-1] += distance_traveled
            elif is_autonomous:
                meta_data["time_elapsed_auto"][-1] += dt
                meta_data["distance_traveled_auto"][-1] += distance_traveled

            if in_water:
                meta_data["time_elapsed_in_water"][-1] += dt
            else:
                meta_data["time_elapsed_out_water"][-1] += dt

        except ValueError as e:
            raise ValueError("Aborted after invalid JSON log message '{:s}': {:s}".format(message, e))

    fig, ax1 = plt.subplots()
    ax1.plot(meta_data["time_elapsed_total"], meta_data["battery_voltage"], 'r')
    ax1.set_xlabel('time (s)')
    ax1.set_ylabel('battery voltage above 14 V', color="r")
    ax1.tick_params('y', colors='r')

    ax2 = ax1.twinx()
    ax2.plot(meta_data["time_elapsed_total"], meta_data["distance_traveled_total"], 'b')
    ax2.set_xlabel('time (s)')
    ax2.set_ylabel('distance traveled (m)', color="b")
    ax2.tick_params('y', colors='b')

    plt.show()


if __name__ == "__main__":
    args = sys.argv
    args = args[1:]
    if args != list():
        filename = args[0]
    else:
        print("YOU NEED TO INCLUDE FILENAME AS AN ARGUMENT. USING EXAMPLE FILE...")
        filename = "/home/jason/Documents/INTCATCH/phone logs/Garda/platypus_20180712_040554.txt"

    parse(filename)

