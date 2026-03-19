# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:

from __future__ import annotations

import os
os.environ["QT_API"] = "pyside6"

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple

import numpy as np
import pandas as pd
from PySide6 import QtCore, QtWidgets, QtGui
import pyqtgraph as pg

import calinet.core.io as cio
from calinet_artifacts.export import mat_to_physioevents_df


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
    if "Columns" in meta and isinstance(meta["Columns"], list):
        cols = [c for c in meta["Columns"] if c in df.columns]
        if cols:
            return cols
    return list(df.columns)


def build_derivative_paths(
    physio_tsv: Path,
    out_root: Optional[Path] = None,
    desc: str = "artifacts",
) -> Tuple[Path, Path]:
    """
    Raw example:
      bonn/sub-001/physio/sub-001_task-acquisition_recording-ecg_physio.tsv.gz

    Output:
      bonn/derivatives/artifacts/sub-001/physio/
        sub-001_task-acquisition_recording-ecg_desc-artifacts_physioevents.tsv.gz
        sub-001_task-acquisition_recording-ecg_desc-artifacts_physioevents.json
    """
    entities = parse_bids_physio_name(physio_tsv)
    sub = entities.get("sub", "unknown")

    raw_root = physio_tsv.parent.parent.parent
    if out_root is None:
        out_root = raw_root / "derivatives" / "artifacts"

    out_dir = out_root / ("sub-" + sub) / "physio"
    out_dir.mkdir(parents=True, exist_ok=True)

    base = physio_tsv.name
    if base.endswith("_physio.tsv.gz"):
        base = base[:-14]
    elif base.endswith("_physio.tsv"):
        base = base[:-11]
    else:
        raise ValueError("Input file does not look like a *_physio.tsv[.gz] file")

    out_base = base + "_desc-" + desc + "_physioevents"
    out_tsv = out_dir / (out_base + ".tsv.gz")
    out_json = out_dir / (out_base + ".json")
    return out_tsv, out_json


def write_physioevents(
    out_tsv_gz: Path,
    out_json: Path,
    intervals: List[ArtifactInterval],
    sampling_frequency: Optional[float] = None,
) -> None:
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
    df.sort_values(
        ["onset"],
        ascending=True,
        inplace=True
    )
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


def read_existing_physioevents(path: Path) -> List[ArtifactInterval]:
    if not path.exists():
        return []

    df = pd.read_csv(path, sep="\t", compression="infer")
    return intervals_from_physioevents_df(df)


def intervals_from_physioevents_df(df: pd.DataFrame) -> List[ArtifactInterval]:
    required = {"onset", "duration"}
    missing = required - set(df.columns)
    if missing:
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
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("calinet-artifacts")
        self.resize(1400, 900)

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

        self.load_existing_btn = QtWidgets.QPushButton("Load from BIDS")
        self.load_existing_btn.clicked.connect(self.load_existing_annotations)
        self.load_existing_btn.setToolTip("Load annotations from the expected BIDS derivatives path")
        controls.addWidget(self.load_existing_btn)

        self.load_custom_btn = QtWidgets.QPushButton("Load custom annotations")
        self.load_custom_btn.clicked.connect(self.load_custom_annotations_dialog)
        self.load_custom_btn.setToolTip("Load annotations from a TSV or MAT file")
        controls.addWidget(self.load_custom_btn)

        self.save_btn = QtWidgets.QPushButton("Save physioevents")
        self.save_btn.clicked.connect(self.save_annotations)
        self.save_btn.setToolTip("Save annotations to BIDS derivatives (physioevents.tsv.gz + JSON)")
        controls.addWidget(self.save_btn)

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
        QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Left), self, activated=lambda: self.pan_view(-0.2))
        QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Right), self, activated=lambda: self.pan_view(0.2))


    def load_annotations_from_df(self, df: pd.DataFrame, source_label: str = "") -> None:
        try:
            items = intervals_from_physioevents_df(df)
        except Exception as e:
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

        if source_label:
            self.info_label.setText(f"Loaded {len(items)} annotation(s) from {source_label}")

        QtWidgets.QMessageBox.information(
            self,
            "Annotations loaded",
            f"Loaded {len(items)} annotation(s) from:\n{source_label}",
        )


    def load_annotations_from_path(self, path: Path) -> None:
        suffixes = [s.lower() for s in path.suffixes]

        try:
            if path.name.lower().endswith(".mat"):
                df = mat_to_physioevents_df(
                    path,
                    channel=self.current_channel or "scr",
                    annotator=self.annotator_edit.text().strip() or "manual",
                )
            else:
                df = cio.read_physio_tsv_headerless(path)
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self,
                "Load error",
                f"Could not read annotations:\n{path}\n\n{e}",
            )
            return

        self.load_annotations_from_df(df, str(path))


    def _create_menu(self) -> None:
        menu = self.menuBar().addMenu("File")

        open_action = QtGui.QAction("Open physio", self)
        open_action.triggered.connect(self.open_physio_dialog)
        menu.addAction(open_action)

        save_action = QtGui.QAction("Save physioevents", self)
        save_action.triggered.connect(self.save_annotations)
        menu.addAction(save_action)

        load_existing_action = QtGui.QAction("Load existing annotations", self)
        load_existing_action.triggered.connect(self.load_existing_annotations)
        menu.addAction(load_existing_action)

        load_custom_action = QtGui.QAction("Load custom annotations", self)
        load_custom_action.triggered.connect(self.load_custom_annotations_dialog)
        menu.addAction(load_custom_action)


    def interval_exists(self, onset: float, offset: float, channel: str, tol: float = 1e-6) -> bool:
        for item in self.table_model.intervals:
            if (
                item.channel == channel
                and abs(item.onset - onset) < tol
                and abs(item.offset - offset) < tol
            ):
                return True
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


    def add_interval_from_drag(self, onset: float, offset: float) -> None:
        if self.current_channel is None:
            return

        lo = float(min(onset, offset))
        hi = float(max(onset, offset))

        if abs(hi - lo) < 1e-6:
            return

        if self.interval_exists(lo, hi, self.current_channel):
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
        self.select_table_row(self._find_interval_row(item))


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
            return

        self.table_model.remove_rows(rows)
        self.rebuild_regions_from_model()


    def sync_region_from_model_change(self, top_left, bottom_right, roles=None) -> None:
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


    def add_interval_from_drag(self, onset: float, offset: float) -> None:
        if self.current_channel is None:
            return

        lo = float(min(onset, offset))
        hi = float(max(onset, offset))

        if abs(hi - lo) < 1e-6:
            return

        if self.interval_exists(lo, hi, self.current_channel):
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

        json_path = cio.infer_json_sidecar(tsv_path)
        if not json_path.exists():
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

        self.plot_widget.setFocus()

        # autoload existing annotations
        self.load_existing_annotations()


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
        if sfreq is not None:
            try:
                return float(sfreq)
            except Exception:
                pass
        return None


    def _guess_default_channel(self, names: List[str]) -> str:
        for target in ["ecg", "scr", "resp", "pupil", "x_coordinate", "y_coordinate"]:
            if target in names:
                return target
        return names[0]


    def on_channel_changed(self, channel: str) -> None:
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
            return
        if self.current_channel not in self.df.columns:
            return

        self.plot_widget.clear()

        x = self.time_axis()
        y = self.df[self.current_channel].to_numpy(dtype=float)

        self.plot_widget.plot(x, y)

        if self.drag_preview is not None:
            self.plot_widget.addItem(self.drag_preview)

        for region in self.regions:
            self.plot_widget.addItem(region)


    def clear_regions(self) -> None:
        for region in self.regions:
            self.plot_widget.removeItem(region)
        self.regions = []


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
        

    def load_existing_annotations(self) -> None:
        if self.physio_tsv is None:
            QtWidgets.QMessageBox.warning(
                self,
                "No physio loaded",
                "Load a physio file first.",
            )
            return

        try:
            out_tsv, _ = build_derivative_paths(self.physio_tsv)
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self,
                "Path error",
                f"Could not determine physioevents path:\n{e}",
            )
            return

        if not out_tsv.exists():
            return

        self.load_annotations_from_path(out_tsv)


    def save_annotations(self) -> None:
        if self.physio_tsv is None:
            QtWidgets.QMessageBox.warning(self, "No file", "Load a physio file first.")
            return

        self.table_model.sort_by_onset()

        out_tsv, out_json = build_derivative_paths(self.physio_tsv)
        write_physioevents(
            out_tsv,
            out_json,
            self.table_model.intervals,
            sampling_frequency=self.sampling_frequency,
        )

        QtWidgets.QMessageBox.information(
            self,
            "Saved",
            "Saved:\n{0}\n{1}".format(str(out_tsv), str(out_json)),
        )


def run(file: Optional[str] = None) -> None:
    app = QtWidgets.QApplication([])
    pg.setConfigOptions(antialias=True)

    window = MainWindow()
    window.show()

    if file:
        try:
            window.load_physio(Path(file))
        except Exception as e:
            QtWidgets.QMessageBox.critical(window, "Error loading file", str(e))

    app.exec()


if __name__ == "__main__":
    run()