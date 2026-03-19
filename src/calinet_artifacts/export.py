# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:

import pandas as pd

from pathlib import Path
from typing import List, Optional, Union

import pandas as pd
from scipy.io import loadmat

import calinet.core.io as cio

def mat_to_physioevents_df(
    mat_file: Union[str, Path],
    out_tsv_gz: Optional[Union[str, Path]] = None,
    out_json: Optional[Union[str, Path]] = None,
    *,
    mat_key: str = "epochs",
    artifact_type: str = "artifact",
    channel: str = "unknown",
    annotator: str = "manual",
    message: str = "",
    sampling_frequency: Optional[float] = None,
) -> pd.DataFrame:
    """
    Read a MATLAB artifact file containing Nx2 onset/offset intervals and convert it
    to a BIDS-like physioevents DataFrame.

    Parameters
    ----------
    mat_file
        Path to .mat file.
    out_tsv_gz
        Optional output path for *_physioevents.tsv.gz.
    out_json
        Optional output path for matching JSON sidecar.
    mat_key
        Variable name inside the .mat file. Default: 'epochs'.
    artifact_type
        Value for artifact_type column.
    channel
        Value for channel column.
    annotator
        Value for annotator column.
    message
        Value for message column.
    sampling_frequency
        Optional sampling frequency to include in JSON sidecar.

    Returns
    -------
    pd.DataFrame
        DataFrame with columns:
        onset, duration, trial_type, artifact_type, channel, annotator, message

    Notes
    -----
    Expects mat_key to contain an array of shape (N, 2):
        [[onset_1, offset_1],
         [onset_2, offset_2],
         ...]
    with onset/offset in seconds.
    """
    mat_file = Path(mat_file)

    mat = loadmat(str(mat_file))
    if mat_key not in mat:
        available = sorted(k for k in mat.keys() if not k.startswith("__"))
        raise KeyError(
            f"Key '{mat_key}' not found in {mat_file}. "
            f"Available keys: {available}"
        )

    data = mat[mat_key]

    if not hasattr(data, "shape") or len(data.shape) != 2 or data.shape[1] != 2:
        raise ValueError(
            f"Expected '{mat_key}' to have shape (N, 2), got {getattr(data, 'shape', None)}"
        )

    rows = []
    for row in data:
        onset = float(row[0])
        offset = float(row[1])

        if offset < onset:
            onset, offset = offset, onset

        rows.append(
            {
                "onset": onset,
                "duration": offset - onset,
                "trial_type": "artifact",
                "artifact_type": artifact_type,
                "channel": channel,
                "annotator": annotator,
                "message": message,
            }
        )

    df = pd.DataFrame(rows)
    if not df.empty:
        df.sort_values(["onset"], ascending=True, inplace=True, ignore_index=True)

    if out_tsv_gz is not None or out_json is not None:
        if out_tsv_gz is None or out_json is None:
            raise ValueError("Provide both out_tsv_gz and out_json, or neither.")

        out_tsv_gz = Path(out_tsv_gz)
        out_json = Path(out_json)

        sidecar = {
            "Columns": [
                "onset",
                "duration",
                "trial_type",
                "artifact_type",
                "channel",
                "annotator",
                "message",
            ],
            "Description": "Manual artifact intervals created with calinet-artifacts.",
            "trial_type": {
                "Description": "Primary event category.",
                "Levels": {
                    "artifact": "Interval marked as artifact."
                },
            },
            "artifact_type": {
                "Description": "Subtype of artifact."
            },
            "channel": {
                "Description": "Physiological channel to which the artifact applies."
            },
            "annotator": {
                "Description": "Identifier of the annotator."
            },
            "message": {
                "Description": "Optional free-text note."
            },
        }

        if sampling_frequency is not None:
            sidecar["SamplingFrequency"] = sampling_frequency

        cio.write_physio_tsv_gz_headerless(df, out_tsv_gz)
        cio.save_json(out_json, sidecar)

    return df