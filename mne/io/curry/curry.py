# -*- coding: UTF-8 -*-
#
# Authors: Dirk Gütlin <dirk.guetlin@stud.sbg.ac.at>
#
#
# License: BSD (3-clause)

import os
import re
import numpy as np
import mne

from ..base import BaseRaw, _check_update_montage
from ..meas_info import create_info
from ..utils import _read_segments_file, warn, _find_channels, _create_chs


def _read_curry_events(fname_base, curry_vers):
    """
    Read curry files and convert the data for mne.
    Inspired by Matt Pontifex' EEGlab loadcurry() extension.

    :param full_fname:
    :return:
    """

    #####################################

    EVENT_FILE_EXTENSION = {7: ['.cef', '.ceo'], 8: ['.cdt.cef', '.cdt.ceo']}

    event_file = None
    for ext in EVENT_FILE_EXTENSION[curry_vers]:
        if os.path.isfile(fname_base + ext):
            event_file = fname_base + ext

    if event_file is not None:
        curry_events = []
        save_events = False
        with open(event_file) as fid:
            for line in fid:

                if "NUMBER_LIST END_LIST" in line:
                    save_events = False

                if save_events:
                    curry_events.append(line.split("\t"))

                if "NUMBER_LIST START_LIST" in line:
                    save_events = True

        curry_events = np.array(curry_events, dtype=int)

    else:
        curry_events = None

    # TODO: This returns events in a curry specific format. This might be reformatted to fit mne
    return curry_events


def _get_curry_version(file_extension):
    """check out the curry file version"""

    if 'cdt' in file_extension:
        curry_vers = 8

    else:
        curry_vers = 7

    return curry_vers


def _check_missing_files(full_fname, fname_base, curry_vers):
    """
    Check if all neccessary files exist.
     """

    if curry_vers == 8:
        for check_ext in [".cdt", ".cdt.dpa"]:
            if not os.path.isfile(fname_base + check_ext):
                raise FileNotFoundError("The following required file cannot be"
                                        " found: %s. Please make sure it is "
                                        "located in the same directory as %s."
                                        % (fname_base + check_ext, full_fname))

    if curry_vers == 7:
        for check_ext in [".dap", ".dat", ".rs3"]:
            if not os.path.isfile(fname_base + check_ext):
                raise FileNotFoundError("The following required file cannot be"
                                        " found: %s. Please make sure it is "
                                        "located in the same directory as %s."
                                        % (fname_base + check_ext, full_fname))


def _read_curry_info(fname_base, curry_vers):

    #####################################
    # read parameters from the param file

    CAL = 1e-6
    INFO_FILE_EXTENSION = {7: '.dap', 8: '.cdt.dpa'}
    LABEL_FILE_EXTENSION = {7: '.rs3', 8: '.cdt.dpa'}


    var_names = ['NumSamples', 'NumChannels', 'NumTrials', 'SampleFreqHz',
                 'TriggerOffsetUsec', 'DataFormat', 'SampleTimeUsec',
                 'NUM_SAMPLES', 'NUM_CHANNELS', 'NUM_TRIALS', 'SAMPLE_FREQ_HZ',
                 'TRIGGER_OFFSET_USEC', 'DATA_FORMAT', 'SAMPLE_TIME_USEC']

    param_dict = dict()
    with open(fname_base + INFO_FILE_EXTENSION[curry_vers]) as fid:
        for line in fid:
            if any(var_name in line for var_name in var_names):
                key, val = line.replace(" ", "").replace("\n", "").split("=")
                param_dict[key.lower().replace("_", "")] = val

    for var in var_names[:7]:
        if var.lower() not in param_dict:
            raise KeyError("Variable %s cannot be found in the parameter file."
                           % var)

    n_samples = int(param_dict["numsamples"])
    # n_ch = int(param_dict["numchannels"])
    n_trials = int(param_dict["numtrials"])
    sfreq = float(param_dict["samplefreqhz"])
    offset = float(param_dict["triggeroffsetusec"]) * CAL
    time_step = float(param_dict["sampletimeusec"]) * CAL
    data_format = param_dict["dataformat"]

    if (sfreq == 0) and (time_step != 0):
        sfreq = 1. / time_step

    #####################################
    # read labels from label files

    ch_names = []
    ch_pos = []

    save_labels = False
    save_ch_pos = False
    with open(fname_base + LABEL_FILE_EXTENSION[curry_vers]) as fid:
        for line in fid:

            if re.match("LABELS.*? END_LIST", line):
                save_labels = False

            # if "SENSORS END_LIST" in line:
            if re.match("SENSORS.*? END_LIST", line):
                save_ch_pos = False

            if save_labels and line != "\n":
                    ch_names.append(line.replace("\n", ""))

            if save_ch_pos:
                ch_pos.append(line.split("\t"))

            if re.match("LABELS.*? START_LIST", line):
                save_labels = True

            # if "SENSORS START_LIST" in line:
            if re.match("SENSORS.*? START_LIST", line):
                save_ch_pos = True

    ch_pos = np.array(ch_pos, dtype=float)
    # TODO find a good method to set montage (do it in read_montage instead?)

    info = create_info(ch_names, sfreq)

    # TODO; There's still a lot more information that can be brought into info["chs"]. However i'm not sure what to do with MEG chans here
    for ch_dict in info["chs"]:
        ch_dict["cal"] = CAL

    return info, n_trials, n_samples, curry_vers, data_format


def read_raw_curry(input_fname, preload=False):
    """
    Read raw data from Curry files.

    Parameters
    ----------
    input_fname : str
        Path to a curry file with extensions .dat, .dap, .rs3, .cdt, cdt.dpa,
        .cdt.cef or .cef.
    preload : bool or str (default False)
        Preload data into memory for data manipulation and faster indexing.
        If True, the data will be preloaded into memory (fast, requires
        large amount of memory). If preload is a string, preload is the
        file name of a memory-mapped file which is used to store the data
        on the hard drive (slower, requires less memory). If the curry file
        is stored in ASCII data format, then preload must be `True`.

    Returns
    -------
    raw : instance of RawCurry
        A Raw object containing CURRY data.

    """

    DATA_FILE_EXTENSION = {7: '.dat', 8: '.cdt'}

    # we don't use os.path.splitext to also handle extensions like .cdt.dpa
    fname_base, ext = input_fname.split(".", maxsplit=1)

    curry_vers = _get_curry_version(ext)
    _check_missing_files(input_fname, fname_base, curry_vers)

    info, n_trials, n_samples, curry_vers, data_format = _read_curry_info(fname_base, curry_vers)
    events = _read_curry_events(fname_base, curry_vers)
    info["events"] = events

    raw = RawCurry(fname_base + DATA_FILE_EXTENSION[curry_vers], info, n_samples, data_format)

    return raw


class RawCurry(BaseRaw):
    """"""

    def __init__(self, data_fname, info, n_samples, data_format, montage=None, eog=(), ecg=(),
                 emg=(), misc=(), preload=False, verbose=None):  # noqa: D102

        data_fname = os.path.abspath(data_fname)

        last_samps = [n_samples - 1]

        if preload == False and data_format == "ASCII":
            warn('Got ASCII format data as input. Data will be preloaded.')


        super(RawCurry, self).__init__(
            info, preload, filenames=[data_fname], last_samps=last_samps, orig_format='int',
            verbose=verbose)

    def _read_segment_file(self, data, idx, fi, start, stop, cals, mult):
        """Read a chunk of raw data."""

        _read_segments_file(self, data, idx, fi, start, stop, cals, mult, dtype="float32")
