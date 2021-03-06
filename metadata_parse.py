import sys
import datetime
import re as regex
import json
import six
import numpy as np
import sklearn.linear_model as lm
import matplotlib.pyplot as plt


_REGEX_FLOAT = regex.compile(r"[-+]?[0-9]*\.?[0-9]+")

_REGEX_FILENAME = regex.compile(
    r".*platypus"
    r"_(?P<year>\d{4})(?P<month>\d{2})(?P<day>\d{2})"
    r"_(?P<hour>\d{2})(?P<minute>\d{2})(?P<second>\d{2})"
    r".txt$")

_TIME_STEP = 10  # in seconds
_EC_IN_WATER_CUTOFF = 100  # EC below this will be treated as if the boat is out of water
_DANGER_VOLTAGE = 14  # show battery voltage above this value
_VOLTAGE_MEDIAN_WINDOW = 500  # size of the window of previous voltage values to take the median of
_VELOCITY_WINDOW = 50  # size of the window of previous pose values to use for velocity estimate


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
    voltage_median_window = [0.0] * _VOLTAGE_MEDIAN_WINDOW
    voltage_time_window = [0]*_VOLTAGE_MEDIAN_WINDOW
    voltage_drain_rate_initialized = False
    pose_window = [[0.0, 0.0]]*_VELOCITY_WINDOW
    velocity_time_window = [0]*_VELOCITY_WINDOW
    velocity_initialized = False
    first_easting = 0
    first_northing = 0

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
        "velocity_over_ground": [0.0],
        "velocity_surge": [0.0],
        "velocity_sway": [0.0],
        "battery_voltage": [0.0],
        "battery_voltage_median": [0.0],
        "battery_voltage_drain_rate": [0.0],
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
            print("Parsing, @ {:.1f} seconds".format(timestamp_seconds))
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
                        del pose_window[0]
                        del velocity_time_window[0]
                        pose_window.append(new_pose)
                        velocity_time_window.append(timestamp_seconds)
                        # calculate velocity
                        """
                        distance_easting = pose_window[-1][0] - pose_window[0][0]
                        distance_northing = pose_window[-1][1] - pose_window[0][1]
                        distance_over_ground = np.sqrt(np.power(distance_easting, 2) + np.power(distance_northing, 2))
                        velocity_dt = time_window[-1] - time_window[0]
                        """
                        if not velocity_initialized and velocity_time_window[0] != 0:
                            velocity_initialized = True
                            first_easting = current_pose[0]
                            first_northing = current_pose[1]
                        if velocity_initialized:
                            # meta_data["velocity_over_ground"][-1] = distance_over_ground/velocity_dt
                            #  https://docs.scipy.org/doc/numpy-1.13.0/reference/generated/numpy.linalg.lstsq.html
                            #  http://scikit-learn.org/stable/auto_examples/linear_model/plot_ransac.html
                            pose_window_array = np.array(pose_window)
                            pose_window_array[:, 0] -= first_easting
                            pose_window_array[:, 1] -= first_northing
                            #time_array = np.atleast_2d(np.array(time_window)-time_window[0]).T
                            #ransac = lm.RANSACRegressor()
                            #ransac.fit(time_array, pose_window_array)
                            #vE = ransac.estimator_.coef_[0]
                            #vN = ransac.estimator_.coef_[1]
                            A = np.vstack([velocity_time_window, np.ones(pose_window_array.shape[0])]).T
                            velE, _ = np.linalg.lstsq(A, pose_window_array[:, 0], rcond=None)[0]
                            velN, _ = np.linalg.lstsq(A, pose_window_array[:, 1], rcond=None)[0]
                            vel = np.sqrt(np.power(velE, 2) + np.power(velN, 2))
                            meta_data["velocity_over_ground"][-1] = vel

                    if k == "home_pose":
                        m = _REGEX_FLOAT.findall(v)
                        home_pose = (float(m[0]), float(m[1]))
                        current_pose = home_pose
                if k == "sensor":
                    if v["type"] == "EC_GOSYS":
                        ec = v["data"]
                        if ec > _EC_IN_WATER_CUTOFF:
                            if not in_water:
                                print("Boat entered water at {}".format(timestamp_seconds))
                            in_water = True

                        else:
                            if in_water:
                                print("Boat exited water at {}".format(timestamp_seconds))
                            in_water = False
                    if v["type"] == "BATTERY":
                        voltage_above_danger = float(v["data"]) - _DANGER_VOLTAGE
                        meta_data["battery_voltage"][-1] = voltage_above_danger
                        del voltage_median_window[0]
                        del voltage_time_window[0]
                        voltage_median_window.append(voltage_above_danger)
                        voltage_time_window.append(timestamp_seconds)
                        meta_data["battery_voltage_median"][-1] = np.median(voltage_median_window)
                        if not voltage_drain_rate_initialized and voltage_median_window[0] != 0:
                            voltage_drain_rate_initialized = True
                        if voltage_drain_rate_initialized:
                            A = np.vstack([voltage_time_window, np.ones(len(voltage_time_window))]).T
                            voltage_drain_rate, _ = np.linalg.lstsq(A, voltage_median_window, rcond=None)[0]
                            meta_data["battery_voltage_drain_rate"][-1] = voltage_drain_rate*3600  # per HOUR

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

    ax1.plot(meta_data["time_elapsed_total"], meta_data["battery_voltage_median"], 'r')
    ax1.set_xlabel('time (s)')
    ax1.set_ylabel('battery voltage above 14 V', color="r")
    ax1.tick_params('y', colors='r')

    ax2 = ax1.twinx()

    """
    ax1.plot(meta_data["time_elapsed_total"], meta_data["distance_traveled_rc"], 'g')
    ax1.set_xlabel('time (s)')
    ax1.set_ylabel('distance traveled (m)', color="g")
    ax1.tick_params('y', colors='g')
    """

    ax2.plot(meta_data["time_elapsed_total"], meta_data["battery_voltage_drain_rate"], 'kx')
    ax2.set_xlabel('time (s)')
    ax2.set_ylabel('battery drain rate (V/hr)', color="k")
    ax2.tick_params('y', colors='k')

    #ax2.plot(meta_data["time_elapsed_total"], meta_data["velocity_over_ground"], 'b')
    #ax2.set_xlabel('time (s)')
    #ax2.set_ylabel('velocity over ground (m/s)', color="b")
    #ax2.tick_params('y', colors='b')

    plt.show()


if __name__ == "__main__":
    args = sys.argv
    args = args[1:]
    if args != list():
        filename = args[0]
    else:
        print("YOU NEED TO INCLUDE FILENAME AS AN ARGUMENT. USING EXAMPLE FILE...")
        filename = "/home/jason/Documents/INTCATCH/phone logs/Laghetto del Frassino/platypus_20180720_033339.txt"

    parse(filename)

