# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:

import numpy as np
from typing import List
from .models import ArtifactInterval
from scipy.io import loadmat, savemat

def load_pspm_mat(path: str, key: str = "artifacts") -> List[ArtifactInterval]:
    mat = loadmat(path)
    arr = mat[key]
    intervals = []
    for row in arr:
        intervals.append(
            ArtifactInterval(
                onset=float(row[0]),
                offset=float(row[1])
            )
        )
        
    return intervals

def write_pspm_mat(path, intervals):
    epochs = np.array(
        [[i.onset, i.offset] for i in intervals],
        dtype=float,
    )
    
    path.parent.mkdir(parents=True, exist_ok=True)
    savemat(path, {"epochs": epochs})
    