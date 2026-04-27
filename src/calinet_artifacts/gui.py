# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:

from __future__ import annotations

import os
os.environ["QT_API"] = "pyside6"

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple, Union

import numpy as np
import pandas as pd
from PySide6 import QtCore, QtWidgets, QtGui
import pyqtgraph as pg

import calinet.core.io as cio
from calinet.imports.pspm import read_pspm_files
from calinet_artifacts.export import mat_to_physioevents_df
from calinet_artifacts.pspm import write_pspm_mat

logger = logging.getLogger(__name__)


@dataclass
class ArtifactInterval:
    onset: float
    offset: float
    artifact_type: str = "unknown"
    channel: str = "ecg"
    annotator: str = "manual"
    note: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.offset - self.onset)


def parse_bids_physio_name(path: Path) -> Dict[str, str]:
    """
    Parses names like:
    sub-001_task-acquisition_recording-ecg_physio.tsv.gz
    """
    stem = path.name
    if stem.endswith(".tsv.gz"):
        stem = stem[:-7]
    elif stem.endswith(".tsv"):
        stem = stem[:-4]

    parts = stem.split("_")
    entities = {}
    suffix = None

    for part in parts:
        if "-" in part:
            key, value = part.split("-", 1)
            entities[key] = value
        else:
            suffix = part

    if suffix is not None:
        entities["suffix"] = suffix

    return entities


def get_channel_names(df: pd.DataFrame, meta: Dict[str, Any]) -> List[str]:
    df_cols = [str(c) for c in df.columns]

    columns = meta.get("Columns")
    if isinstance(columns, str):
        columns = [columns]

    if isinstance(columns, list):
        cols = [str(c) for c in columns if str(c) in df_cols]
        if cols:
            return cols

    return df_cols


def build_derivative_paths(
    physio_tsv: Path,
    out_root: Optional[Path] = None,
    desc: str = "artifacts",
    fmt: str = "bids",
) -> Tuple[Path, Path]:
    """
    Raw example:
      bonn/sub-001/physio/sub-001_task-acquisition_recording-ecg_physio.tsv.gz

    Output:
      bonn/derivatives/artifacts/sub-001/physio/
        sub-001_task-acquisition_recording-ecg_desc-artifacts_physioevents.tsv.gz
        sub-001_task-acquisition_recording-ecg_desc-artifacts_physioevents.json
    """
    logger.debug(
        "Building derivative paths for physio_tsv=%s, out_root=%s, desc=%s, fmt=%s",
        physio_tsv,
        out_root,
        desc,
        fmt,
    )

    entities = parse_bids_physio_name(physio_tsv)
    sub = entities.get("sub", "unknown")
    logger.debug("Parsed BIDS entities=%s, resolved subject=%s", entities, sub)

    raw_root = physio_tsv.parent.parent.parent
    if out_root is None:
        out_root = raw_root / "derivatives" / "artifacts"
        logger.debug("No out_root provided; using default derivative root=%s", out_root)

    out_dir = out_root / f"sub-{sub}" / "physio"
    base = physio_tsv.name

    if base.endswith("_physio.tsv.gz"):
        base = base[:-14]
    elif base.endswith("_physio.tsv"):
        base = base[:-11]
    elif base.endswith(".mat"):
        base = base[:-4]
    else:
        logger.error("Unsupported input filename for derivative path construction: %s", physio_tsv)
        raise ValueError("Input file does not look like a *_physio.tsv[.gz] file")

    if fmt == "bids":
        out_base = f"{base}_desc-{desc}_physioevents"
        out_tsv = out_dir / f"{out_base}.tsv.gz"
    elif fmt.lower() == "pspm":
        out_base = f"missing_{base}"
        out_tsv = out_root / f"{out_base}.mat"
    else:
        logger.error("Invalid fmt=%s while building derivative paths", fmt)
        raise ValueError(f"'fmt' must be one of 'pspm' or 'bids', not '{fmt}'")

    out_json = out_dir / f"{out_base}.json"

    logger.info("Resolved derivative output paths: data=%s sidecar=%s", out_tsv, out_json)
    return out_tsv, out_json


def write_physioevents(
    out_tsv_gz: Path,
    out_json: Path,
    intervals: List[ArtifactInterval],
    sampling_frequency: Optional[float] = None,
) -> None:
    logger.info(
        "Writing physioevents outputs to tsv=%s json=%s with %d intervals",
        out_tsv_gz,
        out_json,
        len(intervals),
    )
    logger.debug("Sampling frequency for physioevents export: %s", sampling_frequency)

    rows = []
    for item in intervals:
        rows.append(
            {
                "onset": float(item.onset),
                "duration": float(item.duration),
                "trial_type": "artifact",
                "artifact_type": item.artifact_type,
                "channel": item.channel,
                "annotator": item.annotator,
                "message": item.note,
            }
        )

    df = pd.DataFrame(rows)
    logger.debug("Constructed physioevents dataframe with shape=%s", df.shape)

    df.sort_values(["onset"], ascending=True, inplace=True)

    out_tsv_gz.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    logger.debug("Ensured output directories exist for %s and %s", out_tsv_gz.parent, out_json.parent)

    cio.write_physio_tsv_gz_headerless(df, out_tsv_gz)

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

    cio.save_json(out_json, sidecar)
    logger.info("Finished writing physioevents outputs")


def read_existing_physioevents(path: Path) -> List[ArtifactInterval]:
    logger.debug("Reading existing physioevents from %s", path)

    if not path.exists():
        logger.info("No existing physioevents file found at %s", path)
        return []

    df = pd.read_csv(path, sep="\t", compression="infer")
    logger.debug("Loaded physioevents dataframe from %s with shape=%s", path, df.shape)

    intervals = intervals_from_physioevents_df(df)
    logger.info("Parsed %d existing intervals from %s", len(intervals), path)
    return intervals


def intervals_from_physioevents_df(df: pd.DataFrame) -> List[ArtifactInterval]:
    logger.debug("Converting dataframe to ArtifactInterval list; columns=%s shape=%s", list(df.columns), df.shape)

    required = {"onset", "duration"}
    missing = required - set(df.columns)
    if missing:
        logger.error("Missing required physioevents columns: %s", sorted(missing))
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    intervals: List[ArtifactInterval] = []
    for _, row in df.iterrows():
        onset = float(row["onset"])
        duration = float(row["duration"])
        intervals.append(
            ArtifactInterval(
                onset=onset,
                offset=onset + duration,
                artifact_type=str(row.get("artifact_type", "artifact")),
                channel=str(row.get("channel", "ecg")),
                annotator=str(row.get("annotator", "manual")),
                note=str(row.get("message", "")),
            )
        )

    logger.info("Converted dataframe to %d ArtifactInterval objects", len(intervals))
    return intervals


class ArtifactViewBox(pg.ViewBox):
    sigIntervalDragged = QtCore.Signal(float, float)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._drag_start_x = None
        self.main_window = None

    def mouseDragEvent(self, ev, axis=None):
        mods = ev.modifiers()

        if ev.button() == QtCore.Qt.LeftButton and (mods & QtCore.Qt.ShiftModifier):
            if ev.isStart():
                start_pos = ev.buttonDownPos()
                self._drag_start_x = float(self.mapToView(start_pos).x())
                if self.main_window is not None:
                    self.main_window.show_drag_preview(self._drag_start_x, self._drag_start_x)
                ev.accept()
                return

            current_x = float(self.mapToView(ev.pos()).x())

            if ev.isFinish():
                if self.main_window is not None:
                    self.main_window.hide_drag_preview()

                if self._drag_start_x is not None:
                    x0 = min(self._drag_start_x, current_x)
                    x1 = max(self._drag_start_x, current_x)
                    if abs(x1 - x0) > 1e-6:
                        self.sigIntervalDragged.emit(x0, x1)

                self._drag_start_x = None
                ev.accept()
                return

            if self._drag_start_x is not None and self.main_window is not None:
                self.main_window.show_drag_preview(self._drag_start_x, current_x)

            ev.accept()
            return

        super().mouseDragEvent(ev, axis=axis)
        

class ArtifactTableModel(QtCore.QAbstractTableModel):
    HEADERS = [
        "onset",
        "offset",
        "duration",
        "artifact_type",
        "channel",
        "annotator",
        "message",
    ]

    def __init__(self, intervals: Optional[List[ArtifactInterval]] = None) -> None:
        super().__init__()
        self.intervals = intervals or []
        self.sort_by_onset()

    def sort_by_onset(self) -> None:
        self.intervals.sort(key=lambda x: (x.onset, x.offset))

    def rowCount(self, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> int:
        return len(self.intervals)

    def columnCount(self, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> int:
        return len(self.HEADERS)

    def headerData(
        self,
        section: int,
        orientation: QtCore.Qt.Orientation,
        role: int = QtCore.Qt.DisplayRole,
    ) -> Any:
        if role != QtCore.Qt.DisplayRole:
            return None
        if orientation == QtCore.Qt.Horizontal:
            return self.HEADERS[section]
        return str(section)

    def data(self, index: QtCore.QModelIndex, role: int = QtCore.Qt.DisplayRole) -> Any:
        if not index.isValid():
            return None

        item = self.intervals[index.row()]
        col = self.HEADERS[index.column()]

        if role in (QtCore.Qt.DisplayRole, QtCore.Qt.EditRole):
            if col == "onset":
                return round(item.onset, 6)
            if col == "offset":
                return round(item.offset, 6)
            if col == "duration":
                return round(item.duration, 6)
            if col == "artifact_type":
                return item.artifact_type
            if col == "channel":
                return item.channel
            if col == "annotator":
                return item.annotator
            if col == "message":
                return item.note
        return None

    def flags(self, index: QtCore.QModelIndex) -> QtCore.Qt.ItemFlags:
        if not index.isValid():
            return QtCore.Qt.ItemIsEnabled
        col = self.HEADERS[index.column()]
        editable = col in {"onset", "offset", "artifact_type", "channel", "annotator", "message"}
        flags = QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsEnabled
        if editable:
            flags |= QtCore.Qt.ItemIsEditable
        return flags

    def setData(
        self,
        index: QtCore.QModelIndex,
        value: Any,
        role: int = QtCore.Qt.EditRole,
    ) -> bool:
        if role != QtCore.Qt.EditRole or not index.isValid():
            return False

        item = self.intervals[index.row()]
        col = self.HEADERS[index.column()]

        try:
            if col == "onset":
                item.onset = float(value)
            elif col == "offset":
                item.offset = float(value)
            elif col == "artifact_type":
                item.artifact_type = str(value)
            elif col == "channel":
                item.channel = str(value)
            elif col == "annotator":
                item.annotator = str(value)
            elif col == "message":
                item.note = str(value)
            else:
                return False
        except Exception:
            return False

        if item.offset < item.onset:
            item.onset, item.offset = item.offset, item.onset

        self.sort_by_onset()
        self.layoutChanged.emit()
        return True

    def add_interval(self, item: ArtifactInterval) -> None:
        self.beginResetModel()
        self.intervals.append(item)
        self.sort_by_onset()
        self.endResetModel()

    def remove_rows(self, rows: List[int]) -> None:
        self.beginResetModel()
        for row in sorted(set(rows), reverse=True):
            del self.intervals[row]
        self.sort_by_onset()
        self.endResetModel()      


class ArtifactRegion(pg.LinearRegionItem):
    sigClicked = QtCore.Signal(int)

    def __init__(self, row_index: int, values: List[float]) -> None:
        super().__init__(
            values=values,
            movable=True,
            brush=(255, 200, 0, 50),
            pen=pg.mkPen((255, 220, 0), width=2),
        )
        self.row_index = row_index
        self.setZValue(10)

    def mouseClickEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton:
            self.sigClicked.emit(self.row_index)
            ev.accept()
            return
        super().mouseClickEvent(ev)
        

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, file=None, output_dir=None, force_pspm=False):
        super().__init__()
        self.setWindowTitle("calinet-artifacts")
        self.resize(1400, 900)

        self.output_dir = Path(output_dir) if output_dir else None
        self.force_pspm = force_pspm

        self.physio_tsv: Optional[Path] = None
        self.physio_json: Optional[Path] = None
        self.meta: Dict[str, Any] = {}
        self.df: Optional[pd.DataFrame] = None
        self.channel_names: List[str] = []
        self.current_channel: Optional[str] = None
        self.sampling_frequency: Optional[float] = None
        self.regions: List[ArtifactRegion] = []

        self.dragging_new_interval = False
        self.drag_start_x = None
        self.drag_preview = None

        self._build_ui()

        if self.force_pspm:
            self.save_pspm_checkbox.setChecked(True)

        if file:
            self.physio_tsv = Path(file)
            try:
                self.load_physio(self.physio_tsv)
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Error loading file", str(e))


    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)

        main_layout = QtWidgets.QVBoxLayout(central)

        controls = QtWidgets.QHBoxLayout()
        main_layout.addLayout(controls)

        self.open_btn = QtWidgets.QPushButton("Open physio")
        self.open_btn.clicked.connect(self.open_physio_dialog)
        controls.addWidget(self.open_btn)

        controls.addWidget(QtWidgets.QLabel("Channel:"))
        self.channel_combo = QtWidgets.QComboBox()
        self.channel_combo.currentTextChanged.connect(self.on_channel_changed)
        controls.addWidget(self.channel_combo)

        controls.addWidget(QtWidgets.QLabel("Artifact type:"))
        self.artifact_type_edit = QtWidgets.QLineEdit("unknown")
        controls.addWidget(self.artifact_type_edit)

        controls.addWidget(QtWidgets.QLabel("Annotator:"))
        self.annotator_edit = QtWidgets.QLineEdit("manual")
        controls.addWidget(self.annotator_edit)

        self.delete_btn = QtWidgets.QPushButton("Delete selected")
        self.delete_btn.clicked.connect(self.delete_selected_rows)
        self.delete_btn.setToolTip("Delete selected annotation rows (or press Delete key)")
        controls.addWidget(self.delete_btn)

        self.load_existing_btn = QtWidgets.QPushButton("Load existing")
        self.load_existing_btn.clicked.connect(self.load_existing_annotations)
        self.load_existing_btn.setToolTip("Load annotations from the expected BIDS derivatives/PsPM path (tick 'As PsPM' box to reload from .mat file or use --pspm in the cli)")
        controls.addWidget(self.load_existing_btn)

        self.load_custom_btn = QtWidgets.QPushButton("Load custom annotations")
        self.load_custom_btn.clicked.connect(self.load_custom_annotations_dialog)
        self.load_custom_btn.setToolTip("Load annotations from a TSV or MAT file")
        controls.addWidget(self.load_custom_btn)

        self.save_btn = QtWidgets.QPushButton("Save physioevents")
        self.save_btn.clicked.connect(self.save_annotations)
        self.save_btn.setToolTip("Save annotations BIDS or PsPM format")
        controls.addWidget(self.save_btn)

        self.save_pspm_checkbox = QtWidgets.QCheckBox("As PsPM")
        self.save_pspm_checkbox.setToolTip("Load/Save annotations PsPM-format (2-column 'missing_<filename>.mat')")
        controls.addWidget(self.save_pspm_checkbox)

        self.help_label = QtWidgets.QLabel("Shift+drag on plot to add artifact | Delete removes selected")
        controls.addWidget(self.help_label)

        controls.addStretch(1)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        main_layout.addWidget(splitter)

        top_widget = QtWidgets.QWidget()
        top_layout = QtWidgets.QVBoxLayout(top_widget)
        splitter.addWidget(top_widget)

        self.view_box = ArtifactViewBox()
        self.view_box.main_window = self
        self.view_box.sigIntervalDragged.connect(self.add_interval_from_drag)

        self.plot_widget = pg.PlotWidget(viewBox=self.view_box)
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.setLabel("bottom", "Time", units="s")
        self.plot_widget.setLabel("left", "Signal")
        top_layout.addWidget(self.plot_widget)

        self.drag_preview = pg.LinearRegionItem(
            values=[0, 0],
            movable=False,
            brush=(80, 160, 255, 50),
            pen=pg.mkPen((80, 160, 255), width=2),
        )
        self.drag_preview.hide()
        self.plot_widget.addItem(self.drag_preview)

        info_row = QtWidgets.QHBoxLayout()
        top_layout.addLayout(info_row)
        self.info_label = QtWidgets.QLabel("No file loaded.")
        info_row.addWidget(self.info_label)
        info_row.addStretch(1)

        bottom_widget = QtWidgets.QWidget()
        bottom_layout = QtWidgets.QVBoxLayout(bottom_widget)
        splitter.addWidget(bottom_widget)

        self.table_model = ArtifactTableModel([])
        self.table_view = QtWidgets.QTableView()
        self.table_view.setModel(self.table_model)
        self.table_view.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table_view.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.table_view.horizontalHeader().setStretchLastSection(True)
        self.table_view.setSortingEnabled(False)
        self.table_view.doubleClicked.connect(self.on_table_double_clicked)
        bottom_layout.addWidget(self.table_view)

        splitter.setSizes([600, 250])

        self.reconnect_model_signals()
        self._create_menu()

        QtGui.QShortcut(QtGui.QKeySequence("Delete"), self, activated=self.delete_selected_rows)
        QtGui.QShortcut(QtGui.QKeySequence("A"), self, activated=self.add_interval_from_current_view)
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+S"), self, activated=self.save_annotations)
        QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Left), self, activated=lambda: self.pan_view(-0.2))
        QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Right), self, activated=lambda: self.pan_view(0.2))


    def load_annotations_from_df(
        self,
        df: pd.DataFrame,
        source_label: str = ""
    ) -> None:
        logger.info("Loading annotations from dataframe source=%s", source_label or "<in-memory>")
        logger.debug("Annotation dataframe shape=%s columns=%s", df.shape, list(df.columns))

        try:
            items = intervals_from_physioevents_df(df)
        except Exception as e:
            logger.exception("Failed to interpret annotations from %s", source_label or "<in-memory>")
            QtWidgets.QMessageBox.critical(
                self,
                "Load error",
                f"Could not interpret annotations from:\n{source_label}\n\n{e}",
            )
            return

        self.table_model = ArtifactTableModel(items)
        self.table_view.setModel(self.table_model)
        self.reconnect_model_signals()
        self.rebuild_regions_from_model()

        logger.info("Loaded %d annotation intervals from %s", len(items), source_label or "<in-memory>")

        if source_label:
            self.info_label.setText(f"Loaded {len(items)} annotation(s) from {source_label}")


    def load_annotations_from_path(self, path: Path) -> None:
        logger.info("Loading annotations from path=%s", path)
        suffixes = [s.lower() for s in path.suffixes]
        logger.debug("Detected suffixes for annotation path %s: %s", path, suffixes)

        try:
            if path.name.lower().endswith(".mat"):
                logger.debug(
                    "Reading PsPM MAT annotations with channel=%s annotator=%s",
                    self.current_channel or "scr",
                    self.annotator_edit.text().strip() or "manual",
                )
                df = mat_to_physioevents_df(
                    path,
                    channel=self.current_channel or "scr",
                    annotator=self.annotator_edit.text().strip() or "manual",
                )
            else:
                logger.debug("Reading TSV-based annotations from %s", path)
                df = cio.read_physio_tsv_headerless(path)
        except Exception as e:
            logger.exception("Could not read annotations from %s", path)
            QtWidgets.QMessageBox.critical(
                self,
                "Load error",
                f"Could not read annotations:\n{path}\n\n{e}",
            )
            return

        logger.debug("Loaded annotation dataframe from %s with shape=%s", path, df.shape)
        self.load_annotations_from_df(df, str(path))


    def _create_menu(self) -> None:
        menu = self.menuBar().addMenu("File")

        open_action = QtGui.QAction("Open physio", self)
        open_action.triggered.connect(self.open_physio_dialog)
        menu.addAction(open_action)

        save_action = QtGui.QAction("Save physioevents", self)
        save_action.setShortcut(QtGui.QKeySequence("Ctrl+S"))
        save_action.triggered.connect(self.save_annotations)
        menu.addAction(save_action)

        load_existing_action = QtGui.QAction("Load existing annotations", self)
        load_existing_action.triggered.connect(self.load_existing_annotations)
        menu.addAction(load_existing_action)

        load_custom_action = QtGui.QAction("Load custom annotations", self)
        load_custom_action.triggered.connect(self.load_custom_annotations_dialog)
        menu.addAction(load_custom_action)


    def interval_exists(
        self,
        onset: float,
        offset: float,
        channel: str,
        tol: float = 1e-6
    ) -> bool:
        logger.debug(
            "Checking for existing interval onset=%s offset=%s channel=%s tol=%s",
            onset,
            offset,
            channel,
            tol,
        )

        for item in self.table_model.intervals:
            if (
                item.channel == channel
                and abs(item.onset - onset) < tol
                and abs(item.offset - offset) < tol
            ):
                logger.debug("Found matching existing interval for channel=%s", channel)
                return True

        logger.debug("No matching interval found for channel=%s", channel)
        return False


    def show_drag_preview(self, x0: float, x1: float) -> None:
        lo = min(x0, x1)
        hi = max(x0, x1)
        old = self.drag_preview.blockSignals(True)
        self.drag_preview.setRegion([lo, hi])
        self.drag_preview.blockSignals(old)
        self.drag_preview.show()


    def hide_drag_preview(self) -> None:
        self.drag_preview.hide()


    def rebuild_regions_from_model(self) -> None:
        self.clear_regions()

        for i, item in enumerate(self.table_model.intervals):
            region = ArtifactRegion(i, [item.onset, item.offset])
            region.sigRegionChangeFinished.connect(self.on_region_changed)
            region.sigClicked.connect(self.select_table_row)
            self.regions.append(region)
            self.plot_widget.addItem(region)


    def on_model_reordered(self) -> None:
        self.rebuild_regions_from_model()


    def select_table_row(self, row: int) -> None:
        if row < 0 or row >= len(self.table_model.intervals):
            return

        self.table_view.selectRow(row)
        self.table_view.scrollTo(self.table_model.index(row, 0))


    def center_on_interval(self, row: int) -> None:
        if row < 0 or row >= len(self.table_model.intervals):
            return

        item = self.table_model.intervals[row]
        vb = self.plot_widget.getViewBox()
        x_range, y_range = vb.viewRange()

        width = max(item.duration * 4.0, 5.0)
        center = 0.5 * (item.onset + item.offset)
        vb.setXRange(center - width / 2.0, center + width / 2.0, padding=0)


    def on_table_double_clicked(self, index: QtCore.QModelIndex) -> None:
        if not index.isValid():
            return
        self.center_on_interval(index.row())


    def pan_view(self, frac: float) -> None:
        vb = self.plot_widget.getViewBox()
        x_range, _ = vb.viewRange()
        width = x_range[1] - x_range[0]
        shift = frac * width
        vb.setXRange(x_range[0] + shift, x_range[1] + shift, padding=0)


    def add_interval_from_drag(
        self,
        onset: float,
        offset: float
    ) -> None:
        logger.debug(
            "Adding interval from drag with onset=%s offset=%s current_channel=%s",
            onset,
            offset,
            self.current_channel,
        )

        if self.current_channel is None:
            logger.debug("Ignoring dragged interval because no current channel is selected")
            return

        lo = float(min(onset, offset))
        hi = float(max(onset, offset))

        if abs(hi - lo) < 1e-6:
            logger.debug("Ignoring degenerate dragged interval: [%s, %s]", lo, hi)
            return

        if self.interval_exists(lo, hi, self.current_channel):
            logger.info(
                "Skipped adding duplicate interval [%s, %s] for channel=%s",
                lo,
                hi,
                self.current_channel,
            )
            return

        item = ArtifactInterval(
            onset=lo,
            offset=hi,
            artifact_type=self.artifact_type_edit.text().strip() or "unknown",
            channel=self.current_channel,
            annotator=self.annotator_edit.text().strip() or "manual",
            note="",
        )
        self.table_model.add_interval(item)
        self.rebuild_regions_from_model()

        logger.info(
            "Added interval onset=%s offset=%s channel=%s artifact_type=%s annotator=%s",
            item.onset,
            item.offset,
            item.channel,
            item.artifact_type,
            item.annotator,
        )


    def _find_interval_row(self, item: ArtifactInterval) -> int:
        for i, current in enumerate(self.table_model.intervals):
            if current is item:
                return i
        return -1


    def add_interval_from_current_view(self) -> None:
        if self.current_channel is None:
            return

        vb = self.plot_widget.getViewBox()
        x_range, _ = vb.viewRange()
        width = x_range[1] - x_range[0]
        if width <= 0:
            return

        center = 0.5 * (x_range[0] + x_range[1])
        lo = center - 0.05 * width
        hi = center + 0.05 * width

        self.add_interval_from_drag(lo, hi)


    def on_region_changed(self) -> None:
        region = self.sender()
        if region is None:
            return

        row = region.row_index
        if row < 0 or row >= len(self.table_model.intervals):
            return

        lo, hi = region.getRegion()
        item = self.table_model.intervals[row]
        item.onset = float(min(lo, hi))
        item.offset = float(max(lo, hi))

        self.table_model.sort_by_onset()
        self.table_model.layoutChanged.emit()


    def delete_selected_rows(self) -> None:
        selection = self.table_view.selectionModel().selectedRows()
        rows = [idx.row() for idx in selection]

        if not rows:
            logger.debug("Delete requested but no rows selected")
            return

        logger.info("Deleting %d interval(s): rows=%s", len(rows), rows)

        self.table_model.remove_rows(rows)
        self.rebuild_regions_from_model()

        logger.debug("Finished deleting rows and rebuilding regions")


    def sync_region_from_model_change(
            self,
            top_left
        ) -> None:

        row = top_left.row()
        if row < 0 or row >= len(self.regions):
            return

        item = self.table_model.intervals[row]
        region = self.regions[row]

        old = region.blockSignals(True)
        region.setRegion([item.onset, item.offset])
        region.blockSignals(old)


    def clear_regions(self) -> None:
        for region in self.regions:
            self.plot_widget.removeItem(region)
        self.regions = []


    def open_physio_dialog(self) -> None:
        path_str, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Open physio TSV",
            "",
            "TSV files (*.tsv *.tsv.gz)",
        )
        if not path_str:
            return
        self.load_physio(Path(path_str))


    def load_physio(self, tsv_path: Path) -> None:
        logger.info("Loading physio file: %s", tsv_path)

        if tsv_path.suffix != ".mat":
            json_path = cio.infer_json_sidecar(tsv_path)
            logger.debug("Resolved JSON sidecar path: %s", json_path)

            if not json_path.exists():
                logger.error("Missing JSON sidecar for physio file %s; expected %s", tsv_path, json_path)
                QtWidgets.QMessageBox.critical(
                    self,
                    "Missing JSON",
                    "Could not find matching JSON sidecar:\n{0}".format(str(json_path)),
                )
                return

            self.physio_tsv = tsv_path
            self.physio_json = json_path
            self.meta = cio.load_json(json_path)
            self.df = cio.read_physio_tsv_headerless(tsv_path)
            self.channel_names = get_channel_names(self.df, self.meta)
            self.sampling_frequency = self._infer_sampling_frequency()

            logger.debug(
                "Loaded BIDS physio dataframe shape=%s, channels=%s, sampling_frequency=%s",
                None if self.df is None else self.df.shape,
                self.channel_names,
                self.sampling_frequency,
            )
        else:
            logger.info("Loading PsPM MAT physio file: %s", tsv_path)
            self.save_pspm_checkbox.setChecked(True)
            self.force_pspm = True

            res = read_pspm_files(tsv_path)

            self.df = res.df
            self.sampling_frequency = res.sampling_rate_hz
            chan_info = res.channel_info

            self.channel_names = [i.lower() for i in chan_info["output_name"].tolist()]
            self.df.columns = self.channel_names

            logger.debug(
                "Loaded PsPM dataframe shape=%s, channels=%s, sampling_frequency=%s",
                None if self.df is None else self.df.shape,
                self.channel_names,
                self.sampling_frequency,
            )

        self.channel_combo.blockSignals(True)
        self.channel_combo.clear()
        self.channel_combo.addItems(self.channel_names)
        self.channel_combo.blockSignals(False)

        if self.channel_names:
            preferred = self._guess_default_channel(self.channel_names)
            idx = self.channel_combo.findText(preferred)
            if idx < 0:
                idx = 0
            self.channel_combo.setCurrentIndex(idx)
            self.current_channel = self.channel_combo.currentText()

            logger.debug("Selected default channel=%s", self.current_channel)
            self.plot_current_channel()

        self.table_model = ArtifactTableModel([])
        self.table_view.setModel(self.table_model)
        self.reconnect_model_signals()
        self.clear_regions()

        self.info_label.setText(
            "Loaded: {0} | samples={1} | sfreq={2}".format(
                tsv_path.name,
                len(self.df.index),
                self.sampling_frequency,
            )
        )

        logger.info(
            "Finished loading physio file %s with %d samples and %d channels",
            tsv_path,
            len(self.df.index),
            len(self.channel_names),
        )

        self.plot_widget.setFocus()
        self.load_existing_annotations(suppress_msg=True)


    def reconnect_model_signals(self) -> None:
        try:
            self.table_model.dataChanged.disconnect()
        except Exception:
            pass
        try:
            self.table_model.layoutChanged.disconnect()
        except Exception:
            pass

        self.table_model.dataChanged.connect(self.sync_region_from_model_change)
        self.table_model.layoutChanged.connect(self.on_model_reordered)


    def _infer_sampling_frequency(self) -> Optional[float]:
        sfreq = self.meta.get("SamplingFrequency")
        logger.debug("Inferring sampling frequency from metadata value=%s", sfreq)

        if sfreq is not None:
            try:
                resolved = float(sfreq)
                logger.debug("Resolved sampling frequency=%s", resolved)
                return resolved
            except Exception:
                logger.exception("Failed to parse SamplingFrequency from metadata: %s", sfreq)

        logger.debug("Sampling frequency unavailable in metadata")
        return None


    def _guess_default_channel(self, names: List[str]) -> str:
        for target in ["ecg", "scr", "resp", "pupil", "x_coordinate", "y_coordinate"]:
            if target in names:
                return target
        return names[0]


    def on_channel_changed(self, channel: str) -> None:
        logger.info("Channel changed to %s", channel)
        self.current_channel = channel
        self.plot_current_channel()


    def time_axis(self) -> np.ndarray:
        if self.df is None:
            return np.array([])
        n = len(self.df.index)
        if self.sampling_frequency and self.sampling_frequency > 0:
            return np.arange(n, dtype=float) / float(self.sampling_frequency)
        return np.arange(n, dtype=float)


    def plot_current_channel(self) -> None:
        if self.df is None or self.current_channel is None:
            logger.debug("Skipping plot_current_channel because dataframe or channel is missing")
            return
        if self.current_channel not in self.df.columns:
            logger.warning("Requested channel %s is not present in dataframe columns", self.current_channel)
            return

        logger.debug("Plotting channel=%s", self.current_channel)

        self.plot_widget.clear()

        x = self.time_axis()
        y = self.df[self.current_channel].to_numpy(dtype=float)

        logger.debug(
            "Plot data prepared for channel=%s with n_samples=%d x_range=(%s, %s)",
            self.current_channel,
            len(y),
            x[0] if len(x) else None,
            x[-1] if len(x) else None,
        )

        self.plot_widget.plot(x, y)

        if self.drag_preview is not None:
            self.plot_widget.addItem(self.drag_preview)

        for region in self.regions:
            self.plot_widget.addItem(region)


    def load_custom_annotations_dialog(self) -> None:
        path_str, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Load annotations",
            "",
            "Annotation files (*.tsv *.tsv.gz *.mat);;TSV files (*.tsv *.tsv.gz);;MAT files (*.mat)",
        )
        if not path_str:
            return

        self.load_annotations_from_path(Path(path_str))
        

    def load_existing_annotations(
        self,
        suppress_msg: bool = False,
    ) -> None:
        if self.physio_tsv is None:
            logger.info("Cannot load existing annotations because no physio file is loaded")
            QtWidgets.QMessageBox.warning(
                self,
                "No physio loaded",
                "Load a physio file first.",
            )
            return

        use_pspm = self.force_pspm or self.save_pspm_checkbox.isChecked()
        logger.info("Loading existing annotations for %s using format=%s", self.physio_tsv, "pspm" if use_pspm else "bids")

        try:
            out_tsv, _ = build_derivative_paths(
                self.physio_tsv,
                out_root=self.output_dir,
                fmt="pspm" if use_pspm else "bids",
            )
        except Exception as e:
            logger.exception("Could not determine existing annotation path for %s", self.physio_tsv)
            if not suppress_msg:
                QtWidgets.QMessageBox.critical(
                    self,
                    "Path error",
                    f"Could not determine annotation path:\n{e}",
                )
            return

        logger.debug("Resolved existing annotation candidate path: %s", out_tsv)

        if not out_tsv.exists():
            logger.info("No existing annotation file found at %s", out_tsv)
            if not suppress_msg:
                QtWidgets.QMessageBox.information(
                    self,
                    "Not found",
                    f"No existing annotation file found:\n{out_tsv}",
                )
            return

        logger.info("Found existing annotation file at %s", out_tsv)
        self.load_annotations_from_path(out_tsv)


    def save_annotations(self) -> None:
        if self.physio_tsv is None:
            logger.info("Save requested without a loaded physio file")
            QtWidgets.QMessageBox.warning(self, "No file", "Load a physio file first.")
            return

        self.table_model.sort_by_onset()
        interval_count = len(self.table_model.intervals)
        logger.info(
            "Saving %d annotation intervals for physio=%s",
            interval_count,
            self.physio_tsv,
        )

        if self.save_pspm_checkbox.isChecked():
            logger.debug("Save mode resolved to PsPM")
            out_mat, _ = build_derivative_paths(
                self.physio_tsv,
                out_root=self.output_dir,
                fmt="pspm"
            )

            out_mat.parent.mkdir(parents=True, exist_ok=True)
            logger.debug("Ensured PsPM output directory exists: %s", out_mat.parent)

            write_pspm_mat(
                out_mat,
                self.table_model.intervals,
            )

            logger.info("Saved PsPM annotations to %s", out_mat)
            QtWidgets.QMessageBox.information(
                self,
                "Saved",
                f"Saved PsPM file:\n{out_mat}",
            )

        else:
            logger.debug("Save mode resolved to BIDS physioevents")
            out_tsv, out_json = build_derivative_paths(
                self.physio_tsv,
                out_root=self.output_dir,
                fmt="bids"
            )

            write_physioevents(
                out_tsv,
                out_json,
                self.table_model.intervals,
                sampling_frequency=self.sampling_frequency,
            )

            logger.info("Saved BIDS physioevents to tsv=%s json=%s", out_tsv, out_json)
            QtWidgets.QMessageBox.information(
                self,
                "Saved",
                "Saved:\n{0}\n{1}".format(str(out_tsv), str(out_json)),
            )


def run(
    file: Optional[str] = None,
    output_dir: Optional[Union[str, Path]] = None,
    force_pspm: Optional[bool] = False
) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    logger.info(
        "Starting calinet-artifacts GUI with file=%s output_dir=%s force_pspm=%s",
        file,
        output_dir,
        force_pspm,
    )

    app = QtWidgets.QApplication([])
    pg.setConfigOptions(antialias=True)

    window = MainWindow(
        file=file,
        output_dir=output_dir,
        force_pspm=force_pspm
    )
    window.show()

    logger.debug("Entering Qt event loop")
    app.exec()
    logger.info("Exiting application")


if __name__ == "__main__":
    run()