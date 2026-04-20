# calinet-artifacts

A lightweight tool for annotating physiological signal artifacts and
exporting results in a BIDS-compatible `physioevents` format.

## Overview

`calinet-artifacts` provides an interactive graphical interface for
identifying and annotating artifacts in physiological recordings such as
SCR, ECG, or eye-tracking signals. The tool supports standardized output
compatible with the BIDS specification and downstream analysis
pipelines.

## Features

-   Interactive GUI for artifact annotation
-   Fast artifact creation via shift + drag
-   Editable annotation table
-   Export to BIDS-compatible `physioevents` format
-   Conversion utilities for MATLAB (`.mat`) artifact files

## Installation

Install via your preferred Python environment:

``` bash
pip install git+https://github.com/gjheij/calinet-artifacts.git
```

## Usage

### Launch GUI for a single file

``` bash
calinet-artifacts --file path/to/file_physio.tsv.gz
```

Optional arguments:

-   `--output-dir PATH` --- specify output directory (default: alongside
    input file)\
-   `--pspm` --- export in PsPM format instead of BIDS\
-   `--debug` --- enable verbose logging

### CLI Structure

``` bash
calinet-artifacts gui --file path/to/file_physio.tsv.gz
```

## Batch Processing (PowerShell)

Script:

    scripts/calinet_artifacts_batch.ps1

### Functionality

-   Recursively discovers `*_recording-<modality>_physio.tsv.gz` files\
-   Supports modalities: `scr` (default), `ecg`, `eye2`\
-   Detects existing artifact outputs in the dataset `derivatives`
    folder\
-   Skips files with existing annotations unless forced\
-   Supports running from project root or subject-level directories

### Example Usage

``` powershell
calinet-artifacts-batch -InputDir 'Z:\CALINET2\converted\amsterdam'
```

``` powershell
calinet-artifacts-batch -InputDir 'Z:\CALINET2\converted\amsterdam\sub-CalinetAmsterdam01'
```

``` powershell
calinet-artifacts-batch -InputDir '...' -Modality ecg
```

``` powershell
calinet-artifacts-batch -InputDir '...' -Force
```

### Output Behavior

-   `OPEN` --- no existing artifact file found
-   `REOPEN` --- existing artifact file found but overridden
-   `SKIP` --- existing artifact file found and preserved

Artifacts are detected under:

    ``<dataset>/derivatives/...``

## Output

-   `*_physioevents.tsv.gz`\
-   `*_physioevents.json`

## MATLAB Conversion

``` python
from calinet_artifacts.export import mat_to_physioevents_df

df = mat_to_physioevents_df("artifacts.mat")
```

## Directory Structure

    dataset/
    ├── sub-*/
    │   └── physio/
    │       └── *_physio.tsv.gz
    └── derivatives/
        └── artifacts/
