"""Option dialogs of File ▸ Export (DESIGN.md §4.8, §5.5).

The dialogs only collect options; the actual export lives in ``io/export.py`` and
``ImpedancePlot.export_image`` and is driven by ``MainWindow``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from simple_pi_calculator.io.export import TouchstoneOptions


def _buttons(dialog: QDialog) -> QDialogButtonBox:
    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                           | QDialogButtonBox.StandardButton.Cancel, dialog)
    box.accepted.connect(dialog.accept)
    box.rejected.connect(dialog.reject)
    return box


class CsvExportDialog(QDialog):
    """CSV layout: one file with all PWRs (default) or one file per PWR."""

    def __init__(self, parent: QWidget | None = None, one_file_per_pwr: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Export Results CSV")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Values are written in Ω with 10 significant digits.", self))
        self.single = QRadioButton("One file: Frequency (Hz) and |Z|, Re Z, Im Z per PWR", self)
        self.per_pwr = QRadioButton("One file per PWR (<project>_<PWR>.csv in a folder)", self)
        group = QButtonGroup(self)
        group.addButton(self.single)
        group.addButton(self.per_pwr)
        (self.per_pwr if one_file_per_pwr else self.single).setChecked(True)
        layout.addWidget(self.single)
        layout.addWidget(self.per_pwr)
        layout.addWidget(_buttons(self))

    def one_file_per_pwr(self) -> bool:
        return self.per_pwr.isChecked()


class TouchstoneExportDialog(QDialog):
    """Touchstone v1 options: per-PWR ``.s1p`` or one uncoupled ``.sNp``; Z or S; RI or MA; R."""

    def __init__(self, parent: QWidget | None = None, n_pwrs: int = 1,
                 options: TouchstoneOptions | None = None, combined: bool = False):
        super().__init__(parent)
        options = options or TouchstoneOptions()
        self.setWindowTitle("Export Touchstone")
        layout = QVBoxLayout(self)
        self.per_pwr = QRadioButton("One 1-port file per PWR (.s1p)", self)
        self.combined = QRadioButton(f"One {n_pwrs}-port file (.s{n_pwrs}p), PWRs as uncoupled "
                                     "ports (off-diagonal = 0)", self)
        group = QButtonGroup(self)
        group.addButton(self.per_pwr)
        group.addButton(self.combined)
        if n_pwrs > 99:
            self.combined.setEnabled(False)
            combined = False
        (self.combined if combined else self.per_pwr).setChecked(True)
        layout.addWidget(self.per_pwr)
        layout.addWidget(self.combined)
        form = QFormLayout()
        self.parameter = QComboBox(self)
        self.parameter.addItem("S (S11 = (Z − R)/(Z + R))", "S")
        self.parameter.addItem("Z (normalised to R)", "Z")
        self.parameter.setCurrentIndex(0 if options.parameter.upper() == "S" else 1)
        form.addRow("Parameter:", self.parameter)
        self.data_format = QComboBox(self)
        self.data_format.addItem("RI (real, imaginary)", "RI")
        self.data_format.addItem("MA (magnitude, angle °)", "MA")
        self.data_format.setCurrentIndex(0 if options.data_format.upper() == "RI" else 1)
        form.addRow("Data format:", self.data_format)
        self.freq_unit = QComboBox(self)
        self.freq_unit.addItem("Hz", "Hz")
        self.freq_unit.setEnabled(False)
        form.addRow("Frequency unit:", self.freq_unit)
        self.r_ref = QDoubleSpinBox(self)
        self.r_ref.setDecimals(6)
        self.r_ref.setRange(1e-6, 1e6)
        self.r_ref.setValue(float(options.r_ref))
        self.r_ref.setSuffix(" Ω")
        form.addRow("Reference R:", self.r_ref)
        layout.addLayout(form)
        note = QLabel("R = 1 Ω is the usual PDN convention (S11 stays well resolved for mΩ "
                      "impedances).", self)
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addWidget(_buttons(self))

    def is_combined(self) -> bool:
        return self.combined.isChecked()

    def options(self) -> TouchstoneOptions:
        return TouchstoneOptions(parameter=str(self.parameter.currentData()),
                                 data_format=str(self.data_format.currentData()),
                                 r_ref=float(self.r_ref.value()))


@dataclass
class PlotImagesOptions:
    folder: str = ""
    fmt: str = "png"
    width: int = 1600
    height: int = 1000
    keep_zoom: bool = False


class ExportPlotsDialog(QDialog):
    """Folder, format (PNG/SVG), size and "keep current zoom" for Export all plots."""

    def __init__(self, parent: QWidget | None = None, options: PlotImagesOptions | None = None):
        super().__init__(parent)
        options = options or PlotImagesOptions()
        self.setWindowTitle("Export All Plots")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        row = QHBoxLayout()
        self.folder = QLineEdit(options.folder, self)
        browse = QPushButton("Browse…", self)
        browse.clicked.connect(self._browse)
        row.addWidget(self.folder, 1)
        row.addWidget(browse)
        form.addRow("Folder:", row)
        self.fmt = QComboBox(self)
        self.fmt.addItem("PNG", "png")
        self.fmt.addItem("SVG", "svg")
        self.fmt.setCurrentIndex(0 if options.fmt.lower() == "png" else 1)
        form.addRow("Format:", self.fmt)
        self.width = QSpinBox(self)
        self.width.setRange(200, 10000)
        self.width.setValue(int(options.width))
        self.width.setSuffix(" px")
        form.addRow("Width:", self.width)
        self.height = QSpinBox(self)
        self.height.setRange(200, 10000)
        self.height.setValue(int(options.height))
        self.height.setSuffix(" px")
        form.addRow("Height:", self.height)
        self.keep_zoom = QCheckBox("Keep current zoom (otherwise the default view)", self)
        self.keep_zoom.setChecked(bool(options.keep_zoom))
        form.addRow("", self.keep_zoom)
        layout.addLayout(form)
        layout.addWidget(QLabel("Writes All_PWRs.<ext> and one image per PWR tab, with the "
                                "current unit and marker settings.", self))
        layout.addWidget(_buttons(self))

    def _browse(self) -> None:
        start = self.folder.text() or os.path.expanduser("~")
        folder = QFileDialog.getExistingDirectory(self, "Export All Plots", start)
        if folder:
            self.folder.setText(folder)

    def options(self) -> PlotImagesOptions:
        return PlotImagesOptions(folder=self.folder.text().strip(),
                                 fmt=str(self.fmt.currentData()), width=int(self.width.value()),
                                 height=int(self.height.value()),
                                 keep_zoom=self.keep_zoom.isChecked())


__all__ = ["CsvExportDialog", "TouchstoneExportDialog", "ExportPlotsDialog", "PlotImagesOptions"]
