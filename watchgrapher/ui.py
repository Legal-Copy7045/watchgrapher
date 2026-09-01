"""Qt front end: live trace, readouts, six-position table, advice."""

from __future__ import annotations

import csv
import math
import os
import threading
import time
import traceback
from datetime import datetime, timedelta

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from . import __version__
from . import (audio, advisor, catalog as catdb, collection as coll,
               faults, references as refdb, report as reportmod, timesync)
from .analysis import (AnalyzerConfig, analyze, autotune, trace_points,
                       solve_lift_angle, tuning_score, reserve_analytics)
from .calibers import (CALIBERS, GROUP_ORDER, STANDARD_BPH, grouped,
                       load_user_calibers, search)

pg.setConfigOptions(antialias=True, background="#12151a", foreground="#c8d0dc")

# Everything the app writes lives beside the package, in named folders, so a
# report or a collection is somewhere you can find it later rather than
# wherever the last file dialog happened to be pointing.
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT_DIR = os.path.join(APP_DIR, "reports")
COLLECTION_DIR = os.path.join(APP_DIR, "watches")
for _d in (REPORT_DIR, COLLECTION_DIR):
    os.makedirs(_d, exist_ok=True)

ACCENT = "#4da3ff"
TICK_C = "#4da3ff"
TOCK_C = "#ff9d4d"
SEV_COLOR = {"critical": "#ff5d5d", "warn": "#ffb648", "info": "#7fb2ff", "good": "#57d38c"}


# ==========================================================================
# Worker
# ==========================================================================

class AnalysisWorker(QtCore.QObject):
    result = QtCore.Signal(object)
    failed = QtCore.Signal(str)
    tuned = QtCore.Signal(object, object)
    tune_progress = QtCore.Signal(int, int)
    tune_failed = QtCore.Signal(str)

    def __init__(self):
        super().__init__()
        self.recorder = None
        self.cfg = AnalyzerConfig()
        self.window_s = 20.0
        self.tune_budget_s = 15.0
        self._running = False
        # A plain flag rather than a queued slot call: run() below is a
        # blocking loop, so it owns the thread and never returns to an event
        # loop where a queued invocation could be delivered.
        self._tune = threading.Event()
        self._tune_cancel = threading.Event()

    def request_tune(self):
        self._tune_cancel.clear()
        self._tune.set()

    def cancel_tune(self):
        self._tune_cancel.set()

    @QtCore.Slot()
    def run(self):
        self._running = True
        while self._running:
            rec = self.recorder
            if rec is None:
                time.sleep(0.2)
                continue
            try:
                if self._tune.is_set():
                    self._tune.clear()
                    # Whatever happens in here, the UI must be told, or the
                    # button sits on "Tuning..." forever with no way back.
                    try:
                        data = rec.read(min(10.0, max(6.0, self.window_s)))
                        if data.size < rec.samplerate * 2:
                            self.tune_failed.emit(
                                "Not enough audio yet. Let it listen for about ten "
                                "seconds, then tune.")
                        else:
                            best, rows = autotune(
                                data, rec.samplerate, self.cfg,
                                progress=lambda a, b: self.tune_progress.emit(a, b),
                                cancelled=self._tune_cancel.is_set,
                                deadline=time.monotonic() + self.tune_budget_s)
                            if self._tune_cancel.is_set():
                                self.tune_failed.emit("Tuning cancelled.")
                            else:
                                self.tuned.emit(best, rows)
                    except Exception:
                        self.tune_failed.emit(
                            "Tuning failed:\n\n" + traceback.format_exc(limit=4))
                    continue

                data = rec.read(self.window_s)
                if data.size < rec.samplerate // 2:
                    time.sleep(0.3)
                    continue
                m = analyze(data, rec.samplerate, self.cfg)
                self.result.emit(m)
            except Exception:
                self.failed.emit(traceback.format_exc(limit=3))
                time.sleep(1.0)
            time.sleep(0.35)

    @QtCore.Slot()
    def stop(self):
        self._running = False


# ==========================================================================
# Widgets
# ==========================================================================

class RunFinished(QtWidgets.QDialog):
    """
    What to do with a completed run.

    Four outcomes, stated as buttons rather than buried in a menu, because a
    measurement you have just spent a minute taking should not be one stray
    click away from being lost. The capture checkbox is separate: filing the
    run against a watch and recording this position in the current six-position
    session are different things, and you usually want both.
    """

    def __init__(self, summary, parent=None, title="Test finished"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(470)
        self.choice = "discard"
        self.watch_id = None
        self.capture = False

        lay = QtWidgets.QVBoxLayout(self)
        head = QtWidgets.QLabel(title)
        f = head.font()
        f.setPointSize(15)
        f.setBold(True)
        head.setFont(f)
        lay.addWidget(head)

        body = QtWidgets.QLabel(summary)
        body.setStyleSheet(
            "background:#1a1f27;border:1px solid #2a323e;border-radius:6px;padding:10px;"
            "font-family:Consolas,monospace;")
        body.setWordWrap(True)
        lay.addWidget(body)

        pos = parent.cmb_pos.currentText() if parent else "this position"
        already = bool(parent and any(
            r.position == pos and r.wind_state == parent.cmb_wind.currentText()
            for r in parent.readings))
        self.chk = QtWidgets.QCheckBox(f"Also record this as {pos} in the current session")
        self.chk.setChecked(not already)
        if already:
            self.chk.setText(f"Replace nothing -- {pos} is already captured this session")
            self.chk.setEnabled(False)
        lay.addWidget(self.chk)

        lay.addWidget(self._rule("WHAT NEXT?"))

        watches = parent.collection.sorted_watches() if parent else []
        rowW = QtWidgets.QWidget()
        rl = QtWidgets.QHBoxLayout(rowW)
        rl.setContentsMargins(0, 0, 0, 0)
        self.cmb = QtWidgets.QComboBox()
        for w in watches:
            self.cmb.addItem(w.label, w.id)
        cur = parent.cmb_watch.currentData() if parent else None
        if cur:
            i = self.cmb.findData(cur)
            if i >= 0:
                self.cmb.setCurrentIndex(i)
        b_save = QtWidgets.QPushButton("Save to this watch")
        b_save.setMinimumHeight(34)
        b_save.setStyleSheet(
            f"QPushButton{{background:{ACCENT};color:#08101c;font-weight:bold;"
            "padding:8px 14px;border-radius:6px;}")
        b_save.clicked.connect(lambda: self._pick("existing"))
        rl.addWidget(self.cmb, 1)
        rl.addWidget(b_save, 0)
        if not watches:
            self.cmb.addItem("(no watches yet)", None)
            self.cmb.setEnabled(False)
            b_save.setEnabled(False)
        lay.addWidget(rowW)

        for label, key, tip in (
                ("Create a new watch and save this run to it", "new",
                 "Opens a blank profile. Pick brand, model and reference and the rest "
                 "fills itself in."),
                ("Print / save a report for this run", "print",
                 "One self-contained HTML page. Print to PDF from the browser."),
                ("Discard this run", "discard",
                 "Files nothing. The readouts stay on screen.")):
            b = QtWidgets.QPushButton(label)
            b.setMinimumHeight(32)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, k=key: self._pick(k))
            lay.addWidget(b)

    @staticmethod
    def _rule(text):
        l = QtWidgets.QLabel(text)
        l.setStyleSheet("color:#4da3ff;font-weight:bold;font-size:11px;"
                        "letter-spacing:.06em;margin-top:8px;")
        return l

    def _pick(self, key):
        self.choice = key
        self.capture = self.chk.isChecked() and self.chk.isEnabled()
        if key == "existing":
            self.watch_id = self.cmb.currentData()
        self.accept()


class WatchEditor(QtWidgets.QDialog):
    """
    Watch profile editor.

    The reference field is the pivot. Picking a known reference fills in the
    movement, case metal, bezel, crystal and nickname, because that
    information is already implied by the number -- 126613LB is a yellow
    Rolesor Submariner with a blue bezel that everyone calls a Bluesy, and
    retyping that is just an opportunity to get it wrong. Every derived field
    stays editable: the database will not know about your redial or your
    service replacement bezel.
    """

    def __init__(self, watch=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Watch profile")
        self.resize(700, 720)
        self.watch = watch or coll.Watch()
        self._photo_src = None

        outer = QtWidgets.QVBoxLayout(self)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        inner = QtWidgets.QWidget()
        f = QtWidgets.QFormLayout(inner)
        f.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)

        def line(val=""):
            e = QtWidgets.QLineEdit(val or "")
            return e

        # --- identity ---
        f.addRow(self._hdr("Identity"))
        self.e_nick = line(self.watch.nickname)
        self.e_nick.setPlaceholderText("Bluesy, Batman, the beater ...")
        self.cmb_brand = QtWidgets.QComboBox()
        self.cmb_brand.setEditable(True)
        self.cmb_brand.addItems([""] + catdb.brands())
        self.cmb_brand.setCurrentText(self.watch.brand)
        self.cmb_brand.currentTextChanged.connect(self._brand_changed)
        self.cmb_model = QtWidgets.QComboBox()
        self.cmb_model.setEditable(True)
        self.cmb_model.currentTextChanged.connect(self._model_changed)
        self.cmb_ref = QtWidgets.QComboBox()
        self.cmb_ref.setEditable(True)
        self.cmb_ref.currentTextChanged.connect(self._ref_changed)
        self.lbl_ref = QtWidgets.QLabel("")
        self.lbl_ref.setWordWrap(True)
        self.lbl_ref.setStyleSheet("color:#8a94a4;font-size:11px;")
        f.addRow("Nickname", self.e_nick)
        f.addRow("Brand", self.cmb_brand)
        f.addRow("Model", self.cmb_model)
        f.addRow("Reference", self.cmb_ref)
        f.addRow("", self.lbl_ref)

        # --- movement ---
        f.addRow(self._hdr("Movement"))
        self.cmb_cal = QtWidgets.QComboBox()
        self.cmb_cal.setMaxVisibleItems(25)
        self._fill_cal_combo()
        self.e_lift = line(str(self.watch.lift_angle) if self.watch.lift_angle else "")
        self.e_lift.setPlaceholderText("blank = use the caliber's own value")
        self.e_mserial = line(self.watch.movement_serial)
        self.e_target = line(self.watch.target_rate)
        self.e_target.setPlaceholderText("s/day you are regulating toward, e.g. 0 or +2")
        f.addRow("Caliber", self.cmb_cal)
        f.addRow("Lift angle override", self.e_lift)
        f.addRow("Movement serial", self.e_mserial)
        f.addRow("Target rate", self.e_target)

        # --- case ---
        f.addRow(self._hdr("Case"))
        self.cmb_mat = QtWidgets.QComboBox()
        self.cmb_mat.setEditable(True)
        self.cmb_mat.addItems([""] + coll.MATERIALS)
        self.cmb_mat.setCurrentText(self.watch.material)
        self.e_bezel = line(self.watch.bezel)
        self.cmb_crystal = QtWidgets.QComboBox()
        self.cmb_crystal.setEditable(True)
        self.cmb_crystal.addItems([""] + coll.CRYSTALS)
        self.cmb_crystal.setCurrentText(self.watch.crystal)
        self.e_size = line(self.watch.case_size_mm)
        self.e_wr = line(self.watch.water_resistance)
        self.e_bracelet = line(self.watch.bracelet)
        self.e_serial = line(self.watch.serial)
        self.e_year = line(self.watch.production_year)
        f.addRow("Case material", self.cmb_mat)
        f.addRow("Bezel", self.e_bezel)
        f.addRow("Crystal", self.cmb_crystal)
        f.addRow("Case size (mm)", self.e_size)
        f.addRow("Water resistance", self.e_wr)
        f.addRow("Bracelet / strap", self.e_bracelet)
        f.addRow("Case serial", self.e_serial)
        f.addRow("Production year", self.e_year)

        # --- provenance ---
        f.addRow(self._hdr("Purchase and service"))
        self.e_pdate = QtWidgets.QDateEdit()
        self.e_pdate.setCalendarPopup(True)
        self.e_pdate.setDisplayFormat("yyyy-MM-dd")
        self.e_pdate.setSpecialValueText(" ")
        self.e_pdate.setMinimumDate(QtCore.QDate(1900, 1, 1))
        self.e_pdate.setDate(QtCore.QDate.fromString(self.watch.purchase_date, "yyyy-MM-dd")
                             if self.watch.purchase_date else QtCore.QDate(1900, 1, 1))
        self.e_price = line(self.watch.purchase_price)
        self.cmb_cur = QtWidgets.QComboBox()
        self.cmb_cur.addItems(coll.CURRENCIES)
        self.cmb_cur.setCurrentText(self.watch.purchase_currency or "GBP")
        self.cmb_cond = QtWidgets.QComboBox()
        self.cmb_cond.addItems([""] + coll.CONDITIONS)
        self.cmb_cond.setCurrentText(self.watch.purchase_condition)
        self.e_from = line(self.watch.purchased_from)
        self.e_service = QtWidgets.QDateEdit()
        self.e_service.setCalendarPopup(True)
        self.e_service.setDisplayFormat("yyyy-MM-dd")
        self.e_service.setSpecialValueText(" ")
        self.e_service.setMinimumDate(QtCore.QDate(1900, 1, 1))
        self.e_service.setDate(QtCore.QDate.fromString(self.watch.last_service, "yyyy-MM-dd")
                               if self.watch.last_service else QtCore.QDate(1900, 1, 1))
        self.e_interval = line(self.watch.service_interval_years or "5")
        pw = QtWidgets.QWidget()
        ph = QtWidgets.QHBoxLayout(pw)
        ph.setContentsMargins(0, 0, 0, 0)
        ph.addWidget(self.e_price, 3)
        ph.addWidget(self.cmb_cur, 1)
        f.addRow("Purchase date", self.e_pdate)
        f.addRow("Purchase price", pw)
        f.addRow("Condition at purchase", self.cmb_cond)
        f.addRow("Purchased from", self.e_from)
        f.addRow("Last serviced", self.e_service)
        f.addRow("Service interval (yrs)", self.e_interval)

        # --- photo and notes ---
        f.addRow(self._hdr("Photo and notes"))
        self.lbl_photo = QtWidgets.QLabel("No photo")
        self.lbl_photo.setFixedHeight(150)
        self.lbl_photo.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_photo.setStyleSheet(
            "border:1px dashed #3a434f;border-radius:6px;color:#8a94a4;")
        bph = QtWidgets.QPushButton("Choose photo...")
        bph.clicked.connect(self._pick_photo)
        bclr = QtWidgets.QPushButton("Remove photo")
        bclr.clicked.connect(self._clear_photo)
        hb = QtWidgets.QWidget()
        hl = QtWidgets.QHBoxLayout(hb)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(bph)
        hl.addWidget(bclr)
        self.e_notes = QtWidgets.QPlainTextEdit(self.watch.notes)
        self.e_notes.setFixedHeight(90)
        f.addRow(self.lbl_photo)
        f.addRow(hb)
        f.addRow("Notes", self.e_notes)

        scroll.setWidget(inner)
        outer.addWidget(scroll)
        box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        outer.addWidget(box)

        self._loading = True
        if self.watch.brand:
            self._brand_changed(self.watch.brand)
            self.cmb_model.setCurrentText(self.watch.model)
            self._model_changed(self.watch.model)
            self.cmb_ref.setCurrentText(self.watch.reference)
        self._loading = False
        self._show_photo(parent.collection.photo_path(self.watch)
                         if parent and self.watch.photo else None)

    @staticmethod
    def _hdr(text):
        l = QtWidgets.QLabel(text.upper())
        l.setStyleSheet("color:#4da3ff;font-weight:bold;font-size:11px;"
                        "margin-top:12px;letter-spacing:0.06em;")
        return l

    def _fill_cal_combo(self):
        from .calibers import grouped, GROUP_ORDER
        groups = {g: v for g, v in grouped().items() if g != "Reference list (WatchGuy)"}
        self.cmb_cal.clear()
        self.cmb_cal.addItem("(not set)", None)
        model = self.cmb_cal.model()
        order = ([g for g in GROUP_ORDER if g in groups]
                 + [g for g in groups if g not in GROUP_ORDER])
        for g in order:
            self.cmb_cal.addItem(f"\u2500\u2500 {g} \u2500\u2500", None)
            model.item(self.cmb_cal.count() - 1).setEnabled(False)
            for c in groups[g]:
                bph = f"{c.bph} bph" if c.bph else "bph auto"
                self.cmb_cal.addItem(f"    {c.brand} {c.name}  ({bph}, "
                                     f"{c.lift_angle:g}\u00b0)", c.key)
        i = self.cmb_cal.findData(self.watch.caliber_key)
        self.cmb_cal.setCurrentIndex(max(0, i))

    def _brand_changed(self, brand):
        cur = self.cmb_model.currentText()
        self.cmb_model.blockSignals(True)
        self.cmb_model.clear()
        self.cmb_model.addItems([""] + catdb.models_for(brand))
        self.cmb_model.setCurrentText(cur)
        self.cmb_model.blockSignals(False)

    def _model_changed(self, model):
        brand = self.cmb_brand.currentText()
        refs = catdb.references_for(brand, model)
        cur = self.cmb_ref.currentText()
        self.cmb_ref.blockSignals(True)
        self.cmb_ref.clear()
        self.cmb_ref.addItems([""] + [r.reference for r in refs])
        self.cmb_ref.setCurrentText(cur)
        self.cmb_ref.blockSignals(False)
        if refs and not self._loading:
            self.lbl_ref.setText(
                f"{len(refs)} known reference(s) for this model. Picking one fills in "
                f"the movement, metal, bezel and nickname -- all still editable.")

    def _ref_changed(self, text):
        if self._loading or not text.strip():
            return
        r = catdb.lookup(text, self.cmb_brand.currentText())
        if r is None:
            self.lbl_ref.setText(
                "Not a reference I recognise. Fill the rest in by hand -- nothing "
                "downstream depends on it being known.")
            return
        bits = []
        if r.material and not self.cmb_mat.currentText():
            self.cmb_mat.setCurrentText(r.material)
            bits.append("material")
        elif r.material:
            self.cmb_mat.setCurrentText(r.material)
            bits.append("material")
        if r.bezel:
            self.e_bezel.setText(r.bezel)
            bits.append("bezel")
        if r.crystal:
            self.cmb_crystal.setCurrentText(r.crystal)
            bits.append("crystal")
        if r.nickname and not self.e_nick.text().strip():
            self.e_nick.setText(r.nickname)
            bits.append("nickname")
        if r.caliber_key:
            i = self.cmb_cal.findData(r.caliber_key)
            if i >= 0:
                self.cmb_cal.setCurrentIndex(i)
                bits.append("movement")
        if r.model and not self.cmb_model.currentText().strip():
            self.cmb_model.setCurrentText(r.model)
        msg = f"{r.brand} {r.model} {r.reference}"
        if r.years:
            msg += f" ({r.years})"
        if r.nickname:
            msg += f", known as the {r.nickname}"
        msg += ". Filled in: " + ", ".join(bits) + ". " if bits else ". "
        if r.notes:
            msg += r.notes
        self.lbl_ref.setText(msg)

    def _pick_photo(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Watch photo", "", "Images (*.png *.jpg *.jpeg *.webp *.bmp)")
        if path:
            self._photo_src = path
            self._show_photo(path)

    def _clear_photo(self):
        self._photo_src = None
        self.watch.photo = ""
        self._show_photo(None)

    def _show_photo(self, path):
        if path and os.path.exists(path):
            pm = QtGui.QPixmap(path)
            if not pm.isNull():
                self.lbl_photo.setPixmap(pm.scaledToHeight(
                    148, QtCore.Qt.SmoothTransformation))
                return
        self.lbl_photo.setText("No photo")
        self.lbl_photo.setPixmap(QtGui.QPixmap())

    def result_watch(self):
        w = self.watch
        w.nickname = self.e_nick.text().strip()
        w.brand = self.cmb_brand.currentText().strip()
        w.model = self.cmb_model.currentText().strip()
        w.reference = self.cmb_ref.currentText().strip()
        w.caliber_key = self.cmb_cal.currentData() or ""
        try:
            w.lift_angle = float(self.e_lift.text()) if self.e_lift.text().strip() else None
        except ValueError:
            w.lift_angle = None
        w.movement_serial = self.e_mserial.text().strip()
        w.target_rate = self.e_target.text().strip()
        w.material = self.cmb_mat.currentText().strip()
        w.bezel = self.e_bezel.text().strip()
        w.crystal = self.cmb_crystal.currentText().strip()
        w.case_size_mm = self.e_size.text().strip()
        w.water_resistance = self.e_wr.text().strip()
        w.bracelet = self.e_bracelet.text().strip()
        w.serial = self.e_serial.text().strip()
        w.production_year = self.e_year.text().strip()
        d = self.e_pdate.date()
        w.purchase_date = "" if d.year() <= 1900 else d.toString("yyyy-MM-dd")
        w.purchase_price = self.e_price.text().strip()
        w.purchase_currency = self.cmb_cur.currentText()
        w.purchase_condition = self.cmb_cond.currentText().strip()
        w.purchased_from = self.e_from.text().strip()
        sd = self.e_service.date()
        w.last_service = "" if sd.year() <= 1900 else sd.toString("yyyy-MM-dd")
        w.service_interval_years = self.e_interval.text().strip() or "5"
        w.notes = self.e_notes.toPlainText().strip()
        return w, self._photo_src


class WavScrubber(QtWidgets.QDialog):
    """
    Load a long recording and re-analyse any window of it.

    A noisy or intermittent signal usually has a good stretch in it
    somewhere; this lets you find it instead of living with whatever the
    first twenty seconds happened to contain.
    """

    def __init__(self, data, fs, cfg, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Scrub a recording")
        self.setMinimumSize(820, 520)
        self._data = np.asarray(data, dtype=np.float64)
        self._fs = int(fs)
        self._cfg = cfg
        self.result_m = None
        dur = len(self._data) / self._fs

        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(QtWidgets.QLabel(
            f"{dur:.1f} s at {self._fs} Hz. Drag the shaded window, or use the "
            f"controls, then Analyse."))

        self.plot = pg.PlotWidget()
        self.plot.setLabel("bottom", "time", units="s")
        self.plot.setLabel("left", "level")
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        # Fast overview: block-max of |signal|, a few thousand points.
        n = self._data.size
        step = max(1, n // 4000)
        env = np.abs(self._data[:n - n % step].reshape(-1, step)).max(axis=1)
        t = np.arange(env.size) * step / self._fs
        self.plot.plot(t, env, pen=pg.mkPen("#57d38c", width=1))
        w0 = min(20.0, dur)
        self.region = pg.LinearRegionItem([0, w0], brush=(90, 163, 255, 45),
                                          pen=pg.mkPen("#4da3ff"))
        self.region.setBounds([0, dur])
        self.plot.addItem(self.region)
        self.region.sigRegionChangeFinished.connect(self._analyze)
        lay.addWidget(self.plot, 1)

        ctl = QtWidgets.QHBoxLayout()
        self.spn_w = QtWidgets.QDoubleSpinBox()
        self.spn_w.setRange(4.0, max(4.0, dur))
        self.spn_w.setValue(w0)
        self.spn_w.setSuffix(" s window")
        self.spn_w.valueChanged.connect(self._resize_region)
        b_prev = QtWidgets.QPushButton("<< prev")
        b_next = QtWidgets.QPushButton("next >>")
        b_prev.clicked.connect(lambda: self._step(-1))
        b_next.clicked.connect(lambda: self._step(1))
        b_an = QtWidgets.QPushButton("Analyse window")
        b_an.setStyleSheet(f"QPushButton{{background:{ACCENT};color:#08101c;"
                           "font-weight:bold;padding:6px 12px;border-radius:6px;}")
        b_an.clicked.connect(self._analyze)
        for wdg in (self.spn_w, b_prev, b_next, b_an):
            ctl.addWidget(wdg)
        ctl.addStretch(1)
        lay.addLayout(ctl)

        self.lbl_res = QtWidgets.QLabel("--")
        self.lbl_res.setStyleSheet("font-family:Consolas,monospace;color:#c8d0dc;"
                                   "background:#1a1f27;border:1px solid #2a323e;"
                                   "border-radius:6px;padding:10px;")
        self.lbl_res.setWordWrap(True)
        lay.addWidget(self.lbl_res)

        bb = QtWidgets.QDialogButtonBox()
        self.b_send = bb.addButton("Send window to main view", QtWidgets.QDialogButtonBox.AcceptRole)
        bb.addButton(QtWidgets.QDialogButtonBox.Close)
        self.b_send.setEnabled(False)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

        self._analyze()

    def _resize_region(self, w):
        lo, _hi = self.region.getRegion()
        dur = len(self._data) / self._fs
        lo = min(lo, max(0.0, dur - w))
        self.region.setRegion([lo, lo + w])

    def _step(self, direction):
        lo, hi = self.region.getRegion()
        w = hi - lo
        dur = len(self._data) / self._fs
        lo = min(max(0.0, lo + direction * w), max(0.0, dur - w))
        self.region.setRegion([lo, lo + w])

    def _analyze(self):
        lo, hi = self.region.getRegion()
        a = int(max(0, lo * self._fs))
        b = int(min(len(self._data), hi * self._fs))
        seg = self._data[a:b]
        if seg.size < self._fs:
            self.lbl_res.setText("Window too short.")
            return
        m = analyze(seg, self._fs, self._cfg)
        self.result_m = m
        self.b_send.setEnabled(m.ok)
        if not m.ok:
            self.lbl_res.setText(f"{lo:.1f}-{hi:.1f} s:  {m.message}")
            return
        self.lbl_res.setText(
            f"{lo:.1f}-{hi:.1f} s   ({hi-lo:.0f} s window)\n"
            f"Rate {m.rate:+.1f}"
            + (f" +/-{m.rate_ci:.1f}" if m.rate_ci == m.rate_ci else "")
            + f" s/d    Amplitude {'--' if m.amplitude != m.amplitude else f'{m.amplitude:.0f}'} deg"
            f"    Beat error {'--' if m.beat_error != m.beat_error else f'{m.beat_error:.2f}'} ms\n"
            f"{m.detected_bph} bph    {m.beats} beats    match {m.quality:.2f}    "
            f"{3 + m.extra_peaks:.1f} noises/beat"
            + (f"\n{m.message}" if m.message not in ('OK', '') else ""))


class ModelFinder(QtWidgets.QDialog):
    """
    Look a movement up by the watch it lives in.

    Deliberately shows every candidate rather than picking one. Most model
    names span several movements -- an Air-King has carried five calibers --
    and silently choosing the wrong generation puts the lift angle out by
    enough to misread amplitude badly. The reference and year columns are how
    you tell them apart, so they are the point of the table, not decoration.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Find movement by watch model")
        self.resize(760, 460)
        self.chosen = None

        lay = QtWidgets.QVBoxLayout(self)
        self.txt = QtWidgets.QLineEdit()
        self.txt.setPlaceholderText(
            "air king, skx007, black bay, PRX, 126900, aquis, amphibia ...")
        self.txt.textChanged.connect(self._refresh)
        lay.addWidget(self.txt)

        self.tbl = QtWidgets.QTableWidget(0, 6)
        self.tbl.setHorizontalHeaderLabels(
            ["Brand", "Model", "Reference / variant", "Years", "Movement", "Lift"])
        self.tbl.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tbl.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.tbl.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tbl.verticalHeader().setVisible(False)
        h = self.tbl.horizontalHeader()
        # PySide6 6.11 requires these enums through their scope class; reading
        # them off the widget instance raises AttributeError and kills the
        # dialog before it can be shown.
        RM = QtWidgets.QHeaderView.ResizeMode
        for i, mode in enumerate([RM.ResizeToContents, RM.Stretch, RM.Stretch,
                                  RM.ResizeToContents, RM.Stretch, RM.ResizeToContents]):
            h.setSectionResizeMode(i, mode)
        self.tbl.doubleClicked.connect(lambda *_: self._accept())
        lay.addWidget(self.tbl)

        self.lbl = QtWidgets.QLabel("")
        self.lbl.setWordWrap(True)
        self.lbl.setStyleSheet("color:#8a94a4;font-size:11px;")
        lay.addWidget(self.lbl)
        self.tbl.itemSelectionChanged.connect(self._describe)

        box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        box.accepted.connect(self._accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

        self._rows = []
        self._refresh("")
        self.txt.setFocus()

    def _refresh(self, text=""):
        from .calibers import CALIBERS
        self._rows = catdb.search(self.txt.text())
        self.tbl.setRowCount(0)
        for m in self._rows:
            c = CALIBERS.get(m.caliber_key)
            if not c:
                continue
            r = self.tbl.rowCount()
            self.tbl.insertRow(r)
            vals = [m.brand, m.model, m.reference, m.years,
                    f"{c.brand} {c.name}", f"{c.lift_angle:g}\u00b0"]
            for col, v in enumerate(vals):
                it = QtWidgets.QTableWidgetItem(v)
                if m.confidence != "sure":
                    it.setForeground(QtGui.QColor("#ffb648"))
                self.tbl.setItem(r, col, it)
        if self.tbl.rowCount():
            self.tbl.selectRow(0)
        else:
            st = catdb.stats()
            self.lbl.setText(
                f"No match. The catalog holds {st['usable']} references across "
                f"{st['models']} models and {st['brands']} brands -- broad, but not "
                f"every watch ever made. If yours is missing, search the movement "
                f"database directly, or add the caliber via caliber CSV.")

    def _describe(self):
        from .calibers import CALIBERS
        r = self.tbl.currentRow()
        if r < 0 or r >= len(self._rows):
            return
        m = self._rows[r]
        c = CALIBERS.get(m.caliber_key)
        if not c:
            return
        detail = " / ".join(x for x in (m.material, m.bezel, m.crystal) if x)
        bits = [f"{m.label} ({m.years}) uses the {c.brand} {c.name}, "
                f"{c.bph if c.bph else 'beat rate auto-detected'}"
                f"{' bph' if c.bph else ''}, lift angle {c.lift_angle:g} degrees."]
        if detail:
            bits.append(f"Case: {detail}.")
        if m.nickname:
            bits.append(f"Known as the {m.nickname}.")
        if m.confidence != "sure":
            bits.append("This mapping is generally accepted rather than confirmed -- "
                        "check the movement itself before trusting the amplitude figure.")
        if c.notes:
            bits.append(c.notes)
        self.lbl.setText("  ".join(bits))

    def _accept(self):
        r = self.tbl.currentRow()
        if 0 <= r < len(self._rows):
            self.chosen = self._rows[r]
            self.accept()


class Collapsible(QtWidgets.QWidget):
    """
    A group box that folds away. Screen real estate on the control column is
    the scarce resource -- pickup tuning and file handling are things you
    touch once a session, not something that should push the Start button off
    the bottom of a laptop screen.
    """

    def __init__(self, title, expanded=True, parent=None):
        super().__init__(parent)
        self.btn = QtWidgets.QToolButton()
        self.btn.setText(title)
        self.btn.setCheckable(True)
        self.btn.setChecked(expanded)
        self.btn.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.btn.setArrowType(QtCore.Qt.DownArrow if expanded else QtCore.Qt.RightArrow)
        self.btn.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.btn.setStyleSheet(
            "QToolButton{border:none;color:#9fb0c6;font-weight:bold;padding:6px 2px;"
            "text-align:left;} QToolButton:hover{color:#e8eef7;}")
        self.content = QtWidgets.QWidget()
        self.form = QtWidgets.QFormLayout(self.content)
        self.form.setContentsMargins(8, 2, 2, 8)
        self.form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        self.form.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._persist = QtWidgets.QVBoxLayout()
        self._persist.setContentsMargins(8, 0, 2, 4)
        self._persist.setSpacing(4)
        lay.addWidget(self.btn)
        lay.addLayout(self._persist)
        lay.addWidget(self.content)
        self.content.setVisible(expanded)
        self.btn.toggled.connect(self._toggle)

    def addPersistent(self, widget):
        """A control that stays visible when the section is folded shut."""
        self._persist.addWidget(widget)

    def _toggle(self, on):
        self.content.setVisible(on)
        self.btn.setArrowType(QtCore.Qt.DownArrow if on else QtCore.Qt.RightArrow)

    def addRow(self, *a):
        self.form.addRow(*a)


class Readout(QtWidgets.QFrame):
    def __init__(self, title, unit, parent=None):
        super().__init__(parent)
        self.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self.setStyleSheet(
            "QFrame{background:#1a1f27;border:1px solid #2a323e;border-radius:8px;}")
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(0)
        self.t = QtWidgets.QLabel(title)
        self.t.setStyleSheet("color:#8a94a4;font-size:11px;border:none;")
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        self.setMinimumWidth(96)
        self.v = QtWidgets.QLabel("--")
        f = self.v.font()
        f.setPointSize(22)
        f.setBold(True)
        self.v.setFont(f)
        self.v.setStyleSheet("color:#e8eef7;border:none;")
        self.u = QtWidgets.QLabel(unit)
        self.u.setStyleSheet("color:#8a94a4;font-size:11px;border:none;")
        for w in (self.t, self.v, self.u):
            lay.addWidget(w)

    def set(self, text, color="#e8eef7"):
        self.v.setText(text)
        self.v.setStyleSheet(f"color:{color};border:none;")


class AnalogClock(QtWidgets.QWidget):
    """A plain analog face driven from an external datetime, with a beat flash."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(260, 260)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self._dt = None
        self._flash = 0.0        # 0..1

    def show_time(self, dt, flash=0.0):
        self._dt = dt
        self._flash = float(flash)
        self.update()

    def paintEvent(self, _ev):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        side = min(self.width(), self.height())
        p.translate(self.width() / 2.0, self.height() / 2.0)
        p.scale(side / 220.0, side / 220.0)

        face = QtGui.QColor("#1a1f27")
        if self._flash > 0:
            f = self._flash
            face = QtGui.QColor(int(26 + (235 - 26) * f), int(31 + (240 - 31) * f),
                                int(39 + (247 - 39) * f))
        p.setPen(QtGui.QPen(QtGui.QColor("#2a323e"), 3))
        p.setBrush(face)
        p.drawEllipse(-100, -100, 200, 200)

        for i in range(60):
            p.save()
            p.rotate(i * 6)
            if i % 5 == 0:
                p.setPen(QtGui.QPen(QtGui.QColor("#8a94a4"), 3))
                p.drawLine(0, -100, 0, -87)
            else:
                p.setPen(QtGui.QPen(QtGui.QColor("#3a4553"), 1))
                p.drawLine(0, -100, 0, -94)
            p.restore()

        if self._dt is None:
            p.setPen(QtGui.QColor("#8a94a4"))
            p.drawText(QtCore.QRectF(-100, -12, 200, 24), QtCore.Qt.AlignCenter,
                       "pick a time source")
            return

        dt = self._dt
        secs = dt.second + dt.microsecond / 1e6
        mins = dt.minute + secs / 60.0
        hrs = (dt.hour % 12) + mins / 60.0

        def hand(angle, length, width, color, back=14):
            p.save()
            p.rotate(angle)
            pen = QtGui.QPen(QtGui.QColor(color), width)
            pen.setCapStyle(QtCore.Qt.RoundCap)
            p.setPen(pen)
            p.drawLine(0, back, 0, -length)
            p.restore()

        hand(hrs * 30.0, 52, 6, "#e8eef7")
        hand(mins * 6.0, 78, 4, "#e8eef7")
        hand(secs * 6.0, 92, 2, "#ff5d5d")
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor("#ff5d5d"))
        p.drawEllipse(-4, -4, 8, 8)


class EscapementView(QtWidgets.QWidget):
    """
    Schematic Swiss lever escapement, animated from the measured numbers.

    Not a mechanism simulation -- it is driven by a phase clock so you can
    watch, slowed right down, what the balance, fork and escape wheel are
    doing at each of the three noises the analyzer listens for.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 300)
        self.amp = 275.0
        self.bph = 28800
        self.dt = 0.006          # unlocking-to-drop interval, seconds
        self.slowdown = 0.1
        self._t0 = time.monotonic()
        self._phase = 0.0        # seconds into the watch's own timeline

    def set_params(self, amp, bph, dt):
        if amp == amp:
            self.amp = float(np.clip(amp, 120, 340))
        if bph:
            self.bph = int(bph)
        if dt == dt and dt > 0:
            self.dt = float(dt)

    def resync(self):
        """Drop the elapsed gap so the phase does not jump after being hidden."""
        self._t0 = time.monotonic()

    def advance(self):
        now = time.monotonic()
        self._phase += (now - self._t0) * self.slowdown
        self._t0 = now
        self.update()

    def paintEvent(self, _ev):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.fillRect(self.rect(), QtGui.QColor("#12151a"))
        s = min(self.width(), self.height())
        p.translate(self.width() / 2.0, self.height() / 2.0)
        p.scale(s / 300.0, s / 300.0)

        t_beat = 3600.0 / self.bph
        t_osc = 2.0 * t_beat
        k = int(self._phase / t_beat)              # beat index
        into = self._phase - k * t_beat            # seconds into this beat
        theta = self.amp * math.sin(2 * math.pi * self._phase / t_osc)   # balance angle, deg

        # phase within the beat: unlock at 0, impulse ~dt/2, drop at dt
        stage = ("unlock" if into < self.dt * 0.35 else
                 "impulse" if into < self.dt else "free")

        # ---- escape wheel (top) : steps one half-tooth per beat -------------
        p.save()
        p.translate(-70, -78)
        ew_ang = -k * (360.0 / 15.0 / 2.0) - (min(into, self.dt) / self.dt) * 6.0
        p.rotate(ew_ang)
        p.setPen(QtGui.QPen(QtGui.QColor("#8a94a4"), 2))
        p.setBrush(QtGui.QColor("#1a1f27"))
        pts = []
        for i in range(15):
            a0 = math.radians(i * 24)
            a1 = math.radians(i * 24 + 10)
            pts.append(QtCore.QPointF(34 * math.cos(a0), 34 * math.sin(a0)))
            pts.append(QtCore.QPointF(22 * math.cos(a1), 22 * math.sin(a1)))
        p.drawPolygon(QtGui.QPolygonF(pts))
        p.restore()

        # ---- pallet fork : flips between bankings each beat ----------------
        p.save()
        p.translate(-22, -30)
        p.rotate(12.0 if k % 2 == 0 else -12.0)
        col = ("#ff5d5d" if stage == "unlock"
               else "#ffb648" if stage == "impulse" else "#8a94a4")
        pen = QtGui.QPen(QtGui.QColor(col), 5)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        p.setPen(pen)
        p.drawLine(0, -34, 0, 20)
        p.drawLine(-10, -34, 10, -34)
        p.setBrush(QtGui.QColor(col))
        p.drawEllipse(-3, 16, 6, 6)
        p.restore()

        # ---- balance wheel (fills the lower area) -------------------------
        p.save()
        p.translate(0, 46)
        p.rotate(theta)
        p.setPen(QtGui.QPen(QtGui.QColor("#e8eef7"), 4))
        p.setBrush(QtCore.Qt.NoBrush)
        p.drawEllipse(-88, -88, 176, 176)
        for a in (0, 60, 120):
            p.save()
            p.rotate(a)
            p.drawLine(-88, 0, 88, 0)
            p.restore()
        p.setPen(QtGui.QPen(QtGui.QColor("#ff5d5d"), 5))
        p.drawLine(0, 0, 0, -88)
        p.restore()

        # ---- labels ------------------------------------------------------
        p.setPen(QtGui.QColor("#c8d0dc"))
        p.setFont(QtGui.QFont("Segoe UI", 9))
        p.drawText(QtCore.QRectF(-150, 118, 300, 18), QtCore.Qt.AlignCenter,
                   f"amplitude {self.amp:.0f} deg   {self.bph} bph   "
                   f"slowed {1/self.slowdown:.0f}x")
        for i, (name, active) in enumerate([("unlock", stage == "unlock"),
                                            ("impulse", stage == "impulse"),
                                            ("drop / lock", stage == "free")]):
            p.setPen(QtGui.QColor("#ff5d5d" if (i == 0 and active) else
                                  "#ffb648" if (i == 1 and active) else
                                  "#57d38c" if (i == 2 and active) else "#5a6472"))
            p.drawText(QtCore.QRectF(-150, -142 + i * 16, 300, 16),
                       QtCore.Qt.AlignHCenter, ("> " if active else "") + name)


class EscapementDialog(QtWidgets.QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Escapement animation")
        self.setMinimumSize(380, 460)
        self._parent = parent
        lay = QtWidgets.QVBoxLayout(self)
        self.view = EscapementView()
        lay.addWidget(self.view, 1)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Speed"))
        self.sld = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.sld.setRange(2, 100)          # percent of real time
        self.sld.setValue(10)
        self.sld.valueChanged.connect(
            lambda v: setattr(self.view, "slowdown", v / 100.0))
        row.addWidget(self.sld, 1)
        self.b_sound = QtWidgets.QPushButton("Play the beat, slowed")
        self.b_sound.clicked.connect(self._play)
        row.addWidget(self.b_sound)
        lay.addLayout(row)

        self._tmr = QtCore.QTimer(self)
        self._tmr.setInterval(33)
        self._tmr.timeout.connect(self._tick)

    def showEvent(self, e):
        self.view.resync()
        self._tmr.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self._tmr.stop()
        super().hideEvent(e)

    def _tick(self):
        m = getattr(self._parent, "last", None)
        if m is not None and m.ok:
            self.view.set_params(m.amplitude, m.nominal_bph or m.detected_bph, m.dt_mean)
        self.view.advance()

    def _play(self):
        if not audio.HAVE_SD:
            QtWidgets.QMessageBox.information(self, "Slowed playback",
                                             "sounddevice is not available.")
            return
        m = getattr(self._parent, "last", None)
        if m is None or m.beat_wave is None or not m.beat_wave_fs:
            QtWidgets.QMessageBox.information(
                self, "Slowed playback",
                "No beat captured yet -- take a reading (live or from a recording) first.")
            return
        import sounddevice as sd
        w = np.asarray(m.beat_wave, dtype=np.float32)
        w = w / (np.max(np.abs(w)) + 1e-9) * 0.5
        slow = max(4, int(round(1.0 / max(0.02, self.sld.value() / 100.0))))
        gap = np.zeros(int(m.beat_wave_fs * 0.10), dtype=np.float32)
        seq = np.concatenate([np.concatenate([w, gap]) for _ in range(4)])
        try:
            sd.play(seq, samplerate=max(2000, m.beat_wave_fs // slow))
        except Exception as e:                       # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "Slowed playback", str(e))



class _NtpProbe(QtCore.QObject):
    done = QtCore.Signal(str, float, float, str)   # label, offset_s, roundtrip_s, error

    def __init__(self, host, label):
        super().__init__()
        self.host, self.label = host, label

    @QtCore.Slot()
    def run(self):
        try:
            from . import timesync
            off, rt = timesync.query_ntp(self.host)
            self.done.emit(self.label, float(off), float(rt), "")
        except Exception as e:                       # noqa: BLE001
            self.done.emit(self.label, 0.0, 0.0, f"{type(e).__name__}: {e}")


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"WatchGrapher {__version__} -- acoustic timegrapher")
        # Minimum small enough for a 1366x768 laptop; the control column
        # scrolls, so nothing can be pushed out of reach.
        self.setMinimumSize(940, 560)
        self.resize(1380, 860)
        self.recorder = None
        self.last = None
        self.readings = []
        self._rate_hist = []       # (elapsed_s, rate_spd) for the rate-history plot
        self._be_hist = []         # (elapsed_s, beat_error_ms) for the diagnostics strip
        self._listen_t0 = None     # wall-clock start of the current listen session
        self._rate_last_update = None   # monotonic() of the last appended rate point
        self._cap_frames = None    # last-seen recorder.frames, for the stream watchdog
        self._cap_frames_t = 0.0   # monotonic() when frames last advanced
        self._stream_restart_t = 0.0    # monotonic() of the last auto-restart
        self._stream_restarts = 0
        self._last_rate_for_calib = None
        self._tuning = False       # a self-tune sweep is in flight
        self._selftune_session = False       # one-press auto run active
        self._selftune_started_listen = False  # this feature opened the recorder
        self._selftune_baseline = None       # tuning_score(self.last) at session start
        self._selftune_deadline = 0.0        # monotonic() escape time for the sweep
        self._noise_session = False          # room-noise check running
        self._noise_started_listen = False   # this feature opened the recorder
        self._noise_deadline = 0.0
        self._suppress_finish = False   # internal stream restarts, not user stops
        self._closing = False
        self._sync_offset = 0.0     # seconds to add to time.time() for true time
        self._sync_info = "Using this computer's clock, uncorrected."
        self._sync_last_sec = 0
        self._sync_thread = None
        self._watch_set_ref = None  # (true_epoch, watch_epoch) when the watch was set
        self._settle_pending = False
        self._settle_buf = []
        self._settle_secs = 0
        self._settle_deadline = 0.0
        self._run_t0 = None        # timed run start, or None
        self._run_len = 0.0
        self._stable = []          # recent readings, for auto-capture
        self._reserve = []         # (elapsed_s, rate, amplitude, beat_error)
        self._res_t0 = None
        self._res_next = 0.0

        self.collection = coll.Collection(COLLECTION_DIR)
        self._watch_ids = []

        self._build()
        self._refresh_watches()
        self._start_worker()
        self._refresh_devices()
        self._load_user_db()
        i = self.cmb_cal.findData("eta_2824_2")
        if i >= 0:
            self.cmb_cal.setCurrentIndex(i)

    # ---------------------------------------------------------------- build
    def _build(self):
        """
        Two top-level modes rather than one crowded screen.

        Measuring a watch and managing a collection are different activities
        with different rhythms -- one is a live instrument, the other is a
        records browser. Squeezing the collection into a 300px tab strip below
        the trace made it look like a minor accessory to the timegrapher when
        it is really half the point of keeping one.
        """
        measure = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        measure.addWidget(self._build_controls())
        right = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        right.addWidget(self._build_display())
        right.addWidget(self._build_tabs())
        right.setSizes([540, 320])
        right.setCollapsible(0, False)
        measure.addWidget(right)
        measure.setStretchFactor(1, 1)
        measure.setSizes([getattr(self, "_sidebar_width", 420), 1000])

        self.stack = QtWidgets.QStackedWidget()
        self.stack.addWidget(measure)
        self.stack.addWidget(self._build_watches_page())
        self.stack.addWidget(self._build_sync_page())
        self.stack.addWidget(self._build_help_page())
        self.stack.currentChanged.connect(self._on_page_changed)

        central = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self._build_header())
        v.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self._build_menu()
        self.status = self.statusBar()
        self.status.showMessage("Idle")

    def _build_menu(self):
        """
        The old Files panel was taking permanent space on the control column
        for things touched once a session. A menu is the right home for them.
        """
        mb = self.menuBar()
        fm = mb.addMenu("&File")
        self.act_rec = QtGui.QAction("Record WAV...", self)
        self.act_rec.setCheckable(True)
        self.act_rec.toggled.connect(self._toggle_record)
        fm.addAction(self.act_rec)
        for label, slot, key in (("Analyze a WAV file...", self._analyze_file, None),
                                 ("Load caliber CSV...", self._load_csv_dialog, None),
                                 ("Save session...", self._save_session, "Ctrl+S")):
            act = QtGui.QAction(label, self)
            act.triggered.connect(slot)
            if key:
                act.setShortcut(key)
            fm.addAction(act)
        fm.addSeparator()
        for label, slot in (("Back up collection...", self._backup_collection),
                            ("Restore collection...", self._restore_collection)):
            act = QtGui.QAction(label, self)
            act.triggered.connect(slot)
            fm.addAction(act)
        fm.addSeparator()
        quit_act = QtGui.QAction("Exit", self)
        quit_act.triggered.connect(self.close)
        fm.addAction(quit_act)

        vm = mb.addMenu("&View")
        for label, idx, key in (("Measure", 0, "Ctrl+1"),
                                ("My Watches", 1, "Ctrl+2"),
                                ("Sync", 2, "Ctrl+3"),
                                ("Help", 3, "Ctrl+4")):
            act = QtGui.QAction(label, self)
            act.setShortcut(key)
            act.triggered.connect(lambda _=False, i=idx: self._goto_page(i))
            vm.addAction(act)
        vm.addSeparator()
        esc_act = QtGui.QAction("Escapement animation...", self)
        esc_act.triggered.connect(self._open_escapement)
        vm.addAction(esc_act)
        rescan_act = QtGui.QAction("Rescan audio devices", self)
        rescan_act.setShortcut("Ctrl+R")
        rescan_act.triggered.connect(self._refresh_devices)
        vm.addAction(rescan_act)

    def _build_header(self):
        bar = QtWidgets.QFrame()
        bar.setStyleSheet("QFrame{background:#171b22;border-bottom:1px solid #2a323e;}")
        h = QtWidgets.QHBoxLayout(bar)
        h.setContentsMargins(10, 6, 10, 6)
        h.setSpacing(8)

        self.nav = QtWidgets.QButtonGroup(self)
        self.nav.setExclusive(True)
        style = ("QPushButton{background:transparent;color:#8a94a4;border:1px solid #2a323e;"
                 "border-radius:6px;padding:7px 20px;font-weight:bold;letter-spacing:.05em;}"
                 "QPushButton:hover{color:#c8d0dc;}"
                 f"QPushButton:checked{{background:{ACCENT};color:#08101c;border-color:{ACCENT};}}")
        for i, (text, tip) in enumerate([
                ("MEASURE", "Live timing: trace, readouts, positions and advice."),
                ("MY WATCHES", "Your collection: profiles, timing history and trends."),
                ("SYNC", "Reference clock for hand-setting a watch to true time."),
                ("HELP", "How to use the tool, and how to read what it tells you.")]):
            b = QtWidgets.QPushButton(text)
            b.setCheckable(True)
            b.setStyleSheet(style)
            b.setToolTip(tip)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            self.nav.addButton(b, i)
            h.addWidget(b)
        self.nav.button(0).setChecked(True)
        self.nav.idClicked.connect(self.stack.setCurrentIndex)

        h.addSpacing(16)
        self.lbl_now = QtWidgets.QLabel("No watch selected")
        self.lbl_now.setStyleSheet("color:#8a94a4;font-size:12px;")
        h.addWidget(self.lbl_now)
        h.addStretch(1)

        self.lbl_live = QtWidgets.QLabel("")
        self.lbl_live.setStyleSheet("color:#5a6472;font-size:12px;")
        h.addWidget(self.lbl_live)
        return bar

    # ============================================================== sync page
    def _build_sync_page(self):
        page = QtWidgets.QWidget()
        outer = QtWidgets.QHBoxLayout(page)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(20)

        self.clock = AnalogClock()
        outer.addWidget(self.clock, 3)

        side = QtWidgets.QVBoxLayout()
        side.setSpacing(10)

        self.lbl_sync_digital = QtWidgets.QLabel("--:--:--")
        f = self.lbl_sync_digital.font()
        f.setPointSize(34)
        f.setBold(True)
        f.setFamily("Consolas")
        self.lbl_sync_digital.setFont(f)
        self.lbl_sync_digital.setStyleSheet("color:#e8eef7;")
        side.addWidget(self.lbl_sync_digital)

        self.lbl_sync_date = QtWidgets.QLabel("")
        self.lbl_sync_date.setStyleSheet("color:#8a94a4;")
        side.addWidget(self.lbl_sync_date)

        srow = QtWidgets.QHBoxLayout()
        self.cmb_src = QtWidgets.QComboBox()
        self.cmb_src.addItem("This computer's clock", "SYSTEM")
        for host, label in timesync.NTP_SERVERS:
            self.cmb_src.addItem(f"NTP -- {label}", host)
        self.cmb_src.addItem("Manual offset...", "MANUAL")
        self.cmb_src.currentIndexChanged.connect(self._sync_source_changed)
        self.btn_sync = QtWidgets.QPushButton("Sync now")
        self.btn_sync.setStyleSheet(
            f"QPushButton{{background:{ACCENT};color:#08101c;font-weight:bold;"
            "padding:7px 16px;border-radius:6px;}")
        self.btn_sync.clicked.connect(self._sync_now)
        srow.addWidget(self.cmb_src, 1)
        srow.addWidget(self.btn_sync, 0)
        side.addLayout(srow)

        self.spn_manual = QtWidgets.QDoubleSpinBox()
        self.spn_manual.setRange(-120.0, 120.0)
        self.spn_manual.setDecimals(2)
        self.spn_manual.setSingleStep(0.1)
        self.spn_manual.setSuffix(" s manual correction")
        self.spn_manual.valueChanged.connect(
            lambda v: self._apply_offset(v, f"Manual correction {v:+.2f} s.")
            if self.cmb_src.currentData() == "MANUAL" else None)
        self.spn_manual.setVisible(False)
        side.addWidget(self.spn_manual)

        self.lbl_sync_info = QtWidgets.QLabel(self._sync_info)
        self.lbl_sync_info.setWordWrap(True)
        self.lbl_sync_info.setStyleSheet("color:#c8d0dc;font-size:12px;")
        side.addWidget(self.lbl_sync_info)

        self.lbl_sync_dev = QtWidgets.QLabel("")
        self.lbl_sync_dev.setWordWrap(True)
        self.lbl_sync_dev.setStyleSheet("color:#ffb648;font-size:12px;")
        side.addWidget(self.lbl_sync_dev)

        self.chk_sync_flash = QtWidgets.QCheckBox("Flash the face on every second")
        self.chk_sync_beep = QtWidgets.QCheckBox("Beep on every second")
        for c in (self.chk_sync_flash, self.chk_sync_beep):
            c.setStyleSheet("color:#c8d0dc;")
            side.addWidget(c)

        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setStyleSheet("color:#2a323e;")
        side.addWidget(line)

        _wd = QtWidgets.QLabel("Watch drift")
        _wd.setStyleSheet("color:#4da3ff;font-weight:bold;")
        side.addWidget(_wd)
        self.btn_watch_set = QtWidgets.QPushButton("Mark: watch is set to this time now")
        self.btn_watch_set.clicked.connect(self._mark_watch_set)
        side.addWidget(self.btn_watch_set)

        drow = QtWidgets.QHBoxLayout()
        self.te_watch_now = QtWidgets.QTimeEdit()
        self.te_watch_now.setDisplayFormat("HH:mm:ss")
        self.te_watch_now.setTime(QtCore.QTime.currentTime())
        b_drift = QtWidgets.QPushButton("Watch now reads this -> drift")
        b_drift.clicked.connect(self._compute_watch_drift)
        drow.addWidget(self.te_watch_now, 0)
        drow.addWidget(b_drift, 1)
        side.addLayout(drow)

        self.lbl_watch_drift = QtWidgets.QLabel("")
        self.lbl_watch_drift.setWordWrap(True)
        self.lbl_watch_drift.setStyleSheet("color:#c8d0dc;font-size:12px;")
        side.addWidget(self.lbl_watch_drift)

        side.addStretch(1)
        sw = QtWidgets.QWidget()
        sw.setLayout(side)
        sw.setMaximumWidth(380)
        outer.addWidget(sw, 2)

        self._sync_tmr = QtCore.QTimer(self)
        self._sync_tmr.setInterval(33)
        self._sync_tmr.timeout.connect(self._sync_tick)
        return page

    def _on_page_changed(self, idx):
        # The Sync clock only needs its 30 fps repaint while it is on screen.
        if not hasattr(self, "_sync_tmr"):
            return
        if idx == 2:
            self._sync_tick()
            self._sync_tmr.start()
        else:
            self._sync_tmr.stop()

    def _sync_source_changed(self):
        self.spn_manual.setVisible(self.cmb_src.currentData() == "MANUAL")
        if self.cmb_src.currentData() == "SYSTEM":
            self._apply_offset(0.0, "Using this computer's clock, uncorrected.")

    def _apply_offset(self, offset, info, deviation=""):
        self._sync_offset = float(offset)
        self._sync_info = info
        self.lbl_sync_info.setText(info)
        self.lbl_sync_dev.setText(deviation)

    def _sync_now(self):
        src = self.cmb_src.currentData()
        if src == "SYSTEM":
            self._apply_offset(0.0, "Using this computer's clock, uncorrected.")
            return
        if src == "MANUAL":
            v = self.spn_manual.value()
            self._apply_offset(v, f"Manual correction {v:+.2f} s.")
            return
        if self._sync_thread is not None and self._sync_thread.isRunning():
            return
        label = self.cmb_src.currentText().replace("NTP -- ", "")
        self.btn_sync.setEnabled(False)
        self.btn_sync.setText("Contacting...")
        self._sync_thread = QtCore.QThread(self)
        self._sync_probe = _NtpProbe(src, label)
        self._sync_probe.moveToThread(self._sync_thread)
        self._sync_thread.started.connect(self._sync_probe.run)
        self._sync_probe.done.connect(self._on_ntp_done)
        self._sync_probe.done.connect(self._sync_thread.quit)
        self._sync_thread.start()

    def _on_ntp_done(self, label, offset, roundtrip, error):
        self.btn_sync.setEnabled(True)
        self.btn_sync.setText("Sync now")
        if error:
            self.lbl_sync_dev.setText("")
            self._apply_offset(self._sync_offset,
                               f"Could not reach {label}: {error}. Clock unchanged.")
            return
        when = datetime.now().strftime("%H:%M:%S")
        self._apply_offset(
            offset,
            f"Synced to {label} at {when}. Offset {offset:+.3f} s, "
            f"round trip {roundtrip * 1000:.0f} ms.",
            deviation=(f"This computer's clock is {abs(offset):.2f} s "
                       f"{'slow' if offset > 0 else 'fast'} versus {label}."))

    def _true_now(self):
        return time.time() + self._sync_offset

    def _sync_tick(self):
        now = self._true_now()
        dt = datetime.fromtimestamp(now)
        whole = math.floor(now)
        if whole != self._sync_last_sec:
            self._sync_last_sec = whole
            if self.chk_sync_beep.isChecked():
                QtWidgets.QApplication.beep()
        frac = now - whole
        flash = max(0.0, 1.0 - frac / 0.12) if self.chk_sync_flash.isChecked() else 0.0
        self.clock.show_time(dt, flash)
        self.lbl_sync_digital.setText(dt.strftime("%H:%M:%S") + f".{dt.microsecond // 1000:03d}")
        self.lbl_sync_date.setText(dt.strftime("%A %d %B %Y  (local time)"))

    def _mark_watch_set(self):
        t = self._true_now()
        self._watch_set_ref = t
        self.te_watch_now.setTime(QtCore.QTime.currentTime())
        self.lbl_watch_drift.setText(
            f"Marked at {datetime.fromtimestamp(t):%H:%M:%S}. Come back later, read the "
            f"watch, enter it above and press the drift button.")

    def _compute_watch_drift(self):
        if self._watch_set_ref is None:
            self.lbl_watch_drift.setText("Press 'Mark' when you set the watch first.")
            return
        now = self._true_now()
        ref0 = datetime.fromtimestamp(self._watch_set_ref)
        wt = self.te_watch_now.time()
        # Assume the watch shows a time on the same day as 'now', nearest wrap.
        base = datetime.fromtimestamp(now).replace(
            hour=wt.hour(), minute=wt.minute(), second=wt.second(), microsecond=0)
        for cand in (base, base - timedelta(days=1), base + timedelta(days=1)):
            if abs((cand - datetime.fromtimestamp(now)).total_seconds()) < 43200:
                watch_dt = cand
                break
        else:
            watch_dt = base
        elapsed = now - self._watch_set_ref
        drift = (watch_dt - datetime.fromtimestamp(now)).total_seconds()
        if elapsed < 60:
            self.lbl_watch_drift.setText("Give it longer than a minute before reading drift.")
            return
        rate = drift / elapsed * 86400.0
        self.lbl_watch_drift.setText(
            f"Set {ref0:%H:%M:%S}, {elapsed / 3600:.1f} h ago. Watch is {drift:+.0f} s "
            f"versus true time now -> {rate:+.1f} s/day.")

    def _build_help_page(self):
        page = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(page)
        v.setContentsMargins(24, 18, 24, 18)
        t = QtWidgets.QTextBrowser()
        t.setOpenExternalLinks(True)
        t.setStyleSheet(
            "QTextBrowser{background:#1a1f27;border:1px solid #2a323e;border-radius:8px;"
            "padding:14px;}")
        t.setHtml("""
<div style='font-family:Segoe UI,sans-serif;color:#c8d0dc;max-width:900px'>
<h2 style='color:#4da3ff;margin-top:0'>Taking a measurement</h2>
<ol style='line-height:1.7'>
<li><b>Audio input.</b> Press the case back firmly against the pickup. A piezo
disc against the case back beats a good microphone in open air, because
amplitude depends on resolving a very quiet unlocking noise. Set the gain so
the level meter sits in the upper half without touching red.</li>
<li><b>Watch.</b> Pick one of your own from <i>My watch</i> and its caliber and
lift angle load automatically. Otherwise search a caliber number, or a watch:
<i>Rolex Submariner</i>, <i>SKX007</i>, <i>126610LN</i>. Use
<i>Find by watch model</i> when a model spans several generations.</li>
<li><b>Conditions.</b> Wind fully, let it settle 10-15 minutes, choose the
position and wind state.</li>
<li><b>Start.</b> Set a duration and press Start. At the end the whole capture
is analysed in one pass and you are asked what to do with the result. Duration
0 runs open-ended until you press Stop.</li>
</ol>

<h2 style='color:#4da3ff'>Order of operations</h2>
<p>Work in this order or you will do it twice:</p>
<ol style='line-height:1.7'>
<li><b>Confirm the beat rate and lift angle.</b> A wrong lift angle makes a
healthy watch look sick. One degree of lift angle error is worth about five
degrees of amplitude.</li>
<li><b>Amplitude.</b> It is the energy budget for everything else. A movement
with weak amplitude will not hold whatever rate you regulate it to.</li>
<li><b>Beat error.</b> Cheap on most calibers, and it destabilises rate across
positions when large.</li>
<li><b>Rate.</b> Last, because the two steps above both move it.</li>
<li><b>Positional delta and isochronism.</b> Poise and hairspring problems. No
amount of regulating fixes them.</li>
</ol>

<h2 style='color:#4da3ff'>Reading the screen</h2>
<p><b>Trace.</b> Slope is rate; the vertical gap between the two colours is
beat error. Fuzzy lines mean the escapement is not repeating cleanly, or the
pickup is noisy. Wandering, non-straight lines point at a real fault.</p>
<p><b>Beat waveform.</b> The two markers show exactly which noises the
amplitude calculation used. If they are not sitting on obvious peaks, the
amplitude number is wrong regardless of what any other tool says.</p>
<p><b>Noises per beat</b> (top right). A lever escapement makes exactly three:
unlocking, impulse, drop. Well above three means the pickup is hearing the
room, and amplitude is the reading that suffers first. Try
<i>Self-tune pickup</i>; if it stays high, improve the physical contact.</p>

<h2 style='color:#4da3ff'>What good looks like</h2>
<table cellpadding='6' style='border-collapse:collapse'>
<tr style='color:#8a94a4'><th align='left'></th><th align='left'>Target</th>
<th align='left'>Acceptable</th><th align='left'>Investigate</th></tr>
<tr><td>Rate</td><td>0 to &plusmn;5 s/d</td><td>&plusmn;10</td><td>beyond &plusmn;20</td></tr>
<tr><td>Amplitude, dial up, full wind</td><td>270-310&deg;</td><td>250-270&deg;</td>
<td>under 250, or over 330</td></tr>
<tr><td>Amplitude, vertical</td><td>within 20-40&deg; of horizontal</td><td>50&deg;</td>
<td>more than 60&deg; drop</td></tr>
<tr><td>Beat error</td><td>under 0.3 ms</td><td>under 0.5 ms</td><td>over 0.8 ms</td></tr>
<tr><td>Positional delta</td><td>under 10 s/d</td><td>under 20 s/d</td><td>over 25 s/d</td></tr>
</table>
<p style='color:#8a94a4'>Those are modern-Swiss numbers and the app adjusts its
expectations per caliber. Vintage and budget movements run lower by design.
Amplitude over 330&deg; is a problem, not an achievement -- that is where the
impulse pin starts striking the back of the fork horn.</p>

<h2 style='color:#4da3ff'>The other tabs</h2>
<p><b>Positions</b> collects the six-position set. <b>Advice</b> reads it and
tells you what to adjust for your caliber. <b>Tools</b> has the regulator
sensitivity helper, the lift-angle solver, a demagnetiser A/B and the report
builder. <b>Power reserve</b> logs amplitude decay over hours. <b>Diagnostics</b>
scans the timing residuals for repeating faults like a bent escape wheel tooth.</p>

<h2 style='color:#4da3ff'>Sync</h2>
<p>A reference clock for hand-setting a watch. Pick a time source -- this
computer, or a public NTP server for true time independent of the PC clock --
and press <i>Sync now</i>. The face shows corrected time and the panel reports
how far the computer's own clock is off. Turn on the per-second flash or beep
to land the seconds hand precisely. <i>Mark</i> when you set the watch, come
back later, enter what it reads, and it works out the daily rate.</p>

<h2 style='color:#4da3ff'>My Watches</h2>
<p>Save runs against a watch and the trend builds up. A single run says how it
is behaving today; repeated runs say whether it is drifting, which is the
question that decides when a service is due. Three runs over months are the
minimum before the trend figures mean anything.</p>

<p style='color:#8a94a4;margin-top:20px'>Full documentation is in README.md
alongside the application.</p>
<p style='color:#5a6472'>Version %s</p>
</div>""" % __version__)
        v.addWidget(t)
        return page

    def _build_watches_page(self):
        page = QtWidgets.QWidget()
        outer = QtWidgets.QHBoxLayout(page)
        outer.setContentsMargins(10, 10, 10, 10)

        # --- left: the collection list ---
        left = QtWidgets.QVBoxLayout()
        cap = QtWidgets.QLabel("MY WATCHES")
        cap.setStyleSheet("color:#4da3ff;font-weight:bold;letter-spacing:.06em;"
                          "font-size:12px;padding:2px 0 6px;")
        left.addWidget(cap)
        self.lst_watches = QtWidgets.QListWidget()
        self.lst_watches.setMinimumWidth(260)
        self.lst_watches.setMaximumWidth(330)
        self.lst_watches.setIconSize(QtCore.QSize(46, 46))
        self.lst_watches.setStyleSheet(
            "QListWidget{background:#1a1f27;border:1px solid #2a323e;border-radius:8px;}"
            "QListWidget::item{padding:7px;border-bottom:1px solid #232a34;}"
            f"QListWidget::item:selected{{background:{ACCENT};color:#08101c;}}")
        self.lst_watches.currentRowChanged.connect(self._watch_selected)
        left.addWidget(self.lst_watches, 1)

        row = QtWidgets.QHBoxLayout()
        for label, slot in (("Add", self._watch_add), ("Edit", self._watch_edit),
                            ("Delete", self._watch_delete)):
            b = QtWidgets.QPushButton(label)
            b.clicked.connect(slot)
            row.addWidget(b)
        left.addLayout(row)

        self.btn_save_test = QtWidgets.QPushButton("Save current run to this watch")
        self.btn_save_test.setMinimumHeight(32)
        self.btn_save_test.setStyleSheet(
            f"QPushButton{{background:{ACCENT};color:#08101c;font-weight:bold;"
            "padding:8px;border-radius:6px;}")
        self.btn_save_test.clicked.connect(self._save_test_to_watch)
        left.addWidget(self.btn_save_test)

        self.btn_test_this = QtWidgets.QPushButton("Measure this watch now")
        self.btn_test_this.setMinimumHeight(30)
        self.btn_test_this.clicked.connect(self._measure_selected)
        left.addWidget(self.btn_test_this)
        outer.addLayout(left, 0)

        # --- right: detail, trend, history ---
        right = QtWidgets.QVBoxLayout()
        top = QtWidgets.QHBoxLayout()
        self.lbl_wphoto = QtWidgets.QLabel()
        self.lbl_wphoto.setFixedSize(190, 190)
        self.lbl_wphoto.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_wphoto.setStyleSheet(
            "border:1px solid #2a323e;border-radius:8px;color:#5a6472;background:#1a1f27;")
        self.txt_wdetail = QtWidgets.QTextBrowser()
        self.txt_wdetail.setStyleSheet(
            "QTextBrowser{background:#1a1f27;border:1px solid #2a323e;border-radius:8px;}")
        top.addWidget(self.lbl_wphoto)
        top.addWidget(self.txt_wdetail, 1)
        right.addLayout(top, 2)

        self.p_trend = pg.PlotWidget(title="Performance over time")
        self.p_trend.setLabel("bottom", "date")
        self.p_trend.showGrid(x=True, y=True, alpha=0.25)
        self.p_trend.addLegend(offset=(-10, 10))
        self.p_trend.setAxisItems({"bottom": pg.DateAxisItem(orientation="bottom")})
        self.c_tr_amp = self.p_trend.plot(pen=pg.mkPen("#57d38c", width=2), symbol="o",
                                          symbolSize=7, symbolBrush="#57d38c",
                                          name="peak amplitude (deg)")
        self.c_tr_rate = self.p_trend.plot(pen=pg.mkPen("#4da3ff", width=2), symbol="o",
                                           symbolSize=7, symbolBrush="#4da3ff",
                                           name="mean rate (s/d)")
        self.c_tr_delta = self.p_trend.plot(pen=pg.mkPen("#ff9d4d", width=2), symbol="o",
                                            symbolSize=7, symbolBrush="#ff9d4d",
                                            name="positional delta (s/d)")
        right.addWidget(self.p_trend, 3)

        self.tbl_hist = QtWidgets.QTableWidget(0, 7)
        self.tbl_hist.setHorizontalHeaderLabels(
            ["Date", "Mean rate", "Delta", "Amp max", "Amp min", "Beat err", "Notes"])
        self.tbl_hist.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.tbl_hist.verticalHeader().setVisible(False)
        right.addWidget(self.tbl_hist, 2)

        hb = QtWidgets.QHBoxLayout()
        self.btn_wreport = QtWidgets.QPushButton("Print / save watch report")
        self.btn_wreport.setMinimumHeight(32)
        self.btn_wreport.setStyleSheet(
            "QPushButton{background:#57d38c;color:#08101c;font-weight:bold;"
            "padding:8px 18px;border-radius:6px;}")
        self.btn_wreport.clicked.connect(self._watch_report)
        hb.addWidget(self.btn_wreport)
        for label, slot in (("Delete selected run", self._hist_delete),
                            ("Export history CSV", self._hist_export)):
            b = QtWidgets.QPushButton(label)
            b.clicked.connect(slot)
            hb.addWidget(b)
        hb.addStretch(1)
        right.addLayout(hb)
        outer.addLayout(right, 1)
        return page

    def _build_controls(self):
        """
        Left column, in the order the work actually happens: pick an input,
        pick the watch, set the conditions, then start.

        The Start button sits at the bottom because that is the last thing you
        touch, and there is only one of it -- a separate "listen" and "run"
        pair asked the user to distinguish two things that are really one
        action with a duration attached.
        """
        inner = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(inner)
        lay.setSpacing(6)
        lay.setContentsMargins(8, 8, 8, 8)

        # ---------- 1. audio input ----------
        g = Collapsible("1.  AUDIO INPUT", True)
        self.cmb_dev = QtWidgets.QComboBox()
        self.cmb_dev.currentIndexChanged.connect(self._device_changed)
        self.cmb_sr = QtWidgets.QComboBox()
        self.cmb_sr.addItems(["44100", "48000", "96000", "192000"])
        self.cmb_sr.setCurrentText("48000")
        self.lvl = QtWidgets.QProgressBar()
        self.lvl.setRange(0, 100)
        self.lvl.setTextVisible(False)
        self.lvl.setFixedHeight(10)
        dev_row = QtWidgets.QWidget()
        dv = QtWidgets.QHBoxLayout(dev_row)
        dv.setContentsMargins(0, 0, 0, 0)
        dv.setSpacing(6)
        dv.addWidget(self.cmb_dev, 3)
        dv.addWidget(self.cmb_sr, 1)
        g.addRow("Device", dev_row)
        g.addRow("Level", self.lvl)
        lay.addWidget(g)

        # ---------- 2. the watch ----------
        g2 = Collapsible("2.  WATCH", True)
        self.btn_mywatches = QtWidgets.QPushButton("My Watches...")
        self.btn_mywatches.setMinimumHeight(30)
        self.btn_mywatches.clicked.connect(self._goto_watches)
        self.btn_model = QtWidgets.QPushButton("Find by model...")
        self.btn_model.setMinimumHeight(28)
        self.btn_model.setToolTip(
            "Do not know the caliber? Search by the watch instead -- Air King,\n"
            "SKX007, Black Bay, PRX. Shows every movement that model has used,\n"
            "with references and years, so you can pick the right generation.")
        self.btn_model.clicked.connect(self._find_model)
        self.cmb_watch = QtWidgets.QComboBox()
        self.cmb_watch.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.cmb_watch.setMinimumContentsLength(12)
        self.cmb_watch.setToolTip(
            "Attribute this run to one of your watches. Selecting one applies its\n"
            "caliber and lift angle, and saving files the results into its history.")
        self.cmb_watch.currentIndexChanged.connect(self._watch_combo_changed)
        self.txt_search = QtWidgets.QLineEdit()
        self.txt_search.setPlaceholderText(
            "caliber or watch: 2824, nh35, Rolex Submariner, 126610LN")
        self.txt_search.textChanged.connect(self._filter_calibers)
        self.lbl_hint = QtWidgets.QLabel("")
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.setStyleSheet("color:#7fb2ff;font-size:11px;")
        self.cmb_cal = QtWidgets.QComboBox()
        self.cmb_cal.setMaxVisibleItems(25)
        self.cmb_cal.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.cmb_cal.setMinimumContentsLength(12)
        self.cmb_cal.currentIndexChanged.connect(self._caliber_changed)
        self.spn_lift = QtWidgets.QDoubleSpinBox()
        self.spn_lift.setRange(20, 90)
        self.spn_lift.setDecimals(1)
        self.spn_lift.setSingleStep(0.5)
        self.spn_lift.setValue(52.0)
        self.spn_lift.valueChanged.connect(self._push_cfg)
        self.cmb_bph = QtWidgets.QComboBox()
        self.cmb_bph.addItem("Auto-detect", None)
        for b in STANDARD_BPH:
            self.cmb_bph.addItem(str(b), b)
        self.cmb_bph.currentIndexChanged.connect(self._push_cfg)
        self.lbl_calinfo = QtWidgets.QLabel("--")
        self.lbl_calinfo.setWordWrap(True)
        self.lbl_calinfo.setStyleSheet("color:#8a94a4;font-size:11px;")

        lb_row = QtWidgets.QWidget()
        lb_lay = QtWidgets.QHBoxLayout(lb_row)
        lb_lay.setContentsMargins(0, 0, 0, 0)
        lb_lay.setSpacing(6)
        lb_lay.addWidget(self.spn_lift, 1)
        _bl = QtWidgets.QLabel("beat")
        _bl.setStyleSheet("color:#8a94a4;font-size:11px;")
        lb_lay.addWidget(_bl, 0)
        lb_lay.addWidget(self.cmb_bph, 1)

        wbtn_row = QtWidgets.QWidget()
        wb = QtWidgets.QHBoxLayout(wbtn_row)
        wb.setContentsMargins(0, 0, 0, 0)
        wb.setSpacing(6)
        wb.addWidget(self.btn_mywatches, 1)
        wb.addWidget(self.btn_model, 1)

        g2.addRow(wbtn_row)
        g2.addRow("My watch", self.cmb_watch)
        g2.addRow("Search", self.txt_search)
        g2.addRow("", self.lbl_hint)
        g2.addRow("Caliber", self.cmb_cal)
        g2.addRow("Lift / bph", lb_row)
        g2.addRow(self.lbl_calinfo)
        lay.addWidget(g2)

        # ---------- 3. test conditions ----------
        g3 = Collapsible("3.  TEST CONDITIONS", True)
        self.cmb_pos = QtWidgets.QComboBox()
        self.cmb_pos.addItems(advisor.POSITIONS)
        self.cmb_pos.setCurrentText("Dial up")
        self.cmb_wind = QtWidgets.QComboBox()
        self.cmb_wind.addItems(["Full wind", "6h", "12h", "24h", "36h"])
        self.btn_capture = QtWidgets.QPushButton("Capture position")
        self.btn_capture.setMinimumHeight(28)
        self.btn_capture.setToolTip("Record the current live reading as this position/wind state.")
        self.btn_capture.clicked.connect(self._capture)
        self.chk_auto = QtWidgets.QCheckBox("Auto-capture when stable")
        self.chk_auto.setToolTip(
            "Captures the position by itself once several consecutive readings agree.")
        self.chk_auto.toggled.connect(lambda _: self._stable.clear())

        pos_row = QtWidgets.QWidget()
        pr_lay = QtWidgets.QHBoxLayout(pos_row)
        pr_lay.setContentsMargins(0, 0, 0, 0)
        pr_lay.setSpacing(6)
        pr_lay.addWidget(self.cmb_pos, 3)
        pr_lay.addWidget(self.cmb_wind, 2)

        g3.addRow("Pos / wind", pos_row)
        g3.addRow(self.btn_capture)
        g3.addRow(self.chk_auto)
        lay.addWidget(g3)

        # ---------- 4. pickup tuning: button out front, dials folded away ----
        g4 = Collapsible("4.  PICKUP TUNING", False)
        self.btn_tune = QtWidgets.QPushButton("Self-tune")
        self.btn_tune.setMinimumHeight(30)
        self.btn_tune.setToolTip(
            "Listens to what the pickup is actually delivering and sweeps the filter\n"
            "band, envelope window and sub-noise threshold to find the settings that\n"
            "resolve the escapement most cleanly. About ten seconds.")
        self.btn_tune.clicked.connect(self._self_tune)

        self.btn_noise = QtWidgets.QPushButton("Room noise")
        self.btn_noise.setMinimumHeight(30)
        self.btn_noise.setToolTip(
            "Lift the watch off the pickup and press this. It listens for a couple of\n"
            "seconds and tells you whether the room is quiet enough to trust an\n"
            "amplitude reading. Starts listening on its own if nothing is running.")
        self.btn_noise.clicked.connect(self._noise_check)

        tune_row = QtWidgets.QWidget()
        tr = QtWidgets.QHBoxLayout(tune_row)
        tr.setContentsMargins(0, 0, 0, 0)
        tr.setSpacing(6)
        tr.addWidget(self.btn_tune, 1)
        tr.addWidget(self.btn_noise, 1)
        g4.addPersistent(tune_row)

        self.spn_win = QtWidgets.QDoubleSpinBox()
        self.spn_win.setRange(4, 120)
        self.spn_win.setValue(20)
        self.spn_win.setSuffix(" s")
        self.spn_win.setToolTip(
            "How much of the most recent audio each LIVE reading uses. Not the\n"
            "length of a test -- that is the duration beside the Start button.")
        self.spn_win.valueChanged.connect(self._push_cfg)
        self.spn_lo = QtWidgets.QSpinBox()
        self.spn_lo.setRange(100, 8000)
        self.spn_lo.setValue(1500)
        self.spn_lo.setSuffix(" Hz")
        self.spn_lo.valueChanged.connect(self._push_cfg)
        self.spn_hi = QtWidgets.QSpinBox()
        self.spn_hi.setRange(2000, 90000)
        self.spn_hi.setValue(12000)
        self.spn_hi.setSuffix(" Hz")
        self.spn_hi.valueChanged.connect(self._push_cfg)
        self.spn_env = QtWidgets.QDoubleSpinBox()
        self.spn_env.setRange(0.05, 3.0)
        self.spn_env.setSingleStep(0.05)
        self.spn_env.setValue(0.35)
        self.spn_env.setSuffix(" ms")
        self.spn_env.valueChanged.connect(self._push_cfg)
        self.spn_thr = QtWidgets.QDoubleSpinBox()
        self.spn_thr.setRange(0.02, 0.60)
        self.spn_thr.setSingleStep(0.01)
        self.spn_thr.setValue(0.16)
        self.spn_thr.setToolTip(
            "How loud a sub-noise must be, relative to the loudest noise in that\n"
            "beat, to count. Only affects amplitude.")
        self.spn_thr.valueChanged.connect(self._push_cfg)
        self.spn_trace = QtWidgets.QDoubleSpinBox()
        self.spn_trace.setRange(2, 200)
        self.spn_trace.setValue(20)
        self.spn_trace.setSuffix(" ms")
        self.chk_parity = QtWidgets.QCheckBox("Correct tick/tock anchor")
        self.chk_parity.setChecked(True)
        self.chk_parity.setToolTip(
            "Removes the offset caused by the detector locking onto a different\n"
            "sub-noise for ticks than for tocks. Untick to see the raw beat error.")
        self.chk_parity.toggled.connect(self._push_cfg)
        b_reset = QtWidgets.QPushButton("Reset")
        b_reset.setToolTip("Filter band, envelope window and sub-noise threshold back to defaults.")
        b_reset.clicked.connect(self._reset_tuning)
        b_prof = QtWidgets.QPushButton("Save profile")
        b_prof.setToolTip(
            "Save the filter band, envelope window and sub-noise threshold against the\n"
            "selected input device. They load automatically whenever you pick that\n"
            "device again -- handy if you switch between a piezo and a microphone.")
        b_prof.clicked.connect(self._save_pickup_profile)
        b_forget = QtWidgets.QPushButton("Forget")
        b_forget.setMaximumWidth(72)
        b_forget.clicked.connect(self._forget_pickup_profile)
        prow = QtWidgets.QHBoxLayout()
        prow.addWidget(b_prof, 1)
        prow.addWidget(b_forget, 0)
        pw2 = QtWidgets.QWidget()
        pw2.setLayout(prow)
        band_row = QtWidgets.QWidget()
        bd_lay = QtWidgets.QHBoxLayout(band_row)
        bd_lay.setContentsMargins(0, 0, 0, 0)
        bd_lay.setSpacing(6)
        bd_lay.addWidget(self.spn_lo, 1)
        _dash = QtWidgets.QLabel("to")
        _dash.setStyleSheet("color:#8a94a4;font-size:11px;")
        bd_lay.addWidget(_dash, 0)
        bd_lay.addWidget(self.spn_hi, 1)

        g4.addRow("Window", self.spn_win)
        g4.addRow("Band", band_row)
        g4.addRow("Envelope", self.spn_env)
        g4.addRow("Threshold", self.spn_thr)
        g4.addRow("Trace", self.spn_trace)
        g4.addRow(self.chk_parity)
        g4.addRow(pw2)
        g4.addRow(b_reset)
        lay.addWidget(g4)

        # ---------- simulator, only with the simulated device ----------
        self.g_sim = Collapsible("SIMULATED WATCH", True)
        self.sim_bph = QtWidgets.QComboBox()
        for b in STANDARD_BPH:
            self.sim_bph.addItem(str(b), b)
        self.sim_bph.setCurrentText("28800")
        self.sim_amp = QtWidgets.QDoubleSpinBox()
        self.sim_amp.setRange(80, 350)
        self.sim_amp.setValue(275)
        self.sim_amp.setSuffix(" deg")
        self.sim_rate = QtWidgets.QDoubleSpinBox()
        self.sim_rate.setRange(-300, 300)
        self.sim_rate.setValue(0)
        self.sim_rate.setSuffix(" s/d")
        self.sim_be = QtWidgets.QDoubleSpinBox()
        self.sim_be.setRange(0, 5)
        self.sim_be.setSingleStep(0.1)
        self.sim_be.setValue(0.0)
        self.sim_be.setSuffix(" ms")
        self.sim_snr = QtWidgets.QDoubleSpinBox()
        self.sim_snr.setRange(0, 40)
        self.sim_snr.setValue(18)
        self.sim_snr.setSuffix(" dB")
        for wgt in (self.sim_bph, self.sim_amp, self.sim_rate, self.sim_be, self.sim_snr):
            (wgt.currentIndexChanged if isinstance(wgt, QtWidgets.QComboBox)
             else wgt.valueChanged).connect(self._push_sim)
        self.g_sim.addRow("Beat rate", self.sim_bph)
        self.g_sim.addRow("Amplitude", self.sim_amp)
        self.g_sim.addRow("Rate", self.sim_rate)
        self.g_sim.addRow("Beat error", self.sim_be)
        self.g_sim.addRow("Noise", self.sim_snr)
        lay.addWidget(self.g_sim)
        self.g_sim.setVisible(False)
        lay.addStretch(1)

        # ---------- 5. start -- pinned below the scroll area, always visible ----
        startbox = QtWidgets.QFrame()
        startbox.setStyleSheet(
            "QFrame{background:#1a1f27;border:1px solid #2a323e;border-radius:8px;}")
        sv = QtWidgets.QVBoxLayout(startbox)
        sv.setContentsMargins(10, 8, 10, 10)
        sv.setSpacing(6)
        durrow = QtWidgets.QHBoxLayout()
        lab = QtWidgets.QLabel("Duration")
        lab.setStyleSheet("color:#8a94a4;font-size:11px;border:none;")
        self.spn_runlen = QtWidgets.QSpinBox()
        self.spn_runlen.setRange(0, 3600)
        self.spn_runlen.setValue(20)
        self.spn_runlen.setSuffix(" s")
        self.spn_runlen.setSpecialValueText("open-ended")
        self.spn_runlen.setToolTip(
            "How long the measurement runs. At the end the whole capture is analysed\n"
            "in one pass -- more accurate than any single live reading -- and you are\n"
            "asked what to do with the result.\n\n"
            "Set to 0 for open-ended: it runs until you press Stop, and asks the same\n"
            "question then.\n\n"
            "For a multi-hour mainspring test use the Power reserve tab instead.")
        durrow.addWidget(lab)
        durrow.addWidget(self.spn_runlen, 1)
        sv.addLayout(durrow)
        self.chk_settle = QtWidgets.QCheckBox("Settle before timing")
        self.chk_settle.setStyleSheet("color:#8a94a4;font-size:11px;")
        self.chk_settle.setToolTip(
            "After a timed run is started, hold off resetting the capture until rate and\n"
            "amplitude have stopped drifting -- so the run records the watch, not the\n"
            "transient from setting it down. Falls back to starting anyway after 90 s.")
        sv.addWidget(self.chk_settle)
        self.btn_go = QtWidgets.QPushButton("Start")
        self.btn_go.setCheckable(True)
        self.btn_go.setMinimumHeight(40)
        f = self.btn_go.font()
        f.setPointSize(11)
        f.setBold(True)
        self.btn_go.setFont(f)
        self.btn_go.setStyleSheet(
            "QPushButton{background:#57d38c;color:#08101c;border-radius:6px;}"
            "QPushButton:checked{background:#ff5d5d;color:#fff;}")
        self.btn_go.toggled.connect(self._on_go)
        sv.addWidget(self.btn_go)
        self.prg_run = QtWidgets.QProgressBar()
        self.prg_run.setRange(0, 100)
        self.prg_run.setFixedHeight(16)
        self.prg_run.setFormat("idle")
        sv.addWidget(self.prg_run)

        self._fill_calibers()

        want = inner.sizeHint().width() + 18
        self._sidebar_width = max(330, min(410, want))
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)

        col = QtWidgets.QWidget()
        cl = QtWidgets.QVBoxLayout(col)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(6)
        cl.addWidget(scroll, 1)
        cl.addWidget(startbox, 0)      # Start stays put no matter how the sections expand
        col.setMinimumWidth(290)
        col.setMaximumWidth(500)
        return col

    def _reset_tuning(self):
        for w, v in ((self.spn_lo, 1500), (self.spn_hi, 12000),
                     (self.spn_env, 0.35), (self.spn_thr, 0.16)):
            w.setValue(v)
        self.chk_parity.setChecked(True)

    def _device_changed(self):
        is_sim = self.cmb_dev.currentData() == "SIM"
        self.g_sim.setVisible(is_sim)
        if not self._tuning and not self._selftune_session:
            self.btn_tune.setEnabled(not is_sim)
        self.btn_tune.setToolTip(
            "Not useful on the simulator -- it generates clean beats by construction."
            if is_sim else
            "One press: starts listening, sweeps the filter band, envelope window and\n"
            "sub-noise threshold for the cleanest reading, applies the result and stops.\n"
            "About 10 seconds; gives up after 15.")
        self._apply_pickup_profile()

    # ------------------------------------------------------------ pickup profiles
    def _pickup_key(self):
        if self.cmb_dev.currentData() == "SIM":
            return None
        return (self.cmb_dev.currentText() or "").strip() or None

    def _profiles_path(self):
        return os.path.join(APP_DIR, "pickup_profiles.json")

    def _load_profiles(self):
        import json
        try:
            with open(self._profiles_path(), encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def _apply_pickup_profile(self):
        key = self._pickup_key()
        prof = getattr(self, "_pickup_profiles", None)
        if prof is None:
            self._pickup_profiles = prof = self._load_profiles()
        p = prof.get(key) if key else None
        if not p:
            return
        for spn, k in ((self.spn_lo, "band_lo"), (self.spn_hi, "band_hi"),
                       (self.spn_env, "env_win_ms"), (self.spn_thr, "sub_threshold")):
            if k in p:
                spn.blockSignals(True)
                spn.setValue(p[k])
                spn.blockSignals(False)
        self._push_cfg()
        self.status.showMessage(f"Loaded saved filter settings for '{key}'.", 4000)

    def _save_pickup_profile(self):
        import json
        key = self._pickup_key()
        if not key:
            QtWidgets.QMessageBox.information(
                self, "Pickup profile", "Select a real input device first.")
            return
        self._pickup_profiles = self._load_profiles()
        self._pickup_profiles[key] = {
            "band_lo": int(self.spn_lo.value()), "band_hi": int(self.spn_hi.value()),
            "env_win_ms": float(self.spn_env.value()),
            "sub_threshold": float(self.spn_thr.value())}
        try:
            with open(self._profiles_path(), "w", encoding="utf-8") as fh:
                json.dump(self._pickup_profiles, fh, indent=2)
        except OSError as e:
            QtWidgets.QMessageBox.warning(self, "Pickup profile", str(e))
            return
        self.status.showMessage(f"Saved these filter settings for '{key}'.", 5000)

    def _forget_pickup_profile(self):
        import json
        key = self._pickup_key()
        self._pickup_profiles = self._load_profiles()
        if key and self._pickup_profiles.pop(key, None) is not None:
            try:
                with open(self._profiles_path(), "w", encoding="utf-8") as fh:
                    json.dump(self._pickup_profiles, fh, indent=2)
            except OSError:
                pass
            self.status.showMessage(f"Forgot the saved settings for '{key}'.", 4000)

    def _self_tune(self):
        if self._selftune_session:
            # Second click cancels. Leaving the button dead during a sweep gives
            # you no way out if the pickup is bad enough to make it slow.
            self.worker.cancel_tune()
            self.btn_tune.setText("Cancelling...")
            self._selftune_finish(reason="cancelled")
            return
        if self.cmb_dev.currentData() == "SIM":
            return

        self._selftune_started_listen = self.recorder is None
        if self.recorder is None:
            self._suppress_finish = True
            try:
                self._toggle_listen(True)
            finally:
                self._suppress_finish = False
            if self.recorder is None:      # audio error already shown
                self._selftune_started_listen = False
                return
            self._set_go(True)             # reflect that we are now listening

        self._selftune_session = True
        self._selftune_baseline = (tuning_score(self.last)
                                   if self.last is not None and self.last.ok else None)
        self.btn_tune.setText("Waiting for audio... (click to cancel)")
        self.btn_tune.setEnabled(True)
        # Backstop for "the buffer never fills"; the sweep itself is bounded by
        # its own 15 s deadline, checked in _tick_ui.
        self._tune_watchdog.start(25000)

    def _noise_check(self):
        """
        Self-contained, like Self-tune: it opens the stream if nothing is
        listening, measures the noise floor for a couple of seconds off the
        UI tick, reports, and closes the stream again if it was the one that
        opened it. Second click cancels; it gives up after 12 s.
        """
        if self._noise_session:
            self._noise_finish(reason="cancelled")
            return
        if self._selftune_session or self._run_t0 is not None:
            QtWidgets.QMessageBox.information(
                self, "Room noise check",
                "Finish the current run or self-tune first, then check the room noise "
                "between runs.")
            return
        if QtWidgets.QMessageBox.question(
                self, "Room noise check",
                "Lift the watch off the pickup, then press OK. Keep still and quiet "
                "for a couple of seconds while it measures.",
                QtWidgets.QMessageBox.Ok | QtWidgets.QMessageBox.Cancel
                ) != QtWidgets.QMessageBox.Ok:
            return

        self._noise_started_listen = self.recorder is None
        if self.recorder is None:
            self._suppress_finish = True
            try:
                self._toggle_listen(True)
            finally:
                self._suppress_finish = False
            if self.recorder is None:            # audio error already shown
                self._noise_started_listen = False
                return
            self._set_go(True)

        self._noise_session = True
        self._noise_deadline = time.monotonic() + 12.0
        self.btn_noise.setText("Measuring... (click to cancel)")
        self.recorder.clear()                    # measure fresh air, not a stale buffer
        self.status.showMessage("Room noise check: measuring the noise floor...")

    def _noise_finish(self, reason):
        was = self._noise_session
        started = self._noise_started_listen
        self._noise_session = False
        self._noise_started_listen = False
        self.btn_noise.setText("Room noise")
        if not was:
            return
        verdict = None
        if reason == "done" and self.recorder is not None:
            data = np.asarray(self.recorder.read(2.0), dtype=np.float64)
            if data.size >= self.recorder.samplerate:
                verdict = self._noise_verdict(data)
        if started and self.recorder is not None:
            self._suppress_finish = True
            self._toggle_listen(False)
            self._suppress_finish = False
        if verdict:
            QtWidgets.QMessageBox.information(self, "Room noise check", verdict)
        elif reason == "timeout":
            QtWidgets.QMessageBox.warning(
                self, "Room noise check",
                "Could not capture two seconds of audio within twelve seconds. "
                "The pickup may not be delivering -- check the device and the level meter.")
        elif reason == "failed":
            self.status.showMessage("Room noise check stopped -- audio unavailable.", 5000)
        else:
            self.status.showMessage("Room noise check cancelled.", 4000)

    @staticmethod
    def _noise_verdict(data):
        rms = float(np.sqrt(np.mean(data * data))) + 1e-12
        peak = float(np.max(np.abs(data)))
        db = 20.0 * np.log10(rms)
        if db < -60:
            v = "Very quiet -- excellent conditions for amplitude."
        elif db < -48:
            v = "Quiet enough. Amplitude readings should be trustworthy."
        elif db < -38:
            v = ("Borderline. Faint sub-noises may be lost -- amplitude could read low "
                 "and jump around. Close doors, stop fans, try again.")
        else:
            v = ("Too loud. The unlocking noise will be buried under the room. Move "
                 "somewhere quieter or improve the pickup's acoustic isolation before "
                 "trusting amplitude.")
        return (f"Noise floor: {db:.0f} dBFS RMS  "
                f"(peak {20 * np.log10(peak + 1e-12):.0f} dBFS)\n\n" + v)

    def _reset_tune_button(self):
        self._tuning = False
        self._tune_watchdog.stop()
        self.btn_tune.setText("Self-tune")
        self.btn_tune.setEnabled(self.cmb_dev.currentData() != "SIM")

    def _selftune_finish(self, reason):
        """End a one-press self-tune session. reason: done|timeout|failed|cancelled."""
        was_session = self._selftune_session
        started_listen = self._selftune_started_listen
        self._selftune_session = False
        self._selftune_started_listen = False
        self._reset_tune_button()
        if not was_session:
            return
        if reason != "done":
            self.worker.cancel_tune()
            if reason == "timeout":
                QtWidgets.QMessageBox.warning(
                    self, "Self-tune",
                    "Self-tune did not finish within 15 seconds and has been stopped.\n\n"
                    "Settings are unchanged. The pickup is still listening, so you can "
                    "set the filter band and sub-noise threshold by hand while watching "
                    "the beat waveform panel.")
            elif reason == "failed":
                self.status.showMessage("Self-tune stopped -- audio unavailable.", 6000)
            else:
                self.status.showMessage("Self-tune cancelled -- settings unchanged.", 5000)
            return
        if started_listen:
            # "start a run, tune, then end" -- only tear down the stream this
            # feature opened; a session already listening is left as we found it.
            self._suppress_finish = True
            self._toggle_listen(False)
            self._suppress_finish = False

    def _on_tune_failed(self, msg):
        if self._selftune_session:
            self._selftune_finish(
                reason="cancelled" if msg.startswith("Tuning cancelled") else "failed")
            return
        self._reset_tune_button()
        if msg.startswith("Tuning cancelled"):
            self.status.showMessage("Tuning cancelled -- settings unchanged.", 5000)
            return
        QtWidgets.QMessageBox.warning(self, "Self-tune", msg)

    def _tune_timed_out(self):
        if self._selftune_session:
            self._selftune_finish(reason="timeout")
            return
        self._reset_tune_button()
        self.worker.cancel_tune()
        QtWidgets.QMessageBox.warning(
            self, "Self-tune",
            "Tuning did not finish in time and has been stopped.\n\n"
            "Settings are unchanged. Try a shorter rolling window, or set the filter "
            "band and sub-noise threshold by hand while watching the beat waveform "
            "panel.")

    def _on_tune_progress(self, done, total):
        if self._tuning or self._selftune_session:
            self.btn_tune.setText(f"Tuning... {done}/{total} (click to cancel)")

    def _on_tuned(self, cfg, rows):
        session = self._selftune_session
        self._reset_tune_button()
        before = self._selftune_baseline if session else None
        if not session and self.last is not None and self.last.ok:
            before = tuning_score(self.last)

        for w, v in ((self.spn_lo, int(cfg.band_lo)), (self.spn_hi, int(cfg.band_hi)),
                     (self.spn_env, cfg.env_win_ms), (self.spn_thr, cfg.sub_threshold)):
            w.blockSignals(True)
            w.setValue(v)
            w.blockSignals(False)
        self._push_cfg()

        # Every trial can fail on a hopeless signal. Say so plainly rather
        # than applying settings that scored nothing.
        usable = [r for r in rows if r[2] is not None and r[1] > -1e8]
        if not usable:
            QtWidgets.QMessageBox.warning(
                self, "Self-tune",
                "No setting produced a usable reading, so nothing has been changed.\n\n"
                "The pickup is not resolving an escapement at all. Press the case back "
                "firmly against the sensor, check the level meter is moving without "
                "hitting red, and silence anything running nearby.")
            if session:
                self._selftune_finish(reason="done")
            return
        best = max(usable, key=lambda r: r[1])
        m = best[2]
        msg = [
            f"Filter band     {cfg.band_lo:.0f} - {cfg.band_hi:.0f} Hz",
            f"Envelope window {cfg.env_win_ms:.2f} ms",
            f"Sub-noise thresh {cfg.sub_threshold:.2f}",
            "",
            f"Noises per beat  {3 + m.extra_peaks:.1f}   (a lever escapement makes 3)",
            f"Template match   {m.quality:.2f}",
            f"Usable beats     {m.valid_frac*100:.0f}%",
        ]
        if before is not None:
            msg.append(f"\nSignal score {before:+.2f} -> {best[1]:+.2f}")
        if m.extra_peaks > 1.0:
            msg.append(
                "\nStill seeing well over three noises per beat. No filter setting fixes "
                "a pickup that is hearing the room: press the case back harder against "
                "the sensor, kill background noise, and back the input gain off if the "
                "level meter is anywhere near red.")

        if session:
            improved = before is None or best[1] > before
            self.status.showMessage(
                "Self-tune complete -- settings improved." if improved else
                "Self-tune complete -- no better settings found; left as they were.", 8000)
            self._selftune_finish(reason="done")
            return
        QtWidgets.QMessageBox.information(self, "Pickup tuned", "\n".join(msg))

    def _save_session(self):
        import json
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save session", f"session_{datetime.now():%Y%m%d_%H%M}.json",
            "JSON (*.json)")
        if not path:
            return
        c = self._current_caliber()
        data = {
            "saved": datetime.now().isoformat(timespec="seconds"),
            "caliber": c.key if c else None,
            "caliber_label": c.label if c else None,
            "lift_angle": self.spn_lift.value(),
            "tuning": {"band_lo": self.spn_lo.value(), "band_hi": self.spn_hi.value(),
                       "env_win_ms": self.spn_env.value(),
                       "sub_threshold": self.spn_thr.value()},
            "readings": [{"position": r.position, "wind": r.wind_state, "rate": r.rate,
                          "amplitude": r.amplitude, "beat_error": r.beat_error}
                         for r in self.readings],
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        self.status.showMessage(f"Saved {path}", 5000)

    def _build_display(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)

        row = QtWidgets.QHBoxLayout()
        self.r_rate = Readout("RATE", "seconds / day")
        self.r_amp = Readout("AMPLITUDE", "degrees")
        self.r_be = Readout("BEAT ERROR", "milliseconds")
        self.r_bph = Readout("BEAT RATE", "bph detected")
        for r in (self.r_rate, self.r_amp, self.r_be, self.r_bph):
            row.addWidget(r)
        lay.addLayout(row)

        plots = QtWidgets.QSplitter(QtCore.Qt.Horizontal)

        self.p_trace = pg.PlotWidget(title="Trace  --  slope is rate, gap between lines is beat error")
        self.p_trace.setLabel("bottom", "deviation", units="ms")
        self.p_trace.setLabel("left", "elapsed", units="s")
        self.p_trace.showGrid(x=True, y=True, alpha=0.25)
        self.p_trace.invertY(True)
        self.s_tick = pg.ScatterPlotItem(size=4, brush=pg.mkBrush(TICK_C), pen=None)
        self.s_tock = pg.ScatterPlotItem(size=4, brush=pg.mkBrush(TOCK_C), pen=None)
        self.p_trace.addItem(self.s_tick)
        self.p_trace.addItem(self.s_tock)
        plots.addWidget(self.p_trace)

        right = QtWidgets.QSplitter(QtCore.Qt.Vertical)

        wave_box = QtWidgets.QWidget()
        wave_lay = QtWidgets.QVBoxLayout(wave_box)
        wave_lay.setContentsMargins(0, 0, 0, 0)
        wave_lay.setSpacing(2)
        whead = QtWidgets.QHBoxLayout()
        self.lbl_wave = QtWidgets.QLabel()
        self.lbl_wave.setStyleSheet("color:#c8d0dc;font-size:12px;")
        self.cmb_wave = QtWidgets.QComboBox()
        self.cmb_wave.addItems(["Average", "Live beat", "Mic"])
        self.cmb_wave.setToolTip(
            "Average -- every beat in the rolling window, stacked and averaged (drives amplitude).\n"
            "Live beat -- the band-passed waveform of the most recent single beat.\n"
            "Mic -- the raw signal the pickup is delivering right now.")
        self.cmb_wave.currentTextChanged.connect(self._set_wave_mode)
        whead.addWidget(self.lbl_wave, 1)
        whead.addWidget(self.cmb_wave, 0)
        wave_lay.addLayout(whead)

        self.p_wave = pg.PlotWidget()
        self.p_wave.setLabel("bottom", "time within beat", units="ms")
        self.p_wave.showGrid(x=True, y=True, alpha=0.2)
        self.p_wave.setDownsampling(auto=True, mode="peak")
        self.p_wave.setClipToView(True)
        self.c_wave = self.p_wave.plot(pen=pg.mkPen("#57d38c", width=2))
        self.l_p1 = pg.InfiniteLine(angle=90, pen=pg.mkPen(TICK_C, width=2, style=QtCore.Qt.DashLine),
                                    label="unlock", labelOpts={"color": TICK_C, "position": 0.9})
        self.l_p3 = pg.InfiniteLine(angle=90, pen=pg.mkPen(TOCK_C, width=2, style=QtCore.Qt.DashLine),
                                    label="drop", labelOpts={"color": TOCK_C, "position": 0.9})
        self.p_wave.addItem(self.l_p1)
        self.p_wave.addItem(self.l_p3)
        wave_lay.addWidget(self.p_wave)
        right.addWidget(wave_box)
        self._wave_mode = "Average"
        self._set_wave_mode("Average")

        self.p_hist = pg.PlotWidget(title="Rate history")
        self.p_hist.setLabel("left", "s/day")
        self.p_hist.setLabel("bottom", "run time", units="min")
        self.p_hist.showGrid(x=True, y=True, alpha=0.2)
        self.c_hist = self.p_hist.plot(pen=pg.mkPen(ACCENT, width=2))
        right.addWidget(self.p_hist)
        plots.addWidget(right)
        plots.setSizes([620, 420])
        lay.addWidget(plots, 1)
        return w

    def _build_tabs(self):
        tabs = QtWidgets.QTabWidget()

        # positions
        pw = QtWidgets.QWidget()
        pl = QtWidgets.QVBoxLayout(pw)
        psplit = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.tbl = QtWidgets.QTableWidget(0, 6)
        self.tbl.setHorizontalHeaderLabels(
            ["Position", "Wind", "Rate s/d", "Amplitude", "Beat error ms", "Time"])
        self.tbl.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        psplit.addWidget(self.tbl)

        self.p_pos = pg.PlotWidget(title="Positional rate  --  bar height is s/day, dashed line is the mean")
        self.p_pos.setLabel("left", "rate", units="s/d")
        self.p_pos.showGrid(y=True, alpha=0.2)
        self.p_pos.getAxis("bottom").setTicks([[]])
        self.bar_pos = pg.BarGraphItem(x=[], height=[], width=0.6, brush="#4da3ff", pen=None)
        self.p_pos.addItem(self.bar_pos)
        self.l_pos_mean = pg.InfiniteLine(angle=0, pen=pg.mkPen("#e8eef7", width=1,
                                          style=QtCore.Qt.DashLine))
        self.p_pos.addItem(self.l_pos_mean)
        self._pos_labels = []
        psplit.addWidget(self.p_pos)
        psplit.setSizes([260, 220])
        pl.addWidget(psplit, 1)
        br = QtWidgets.QHBoxLayout()
        for label, slot in (("Delete selected", self._del_row),
                            ("Clear all", self._clear_rows),
                            ("Export CSV", self._export_csv)):
            b = QtWidgets.QPushButton(label)
            b.clicked.connect(slot)
            br.addWidget(b)
        br.addStretch(1)
        self.lbl_delta = QtWidgets.QLabel("")
        self.lbl_delta.setStyleSheet("color:#8a94a4;")
        br.addWidget(self.lbl_delta)
        pl.addLayout(br)
        tabs.addTab(pw, "Positions")

        # advice
        aw = QtWidgets.QWidget()
        al = QtWidgets.QVBoxLayout(aw)
        self.lbl_regassist = QtWidgets.QLabel("Regulation assistant: take a reading.")
        self.lbl_regassist.setWordWrap(True)
        self.lbl_regassist.setStyleSheet(
            "background:#1a1f27;border:1px solid #2a323e;border-radius:6px;"
            "padding:10px;color:#c8d0dc;font-size:12px;")
        al.addWidget(self.lbl_regassist)
        self.txt_advice = QtWidgets.QTextBrowser()
        self.txt_advice.setOpenExternalLinks(True)
        al.addWidget(self.txt_advice)
        grow = QtWidgets.QHBoxLayout()
        grow.addWidget(QtWidgets.QLabel("Grade against"))
        self.cmb_standard = QtWidgets.QComboBox()
        self.cmb_standard.addItems(list(advisor.STANDARDS.keys()))
        self.cmb_standard.currentTextChanged.connect(lambda _: self._advise())
        grow.addWidget(self.cmb_standard, 1)
        al.addLayout(grow)
        b = QtWidgets.QPushButton("Analyze and advise")
        b.setStyleSheet(
            f"QPushButton{{background:{ACCENT};color:#08101c;font-weight:bold;padding:8px;border-radius:6px;}}")
        b.clicked.connect(self._advise)
        al.addWidget(b)
        tabs.addTab(aw, "Advice")

        # tools
        tw = QtWidgets.QWidget()
        tl = QtWidgets.QFormLayout(tw)
        self.spn_before = QtWidgets.QDoubleSpinBox()
        self.spn_before.setRange(-900, 900)
        self.spn_after = QtWidgets.QDoubleSpinBox()
        self.spn_after.setRange(-900, 900)
        bcal = QtWidgets.QPushButton("How much further?")
        bcal.clicked.connect(self._calibrate)
        self.lbl_cal = QtWidgets.QLabel("Make one small regulator move, enter the rate before "
                                        "and after, and this works out the rest.")
        self.lbl_cal.setWordWrap(True)
        tl.addRow("Rate before (s/d)", self.spn_before)
        tl.addRow("Rate after (s/d)", self.spn_after)
        tl.addRow(bcal)
        tl.addRow(self.lbl_cal)

        tl.addRow(QtWidgets.QLabel(""))
        self.spn_known = QtWidgets.QDoubleSpinBox()
        self.spn_known.setRange(20, 360)
        self.spn_known.setValue(180)
        bsolve = QtWidgets.QPushButton("Solve lift angle from current signal")
        bsolve.clicked.connect(self._solve_lift)
        self.lbl_solve = QtWidgets.QLabel(
            "Mark one balance arm, let the watch run down until the mark appears to stall "
            "exactly opposite its rest position -- that is 180 degrees. With the watch held "
            "there, this back-solves the caliber's true lift angle.")
        self.lbl_solve.setWordWrap(True)
        tl.addRow("Known amplitude (deg)", self.spn_known)
        tl.addRow(bsolve)
        tl.addRow(self.lbl_solve)
        # ---- demagnetiser A/B, report ----
        tl.addRow(QtWidgets.QLabel(""))
        hb = QtWidgets.QHBoxLayout()
        b_before = QtWidgets.QPushButton("Capture before")
        b_before.clicked.connect(lambda: self._demag("before"))
        b_after = QtWidgets.QPushButton("Capture after")
        b_after.clicked.connect(lambda: self._demag("after"))
        for b in (b_before, b_after):
            hb.addWidget(b)
        hw = QtWidgets.QWidget()
        hw.setLayout(hb)
        self.lbl_demag = QtWidgets.QLabel(
            "Demagnetiser check. Capture before, run the watch through the "
            "demagnetiser, capture after. Magnetised hairspring coils cling "
            "together and behave like a shorter spring, so the classic signature "
            "is a large rate drop with amplitude barely moving.")
        self.lbl_demag.setWordWrap(True)
        tl.addRow("Demagnetiser A/B", hw)
        tl.addRow(self.lbl_demag)
        self._demag_before = None

        tl.addRow(QtWidgets.QLabel(""))
        b_rep = QtWidgets.QPushButton("Build service report")
        b_rep.setStyleSheet(
            f"QPushButton{{background:{ACCENT};color:#08101c;font-weight:bold;"
            "padding:8px;border-radius:6px;}")
        b_rep.clicked.connect(self._build_report)
        tl.addRow(b_rep)
        tabs.addTab(tw, "Tools")

        # ---- power reserve ----
        rw = QtWidgets.QWidget()
        rl = QtWidgets.QVBoxLayout(rw)
        self.p_res = pg.PlotWidget(
            title="Power reserve -- a multi-hour test, separate from a timed run")
        self.p_res.setLabel("bottom", "elapsed", units="h")
        self.p_res.setLabel("left", "amplitude", units="deg")
        self.p_res.showGrid(x=True, y=True, alpha=0.25)
        self.p_res.addLegend(offset=(-10, 10))
        self.c_res_amp = self.p_res.plot(pen=pg.mkPen("#57d38c", width=2), name="amplitude")
        self.res_rate_vb = pg.ViewBox()
        self.p_res.scene().addItem(self.res_rate_vb)
        ax2 = pg.AxisItem("right")
        self.p_res.plotItem.layout.addItem(ax2, 2, 3)
        ax2.linkToView(self.res_rate_vb)
        self.res_rate_vb.setXLink(self.p_res.plotItem)
        ax2.setLabel("rate", units="s/d", color="#ff9d4d")
        self.c_res_rate = pg.PlotDataItem(pen=pg.mkPen("#ff9d4d", width=2))
        self.res_rate_vb.addItem(self.c_res_rate)
        self.p_res.plotItem.vb.sigResized.connect(
            lambda: self.res_rate_vb.setGeometry(self.p_res.plotItem.vb.sceneBoundingRect()))
        rl.addWidget(self.p_res, 2)
        rb = QtWidgets.QHBoxLayout()
        self.btn_res = QtWidgets.QPushButton("Start power reserve log")
        self.btn_res.setCheckable(True)
        self.btn_res.toggled.connect(self._toggle_reserve)
        self.spn_res_int = QtWidgets.QSpinBox()
        self.spn_res_int.setRange(10, 3600)
        self.spn_res_int.setValue(300)
        self.spn_res_int.setSuffix(" s between samples")
        self.spn_res_hours = QtWidgets.QDoubleSpinBox()
        self.spn_res_hours.setRange(0.0, 120.0)
        self.spn_res_hours.setValue(48.0)
        self.spn_res_hours.setSuffix(" h target")
        self.spn_res_hours.setSpecialValueText("run until stopped")
        self.spn_res_hours.setToolTip(
            "Stop and announce when this many hours have elapsed. Set 0 to run\n"
            "until you stop it. A full reserve run needs the watch left on the\n"
            "pickup and the app listening the whole time.")
        b_res_exp = QtWidgets.QPushButton("Export CSV")
        b_res_exp.clicked.connect(self._export_reserve)
        b_res_clr = QtWidgets.QPushButton("Clear")
        b_res_clr.clicked.connect(self._clear_reserve)
        for wdg in (self.btn_res, self.spn_res_int, self.spn_res_hours,
                    b_res_exp, b_res_clr):
            rb.addWidget(wdg)
        rb.addStretch(1)
        self.lbl_res = QtWidgets.QLabel("Not logging.")
        self.lbl_res.setStyleSheet("color:#8a94a4;")
        rb.addWidget(self.lbl_res)
        rl.addLayout(rb)

        iso = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.p_iso = pg.PlotWidget(
            title="Isochronism -- rate against amplitude as the mainspring runs down")
        self.p_iso.setLabel("bottom", "amplitude", units="deg")
        self.p_iso.setLabel("left", "rate", units="s/d")
        self.p_iso.showGrid(x=True, y=True, alpha=0.25)
        self.s_iso = pg.ScatterPlotItem(size=5, brush=pg.mkBrush("#ff9d4d"), pen=None)
        self.c_iso_fit = self.p_iso.plot(pen=pg.mkPen("#e8eef7", width=1, style=QtCore.Qt.DashLine))
        self.p_iso.addItem(self.s_iso)
        iso.addWidget(self.p_iso)
        self.txt_iso = QtWidgets.QTextBrowser()
        self.txt_iso.setMaximumWidth(360)
        self.txt_iso.setHtml("<p style='color:#8a94a4;font-family:Segoe UI'>"
                             "Run a power-reserve log. Once amplitude has fallen far enough "
                             "to see a spread, the isochronism slope, the beat-error / "
                             "amplitude link and the projected runway to 220 deg appear here.</p>")
        iso.addWidget(self.txt_iso)
        iso.setSizes([620, 360])
        rl.addWidget(iso, 3)
        tabs.addTab(rw, "Power reserve")

        # ---- diagnostics ----
        dw = QtWidgets.QWidget()
        dl = QtWidgets.QVBoxLayout(dw)

        live = QtWidgets.QHBoxLayout()

        self.p_amp_hist = pg.PlotWidget(title="Per-beat amplitude  --  spread, not just the median")
        self.p_amp_hist.setLabel("bottom", "amplitude", units="deg")
        self.p_amp_hist.setLabel("left", "beats")
        self.p_amp_hist.showGrid(x=True, y=True, alpha=0.2)
        self.p_amp_hist.setXRange(150, 340, padding=0)
        self.bar_amp = pg.BarGraphItem(x=[], height=[], width=0, brush="#57d38c", pen=None)
        self.p_amp_hist.addItem(self.bar_amp)
        self.l_amp_med = pg.InfiniteLine(angle=90, pen=pg.mkPen("#e8eef7", width=1,
                                         style=QtCore.Qt.DashLine))
        self.p_amp_hist.addItem(self.l_amp_med)
        live.addWidget(self.p_amp_hist)

        self.p_be_hist = pg.PlotWidget(title="Beat error over the run")
        self.p_be_hist.setLabel("bottom", "run time", units="min")
        self.p_be_hist.setLabel("left", "beat error", units="ms")
        self.p_be_hist.showGrid(x=True, y=True, alpha=0.2)
        self.c_be_hist = self.p_be_hist.plot(pen=pg.mkPen("#ff9d4d", width=2))
        live.addWidget(self.p_be_hist)

        self.p_spec = pg.PlotWidget(title="Audio spectrum  --  shaded band is the current filter")
        self.p_spec.setLabel("bottom", "frequency", units="Hz")
        self.p_spec.setLabel("left", "level", units="dB")
        self.p_spec.showGrid(x=True, y=True, alpha=0.2)
        self.p_spec.setLogMode(x=True, y=False)
        self.p_spec.setYRange(-60, 2, padding=0)
        self.c_spec = self.p_spec.plot(pen=pg.mkPen("#4da3ff", width=1.5))
        self.reg_band = pg.LinearRegionItem(brush=(90, 163, 255, 55), pen=pg.mkPen(None),
                                            movable=False)
        self.p_spec.addItem(self.reg_band)
        live.addWidget(self.p_spec)

        dl.addLayout(live, 3)

        self.p_fault = pg.PlotWidget(title="Timing residual spectrum -- peaks are repeating faults")
        self.p_fault.setLabel("bottom", "period", units="beats")
        self.p_fault.setLabel("left", "swing", units="ms")
        self.p_fault.showGrid(x=True, y=True, alpha=0.25)
        self.p_fault.setLogMode(x=True, y=False)
        self.c_fault = self.p_fault.plot(pen=pg.mkPen("#4da3ff", width=1.5))
        dl.addWidget(self.p_fault, 2)
        self.txt_fault = QtWidgets.QTextBrowser()
        dl.addWidget(self.txt_fault, 2)
        b_fault = QtWidgets.QPushButton("Scan for periodic faults")
        b_fault.setStyleSheet(
            f"QPushButton{{background:{ACCENT};color:#08101c;font-weight:bold;"
            "padding:8px;border-radius:6px;}")
        b_fault.clicked.connect(self._scan_faults)
        dl.addWidget(b_fault)
        tabs.addTab(dw, "Diagnostics")

        self.tabs = tabs
        return tabs

    # ------------------------------------------------------------- plumbing
    def _start_worker(self):
        self.thread = QtCore.QThread(self)
        self.worker = AnalysisWorker()
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.result.connect(self._on_result)
        self.worker.tuned.connect(self._on_tuned)
        self.worker.tune_progress.connect(self._on_tune_progress)
        self.worker.tune_failed.connect(self._on_tune_failed)
        self.worker.failed.connect(lambda s: self.status.showMessage(s.splitlines()[-1], 6000))
        self.thread.start()
        self._push_cfg()
        self.tmr = QtCore.QTimer(self)
        self.tmr.timeout.connect(self._tick_ui)
        self.tmr.start(120)
        self._tune_watchdog = QtCore.QTimer(self)
        self._tune_watchdog.setSingleShot(True)
        self._tune_watchdog.timeout.connect(self._tune_timed_out)

    def _push_cfg(self):
        # Widget signals fire while the UI is still being built, before the
        # worker exists. Nothing to push at that point.
        if not hasattr(self, "worker"):
            return
        cfg = AnalyzerConfig(
            band_lo=float(self.spn_lo.value()),
            band_hi=float(self.spn_hi.value()),
            sub_threshold=float(self.spn_thr.value()),
            env_win_ms=float(self.spn_env.value()),
            lift_angle=float(self.spn_lift.value()),
            no_parity_fix=not self.chk_parity.isChecked(),
            forced_bph=self.cmb_bph.currentData())
        self.worker.cfg = cfg
        self.worker.window_s = float(self.spn_win.value())

    def _push_sim(self):
        """Retune the simulator live, so you can watch the readouts follow."""
        r = getattr(self, "recorder", None)
        if isinstance(r, audio.SimulatedRecorder):
            r.set_params(bph=self.sim_bph.currentData(),
                         amplitude=self.sim_amp.value(),
                         lift_angle=self.spn_lift.value(),
                         rate_spd=self.sim_rate.value(),
                         beat_error_ms=self.sim_be.value(),
                         snr_db=self.sim_snr.value())

    # Host API preference on Windows. WASAPI is the modern path and reports
    # honest sample rates; MME and DirectSound resample behind your back, and
    # WDM-KS often wants exclusive access and fails if anything else has the
    # device open. Only used to break ties when the system default is unknown.
    _API_RANK = {"wasapi": 0, "directsound": 1, "mme": 2, "wdm-ks": 3, "asio": 0}

    def _refresh_devices(self):
        """
        List real inputs first and select one. The simulator goes last and is
        only auto-selected when there is genuinely nothing to listen with --
        defaulting to it meant the app opened pretending to measure a watch
        that was not there.
        """
        self.cmb_dev.clear()
        devs = audio.list_input_devices()
        default = audio.default_input_device()

        for i, name, ch, sr, api in devs:
            self.cmb_dev.addItem(f"[{api}] {name}", i)

        if devs:
            def rank(entry):
                a = entry[4].strip().lower()
                for key, r in self._API_RANK.items():
                    if key in a:          # "Windows WASAPI" contains "wasapi"
                        return r
                return 5

            # Windows exposes the same physical microphone several times, once
            # per host API, and PortAudio's "default" is usually the MME copy.
            # MME resamples to 44100 behind your back, which costs about a
            # third of the amplitude resolution. So honour the user's chosen
            # DEVICE but take the best-quality route to it.
            target_name = None
            for i, name, ch, sr, api in devs:
                if i == default:
                    target_name = name
                    break

            candidates = [k for k, d in enumerate(devs)
                          if target_name is None or d[1] == target_name]
            if not candidates:
                candidates = list(range(len(devs)))
            chosen_row = min(candidates, key=lambda k: (rank(devs[k]), k))
            self.cmb_dev.setCurrentIndex(chosen_row)

        self.cmb_dev.addItem("-- Simulated watch (no microphone) --", "SIM")
        if not devs:
            self.cmb_dev.setCurrentIndex(self.cmb_dev.count() - 1)
            self.status.showMessage(
                "No audio input devices found. Plug in the pickup and press Rescan "
                "devices; the simulated watch is selected meanwhile.", 12000)
        self._device_changed()

    def _on_go(self, on):
        """
        One button, one action, with a duration attached.

        A fixed duration runs a timed test; zero runs open-ended until you
        press Stop. Either way the measurement ends the same way -- the capture
        is analysed and you are asked what to do with it.
        """
        if on:
            secs = int(self.spn_runlen.value())
            self._pending_buffer = (secs + 10) if secs else 0
            self._suppress_finish = True
            try:
                self._toggle_listen(True)
            finally:
                self._suppress_finish = False
            if self.recorder is None:
                self.btn_go.blockSignals(True)
                self.btn_go.setChecked(False)
                self.btn_go.blockSignals(False)
                return
            self.btn_go.setText("Stop")
            self._settle_pending = False
            self._settle_buf = []
            if secs and self.chk_settle.isChecked():
                self._run_t0 = None
                self._settle_pending = True
                self._settle_secs = secs
                self._settle_deadline = time.time() + 90.0
                self.prg_run.setValue(0)
                self.prg_run.setFormat("settling...")
                self.status.showMessage("Waiting for rate and amplitude to settle...")
            elif secs:
                self.recorder.clear()
                self._run_t0 = time.time()
                self._run_len = float(secs)
                self.prg_run.setValue(0)
                self.status.showMessage(f"Timed run: {secs} s")
            else:
                self._run_t0 = None
                self.prg_run.setValue(0)
                self.prg_run.setFormat("open-ended -- press Stop when done")
                self.status.showMessage("Listening, open-ended")
        else:
            self.btn_go.setText("Start")
            self._settle_pending = False
            if self._run_t0 is not None:
                self._finish_run(stopped_early=True)
            else:
                self._toggle_listen(False)

    def _settle_check(self, m):
        if not self._settle_pending or self.recorder is None:
            return
        forced = time.time() >= self._settle_deadline
        if m is not None and m.ok and m.rate == m.rate:
            self._settle_buf.append((m.rate, m.amplitude))
            self._settle_buf = self._settle_buf[-5:]
        rates = [x[0] for x in self._settle_buf]
        amps = [x[1] for x in self._settle_buf if x[1] == x[1]]
        steady = (len(rates) >= 5 and max(rates) - min(rates) < 2.0
                  and (len(amps) < 4 or max(amps) - min(amps) < 8.0))
        if steady or forced:
            self._settle_pending = False
            self.recorder.clear()
            self._run_t0 = time.time()
            self._run_len = float(self._settle_secs)
            self.prg_run.setValue(0)
            self.status.showMessage(
                (f"Settled. Timed run: {self._settle_secs} s" if steady else
                 f"Did not fully settle in 90 s -- starting the {self._settle_secs} s run anyway"),
                6000)
        else:
            self.prg_run.setFormat(f"settling... {len(self._settle_buf)}/5")

    def _set_go(self, on):
        """Move the Start button without re-entering _on_go."""
        self.btn_go.blockSignals(True)
        self.btn_go.setChecked(on)
        self.btn_go.setText("Stop" if on else "Start")
        self.btn_go.blockSignals(False)

    def _toggle_listen(self, on):
        if on:
            dev = self.cmb_dev.currentData()
            try:
                buf = max(60.0, self.spn_win.value() * 2,
                          float(getattr(self, "_pending_buffer", 0)),
                          float(self.spn_runlen.value()) + 10.0)
                sr = int(self.cmb_sr.currentText())
                if dev == "SIM":
                    self.recorder = audio.SimulatedRecorder(
                        samplerate=sr, buffer_seconds=buf,
                        bph=self.sim_bph.currentData(),
                        amplitude=self.sim_amp.value(),
                        lift_angle=self.spn_lift.value(),
                        rate_spd=self.sim_rate.value(),
                        beat_error_ms=self.sim_be.value(),
                        snr_db=self.sim_snr.value())
                else:
                    self.recorder = audio.Recorder(
                        device=dev, samplerate=sr, buffer_seconds=buf)
                self.recorder.start()
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Audio error", str(e))
                self.recorder = None
                return
            self._pending_buffer = 0
            self.worker.recorder = self.recorder
            self._rate_hist = []
            self._be_hist = []
            self._listen_t0 = time.time()
            self._rate_last_update = None
            self._cap_frames = None
            self._stream_restarts = 0
            self.c_hist.setData([], [])
            self.c_be_hist.setData([], [])
        else:
            if self._run_t0 is not None:
                self._finish_run(stopped_early=True)
            if self._tuning or self._selftune_session:
                self.worker.cancel_tune()
                self._selftune_session = False
                self._selftune_started_listen = False
                self._reset_tune_button()
            if self._noise_session:
                self._noise_session = False
                self._noise_started_listen = False
                self.btn_noise.setText("Room noise")
            self.worker.recorder = None
            if self.recorder:
                if getattr(self.recorder, "is_recording", False):
                    p = self.recorder.stop_recording()
                    if p:
                        self.status.showMessage(f"WAV saved: {os.path.basename(p)}", 8000)
                self.recorder.stop()
            self.recorder = None
            if self.act_rec.isChecked():
                self.act_rec.blockSignals(True)
                self.act_rec.setChecked(False)
                self.act_rec.blockSignals(False)
                self.act_rec.setText("Record WAV...")
            self._set_go(False)
            self.prg_run.setFormat("idle")
            self.prg_run.setValue(0)
            self.status.showMessage("Idle")
            if not self._suppress_finish and not self._closing:
                self._offer_after_listening()

    def _offer_after_listening(self):
        """Same four options after a continuous session, if there is anything to file."""
        m = self.last
        have_reading = m is not None and m.ok and m.rate == m.rate
        if not have_reading and not self.readings:
            return
        lines = []
        if have_reading:
            lines += [f"Last reading, over the {self.spn_win.value():.0f} s rolling window:",
                      "",
                      f"Rate         {m.rate:+.1f} s/day",
                      "Amplitude    " + ("n/a" if m.amplitude != m.amplitude
                                         else f"{m.amplitude:.0f} degrees"),
                      "Beat error   " + ("n/a" if m.beat_error != m.beat_error
                                         else f"{m.beat_error:.2f} ms"),
                      f"Beat rate    {m.detected_bph} bph", ""]
            lines.append(f"Template match {m.quality:.2f}, "
                         f"{3 + m.extra_peaks:.1f} noises per beat.")
            if m.nominal_bph and m.detected_bph != m.nominal_bph:
                lines.append("\nThe measured beat rate does not match the selected "
                             "caliber, so the rate figure is meaningless until that "
                             "is resolved.")
        if self.readings:
            done = sorted({r.position for r in self.readings})
            lines.append(("\n" if lines else "")
                         + f"{len(self.readings)} position(s) captured this session: "
                         + ", ".join(done))
        if have_reading:
            lines.append("\nA rolling reading is less precise than a timed run of the "
                         "same length, since the window only ever holds its last few "
                         "seconds.")
        self._offer_outcome("\n".join(lines), "Listening stopped")

    def _finish_run(self, stopped_early: bool):
        t0, length = self._run_t0, self._run_len
        self._run_t0 = None
        self.prg_run.setValue(0)
        self.prg_run.setFormat("idle")
        if t0 is None or self.recorder is None:
            self._set_go(False)
            return
        elapsed = time.time() - t0

        # Analyse the whole capture in one pass. Rate precision scales with
        # capture length, so a 60 second run resolves roughly 0.02 s/day where
        # a single 20 second window manages about 0.2.
        m = None
        try:
            data = self.recorder.read(min(elapsed, length))
            if data.size > self.recorder.samplerate:
                m = analyze(data, self.recorder.samplerate, self.worker.cfg)
        except Exception:
            m = None
        if m is None or not m.ok:
            QtWidgets.QMessageBox.warning(
                self, "Run finished",
                f"{'Stopped' if stopped_early else 'Completed'} after {elapsed:.0f} s, "
                f"but the capture could not be analysed.\n\n"
                + (m.message if m else "Not enough usable audio."))
            return

        # A run that ends should actually end. Leaving the stream open meant the
        # readouts kept moving after the summary appeared, which made it unclear
        # whether the numbers on screen were the run's or a later reading's.
        # Stopping the stream here re-enters _toggle_listen; suppress its own
        # end-of-session prompt so the user is not asked the same question twice.
        self._suppress_finish = True
        self._toggle_listen(False)
        self._set_go(False)
        self._suppress_finish = False

        self._on_result(m)
        QtWidgets.QApplication.beep()

        lines = [f"{'Stopped' if stopped_early else 'Completed'} after {elapsed:.0f} s "
                 f"({m.beats} beats analysed in a single pass).", "",
                 f"Rate         {m.rate:+.1f} s/day",
                 f"Amplitude    " + ("n/a" if m.amplitude != m.amplitude
                                     else f"{m.amplitude:.0f} degrees"),
                 f"Beat error   " + ("n/a" if m.beat_error != m.beat_error
                                     else f"{m.beat_error:.2f} ms"),
                 f"Beat rate    {m.detected_bph} bph", "",
                 f"Template match {m.quality:.2f}, {3 + m.extra_peaks:.1f} noises per beat."]
        if m.nominal_bph and m.detected_bph != m.nominal_bph:
            lines.append("\nThe measured beat rate does not match the selected caliber, "
                         "so the rate figure above is meaningless until that is resolved.")
        elif m.extra_peaks > 1.0:
            lines.append("\nMore noises per beat than an escapement makes. Amplitude is "
                         "the figure at risk -- try Self-tune pickup.")

        self._offer_outcome("\n".join(lines), "Test finished")

    def _offer_outcome(self, summary, title):
        """
        The same four choices whichever way a measurement ended.

        Stopping a continuous session is just as much the end of a measurement
        as a timer running out, and losing the numbers because you pressed the
        other button would be a poor reward for the minute you spent taking
        them.
        """
        dlg = RunFinished(summary, self, title=title)
        dlg.exec()
        choice, watch_id, capture = dlg.choice, dlg.watch_id, dlg.capture

        if capture:
            self._capture()

        if choice == "existing" and watch_id:
            self._commit_run(watch_id)
        elif choice == "new":
            ed = WatchEditor(None, self)
            if ed.exec() == QtWidgets.QDialog.Accepted:
                nw, photo = ed.result_watch()
                self.collection.add(nw)
                if photo:
                    nw.photo = self.collection.store_photo(nw.id, photo)
                    self.collection.save()
                self._refresh_watches(nw.id)
                self._commit_run(nw.id)
        elif choice == "print":
            self._build_report()
        # "discard" leaves the readouts on screen and files nothing.

    def _commit_run(self, watch_id):
        """File the current session against a watch without further prompting."""
        w = self.collection.watches.get(watch_id)
        if not w:
            return
        readings = list(self.readings)
        if not readings and self.last and self.last.ok:
            readings = [advisor.Reading(self.cmb_pos.currentText(), self.last.rate,
                                        self.last.amplitude, self.last.beat_error,
                                        self.cmb_wind.currentText())]
        if not readings:
            return
        c = self._current_caliber()
        rec = coll.record_from_readings(
            readings, c.key if c else w.caliber_key, float(self.spn_lift.value()))
        w.history.append(rec)
        self.collection.save()
        n_readings = len(rec.readings)
        self._end_session()
        self._refresh_watches(w.id)
        i = self.cmb_watch.findData(w.id)
        if i >= 0:
            self.cmb_watch.blockSignals(True)
            self.cmb_watch.setCurrentIndex(i)
            self.cmb_watch.blockSignals(False)
            self.lbl_now.setText(f"Testing:  {w.label}")
        self.status.showMessage(
            f"Run ({n_readings} position(s)) saved to {w.label} "
            f"({len(w.history)} on record). Session cleared.", 8000)

    def _toggle_record(self, on):
        if not self.recorder:
            if on:
                QtWidgets.QMessageBox.information(
                    self, "Record WAV", "Press Start first -- there is nothing to record.")
            self.act_rec.blockSignals(True)
            self.act_rec.setChecked(False)
            self.act_rec.blockSignals(False)
            return
        if on:
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Record to WAV",
                os.path.join(REPORT_DIR, f"watch_{datetime.now():%Y%m%d_%H%M%S}.wav"),
                "WAV (*.wav)")
            if not path:
                self.act_rec.blockSignals(True)
                self.act_rec.setChecked(False)
                self.act_rec.blockSignals(False)
                return
            try:
                self.recorder.start_recording(path)
            except Exception as e:
                QtWidgets.QMessageBox.warning(self, "Record WAV", f"Could not start: {e}")
                self.act_rec.blockSignals(True)
                self.act_rec.setChecked(False)
                self.act_rec.blockSignals(False)
                return
            self.act_rec.setText("Stop recording")
            self.status.showMessage(f"Recording to {os.path.basename(path)}", 6000)
        else:
            saved = self.recorder.stop_recording()
            self.act_rec.setText("Record WAV...")
            if saved:
                self.status.showMessage(f"WAV saved: {os.path.basename(saved)}", 8000)

    # --------------------------------------------------------------- events
    def _tick_ui(self):
        if self._selftune_session:
            if not self._tuning:
                buffered = self.recorder.seconds_buffered if self.recorder else 0.0
                if buffered >= 6.0:
                    self._tuning = True
                    self._selftune_deadline = time.monotonic() + 15.0
                    self.worker.tune_budget_s = 15.0
                    self.btn_tune.setText("Tuning... (click to cancel)")
                    self.worker.request_tune()
                elif not self.recorder:
                    self._selftune_finish(reason="failed")
            elif time.monotonic() >= self._selftune_deadline + 3.0:
                # Sweep wedged inside analyze() past its own deadline.
                self._selftune_finish(reason="timeout")

        if self._noise_session:
            if self.recorder is None:
                self._noise_finish(reason="failed")
            elif self.recorder.seconds_buffered >= 2.2:
                self._noise_finish(reason="done")
            elif time.monotonic() >= self._noise_deadline:
                self._noise_finish(reason="timeout")

        if self._run_t0 is not None:
            el = time.time() - self._run_t0
            frac = min(1.0, el / max(self._run_len, 1e-6))
            self.prg_run.setValue(int(frac * 100))
            self.prg_run.setFormat(f"{el:.0f} / {self._run_len:.0f} s")
            if el >= self._run_len:
                self._finish_run(stopped_early=False)
        if self.recorder:
            self.lvl.setValue(int(min(100, self.recorder.peak * 140)))
            p = self.recorder.peak
            col = "#ff5d5d" if p > 0.92 else ("#57d38c" if p > 0.02 else "#5a6472")
            self.lvl.setStyleSheet(
                f"QProgressBar{{background:#1a1f27;border:none;}}"
                f"QProgressBar::chunk{{background:{col};}}")
            if self._rate_last_update is not None and hasattr(self, "lbl_live"):
                age = time.monotonic() - self._rate_last_update
                if age > 4.0:
                    self.lbl_live.setText(f"no new reading for {age:.0f} s")
                    self.lbl_live.setStyleSheet("color:#ffb648;font-size:12px;")
            self._watch_stream()
            self._draw_mic_scope()
            if self._settle_pending:
                self._settle_check(self.last)

    def _watch_stream(self):
        """
        Recover a dead input stream on a long run.

        On some hardware the OS stops delivering audio callbacks after a while
        (USB power management, a WASAPI event timeout, a run of overflows).
        The capture buffer then stops advancing, the analysis worker keeps
        re-reading the same stale seconds, and every live plot freezes on
        identical data while the clock-driven labels carry on -- which is
        exactly the "it paused" symptom. Detect it and rebuild the stream in
        place, keeping the rate history and power-reserve log intact.
        """
        rec = self.recorder
        if rec is None or self.cmb_dev.currentData() == "SIM" or self._run_t0 is not None:
            return
        fr = getattr(rec, "frames", None)
        if fr is None:
            return
        now = time.monotonic()
        if self._cap_frames is None or fr != self._cap_frames:
            self._cap_frames = fr
            self._cap_frames_t = now
            return
        stalled = (now - self._cap_frames_t) > 3.0 or not getattr(rec, "running", True)
        if stalled and (now - self._stream_restart_t) > 5.0:
            self._stream_restart_t = now
            self._restart_stream()

    def _restart_stream(self):
        dev = self.cmb_dev.currentData()
        if dev == "SIM" or self.recorder is None:
            return
        sr = self.recorder.samplerate
        buf = self.recorder.n / sr
        old = self.recorder
        was_recording = getattr(old, "is_recording", False)
        self.worker.recorder = None
        try:
            old_path = old.stop_recording()
        except Exception:
            old_path = ""
        try:
            old.stop()
        except Exception:
            pass
        try:
            self.recorder = audio.Recorder(device=dev, samplerate=sr, buffer_seconds=buf)
            self.recorder.start()
        except Exception as e:
            self.recorder = None
            self.status.showMessage(
                f"Audio stream stopped and could not be restarted: {e}", 10000)
            return
        self.worker.recorder = self.recorder
        self._cap_frames = None
        self._stream_restarts += 1
        cont = ""
        if was_recording and old_path:
            stem, ext = os.path.splitext(old_path)
            new_path = f"{stem}_part{self._stream_restarts + 1}{ext}"
            try:
                self.recorder.start_recording(new_path)
                cont = f" WAV continues in {os.path.basename(new_path)}."
            except Exception:
                cont = " WAV recording could not continue."
        self.status.showMessage(
            f"Audio stream stalled and was restarted (#{self._stream_restarts}). "
            f"Rate history and the power-reserve log continue uninterrupted.{cont}", 8000)

    _WAVE_TITLES = {
        "Average": "Averaged beat  --  every beat in the window, stacked; markers are the "
                   "unlocking and drop noises",
        "Live beat": "Live beat  --  band-passed waveform of the most recent single beat",
        "Mic": "Microphone  --  raw signal the pickup is delivering, last 0.6 s",
    }

    def _set_wave_mode(self, mode):
        self._wave_mode = mode
        self.lbl_wave.setText(self._WAVE_TITLES.get(mode, ""))
        show_markers = mode == "Average"
        self.l_p1.setVisible(show_markers)
        self.l_p3.setVisible(show_markers)
        self.p_wave.setLabel("bottom", "time" if mode == "Mic" else "time within beat", units="ms")
        self.c_wave.setData([], [])
        if mode != "Mic" and getattr(self, "last", None) is not None and self.last.ok:
            self._render_wave(self.last)

    def _render_wave(self, m):
        if self._wave_mode == "Average":
            if m.mean_shape is not None and m.shape_fs:
                t = (np.arange(len(m.mean_shape)) - m.shape_pre) / m.shape_fs * 1000.0
                self.c_wave.setData(t, m.mean_shape)
                if m.p3_idx > m.p1_idx > 0:
                    self.l_p1.setPos((m.p1_idx - m.shape_pre) / m.shape_fs * 1000.0)
                    self.l_p3.setPos((m.p3_idx - m.shape_pre) / m.shape_fs * 1000.0)
        elif self._wave_mode == "Live beat":
            if m.beat_wave is not None and m.beat_wave_fs:
                t = (np.arange(m.beat_wave.size) - m.beat_wave_pre) / m.beat_wave_fs * 1000.0
                self.c_wave.setData(t, np.asarray(m.beat_wave, dtype=float))

    def _update_diag(self, m):
        """Live diagnostics plots: amplitude spread, beat-error trend, spectrum."""
        if m.amp_samples.size >= 5:
            a = np.asarray(m.amp_samples, dtype=float)
            counts, edges = np.histogram(a, bins=28, range=(150.0, 340.0))
            centres = (edges[:-1] + edges[1:]) / 2.0
            self.bar_amp.setOpts(x=centres, height=counts, width=(edges[1] - edges[0]) * 0.9)
            self.l_amp_med.setPos(float(np.median(a)))

        if m.beat_error == m.beat_error and self._listen_t0 is not None:
            el = time.time() - self._listen_t0
            self._be_hist.append((el, float(m.beat_error)))
            self._be_hist = self._decimate_rate_hist(self._be_hist)
            b = np.asarray(self._be_hist, dtype=float)
            self.c_be_hist.setData(b[:, 0] / 60.0, b[:, 1])

        if m.spectrum_f.size:
            self.c_spec.setData(np.asarray(m.spectrum_f, dtype=float),
                                np.asarray(m.spectrum_db, dtype=float))
            lo = max(20.0, float(self.spn_lo.value()))
            hi = max(lo + 1.0, float(self.spn_hi.value()))
            self.reg_band.setRegion([np.log10(lo), np.log10(hi)])

    def _draw_mic_scope(self):
        if self._wave_mode != "Mic" or self.recorder is None:
            return
        raw = self.recorder.read(0.6)
        if raw.size < 16:
            return
        raw = raw - float(np.mean(raw))
        t = np.arange(raw.size) / self.recorder.samplerate * 1000.0
        self.c_wave.setData(t, raw)

    def _on_result(self, m):
        self.last = m
        if self._settle_pending:
            self._settle_check(m)
        if not m.ok:
            self.status.showMessage(m.message, 4000)
            return

        good = m.quality > 0.8
        self.r_rate.set(f"{m.rate:+.1f}", "#e8eef7" if abs(m.rate) < 15 else "#ffb648")
        if m.rate_ci == m.rate_ci:
            self.r_rate.u.setText(f"seconds / day   ±{m.rate_ci:.1f} (95%)")
        else:
            self.r_rate.u.setText("seconds / day")
        self.r_amp.set("--" if m.amplitude != m.amplitude else f"{m.amplitude:.0f}",
                       "#ff5d5d" if m.amplitude > 330 else
                       ("#ffb648" if m.amplitude < 220 else "#e8eef7"))
        self.r_be.set("--" if m.beat_error != m.beat_error else f"{m.beat_error:.2f}",
                      "#ff5d5d" if m.beat_error > 1.2 else
                      ("#ffb648" if m.beat_error > 0.6 else "#57d38c"))
        mismatch = m.nominal_bph is not None and m.detected_bph != m.nominal_bph
        self.r_bph.set(str(m.detected_bph),
                       "#ff5d5d" if mismatch else ("#e8eef7" if good else "#ffb648"))

        xt, yt, xk, yk = trace_points(m, m.nominal_bph or m.detected_bph,
                                      float(self.spn_trace.value()))
        self.s_tick.setData(xt, yt)
        self.s_tock.setData(xk, yk)
        half = self.spn_trace.value() / 2
        self.p_trace.setXRange(-half, half, padding=0.02)

        if self._wave_mode != "Mic":
            self._render_wave(m)

        if m.rate == m.rate and self._listen_t0 is not None:
            el = time.time() - self._listen_t0
            self._rate_hist.append((el, float(m.rate)))
            self._rate_hist = self._decimate_rate_hist(self._rate_hist)
            a = np.asarray(self._rate_hist, dtype=float)
            self.c_hist.setData(a[:, 0] / 60.0, a[:, 1])
            self._rate_last_update = time.monotonic()

        self._update_diag(m)
        self._update_regulation(m)
        self._check_stable(m)
        self._log_reserve(m)

        if hasattr(self, "lbl_live"):
            warn = (m.nominal_bph and m.detected_bph != m.nominal_bph) or m.extra_peaks > 1.0
            self.lbl_live.setText(
                f"{3 + m.extra_peaks:.1f} noises/beat  |  match {m.quality:.2f}")
            self.lbl_live.setStyleSheet(
                f"color:{'#ffb648' if warn else '#5a6472'};font-size:12px;")

        bits = [f"{m.beats} beats", f"SNR {m.snr_db:.0f} dB", f"match {m.quality:.2f}"]
        if m.rate_ci == m.rate_ci:
            bits.append(f"rate +/-{m.rate_ci:.1f} s/d (95%)"
                        + (" -- run longer for a firm figure" if m.rate_ci > 3.0 else ""))
        if m.amplitude_spread == m.amplitude_spread:
            bits.append(f"amp scatter +/-{m.amplitude_spread/2:.0f} deg")
        if abs(m.parity_correction) > 0.15:
            bits.append(f"tick/tock anchor {m.parity_correction:+.2f} ms corrected")
        if m.extra_peaks > 0.2:
            bits.append(f"{3 + m.extra_peaks:.1f} noises/beat")
        if m.message != "OK":
            bits.append(m.message)
        self.status.showMessage("   |   ".join(bits))

    # -------------------------------------------------------- new features

    @staticmethod
    def _decimate_rate_hist(pts):
        """
        Age-tiered thinning so a multi-day run keeps scrolling without the
        point count (or the redraw cost) growing without bound: full
        resolution for the last 10 minutes, one point per 5 s out to 2 hours,
        one per 30 s beyond that. ~8k points at 48 h.
        """
        now = pts[-1][0]
        out, last_t = [], -1e9
        for t, r in pts:
            age = now - t
            step = 0.0 if age < 600.0 else (5.0 if age < 7200.0 else 30.0)
            if t - last_t >= step:
                out.append((t, r))
                last_t = t
        return out

    def _check_stable(self, m):
        """
        Auto-capture once consecutive readings agree.

        Requires agreement across a window rather than a single good-looking
        reading, because amplitude settles slowly after the watch is moved and
        an early capture records the transient rather than the position.
        """
        if not self.chk_auto.isChecked():
            return
        pos = self.cmb_pos.currentText()
        if any(r.position == pos and r.wind_state == self.cmb_wind.currentText()
               for r in self.readings):
            return
        # Never auto-record a reading the app has already flagged. A beat-rate
        # mismatch or a pickup hearing the room produces perfectly steady
        # numbers, and steady wrong numbers are exactly what this would
        # otherwise capture six times over.
        bad = (m.quality < 0.75
               or m.rate != m.rate
               or (m.nominal_bph and m.detected_bph != m.nominal_bph)
               or m.extra_peaks > 1.0
               or m.amplitude != m.amplitude)
        if bad:
            if self._stable:
                self._stable.clear()
            self.status.showMessage(
                "Auto-capture paused: " + (m.message if m.message != "OK" else
                                           "signal not trustworthy yet"), 3000)
            return
        self._stable.append((m.rate, m.amplitude, m.beat_error))
        self._stable = self._stable[-6:]
        if len(self._stable) < 6:
            self.status.showMessage(f"Settling for {pos}: {len(self._stable)}/6", 1500)
            return
        rates = [x[0] for x in self._stable]
        amps = [x[1] for x in self._stable if x[1] == x[1]]
        if max(rates) - min(rates) > 2.5:
            return
        if amps and len(amps) >= 4 and max(amps) - min(amps) > 10:
            return
        self._stable.clear()
        self._capture()
        QtWidgets.QApplication.beep()
        done = {r.position for r in self.readings}
        if len(done) >= len(advisor.POSITIONS):
            self.status.showMessage("All six positions captured.", 10000)
            QtWidgets.QMessageBox.information(
                self, "Six-position run complete",
                f"All {len(advisor.POSITIONS)} positions captured.\n\n"
                "Next: open the Advice tab for the assessment, or save the run to a "
                "watch in the Collection tab so it becomes part of that watch's "
                "history.")
        else:
            remaining = [p for p in advisor.POSITIONS if p not in done]
            self.status.showMessage(
                f"Auto-captured {pos}. {len(done)} of {len(advisor.POSITIONS)} done -- "
                f"move the watch and set the position dropdown for the next one "
                f"({', '.join(remaining)}).", 8000)

    def _toggle_reserve(self, on):
        if on:
            if not self.recorder:
                self.btn_res.setChecked(False)
                QtWidgets.QMessageBox.information(
                    self, "Power reserve", "Start listening first.")
                return
            self._res_t0 = time.time()
            self._res_next = 0.0
            self._res_done = False
            self.btn_res.setText("Stop power reserve log")
            hrs = self.spn_res_hours.value()
            QtWidgets.QMessageBox.information(
                self, "Power reserve started",
                (f"Sampling every {self.spn_res_int.value()} s"
                 + (f" until {hrs:g} hours have elapsed.\n\n" if hrs else
                    ", until you press stop.\n\n")
                 + "This is a long run, not a 20 second test. Leave the watch on the "
                   "pickup and the app listening the whole time -- if either stops, "
                   "the curve has a hole in it.\n\n"
                   "The label beside the buttons counts down to the next sample, so "
                   "you can tell it is alive between points."))
        else:
            self.btn_res.setText("Start power reserve log")
            if self._reserve and not getattr(self, "_res_done", False):
                self._reserve_finished(stopped_early=True)

    def _log_reserve(self, m):
        if not self.btn_res.isChecked() or self._res_t0 is None:
            return
        el = time.time() - self._res_t0
        target_h = float(self.spn_res_hours.value())
        # Check the target here as well as at sample time, or a 48 hour run
        # with a 5 minute interval could overshoot by five minutes before it
        # notices it is done.
        if target_h and el >= target_h * 3600.0:
            self._reserve.append((el, m.rate, m.amplitude, m.beat_error))
            self._redraw_reserve()
            self.btn_res.setChecked(False)
            self._reserve_finished(stopped_early=False)
            return
        if el < self._res_next:
            # Keep the label moving between samples. With a 5 minute interval
            # the plot is otherwise motionless for long enough to look crashed.
            self.lbl_res.setText(
                f"{len(self._reserve)} samples | {el/3600:.2f} h elapsed | "
                f"next in {max(0, self._res_next - el):.0f} s"
                + (f" | {max(0.0, target_h - el/3600):.2f} h remaining" if target_h else ""))
            return
        self._res_next = el + float(self.spn_res_int.value())
        self._reserve.append((el, m.rate, m.amplitude, m.beat_error))
        self._redraw_reserve()
        if target_h and el >= target_h * 3600.0:
            self.btn_res.setChecked(False)
            self._reserve_finished(stopped_early=False)

    def _reserve_finished(self, stopped_early: bool):
        self._res_done = True
        self.btn_res.setText("Start power reserve log")
        if not self._reserve:
            return
        a = np.array(self._reserve, dtype=float)
        hrs = a[-1, 0] / 3600.0
        amps = a[:, 2][np.isfinite(a[:, 2])]
        rates = a[:, 1][np.isfinite(a[:, 1])]
        lines = [f"{'Stopped' if stopped_early else 'Completed'} after {hrs:.2f} hours, "
                 f"{len(self._reserve)} samples."]
        if amps.size >= 2:
            lines.append(f"Amplitude {amps[0]:.0f} -> {amps[-1]:.0f} degrees "
                         f"({amps[-1]-amps[0]:+.0f}).")
            if amps[-1] < 200:
                lines.append("Below 200 degrees the watch loses its grip on rate and "
                             "positional stability, so treat the point it crossed that "
                             "line as the practical end of the reserve rather than the "
                             "moment it stopped.")
        if rates.size >= 2:
            lines.append(f"Rate {rates[0]:+.1f} -> {rates[-1]:+.1f} s/day "
                         f"({rates[-1]-rates[0]:+.1f}). A large swing here is poor "
                         f"isochronism: the hairspring is not developing evenly as "
                         f"the mainspring torque falls.")
        st = reserve_analytics(self._reserve)
        lines.extend(st.verdict)
        lines.append("\nExport CSV keeps the raw samples; the Isochronism panel below "
                     "keeps the rate-vs-amplitude plot.")
        self.lbl_res.setText(lines[0])
        self._update_iso()
        QtWidgets.QApplication.beep()
        QtWidgets.QMessageBox.information(self, "Power reserve run finished",
                                          "\n\n".join(lines))

    def _redraw_reserve(self):
        if not self._reserve:
            self.c_res_amp.setData([], [])
            self.c_res_rate.setData([], [])
            self.lbl_res.setText("Not logging.")
            self._update_iso()
            return
        a = np.array(self._reserve, dtype=float)
        hrs = a[:, 0] / 3600.0
        amp = a[:, 2]
        ok = np.isfinite(amp)
        self.c_res_amp.setData(hrs[ok], amp[ok])
        self.c_res_rate.setData(hrs, a[:, 1])
        drop = ""
        if ok.sum() >= 2:
            drop = f", amplitude {amp[ok][0]:.0f} -> {amp[ok][-1]:.0f} deg"
        self.lbl_res.setText(
            f"{len(self._reserve)} samples over {hrs[-1]:.2f} h{drop}")
        self._update_iso()

    def _update_iso(self):
        if not self._reserve:
            self.s_iso.setData([], [])
            self.c_iso_fit.setData([], [])
            self.txt_iso.setHtml("<p style='color:#8a94a4;font-family:Segoe UI'>"
                                 "Run a power-reserve log; the isochronism analysis appears "
                                 "here once amplitude has fallen far enough to see a spread.</p>")
            return
        st = reserve_analytics(self._reserve)
        a = np.array(self._reserve, dtype=float) if self._reserve else np.zeros((0, 4))
        if a.shape[0]:
            m = np.isfinite(a[:, 2]) & np.isfinite(a[:, 1])
            self.s_iso.setData(a[m, 2], a[m, 1])
        else:
            self.s_iso.setData([], [])
        if st.iso_fit:
            sl, ic = st.iso_fit
            xs = np.array([np.nanmin(a[:, 2]), np.nanmax(a[:, 2])])
            self.c_iso_fit.setData(xs, sl * xs + ic)
        else:
            self.c_iso_fit.setData([], [])
        if st.verdict:
            body = "".join(f"<p style='margin:6px 0'>{ln}</p>" for ln in st.verdict)
            self.txt_iso.setHtml(
                "<div style='color:#c8d0dc;font-family:Segoe UI;font-size:12px'>"
                f"<h4 style='color:#4da3ff;margin:0 0 4px'>Isochronism &amp; torque</h4>{body}</div>")

    def _clear_reserve(self):
        self._reserve.clear()
        self._redraw_reserve()

    def _export_reserve(self):
        if not self._reserve:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export power reserve",
            os.path.join(REPORT_DIR, f"reserve_{datetime.now():%Y%m%d_%H%M}.csv"),
            "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["elapsed_s", "elapsed_h", "rate_spd", "amplitude_deg",
                        "beat_error_ms"])
            for el, r, a, b in self._reserve:
                w.writerow([f"{el:.1f}", f"{el/3600:.4f}", f"{r:.2f}",
                            f"{a:.1f}", f"{b:.3f}"])
        self.status.showMessage(f"Wrote {path}", 5000)

    def _demag(self, which):
        m = self.last
        if m is None or not m.ok:
            self.lbl_demag.setText("No valid measurement to capture.")
            return
        if which == "before":
            self._demag_before = (m.rate, m.amplitude)
            self.lbl_demag.setText(
                f"Before: {m.rate:+.1f} s/d, amplitude {m.amplitude:.0f}. "
                f"Now run the watch through the demagnetiser and capture after.")
            return
        if self._demag_before is None:
            self.lbl_demag.setText("Capture the before reading first.")
            return
        r0, a0 = self._demag_before
        dr, da = m.rate - r0, m.amplitude - a0
        lines = [f"Rate {r0:+.1f} -> {m.rate:+.1f}  ({dr:+.1f} s/d)",
                 f"Amplitude {a0:.0f} -> {m.amplitude:.0f}  ({da:+.0f} deg)", ""]
        if dr < -8 and abs(da) < 25:
            lines.append(
                "That is the magnetism signature: a large rate drop with amplitude "
                "roughly unchanged. Magnetised hairspring coils cling together and "
                "behave like a shorter spring, which makes the watch gain. Re-measure "
                "all positions -- whatever regulation was done while magnetised is now "
                "wrong.")
        elif abs(dr) < 3:
            lines.append(
                "Almost no change, so the watch was not meaningfully magnetised. If it "
                "is running badly, the cause is mechanical: look at amplitude, the "
                "escapement, and the periodic fault scan.")
        else:
            lines.append(
                "The rate moved but not in the classic magnetism pattern. Repeat both "
                "readings to confirm -- a difference this size can also come from the "
                "mainspring unwinding between the two captures, so take them close "
                "together and at the same wind state.")
        self.lbl_demag.setText("\n".join(lines))

    def _scan_faults(self):
        m = self.last
        if m is None or not m.ok or m.index.size < 64:
            self.txt_fault.setPlainText(
                "Need a valid measurement with at least 64 beats. Set the analysis "
                "window to 30 seconds or more and let it fill.")
            return
        c = self._current_caliber()
        teeth = getattr(c, "escape_teeth", 15) if c else 15
        bph = m.nominal_bph or m.detected_bph
        rep = faults.analyze_periodicity(m.index, m.resid, bph, escape_teeth=teeth)
        self._fault_report = rep
        if rep.freqs is not None and rep.power is not None and rep.freqs.size:
            o = np.argsort(rep.freqs)
            self.c_fault.setData(rep.freqs[o], rep.power[o])
        html = [f"<div style='font-family:Segoe UI,sans-serif;color:#c8d0dc'>"]
        html.append(f"<p style='color:#8a94a4'>Escape wheel assumed to have {teeth} teeth, "
                    f"so one revolution spans {2*teeth} beats "
                    f"({2*teeth*3600.0/bph:.2f} s at {bph} bph).</p>")
        if rep.periods:
            for q in rep.periods:
                col = "#ffb648" if q.amplitude_ms > 0.15 else "#7fb2ff"
                html.append(
                    f"<p><b style='color:{col}'>{q.component} -- {q.amplitude_ms:.3f} ms "
                    f"every {q.period_seconds:.2f} s</b><br>"
                    f"<span style='color:#8a94a4'>{q.period_beats:.1f} beats, "
                    f"{q.snr:.0f}x the noise floor</span><br>"
                    f"<span style='color:#b6bfcc'>{q.detail}</span></p>")
        else:
            html.append(f"<p style='color:#b6bfcc'>"
                        f"{rep.message.replace(chr(10), '<br>')}</p>")
        html.append("</div>")
        self.txt_fault.setHtml("".join(html))

    def _build_report(self):
        c = self._current_caliber()
        if not c:
            return
        label, ok = QtWidgets.QInputDialog.getText(
            self, "Service report", "Watch / job reference (optional):")
        if not ok:
            return
        stem = (label or (self._current_caliber().label if self._current_caliber() else "watch"))
        stem = "".join(ch if ch.isalnum() or ch in "-_ " else "" for ch in stem).strip()
        stem = stem.replace(" ", "_") or "report"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save report",
            os.path.join(REPORT_DIR, f"{stem}_{datetime.now():%Y%m%d_%H%M}.html"),
            "HTML (*.html)")
        if not path:
            return
        readings = list(self.readings)
        if not readings and self.last and self.last.ok:
            readings = [advisor.Reading(self.cmb_pos.currentText(), self.last.rate,
                                        self.last.amplitude, self.last.beat_error,
                                        self.cmb_wind.currentText())]
        m = self.last
        findings = advisor.diagnose(
            c, readings,
            detected_bph=m.detected_bph if m and m.ok else None,
            quality=m.quality if m and m.ok else None,
            amplitude_spread=m.amplitude_spread if m and m.ok else None)
        gkey, gpassed, grows = self._current_grade(readings)
        out = reportmod.build(
            path, c, readings, measurement=m, findings=findings,
            fault_report=getattr(self, "_fault_report", None),
            tuning={"band_lo": self.spn_lo.value(), "band_hi": self.spn_hi.value(),
                    "env_win_ms": self.spn_env.value(),
                    "sub_threshold": self.spn_thr.value()},
            reserve_log=self._reserve, watch_label=label,
            grade={"standard": gkey, "passed": gpassed, "rows": grows} if grows else None)
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(out))
        self.status.showMessage(
            f"Wrote {out} -- print to PDF from the browser if you need one", 9000)

    # ------------------------------------------------------------- calibers
    def _fill_calibers(self, items=None):
        """
        Grouped, with non-selectable headers. The 2000-entry WatchGuy
        reference list is excluded from the default view -- it is there to be
        searched, not scrolled -- but any search reaches it.
        """
        if items is None:
            groups = {g: v for g, v in grouped().items()
                      if g != "Reference list (WatchGuy)"}
        else:
            groups = {}
            for c in items:
                groups.setdefault(c.group, []).append(c)
            for v in groups.values():
                v.sort(key=lambda c: (c.brand, c.name))

        keep = self.cmb_cal.currentData() if self.cmb_cal.count() else None
        self.cmb_cal.blockSignals(True)
        self.cmb_cal.clear()
        model = self.cmb_cal.model()
        order = ([g for g in GROUP_ORDER if g in groups]
                 + [g for g in groups if g not in GROUP_ORDER])
        first = None
        for gname in order:
            self.cmb_cal.addItem(f"\u2500\u2500 {gname} \u2500\u2500", None)
            it = model.item(self.cmb_cal.count() - 1)
            it.setEnabled(False)
            it.setForeground(QtGui.QColor("#5f6b7c"))
            for c in groups[gname]:
                bph = f"{c.bph} bph" if c.bph else "bph auto"
                self.cmb_cal.addItem(
                    f"    {c.brand} {c.name}  ({bph}, {c.lift_angle:g}\u00b0)", c.key)
                if first is None:
                    first = self.cmb_cal.count() - 1
        self.cmb_cal.blockSignals(False)
        # Keep the selected caliber if it survived the refill. Clearing the
        # search box silently reverting to some other movement is how you end
        # up measuring against the wrong lift angle without noticing.
        idx = self.cmb_cal.findData(keep) if keep else -1
        self.cmb_cal.setCurrentIndex(idx if idx >= 0 else (first if first is not None else 0))
        self._caliber_changed()

    def _filter_calibers(self, text):
        """
        One search box for two questions.

        Typing a caliber number searches movements directly. Typing a watch --
        "Rolex Submariner", "SKX007", "126610LN" -- looks the model up and
        narrows to the movements that watch actually uses, which is what you
        want when you know the watch and not the caliber.
        """
        q = text.strip()
        if not q:
            self.lbl_hint.setText("")
            self._fill_calibers(None)
            return

        from .calibers import CALIBERS
        direct = search(q)

        # Model and reference hits, mapped back to movements.
        keys, sources = [], []
        for e in catdb.search(q):
            if e.caliber_key in CALIBERS and e.caliber_key not in keys:
                keys.append(e.caliber_key)
            sources.append(f"{e.model} {e.reference}".strip())

        if keys:
            # Movements the watch uses come first; anything the plain caliber
            # search also turned up follows.
            model_hits = [CALIBERS[k] for k in keys]
            extra = [c for c in direct if c.key not in keys]
            self._fill_calibers(model_hits + extra)
            n = len(keys)
            self.lbl_hint.setText(
                f"'{q}' matched {len(sources)} watch reference(s) using {n} movement"
                f"{'s' if n != 1 else ''}. Check the years -- one model usually spans "
                f"several calibers. 'Find by watch model' shows them side by side.")
            return

        if not direct:
            self.lbl_hint.setText(
                f"Nothing matches '{q}'. Try a caliber number, a model name such as "
                f"'Submariner', or a reference such as '126610LN'.")
            self.status.showMessage(f"No match for '{text}'", 3000)
            return
        self.lbl_hint.setText("")
        self._fill_calibers(direct)

    def _current_caliber(self):
        key = self.cmb_cal.currentData()
        return CALIBERS.get(key)

    def _caliber_changed(self):
        c = self._current_caliber()
        if not c:
            return  # a group header, not a caliber
        self.spn_lift.setValue(c.lift_angle)
        i = self.cmb_bph.findData(c.bph) if c.bph else 0
        self.cmb_bph.setCurrentIndex(i if i >= 0 else 0)
        txt = advisor.REGULATOR_LABELS.get(c.regulator, c.regulator)
        src = getattr(c, "lift_source", "community")
        txt += {
            "documented": "\nLift angle: from manufacturer documentation.",
            "measured": "\nLift angle: from published bench measurements.",
            "community": "\nLift angle: community consensus -- unconfirmed.",
            "inherited": "\nLift angle: inherited from the caliber this clones -- "
                         "never actually measured on this movement.",
            "watchguy": "\nLift angle: WatchGuy reference list. No beat rate published, "
                        "so beat rate is auto-detected.",
        }.get(src, "")
        if c.notes:
            txt += f"\n{c.notes}"
        self.lbl_calinfo.setText(txt)
        self._push_cfg()

    # ------------------------------------------------------------ collection

    def _goto_page(self, i):
        self.nav.button(i).setChecked(True)
        self.stack.setCurrentIndex(i)

    def _open_escapement(self):
        dlg = getattr(self, "_esc_dlg", None)
        if dlg is None:
            dlg = self._esc_dlg = EscapementDialog(self)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _goto_watches(self):
        self._goto_page(1)

    def _measure_selected(self):
        """Jump back to the instrument with this watch already loaded."""
        w = self._current_watch()
        if w:
            i = self.cmb_watch.findData(w.id)
            if i >= 0:
                self.cmb_watch.setCurrentIndex(i)
            # Setting a combo to the index it already holds emits nothing, so
            # the caliber and lift angle would silently never load. Apply them
            # directly rather than relying on the signal.
            self._watch_combo_changed()
        self._goto_page(0)

    def _watch_combo_changed(self):
        """Selecting a watch applies its movement and lift angle."""
        wid = self.cmb_watch.currentData()
        if not wid:
            return
        w = self.collection.watches.get(wid)
        if not w:
            return
        if w.caliber_key:
            self.txt_search.blockSignals(True)
            self.txt_search.clear()
            self.txt_search.blockSignals(False)
            self.lbl_hint.setText("")
            self._fill_calibers()
            i = self.cmb_cal.findData(w.caliber_key)
            if i >= 0:
                self.cmb_cal.setCurrentIndex(i)
        if w.lift_angle:
            self.spn_lift.setValue(float(w.lift_angle))
        # Keep the Collection tab list in step, so "Save run" and the trend
        # view are always talking about the same watch.
        if wid in getattr(self, "_watch_ids", []):
            self.lst_watches.setCurrentRow(self._watch_ids.index(wid))
        self.status.showMessage(f"Testing {w.label}", 6000)
        self.lbl_now.setText(f"Testing:  {w.label}")

    def _sync_watch_combo(self, select_id=None):
        self.cmb_watch.blockSignals(True)
        self.cmb_watch.clear()
        self.cmb_watch.addItem("(not attributed)", None)
        for w in self.collection.sorted_watches():
            self.cmb_watch.addItem(w.label, w.id)
        i = self.cmb_watch.findData(select_id) if select_id else -1
        self.cmb_watch.setCurrentIndex(i if i >= 0 else 0)
        self.cmb_watch.blockSignals(False)
        if hasattr(self, "lbl_now"):
            self.lbl_now.setText(
                f"Testing:  {self.cmb_watch.currentText()}"
                if self.cmb_watch.currentData() else "No watch selected")

    def _list_thumb(self, path, px):
        """
        A fixed px-by-px thumbnail: the photo scaled to fit without distortion
        and centred on a white square, so the list reads as an even column
        whatever shape the source images are. A watch with no photo gets the
        same white square, so the text still lines up.
        """
        dpr = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0
        dpr = dpr or 1.0
        n = max(1, int(round(px * dpr)))
        canvas = QtGui.QPixmap(n, n)
        canvas.setDevicePixelRatio(dpr)
        canvas.fill(QtGui.QColor("white"))
        src = QtGui.QPixmap(path) if path else QtGui.QPixmap()
        if not src.isNull():
            scaled = src.scaled(n, n, QtCore.Qt.KeepAspectRatio,
                                QtCore.Qt.SmoothTransformation)
            p = QtGui.QPainter(canvas)
            p.drawPixmap((n - scaled.width()) // 2, (n - scaled.height()) // 2, scaled)
            p.end()
        return canvas

    def _refresh_watches(self, select_id=None):
        self.lst_watches.blockSignals(True)
        self.lst_watches.clear()
        self._watch_ids = []
        px = self.lst_watches.iconSize().height()
        for w in self.collection.sorted_watches():
            n = len(w.history)
            sub = (f"{n} run{'s' if n != 1 else ''}"
                   + (f", last {sorted(h.when for h in w.history)[-1][:10]}" if n else
                      " -- never measured"))
            it = QtWidgets.QListWidgetItem(f"{w.label}\n{sub}")
            it.setIcon(QtGui.QIcon(self._list_thumb(self.collection.photo_path(w), px)))
            self.lst_watches.addItem(it)
            self._watch_ids.append(w.id)
        self.lst_watches.blockSignals(False)
        if self._watch_ids:
            idx = self._watch_ids.index(select_id) if select_id in self._watch_ids else 0
            self.lst_watches.setCurrentRow(idx)
        else:
            self._watch_selected(-1)
        keep = select_id or self.cmb_watch.currentData() if hasattr(self, "cmb_watch") else None
        if hasattr(self, "cmb_watch"):
            self._sync_watch_combo(keep)

    def _current_watch(self):
        r = self.lst_watches.currentRow()
        if r < 0 or r >= len(getattr(self, "_watch_ids", [])):
            return None
        return self.collection.watches.get(self._watch_ids[r])

    def _watch_add(self):
        dlg = WatchEditor(None, self)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        w, photo = dlg.result_watch()
        self.collection.add(w)
        if photo:
            w.photo = self.collection.store_photo(w.id, photo)
            self.collection.save()
        self._refresh_watches(w.id)

    def _watch_edit(self):
        w = self._current_watch()
        if not w:
            return
        dlg = WatchEditor(w, self)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        w, photo = dlg.result_watch()
        if photo:
            w.photo = self.collection.store_photo(w.id, photo)
        self.collection.save()
        self._refresh_watches(w.id)

    def _watch_delete(self):
        w = self._current_watch()
        if not w:
            return
        if QtWidgets.QMessageBox.question(
                self, "Delete watch",
                f"Delete {w.label} and all {len(w.history)} recorded runs?\n\n"
                "This cannot be undone.") != QtWidgets.QMessageBox.Yes:
            return
        self.collection.remove(w.id)
        self._refresh_watches()

    def _watch_selected(self, row):
        w = self._current_watch()
        if not w:
            self.txt_wdetail.setHtml(
                "<div style='font-family:Segoe UI,sans-serif;color:#c8d0dc;padding:6px'>"
                "<h3 style='margin:0 0 8px'>No watches yet</h3>"
                "<p style='color:#b6bfcc'>A single timing run tells you how a watch is "
                "behaving today. Repeated runs tell you whether it is drifting, which is "
                "the question that decides when a service is actually due.</p>"
                "<ol style='color:#b6bfcc;line-height:1.6'>"
                "<li><b>Add</b> a watch. Pick the brand, model and reference and the rest "
                "fills itself in.</li>"
                "<li>Go to <b>Measure</b>, choose it in the Watch dropdown, and run a "
                "timed test in each position.</li>"
                "<li>Come back and press <b>Save current run to this watch</b>.</li>"
                "<li>Repeat monthly. After three runs the trend figures start to mean "
                "something.</li></ol>"
                "<p style='color:#8a94a4'>Everything is stored in "
                "<code>watches/collection.json</code> as plain text.</p></div>")
            self.lbl_wphoto.setPixmap(QtGui.QPixmap())
            self.lbl_wphoto.setText("no photo")
            self.tbl_hist.setRowCount(0)
            for c in (self.c_tr_amp, self.c_tr_rate, self.c_tr_delta):
                c.setData([], [])
            return

        p = self.collection.photo_path(w)
        if p:
            self.lbl_wphoto.setPixmap(self._list_thumb(p, 148))
        else:
            self.lbl_wphoto.setPixmap(QtGui.QPixmap())
            self.lbl_wphoto.setText("no photo")

        from .calibers import CALIBERS
        c = CALIBERS.get(w.caliber_key)
        rows = [("Brand / model", f"{w.brand} {w.model}".strip()),
                ("Reference", w.reference), ("Nickname", w.nickname),
                ("Movement", f"{c.brand} {c.name}" if c else "not set"),
                ("Lift angle", f"{w.lift_angle:g}" if w.lift_angle
                 else (f"{c.lift_angle:g} (from caliber)" if c else "")),
                ("Case", " / ".join(x for x in (w.material, w.bezel, w.crystal) if x)),
                ("Size / WR", " / ".join(x for x in (w.case_size_mm, w.water_resistance) if x)),
                ("Serial", w.serial), ("Movement serial", w.movement_serial),
                ("Produced", w.production_year),
                ("Purchased", " ".join(x for x in (
                    w.purchase_date,
                    f"for {w.purchase_price} {w.purchase_currency}" if w.purchase_price else "",
                    f"({w.purchase_condition})" if w.purchase_condition else "",
                    f"from {w.purchased_from}" if w.purchased_from else "") if x)),
                ("Target rate", f"{w.target_rate} s/day" if w.target_rate else ""),
                ("Last service", w.last_service)]
        html = ["<div style='font-family:Segoe UI,sans-serif;color:#c8d0dc;font-size:12px'>",
                f"<h3 style='margin:0 0 6px'>{w.label}</h3><table>"]
        for k, v in rows:
            if v and v.strip():
                html.append(f"<tr><td style='color:#8a94a4;padding-right:10px'>{k}</td>"
                            f"<td>{v}</td></tr>")
        html.append("</table>")
        if w.notes:
            html.append(f"<p style='color:#b6bfcc'>{w.notes}</p>")

        trends = coll.summarise(w)
        html.append("<h4 style='margin:12px 0 4px;color:#4da3ff'>TREND</h4>")
        for t in trends:
            col = "#8a94a4" if t.n < 3 else "#c8d0dc"
            val = ("" if t.n == 0 else
                   f" &nbsp;<span style='color:#8a94a4'>{t.first:.1f} &rarr; "
                   f"{t.last:.1f} {t.unit}, sd {t.stdev:.2f}</span>")
            html.append(f"<p style='margin:5px 0;color:{col}'><b>{t.metric}</b>{val}<br>"
                        f"<span style='color:#8a94a4'>{t.verdict}</span></p>")
        for n in coll.health_notes(w):
            html.append(f"<p style='margin:6px 0;color:#ffb648'>{n}</p>")
        html.append("</div>")
        self.txt_wdetail.setHtml("".join(html))

        # history table
        hist = sorted(w.history, key=lambda h: h.when)
        self.tbl_hist.setRowCount(0)
        for h in reversed(hist):
            r = self.tbl_hist.rowCount()
            self.tbl_hist.insertRow(r)
            note = h.notes + (" [post-service]" if h.service_event else "")
            for cix, v in enumerate([h.when[:16].replace("T", " "),
                                     f"{h.mean_rate:+.1f}" if h.mean_rate == h.mean_rate else "--",
                                     f"{h.delta_rate:.1f}" if h.delta_rate == h.delta_rate else "--",
                                     f"{h.max_amplitude:.0f}" if h.max_amplitude == h.max_amplitude else "--",
                                     f"{h.min_amplitude:.0f}" if h.min_amplitude == h.min_amplitude else "--",
                                     f"{h.max_beat_error:.2f}" if h.max_beat_error == h.max_beat_error else "--",
                                     note]):
                self.tbl_hist.setItem(r, cix, QtWidgets.QTableWidgetItem(str(v)))

        def series(attr):
            xs, ys = [], []
            for h in hist:
                d = h.date
                v = getattr(h, attr)
                if d is not None and v == v:
                    xs.append(d.timestamp())
                    ys.append(v)
            return xs, ys
        for curve, attr in ((self.c_tr_amp, "max_amplitude"),
                            (self.c_tr_rate, "mean_rate"),
                            (self.c_tr_delta, "delta_rate")):
            xs, ys = series(attr)
            curve.setData(xs, ys)

    def _save_test_to_watch(self):
        wid = self.cmb_watch.currentData() if hasattr(self, "cmb_watch") else None
        w = self.collection.watches.get(wid) if wid else self._current_watch()
        if not w:
            QtWidgets.QMessageBox.information(
                self, "Save run", "Select a watch first, or add one.")
            return
        readings = list(self.readings)
        if not readings and self.last and self.last.ok:
            readings = [advisor.Reading(self.cmb_pos.currentText(), self.last.rate,
                                        self.last.amplitude, self.last.beat_error,
                                        self.cmb_wind.currentText())]
        if not readings:
            QtWidgets.QMessageBox.information(
                self, "Save run", "Nothing to save. Capture at least one position.")
            return
        note, ok = QtWidgets.QInputDialog.getText(
            self, "Save run", "Note for this run (optional):")
        if not ok:
            return
        svc = QtWidgets.QMessageBox.question(
            self, "Save run", "Was this run taken immediately after a service?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No) == QtWidgets.QMessageBox.Yes
        c = self._current_caliber()
        rec = coll.record_from_readings(
            readings, c.key if c else w.caliber_key,
            float(self.spn_lift.value()), notes=note, service_event=svc)
        w.history.append(rec)
        self.collection.save()
        self._end_session()
        self._refresh_watches(w.id)
        QtWidgets.QMessageBox.information(
            self, "Run saved",
            f"Saved to {w.label}, and the current session was cleared.\n\nThat watch "
            f"now has {len(w.history)} recorded run(s). Three or more spread over "
            f"months are needed before the trend figures mean anything.")

    def _watch_report(self):
        w = self._current_watch()
        if not w:
            QtWidgets.QMessageBox.information(
                self, "Watch report", "Select a watch first.")
            return
        from .calibers import CALIBERS
        stem = "".join(ch if ch.isalnum() or ch in "-_ " else "" for ch in w.label)
        stem = stem.strip().replace(" ", "_") or "watch"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save watch report",
            os.path.join(REPORT_DIR, f"{stem}_{datetime.now():%Y%m%d}.html"),
            "HTML (*.html)")
        if not path:
            return
        out = reportmod.build_watch_report(
            path, w, caliber=CALIBERS.get(w.caliber_key),
            trends=coll.summarise(w), notes=coll.health_notes(w),
            photo_path=self.collection.photo_path(w))
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(out))
        self.status.showMessage(
            f"Wrote {out} -- use the browser's Print to make a PDF", 9000)

    def _hist_delete(self):
        w = self._current_watch()
        r = self.tbl_hist.currentRow()
        if not w or r < 0:
            return
        hist = sorted(w.history, key=lambda h: h.when, reverse=True)
        target = hist[r]
        if QtWidgets.QMessageBox.question(
                self, "Delete run",
                f"Delete the run from {target.when[:16]}?") != QtWidgets.QMessageBox.Yes:
            return
        w.history = [h for h in w.history if h is not target]
        self.collection.save()
        self._refresh_watches(w.id)

    def _hist_export(self):
        w = self._current_watch()
        if not w or not w.history:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export history",
            os.path.join(REPORT_DIR, f"history_{w.id}.csv"), "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as fh:
            wr = csv.writer(fh)
            wr.writerow(["watch", w.label, "reference", w.reference,
                         "caliber", w.caliber_key])
            wr.writerow(["when", "position", "wind", "rate_spd", "amplitude_deg",
                         "beat_error_ms", "post_service", "notes"])
            for h in sorted(w.history, key=lambda h: h.when):
                for rd in h.readings:
                    wr.writerow([h.when, rd.get("position"), rd.get("wind"),
                                 f"{rd.get('rate', float('nan')):.2f}",
                                 f"{rd.get('amplitude', float('nan')):.1f}",
                                 f"{rd.get('beat_error', float('nan')):.3f}",
                                 "yes" if h.service_event else "", h.notes])
        self.status.showMessage(f"Wrote {path}", 5000)

    def _find_model(self):
        try:
            dlg = ModelFinder(self)
        except Exception:
            import traceback
            QtWidgets.QMessageBox.critical(
                self, "Could not open the model finder", traceback.format_exc(limit=4))
            return
        if dlg.exec() != QtWidgets.QDialog.Accepted or not dlg.chosen:
            return
        m = dlg.chosen
        # Clear any active caliber filter first, or findData may not see it.
        self.txt_search.blockSignals(True)
        self.txt_search.clear()
        self.txt_search.blockSignals(False)
        self._fill_calibers()
        i = self.cmb_cal.findData(m.caliber_key)
        if i < 0:
            self._fill_calibers(search(m.caliber_key))
            i = self.cmb_cal.findData(m.caliber_key)
        if i >= 0:
            self.cmb_cal.setCurrentIndex(i)
            extra = ("" if m.confidence == "sure"
                     else "  (mapping unconfirmed -- verify against the movement)")
            self.status.showMessage(
                f"{m.label} ({m.years}) -> {self.cmb_cal.currentText().strip()}{extra}",
                10000)

    def _load_user_db(self):
        p = os.path.join(os.path.expanduser("~"), ".watchgrapher_calibers.csv")
        n = load_user_calibers(p)
        if n:
            self._fill_calibers()
            self.status.showMessage(f"Loaded {n} calibers from {p}", 5000)

    def _load_csv_dialog(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Caliber CSV", "", "CSV (*.csv)")
        if path:
            n = load_user_calibers(path)
            self._fill_calibers()
            QtWidgets.QMessageBox.information(self, "Calibers", f"Loaded {n} entries.")

    # -------------------------------------------------------------- capture
    def _capture(self):
        m = self.last
        if m is None or not m.ok:
            QtWidgets.QMessageBox.warning(self, "Nothing to capture",
                                          "No valid measurement yet.")
            return
        r = advisor.Reading(position=self.cmb_pos.currentText(),
                            rate=m.rate, amplitude=m.amplitude,
                            beat_error=m.beat_error,
                            wind_state=self.cmb_wind.currentText())
        self.readings.append(r)
        row = self.tbl.rowCount()
        self.tbl.insertRow(row)
        vals = [r.position, r.wind_state, f"{r.rate:+.1f}",
                "--" if r.amplitude != r.amplitude else f"{r.amplitude:.0f}",
                "--" if r.beat_error != r.beat_error else f"{r.beat_error:.2f}",
                datetime.now().strftime("%H:%M:%S")]
        for c, v in enumerate(vals):
            self.tbl.setItem(row, c, QtWidgets.QTableWidgetItem(v))
        self._update_delta()
        # The position dropdown is left where the user set it -- they choose
        # when to move the watch and which position to record it as.

    def _update_delta(self):
        rates = [r.rate for r in self.readings if r.rate == r.rate]
        if len(rates) >= 2:
            self.lbl_delta.setText(
                f"delta {max(rates)-min(rates):.1f} s/d    mean {sum(rates)/len(rates):+.1f} s/d")
        else:
            self.lbl_delta.setText("")
        self._redraw_positions()

    def _redraw_positions(self):
        for t in self._pos_labels:
            self.p_pos.removeItem(t)
        self._pos_labels = []
        rows = [r for r in self.readings if r.rate == r.rate]
        if not rows:
            self.bar_pos.setOpts(x=[], height=[])
            self.l_pos_mean.setValue(0)
            self.p_pos.getAxis("bottom").setTicks([[]])
            return
        # Keep the canonical position order; unknown labels go on the end.
        order = {p: i for i, p in enumerate(advisor.POSITIONS)}
        rows.sort(key=lambda r: order.get(r.position, 99))
        xs = list(range(len(rows)))
        heights = [r.rate for r in rows]
        mean = sum(heights) / len(heights)
        cols = ["#57d38c" if abs(h - mean) < 5 else ("#ffb648" if abs(h - mean) < 12 else "#ff5d5d")
                for h in heights]
        self.bar_pos.setOpts(x=xs, height=heights, width=0.6, brushes=cols)
        self.l_pos_mean.setValue(mean)
        self.p_pos.getAxis("bottom").setTicks(
            [[(i, r.position.replace("Crown ", "C").replace("Dial ", "D")) for i, r in zip(xs, rows)]])
        for i, r in zip(xs, rows):
            amp = "" if r.amplitude != r.amplitude else f"{r.amplitude:.0f}°"
            t = pg.TextItem(f"{r.rate:+.1f}\n{amp}", color="#8a94a4", anchor=(0.5, 0))
            t.setPos(i, max(heights) if r.rate >= 0 else min(heights))
            self.p_pos.addItem(t)
            self._pos_labels.append(t)

    def _del_row(self):
        rows = sorted({i.row() for i in self.tbl.selectedIndexes()}, reverse=True)
        for r in rows:
            self.tbl.removeRow(r)
            if r < len(self.readings):
                del self.readings[r]
        self._update_delta()

    def _clear_rows(self):
        self.tbl.setRowCount(0)
        self.readings.clear()
        self._update_delta()

    def _end_session(self):
        """
        Close the current measuring session.

        `self.readings` accumulates captured positions and is shared by every
        save path. Once a run has been filed against a watch that session is
        finished -- carrying its readings into the next watch is how the same
        data ends up saved against several watches.
        """
        self.readings.clear()
        self._stable.clear()
        if hasattr(self, "tbl"):
            self.tbl.setRowCount(0)
        self._update_delta()

    def _export_csv(self):
        if not self.readings:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export", f"timing_{datetime.now():%Y%m%d_%H%M}.csv", "CSV (*.csv)")
        if not path:
            return
        c = self._current_caliber()
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["caliber", c.label if c else "", "bph", c.bph if c else "",
                        "lift_angle", self.spn_lift.value()])
            w.writerow(["position", "wind", "rate_spd", "amplitude_deg", "beat_error_ms"])
            for r in self.readings:
                w.writerow([r.position, r.wind_state, f"{r.rate:.2f}",
                            f"{r.amplitude:.1f}", f"{r.beat_error:.3f}"])
        self.status.showMessage(f"Wrote {path}", 5000)

    # --------------------------------------------------------------- advice
    def _backup_collection(self):
        import zipfile
        self.collection.save()
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Back up collection",
            os.path.join(REPORT_DIR, f"watch_collection_{datetime.now():%Y%m%d}.zip"),
            "Zip archive (*.zip)")
        if not path:
            return
        root = self.collection.root
        out_abs = os.path.abspath(path)
        try:
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
                for dirpath, _dirs, files in os.walk(root):
                    for fn in files:
                        full = os.path.join(dirpath, fn)
                        if os.path.abspath(full) == out_abs or ".pre-restore-" in fn \
                                or fn.endswith(".tmp"):
                            continue
                        z.write(full, os.path.relpath(full, root))
        except OSError as e:
            QtWidgets.QMessageBox.warning(self, "Back up collection", str(e))
            return
        n = len(self.collection.watches)
        self.status.showMessage(f"Backed up {n} watches to {os.path.basename(path)}", 6000)

    def _restore_collection(self):
        import zipfile
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Restore collection", REPORT_DIR, "Zip archive (*.zip)")
        if not path:
            return
        if QtWidgets.QMessageBox.warning(
                self, "Restore collection",
                "This replaces your current collection and photos with the contents of "
                "the archive. A backup of the current collection.json is kept alongside "
                "it. Continue?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No) != QtWidgets.QMessageBox.Yes:
            return
        root = self.collection.root
        try:
            if os.path.exists(self.collection.path):
                os.replace(self.collection.path,
                           self.collection.path + f".pre-restore-{datetime.now():%Y%m%d%H%M%S}")
            with zipfile.ZipFile(path) as z:
                bad = next((n for n in z.namelist() if n.startswith(("/", "..")) or ":" in n), None)
                if bad:
                    raise ValueError(f"archive contains an unsafe path: {bad}")
                z.extractall(root)
        except (OSError, ValueError, zipfile.BadZipFile) as e:
            QtWidgets.QMessageBox.warning(self, "Restore collection", str(e))
            return
        self.collection.load()
        self._refresh_watches()
        QtWidgets.QMessageBox.information(
            self, "Restore collection",
            f"Restored {len(self.collection.watches)} watches.")

    def _regulation_target(self):
        w = self._current_watch() if hasattr(self, "_current_watch") else None
        if w and str(getattr(w, "target_rate", "")).strip():
            try:
                return float(str(w.target_rate).replace("+", "").strip())
            except ValueError:
                pass
        return 0.0

    def _update_regulation(self, m):
        if not hasattr(self, "lbl_regassist"):
            return
        c = self._current_caliber()
        if c is None or not m.ok or m.rate != m.rate:
            return
        if m.nominal_bph and m.detected_bph != m.nominal_bph:
            self.lbl_regassist.setText(
                "Regulation assistant: beat rate does not match the caliber, "
                "so the rate figure is not usable yet.")
            return
        target = self._regulation_target()
        err = m.rate - target
        ci = f" ±{m.rate_ci:.1f}" if m.rate_ci == m.rate_ci else ""
        head = (f"<b>Now {m.rate:+.1f}{ci} s/day</b>, target {target:+.1f}. ")
        if abs(err) <= max(2.0, (m.rate_ci if m.rate_ci == m.rate_ci else 0.0)):
            self.lbl_regassist.setText(
                head + "Within tolerance of the target -- leave the regulator "
                "alone and confirm across positions.")
            return
        direction = "slower" if err > 0 else "faster"
        instr = advisor.rate_adjust_instructions(c, direction)
        extra = ""
        if self.spn_before.value() or self.spn_after.value():
            extra = ("<br><br><i>From your Tools-tab calibration:</i> "
                     + advisor.regulator_sensitivity(self.spn_before.value(),
                                                     self.spn_after.value()))
        self.lbl_regassist.setText(
            f"{head}Run <b>{direction}</b> by <b>{abs(err):.1f} s/day</b>.<br><br>{instr}{extra}")

    def _current_grade(self, readings):
        key = self.cmb_standard.currentText()
        spec = advisor.STANDARDS.get(key)
        if not spec:
            return key, False, []
        passed, rows = advisor.grade(readings, spec)
        return key, passed, rows

    def _grade_html(self, readings):
        key, passed, rows = self._current_grade(readings)
        if not rows:
            return f"<p style='color:#8a94a4'>Grade vs {key}: capture at least one position.</p>"
        head = ("#57d38c", "PASS") if passed else ("#ff5d5d", "OUTSIDE")
        out = [f"<p style='margin:2px 0'><b style='color:{head[0]}'>{head[1]}</b> "
               f"&nbsp;<span style='color:#8a94a4'>vs {key} &mdash; indicative, "
               f"not a lab test</span></p>",
               "<table style='border-collapse:collapse'>"]
        for r in rows:
            col = "#57d38c" if r.ok else "#ff5d5d"
            out.append(
                f"<tr><td style='color:#8a94a4;padding:2px 12px 2px 0'>{r.name}</td>"
                f"<td style='padding:2px 12px 2px 0'>{r.value}</td>"
                f"<td style='color:#8a94a4;padding:2px 12px 2px 0'>{r.limit}</td>"
                f"<td style='color:{col}'>{'ok' if r.ok else 'no'}</td></tr>")
        out.append("</table>")
        return "".join(out)

    def _advise(self):
        c = self._current_caliber()
        if not c:
            return
        readings = list(self.readings)
        if not readings and self.last and self.last.ok:
            readings = [advisor.Reading(self.cmb_pos.currentText(), self.last.rate,
                                        self.last.amplitude, self.last.beat_error,
                                        self.cmb_wind.currentText())]
        m = self.last
        findings = advisor.diagnose(
            c, readings,
            detected_bph=m.detected_bph if m and m.ok else None,
            quality=m.quality if m and m.ok else None,
            amplitude_spread=m.amplitude_spread if m and m.ok else None)

        html = [f"<div style='font-family:Segoe UI,sans-serif;color:#c8d0dc'>",
                self._grade_html(readings), "<hr>",
                f"<pre style='color:#8a94a4'>{advisor.workflow_summary(c)}</pre><hr>"]
        for f in findings:
            col = SEV_COLOR.get(f.severity, "#c8d0dc")
            html.append(
                f"<p style='margin:14px 0 4px 0'><b style='color:{col}'>"
                f"[{f.severity.upper()}] {f.title}</b><br>"
                f"<span style='color:#b6bfcc'>{f.detail}</span></p>")
        html.append("</div>")
        self.txt_advice.setHtml("".join(html))

    def _calibrate(self):
        self.lbl_cal.setText(advisor.regulator_sensitivity(
            self.spn_before.value(), self.spn_after.value()))

    def _solve_lift(self):
        m = self.last
        if not m or not m.ok or m.dt_mean != m.dt_mean:
            self.lbl_solve.setText("No usable impulse measurement right now.")
            return
        la = solve_lift_angle(m.dt_mean, m.nominal_bph or m.detected_bph,
                              self.spn_known.value())
        self.lbl_solve.setText(
            f"Implied lift angle: {la:.1f} degrees. If that is within a degree or two of a "
            f"plausible value (most are 38-56), set it above and re-measure. Sanity-check it "
            f"on a second run before trusting it.")

    # ----------------------------------------------------------------- file
    def _analyze_file(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "WAV file", "", "WAV (*.wav)")
        if not path:
            return
        try:
            data, fs = audio.load_wav(path)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Read error", str(e))
            return
        if len(data) / fs > self.spn_win.value() + 4:
            dlg = WavScrubber(data, fs, self.worker.cfg, self)
            if dlg.exec() == QtWidgets.QDialog.Accepted and dlg.result_m is not None:
                self._on_result(dlg.result_m)
                self.status.showMessage(
                    f"{os.path.basename(path)}: window analysed, {dlg.result_m.message}", 8000)
            return
        n = int(self.spn_win.value() * fs)
        m = analyze(data[:n] if len(data) > n else data, fs, self.worker.cfg)
        self._on_result(m)
        self.status.showMessage(f"{os.path.basename(path)}: {m.message}", 8000)

    def closeEvent(self, e):
        self._closing = True
        try:
            self.worker.stop()
            self.thread.quit()
            self.thread.wait(1500)
        except Exception:
            pass
        if self.recorder:
            self.recorder.stop()
        super().closeEvent(e)


def main():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setStyle("Fusion")
    pal = QtGui.QPalette()
    pal.setColor(QtGui.QPalette.Window, QtGui.QColor("#12151a"))
    pal.setColor(QtGui.QPalette.Base, QtGui.QColor("#1a1f27"))
    pal.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor("#20262f"))
    pal.setColor(QtGui.QPalette.Text, QtGui.QColor("#c8d0dc"))
    pal.setColor(QtGui.QPalette.WindowText, QtGui.QColor("#c8d0dc"))
    pal.setColor(QtGui.QPalette.Button, QtGui.QColor("#232a34"))
    pal.setColor(QtGui.QPalette.ButtonText, QtGui.QColor("#c8d0dc"))
    pal.setColor(QtGui.QPalette.Highlight, QtGui.QColor(ACCENT))
    pal.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor("#08101c"))
    app.setPalette(pal)
    w = MainWindow()
    w.show()
    app.exec()
