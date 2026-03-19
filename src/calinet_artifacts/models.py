# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:

from dataclasses import dataclass
from typing import Optional

@dataclass
class ArtifactInterval:
    onset: float
    offset: float
    artifact_type: str = "unknown"
    channel: str = "ecg"
    annotator: str = "manual"
    sample_onset: Optional[int] = None
    sample_offset: Optional[int] = None

    @property
    def duration(self) -> float:
        return self.offset - self.onset