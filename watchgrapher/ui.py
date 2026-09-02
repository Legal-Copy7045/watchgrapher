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
                       solve_lift_angle, tuning_score, reserve_analytics,
                       allan_deviation)
from .calibers import (CALIBERS, GROUP_ORDER, STANDARD_BPH, grouped,
                       load_user_calibers, search)

pg.setConfigOptions(antialias=True, background="#12151a", foreground="#c8d0dc")

# Everything the app writes lives beside the package, in named folders, so a
# report or a collection is somewhere you can find it later rather than
# wherever the last file dialog happened to be pointing. Set WATCHGRAPHER_HOME
# to keep the data (collection, reports, settings) somewhere else -- and so a
# test run can never touch a real collection.
APP_DIR = (os.environ.get("WATCHGRAPHER_HOME")
           or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
APP_DIR = os.path.abspath(APP_DIR)
REPORT_DIR = os.path.join(APP_DIR, "reports")
COLLECTION_DIR = os.path.join(APP_DIR, "watches")
for _d in (REPORT_DIR, COLLECTION_DIR):
    os.makedirs(_d, exist_ok=True)

from . import theme as _T

BG, BG2, PANEL, PANEL2, LINE = (_T.get(k) for k in ("BG", "BG2", "PANEL", "PANEL2", "LINE"))
INK_HI, INK, MUT, MUT2 = (_T.get(k) for k in ("INK_HI", "INK", "MUT", "MUT2"))
ACCENT, ACCENT2, ON_ACCENT = _T.get("ACCENT"), _T.get("ACCENT2"), _T.get("ON_ACCENT")
GOOD, WARN, WARN2, BAD = (_T.get(k) for k in ("GOOD", "WARN", "WARN2", "BAD"))
TICK_C, TOCK_C = _T.get("TICK"), _T.get("TOCK")
SEV_COLOR = {"critical": BAD, "warn": WARN, "info": ACCENT2, "good": GOOD}


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
        pm = _schematic_pixmap(self.watch, 148) if getattr(self, "watch", None) else None
        if pm is not None and not pm.isNull():
            self.lbl_photo.setPixmap(pm)
            self.lbl_photo.setToolTip("Schematic from the reference details -- "
                                      "add your own photo above")
        else:
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


class ServiceEditor(QtWidgets.QDialog):
    """Add or edit one service-history entry, with scanned paperwork attached."""

    DOC_FILTER = "Documents & images (*.pdf *.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff)"

    def __init__(self, record=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Service entry")
        self.setMinimumWidth(460)
        rec = record or coll.ServiceRecord(when=datetime.now().strftime("%Y-%m-%d"))
        self._existing_docs = list(rec.documents)     # already-stored filenames
        self._new_docs = []                           # source paths to copy on accept
        self._removed = []                            # stored filenames to forget

        form = QtWidgets.QFormLayout(self)
        self.e_when = QtWidgets.QDateEdit()
        self.e_when.setCalendarPopup(True)
        self.e_when.setDisplayFormat("yyyy-MM-dd")
        try:
            self.e_when.setDate(QtCore.QDate.fromString(rec.when, "yyyy-MM-dd"))
        except Exception:
            self.e_when.setDate(QtCore.QDate.currentDate())
        self.cmb_kind = QtWidgets.QComboBox()
        self.cmb_kind.addItems(coll.SERVICE_KINDS)
        if rec.kind:
            self.cmb_kind.setCurrentText(rec.kind)
        self.e_by = QtWidgets.QLineEdit(rec.performed_by)
        self.e_loc = QtWidgets.QLineEdit(rec.location)
        cost_row = QtWidgets.QWidget()
        cl = QtWidgets.QHBoxLayout(cost_row)
        cl.setContentsMargins(0, 0, 0, 0)
        self.e_cost = QtWidgets.QLineEdit(rec.cost)
        self.e_cost.setPlaceholderText("amount")
        self.cmb_cur = QtWidgets.QComboBox()
        self.cmb_cur.addItems(coll.CURRENCIES)
        self.cmb_cur.setCurrentText(rec.currency or "GBP")
        cl.addWidget(self.e_cost, 2)
        cl.addWidget(self.cmb_cur, 1)
        self.e_warr = QtWidgets.QLineEdit(rec.warranty_months)
        self.e_warr.setPlaceholderText("months")
        self.e_notes = QtWidgets.QPlainTextEdit(rec.notes)
        self.e_notes.setMaximumHeight(80)

        self.cmb_wr = QtWidgets.QComboBox()
        self.cmb_wr.addItems(["", "Pass", "Fail"])
        self.cmb_wr.setCurrentText(rec.wr_result)
        self.e_wr_rating = QtWidgets.QLineEdit(rec.wr_rating)
        self.e_wr_rating.setPlaceholderText("e.g. 100 m / 10 ATM")
        self.cmb_wr_method = QtWidgets.QComboBox()
        self.cmb_wr_method.addItems(
            ["", "Dry (air pressure)", "Wet", "Condensation", "Vacuum"])
        self.cmb_wr_method.setCurrentText(rec.wr_method)
        self.e_wr_pressure = QtWidgets.QLineEdit(rec.wr_pressure)
        self.e_wr_pressure.setPlaceholderText("e.g. 6 bar")
        wr_box = QtWidgets.QGroupBox("Water resistance test")
        wf = QtWidgets.QFormLayout(wr_box)
        wf.addRow("Result", self.cmb_wr)
        wf.addRow("Rating held", self.e_wr_rating)
        wf.addRow("Method", self.cmb_wr_method)
        wf.addRow("Test pressure", self.e_wr_pressure)

        form.addRow("Date", self.e_when)
        form.addRow("Type", self.cmb_kind)
        form.addRow("Performed by", self.e_by)
        form.addRow("Location", self.e_loc)
        form.addRow("Cost", cost_row)
        form.addRow("Warranty", self.e_warr)
        form.addRow(wr_box)
        form.addRow("Notes", self.e_notes)

        self.lst_docs = QtWidgets.QListWidget()
        self.lst_docs.setMaximumHeight(110)
        self._reload_doc_list()
        drow = QtWidgets.QHBoxLayout()
        b_add = QtWidgets.QPushButton("Attach file...")
        b_rm = QtWidgets.QPushButton("Remove")
        b_add.clicked.connect(self._attach)
        b_rm.clicked.connect(self._remove_doc)
        drow.addWidget(b_add)
        drow.addWidget(b_rm)
        drow.addStretch(1)
        form.addRow("Documents", self.lst_docs)
        dw = QtWidgets.QWidget()
        dw.setLayout(drow)
        form.addRow("", dw)

        bb = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def _reload_doc_list(self):
        self.lst_docs.clear()
        for name in self._existing_docs:
            self.lst_docs.addItem(f"[stored] {name}")
        for src in self._new_docs:
            self.lst_docs.addItem(f"[new] {os.path.basename(src)}")

    def _attach(self):
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "Attach service document", "", self.DOC_FILTER)
        for p in paths:
            self._new_docs.append(p)
        self._reload_doc_list()

    def _remove_doc(self):
        r = self.lst_docs.currentRow()
        if r < 0:
            return
        if r < len(self._existing_docs):
            self._removed.append(self._existing_docs.pop(r))
        else:
            self._new_docs.pop(r - len(self._existing_docs))
        self._reload_doc_list()

    def result(self):
        """(ServiceRecord without new docs applied, list of new source paths, removed names)."""
        rec = coll.ServiceRecord(
            when=self.e_when.date().toString("yyyy-MM-dd"),
            kind=self.cmb_kind.currentText(),
            performed_by=self.e_by.text().strip(),
            location=self.e_loc.text().strip(),
            cost=self.e_cost.text().strip(),
            currency=self.cmb_cur.currentText(),
            warranty_months=self.e_warr.text().strip(),
            notes=self.e_notes.toPlainText().strip(),
            documents=list(self._existing_docs),
            wr_result=self.cmb_wr.currentText(),
            wr_rating=self.e_wr_rating.text().strip(),
            wr_method=self.cmb_wr_method.currentText(),
            wr_pressure=self.e_wr_pressure.text().strip())
        return rec, list(self._new_docs), list(self._removed)


class MicCalDialog(QtWidgets.QDialog):
    """Play a sine sweep, capture it, plot where the pickup rolls off."""

    done = QtCore.Signal(object, object, str)   # freqs, db, error

    def __init__(self, samplerate, in_device, parent=None):
        super().__init__(parent)
        self.sr = int(samplerate)
        self.in_device = in_device
        self.curve = None
        self.setWindowTitle("Microphone response calibration")
        self.setMinimumSize(560, 460)
        v = QtWidgets.QVBoxLayout(self)
        v.addWidget(QtWidgets.QLabel(
            "Plays a 4 s sine sweep (80 Hz - 16 kHz) out the default output and "
            "measures what the pickup returns. This is the whole chain -- speaker, "
            "room, mic -- so read it as 'where is my pickup deaf', not an absolute "
            "calibration. Set the output volume to a comfortable, non-distorting level."))
        self.p = pg.PlotWidget(title="Record-chain magnitude response")
        self.p.setLabel("bottom", "frequency", units="Hz")
        self.p.setLabel("left", "level", units="dB")
        self.p.setLogMode(x=True, y=False)
        self.p.showGrid(x=True, y=True, alpha=0.25)
        self.c = self.p.plot(pen=pg.mkPen("#4da3ff", width=2))
        v.addWidget(self.p, 1)
        self.lbl = QtWidgets.QLabel("")
        self.lbl.setWordWrap(True)
        v.addWidget(self.lbl)
        bb = QtWidgets.QDialogButtonBox()
        self.b_run = bb.addButton("Run sweep", QtWidgets.QDialogButtonBox.ActionRole)
        self.b_save = bb.addButton("Save to pickup profile", QtWidgets.QDialogButtonBox.AcceptRole)
        bb.addButton(QtWidgets.QDialogButtonBox.Close)
        self.b_save.setEnabled(False)
        self.b_run.clicked.connect(self._run)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.done.connect(self._show)

    def _run(self):
        self.b_run.setEnabled(False)
        self.lbl.setText("Playing sweep...")
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        try:
            ref = audio._sweep_and_reference(4.0, self.sr, 80.0, 16000.0)
            play = (ref * 0.5).reshape(-1, 1)
            dev = None if self.in_device in (None, "SIM") else self.in_device
            rec = audio.sd.playrec(play, samplerate=self.sr, channels=1,
                                   device=(None, dev))
            audio.sd.wait()
            rec = np.asarray(rec).reshape(-1)
            f, db = audio.mic_response_from_capture(rec, ref, self.sr)
            self.done.emit(f, db, "")
        except Exception as e:                       # pragma: no cover - hardware
            self.done.emit(None, None, f"{type(e).__name__}: {e}")

    def _show(self, f, db, err):
        self.b_run.setEnabled(True)
        if err or f is None or len(f) < 4:
            self.lbl.setText(err or "Could not measure a response -- check the output "
                             "is audible and the pickup is picking it up.")
            return
        self.c.setData(f, db)
        self.curve = (list(f), list(db))
        self.b_save.setEnabled(True)
        lo = f[db > -3][0] if np.any(db > -3) else f[0]
        hi_mask = db > -6
        hi = f[hi_mask][-1] if np.any(hi_mask) else f[-1]
        self.lbl.setText(
            f"Roughly flat within 6 dB from {lo:.0f} Hz to {hi:.0f} Hz. Escapement "
            f"energy sits around 2-12 kHz -- if the pickup is down more than 10 dB up "
            f"there, tighten the filter band to where it still hears well.")


class CrossCheckDialog(QtWidgets.QDialog):
    """Compare the acoustic reading against a hardware timegrapher's numbers."""

    def __init__(self, measurement, caliber, parent=None):
        super().__init__(parent)
        self.m = measurement
        self.cal = caliber
        self.setWindowTitle("Cross-check against a hardware timegrapher")
        self.setMinimumWidth(460)
        v = QtWidgets.QVBoxLayout(self)

        a_rate = a_amp = a_be = float("nan")
        if measurement is not None and measurement.ok:
            a_rate, a_amp, a_be = measurement.rate, measurement.amplitude, measurement.beat_error
        head = ("This app reads: "
                + (f"{a_rate:+.1f} s/d, {a_amp:.0f} deg, {a_be:.2f} ms"
                   if a_rate == a_rate else "no current reading -- enter both sides by hand")
                + ".")
        lh = QtWidgets.QLabel(head)
        lh.setWordWrap(True)
        v.addWidget(lh)

        form = QtWidgets.QFormLayout()
        self.e_app_rate = QtWidgets.QDoubleSpinBox(); self.e_app_rate.setRange(-900, 900)
        self.e_app_amp = QtWidgets.QDoubleSpinBox(); self.e_app_amp.setRange(0, 360)
        self.e_app_be = QtWidgets.QDoubleSpinBox(); self.e_app_be.setRange(0, 20); self.e_app_be.setDecimals(2)
        if a_rate == a_rate:
            self.e_app_rate.setValue(a_rate); self.e_app_amp.setValue(a_amp)
            self.e_app_be.setValue(a_be if a_be == a_be else 0.0)
        self.e_hw_rate = QtWidgets.QDoubleSpinBox(); self.e_hw_rate.setRange(-900, 900)
        self.e_hw_amp = QtWidgets.QDoubleSpinBox(); self.e_hw_amp.setRange(0, 360)
        self.e_hw_be = QtWidgets.QDoubleSpinBox(); self.e_hw_be.setRange(0, 20); self.e_hw_be.setDecimals(2)
        self.e_machine = QtWidgets.QLineEdit()
        self.e_machine.setPlaceholderText("Witschi Chronoscope / Weishi 1000 / ...")
        form.addRow("This app -- rate s/d", self.e_app_rate)
        form.addRow("This app -- amplitude", self.e_app_amp)
        form.addRow("This app -- beat error", self.e_app_be)
        form.addRow("Machine -- rate s/d", self.e_hw_rate)
        form.addRow("Machine -- amplitude", self.e_hw_amp)
        form.addRow("Machine -- beat error", self.e_hw_be)
        form.addRow("Machine model", self.e_machine)
        v.addLayout(form)

        self.txt = QtWidgets.QTextBrowser()
        v.addWidget(self.txt, 1)

        bb = QtWidgets.QDialogButtonBox()
        b_cmp = bb.addButton("Compare", QtWidgets.QDialogButtonBox.ActionRole)
        b_log = bb.addButton("Compare && log", QtWidgets.QDialogButtonBox.AcceptRole)
        bb.addButton(QtWidgets.QDialogButtonBox.Close)
        b_cmp.clicked.connect(lambda: self._compare(False))
        b_log.clicked.connect(lambda: self._compare(True))
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _compare(self, log):
        dr = self.e_app_rate.value() - self.e_hw_rate.value()
        da = self.e_app_amp.value() - self.e_hw_amp.value()
        db = self.e_app_be.value() - self.e_hw_be.value()
        lines = [f"<b>Rate</b>: app - machine = {dr:+.1f} s/d",
                 f"<b>Amplitude</b>: {da:+.0f} deg",
                 f"<b>Beat error</b>: {db:+.2f} ms", "<hr>"]
        if abs(dr) <= 2:
            lines.append("Rate agreement is good (within 2 s/d).")
        else:
            lines.append(
                f"Rate is off by {dr:+.1f} s/d. If this repeats across watches it is a "
                f"systematic bias -- most likely the sound-card sample clock. Run the "
                f"Sync tab's sample-clock calibration; {abs(dr):.1f} s/d is about "
                f"{abs(dr) / (86400.0 / 1e6):.0f} ppm.")
        if abs(da) <= 12:
            lines.append("Amplitude agreement is within the lift-angle uncertainty.")
        else:
            lines.append(
                f"Amplitude is off by {da:+.0f} deg. Amplitude scales directly with the "
                f"assumed lift angle -- a 1 deg lift-angle error moves amplitude about "
                f"5 deg. Check the caliber's lift angle against a technical sheet, or "
                f"use the 180-degree trick and the lift-angle solver on the Tools tab. "
                f"The machine can also be wrong here if its own lift angle is set loosely.")
        if abs(db) <= 0.2:
            lines.append("Beat error agrees.")
        else:
            lines.append(f"Beat error differs by {db:+.2f} ms -- usually the two are "
                         f"anchoring the tick/tock on different noises. Small and not "
                         f"worth chasing unless it is over ~0.4 ms.")
        self.txt.setHtml("<div style='font-family:Segoe UI;font-size:12px;color:#c8d0dc'>"
                         + "<br>".join(lines) + "</div>")
        if log:
            self._log(dr, da, db)
            self.accept()

    def _log(self, dr, da, db):
        import json
        path = os.path.join(APP_DIR, "crosschecks.json")
        data = []
        try:
            if os.path.exists(path):
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
        except Exception:
            data = []
        data.append({
            "when": datetime.now().isoformat(timespec="seconds"),
            "caliber": getattr(self.cal, "key", "") if self.cal else "",
            "machine": self.e_machine.text().strip(),
            "app": [self.e_app_rate.value(), self.e_app_amp.value(), self.e_app_be.value()],
            "hw": [self.e_hw_rate.value(), self.e_hw_amp.value(), self.e_hw_be.value()],
            "delta": [round(dr, 2), round(da, 1), round(db, 3)]})
        data = data[-200:]
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
            if self.parent() and hasattr(self.parent(), "status"):
                self.parent().status.showMessage(
                    f"Cross-check logged ({len(data)} on file)", 5000)
        except OSError:
            pass


class RegulationWizard(QtWidgets.QDialog):
    """
    Walks a regulation: capture a baseline, fix beat error, then close on the
    rate move by move, learning the index sensitivity from the first move and
    using it to size the next one. Every step ends by asking for a fresh
    reading so the loop is measured, not guessed.
    """

    def __init__(self, get_reading, caliber, parent=None):
        super().__init__(parent)
        self._get = get_reading          # callable -> Measurement or None
        self.cal = caliber
        self.setWindowTitle("Guided regulation")
        self.setMinimumSize(520, 460)
        self.step = 0                    # 0 baseline, 1 beat, 2 first rate move, 3 rate loop, 4 done
        self.caps = []                   # list of (rate, beat_error, amplitude)
        self._pre_rate = None            # rate before the last deliberate move
        self._first_step_delta = None

        v = QtWidgets.QVBoxLayout(self)
        self.lbl_head = QtWidgets.QLabel()
        self.lbl_head.setStyleSheet("font-weight:bold;font-size:14px;color:#e8eef7;")
        v.addWidget(self.lbl_head)
        self.lbl_body = QtWidgets.QLabel()
        self.lbl_body.setWordWrap(True)
        self.lbl_body.setStyleSheet(
            "background:#1a1f27;border:1px solid #2a323e;border-radius:6px;"
            "padding:12px;color:#c8d0dc;font-size:12px;")
        v.addWidget(self.lbl_body, 1)

        tr = QtWidgets.QHBoxLayout()
        tr.addWidget(QtWidgets.QLabel("Target rate"))
        self.spn_target = QtWidgets.QDoubleSpinBox()
        self.spn_target.setRange(-60, 60)
        self.spn_target.setValue(0.0)
        self.spn_target.setSuffix(" s/d")
        tr.addWidget(self.spn_target)
        tr.addWidget(QtWidgets.QLabel("tolerance"))
        self.spn_tol = QtWidgets.QDoubleSpinBox()
        self.spn_tol.setRange(1, 15)
        self.spn_tol.setValue(3.0)
        self.spn_tol.setSuffix(" s/d")
        tr.addWidget(self.spn_tol)
        tr.addStretch(1)
        v.addLayout(tr)

        self.lbl_log = QtWidgets.QLabel()
        self.lbl_log.setStyleSheet("color:#8a94a4;font-family:monospace;font-size:11px;")
        v.addWidget(self.lbl_log)

        br = QtWidgets.QHBoxLayout()
        self.b_cap = QtWidgets.QPushButton("Capture reading")
        self.b_cap.clicked.connect(self._capture)
        self.b_skip = QtWidgets.QPushButton("Beat error good enough")
        self.b_skip.clicked.connect(self._skip_beat)
        self.b_restart = QtWidgets.QPushButton("Restart")
        self.b_restart.clicked.connect(self._restart)
        b_close = QtWidgets.QPushButton("Close")
        b_close.clicked.connect(self.accept)
        for b in (self.b_cap, self.b_skip, self.b_restart, b_close):
            br.addWidget(b)
        v.addLayout(br)
        self._render()

    # -- flow ---------------------------------------------------------------
    def _restart(self):
        self.step = 0
        self.caps = []
        self._pre_rate = None
        self._first_step_delta = None
        self._render()

    def _capture(self):
        m = self._get()
        if m is None or not m.ok or m.rate != m.rate:
            QtWidgets.QMessageBox.information(
                self, "Guided regulation",
                "No steady reading available. Let the trace settle on the Measure tab, "
                "then capture.")
            return
        cap = (float(m.rate), float(m.beat_error), float(m.amplitude))
        self.caps.append(cap)
        self._advance(cap)
        self._render()

    def _skip_beat(self):
        if self.step == 1:
            self.step = 2
            self._render()

    def _advance(self, cap):
        rate, be, amp = cap
        tol = self.spn_tol.value()
        tgt = self.spn_target.value()
        if self.step == 0:
            self.step = 1 if be > 0.3 else 2
        elif self.step == 1:
            if be <= 0.3:
                self.step = 2
        elif self.step == 2:
            # first deliberate rate move just happened; learn the step size
            if self._pre_rate is not None:
                self._first_step_delta = rate - self._pre_rate
            self.step = 3 if abs(rate - tgt) > tol else 4
        elif self.step == 3:
            if abs(rate - tgt) <= tol:
                self.step = 4

    # -- rendering --------------------------------------------------------------
    def _render(self):
        self.b_skip.setVisible(self.step == 1)
        self.b_cap.setVisible(self.step < 4)
        cal = self.cal
        rname = getattr(cal, "regulator", "index") if cal else "index"
        tgt, tol = self.spn_target.value(), self.spn_tol.value()
        last = self.caps[-1] if self.caps else None

        if self.step == 0:
            self.lbl_head.setText("1 / 4  --  Baseline")
            self.lbl_body.setText(
                "Wind the watch fully, sit it dial-up on the pickup, and let the reading "
                "settle for 15-30 seconds. Then press Capture reading.\n\n"
                "Everything below is driven off this baseline, so it needs to be a clean, "
                "steady reading -- demagnetise first if you have not.")
        elif self.step == 1:
            instr = advisor.beat_adjust_instructions(cal) if cal else (
                "Adjust the beat: rotate the moveable stud carrier, or the collet if the "
                "caliber has no moveable stud.")
            self.lbl_head.setText("2 / 4  --  Beat error")
            self.lbl_body.setText(
                f"Beat error is {last[1]:.2f} ms (target under 0.30). Fix this before rate "
                f"-- a large beat error costs amplitude and drags the rate around.\n\n{instr}\n\n"
                f"Make one small adjustment, let it settle, then Capture reading again. "
                f"If it is close enough, use the button.")
        elif self.step == 2:
            direction = "slower" if last[0] > tgt else "faster"
            instr = advisor.rate_adjust_instructions(cal, direction) if cal else (
                f"Move the regulator toward {'-' if direction == 'slower' else '+'} "
                f"in a small step.")
            self._pre_rate = last[0]
            self.lbl_head.setText("3 / 4  --  Rate, first move")
            self.lbl_body.setText(
                f"Rate is {last[0]:+.1f} s/d, you want {tgt:+.0f}. Make one small, "
                f"deliberate move to run {direction} -- do not try to nail it in one go, "
                f"the point of the next step is to measure how far that move took you.\n\n"
                f"{instr}\n\nThen Capture reading.")
        elif self.step == 3:
            note = ""
            if self._first_step_delta:
                note = advisor.regulator_sensitivity(
                    self._pre_rate, last[0], "your last move")
                remaining = tgt - last[0]
                frac = remaining / self._first_step_delta if self._first_step_delta else 0
                note += (f"\n\nSo: about {abs(frac):.2f}x your last move, "
                         f"{'same direction' if frac > 0 else 'back the other way'}.")
            self._pre_rate = last[0]
            self.lbl_head.setText("3 / 4  --  Rate, closing in")
            self.lbl_body.setText(
                f"Rate is {last[0]:+.1f} s/d, target {tgt:+.0f} +/-{tol:.0f}.\n\n{note}\n\n"
                f"Make the move and Capture reading. Halve your step each time you cross "
                f"the target.")
        else:
            self.lbl_head.setText("4 / 4  --  Done at dial-up")
            r, be, amp = last if last else (float("nan"),) * 3
            self.lbl_body.setText(
                f"Dial-up rate {r:+.1f} s/d, beat error {be:.2f} ms, amplitude {amp:.0f}.\n\n"
                f"Now capture the other five positions (Positions tab). Regulate for the "
                f"smallest spread between positions first, then nudge the index to "
                f"re-centre the mean. Re-check after 24 hours -- a fresh service can "
                f"drift as the oils spread.")

        rows = []
        for i, (r, be, amp) in enumerate(self.caps):
            rows.append(f"#{i+1}  rate {r:+7.1f}   beat {be:4.2f}   amp {amp:3.0f}")
        self.lbl_log.setText("\n".join(rows))


class ServiceChecklistDialog(QtWidgets.QDialog):
    """A working checklist for a caliber, saved back as an attached document."""

    def __init__(self, template, caliber_label="", parent=None):
        super().__init__(parent)
        self.t = template
        self.setWindowTitle(f"Service checklist -- {template.title}")
        self.setMinimumSize(560, 640)
        self._boxes = {}                       # "phase\tstep" -> QCheckBox

        outer = QtWidgets.QVBoxLayout(self)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(inner)

        for phase, steps in template.phases:
            gb = QtWidgets.QGroupBox(phase)
            gl = QtWidgets.QVBoxLayout(gb)
            for s in steps:
                cb = QtWidgets.QCheckBox(s)
                self._boxes[f"{phase}\t{s}"] = cb
                gl.addWidget(cb)
            v.addWidget(gb)

        if template.lubrication:
            v.addWidget(self._ref_box("Lubrication map",
                        [f"{p}  --  {o}" for p, o in template.lubrication]))
        if template.specs:
            v.addWidget(self._ref_box("Specs", [f"{k}: {val}" for k, val in template.specs]))
        if template.weak_points:
            v.addWidget(self._ref_box("Known weak points", template.weak_points))

        v.addWidget(QtWidgets.QLabel("Notes"))
        self.e_notes = QtWidgets.QPlainTextEdit()
        self.e_notes.setPlaceholderText("Findings, parts replaced, deviations from the plan...")
        v.addWidget(self.e_notes)
        v.addStretch(1)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        self._caliber_label = caliber_label
        bb = QtWidgets.QDialogButtonBox()
        b_save = bb.addButton("Attach to a new service entry",
                              QtWidgets.QDialogButtonBox.AcceptRole)
        b_copy = bb.addButton("Copy to clipboard", QtWidgets.QDialogButtonBox.ActionRole)
        bb.addButton(QtWidgets.QDialogButtonBox.Close)
        b_save.clicked.connect(self.accept)
        b_copy.clicked.connect(self._copy)
        bb.rejected.connect(self.reject)
        outer.addWidget(bb)

    def _ref_box(self, title, lines):
        gb = QtWidgets.QGroupBox(title)
        gl = QtWidgets.QVBoxLayout(gb)
        lbl = QtWidgets.QLabel("\n".join(f"- {ln}" for ln in lines))
        lbl.setWordWrap(True)
        lbl.setStyleSheet("color:#b6bfcc;")
        gl.addWidget(lbl)
        return gb

    def _filled(self):
        return {k: cb.isChecked() for k, cb in self._boxes.items()}

    def markdown(self):
        from . import service_templates as st
        body = st.render_markdown(self.t, self._filled(), header=self._caliber_label
                                  or self.t.title)
        note = self.e_notes.toPlainText().strip()
        if note:
            body += f"\n## Notes\n\n{note}\n"
        done = sum(1 for v in self._filled().values() if v)
        body += f"\n_{done}/{len(self._boxes)} steps checked, " \
                f"{datetime.now():%Y-%m-%d %H:%M}._\n"
        return body

    def _copy(self):
        QtWidgets.QApplication.clipboard().setText(self.markdown())
        self.parent().status.showMessage("Checklist copied to clipboard", 4000) \
            if self.parent() and hasattr(self.parent(), "status") else None


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


class _ClockCalWorker(QtCore.QObject):
    """Poll an NTP server on an interval; each tick reports a true-time fix."""
    point = QtCore.Signal(float, float, str)        # true_epoch, roundtrip_s, error
    finished = QtCore.Signal()

    def __init__(self, host, interval_s):
        super().__init__()
        self.host = host
        self.interval_s = float(interval_s)
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    @QtCore.Slot()
    def run(self):
        from . import timesync
        # First fix immediately, then on the interval.
        while not self._stop.is_set():
            try:
                t0 = time.time()
                off, rt = timesync.query_ntp(self.host)
                t1 = time.time()
                self.point.emit((t0 + t1) / 2.0 + off, float(rt), "")
            except Exception as e:                   # noqa: BLE001
                self.point.emit(0.0, 0.0, f"{type(e).__name__}: {e}")
            self._stop.wait(self.interval_s)
        self.finished.emit()


def _qr_pixmap(text, box=6, dark="#0d1013", light="#ffffff"):
    """A QR code for `text` as a QPixmap, painted from the module matrix so no
    image library is needed. Returns None if the qrcode package is missing."""
    try:
        import qrcode
    except Exception:
        return None
    q = qrcode.QRCode(border=3, box_size=1,
                      error_correction=qrcode.constants.ERROR_CORRECT_M)
    q.add_data(text)
    q.make(fit=True)
    m = q.get_matrix()
    size = len(m) * box
    img = QtGui.QImage(size, size, QtGui.QImage.Format_RGB32)
    img.fill(QtGui.QColor(light))
    p = QtGui.QPainter(img)
    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(QtGui.QColor(dark))
    for r, row in enumerate(m):
        for c, on in enumerate(row):
            if on:
                p.drawRect(c * box, r * box, box, box)
    p.end()
    return QtGui.QPixmap.fromImage(img)


def _schematic_pixmap(obj, px):
    """
    A square px-by-px schematic illustration of `obj` (a Watch or a catalogue
    entry -- anything with brand/model/material/... attributes), rendered on a
    white ground. Used wherever there is no user photo. Returns None on failure.
    """
    try:
        from PySide6 import QtSvg
        from . import watchart
        svg = watchart.watch_svg_for(obj, size=max(64, int(px))).encode("utf-8")
        r = QtSvg.QSvgRenderer(QtCore.QByteArray(svg))
        dpr = getattr(_schematic_pixmap, "_dpr", 1.0)
        n = max(1, int(round(px * dpr)))
        img = QtGui.QImage(n, n, QtGui.QImage.Format_ARGB32)
        img.fill(QtGui.QColor("white"))
        p = QtGui.QPainter(img)
        r.render(p)
        p.end()
        pm = QtGui.QPixmap.fromImage(img)
        pm.setDevicePixelRatio(dpr)
        return pm
    except Exception:
        return None


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"WatchGrapher {__version__} -- acoustic timegrapher")
        # Minimum small enough for a 1366x768 laptop; the control column
        # scrolls, so nothing can be pushed out of reach.
        self.setMinimumSize(940, 560)
        self.resize(1380, 860)
        self.recorder = None
        self._net_recorder = None       # persistent phone-pickup server for the session
        self._net_fresh = False
        self._net_server_pinned = False  # kept alive across device changes (Tools menu / autostart)
        self._net_kill_pending = False   # unpinned mid-run: stop the server once the run ends
        self._phone_last_save = ""
        self._phone_run = False         # the current run was started from the phone
        self._phone_starting = False
        self._phone_pending = None      # {"summary","have"} awaiting a save/discard from the phone
        self._phone_pending_m = None
        self.last = None
        self.readings = []
        self._rate_hist = []       # (elapsed_s, rate_spd) for the rate-history plot
        self._allan_hist = []      # raw (undecimated) rate points for the Allan curve
        self._amp_hist = []        # (elapsed_s, amplitude_deg) -- right axis of the same plot
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
        self._clock_cal_thread = None
        self._clock_cal_worker = None
        self._clock_cal_seg = []          # (true_epoch, frames) for the current unbroken stream
        self._clock_cal_done_segs = []    # segments closed by an audio-stream restart
        self._clock_cal_rec = None
        self._clock_cal_breaks = 0
        self._settle_pending = False
        self._settle_buf = []
        self._settle_secs = 0
        self._settle_deadline = 0.0
        self._run_t0 = None        # timed run start, or None
        self._run_len = 0.0
        self._stable = []          # recent readings, for auto-capture
        self._reserve = []         # (elapsed_s, rate, amplitude, beat_error)
        self._res_t0 = None
        self._res_watch_id = None
        self._res_next = 0.0

        self.collection = coll.Collection(COLLECTION_DIR)
        self._watch_ids = []
        self._undo_stack = []          # file snapshots before each collection save
        self._install_collection_undo()

        self._build()
        self._refresh_watches()
        self._start_worker()
        self._refresh_devices()
        self._load_user_db()
        i = self.cmb_cal.findData("eta_2824_2")
        if i >= 0:
            self.cmb_cal.setCurrentIndex(i)
        if self._settings_get("phone_autostart", False):
            try:
                self._ensure_net_server(pinned=True)
                self.status.showMessage(self._net_recorder.opened_note, 12000)
            except Exception:
                pass

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
        self.stack.addWidget(self._build_chrono_page())
        self.stack.addWidget(self._build_phone_page())
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

        em = mb.addMenu("&Edit")
        self.act_undo = QtGui.QAction("Undo collection change", self)
        self.act_undo.setShortcut("Ctrl+Z")
        self.act_undo.setEnabled(False)
        self.act_undo.setToolTip(
            "Step back through changes to your watch collection -- an added or edited "
            "watch, a deleted service or run, a filed report. Does not affect the "
            "current measurement.")
        self.act_undo.triggered.connect(self._undo_collection)
        em.addAction(self.act_undo)

        vm = mb.addMenu("&View")
        for label, idx, key in (("Measure", 0, "Ctrl+1"),
                                ("My Watches", 1, "Ctrl+2"),
                                ("Sync", 2, "Ctrl+3"),
                                ("Chrono", 3, "Ctrl+4"),
                                ("Phone Portal", 4, "Ctrl+5"),
                                ("Help", 5, "Ctrl+6")):
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
        vm.addSeparator()
        self.act_gate = QtGui.QAction("Hold readouts on a weak signal", self)
        self.act_gate.setCheckable(True)
        self.act_gate.setChecked(False)
        self.act_gate.setToolTip(
            "When on, the rate/amplitude/beat-error readouts freeze at their last "
            "trustworthy value (greyed) whenever the beats stop matching their "
            "template, the beat rate disagrees with the caliber, or the pickup is "
            "hearing the room. When off, they always show the current number.")
        vm.addAction(self.act_gate)
        vm.addSeparator()
        self.act_pin_ref = QtGui.QAction("Pin current reading as trace reference", self)
        self.act_pin_ref.setShortcut("Ctrl+P")
        self.act_pin_ref.setToolTip(
            "Freeze the current tick/tock trace as a faint reference so the live trace "
            "draws over it -- for before/after a regulation or a service. Triggering it "
            "again with no reading clears the reference.")
        self.act_pin_ref.triggered.connect(self._pin_reference)
        vm.addAction(self.act_pin_ref)
        vm.addSeparator()
        self.act_agc = QtGui.QAction("Auto-gain and clipping guard", self)
        self.act_agc.setCheckable(True)
        self.act_agc.setChecked(True)
        self.act_agc.setToolTip(
            "Auto-gain applies a digital makeup gain into the analysis buffer so the "
            "DSP keeps headroom on a quiet pickup -- it does not touch the Windows "
            "mixer, and the recorded WAV stays raw. The clipping guard warns when the "
            "input hits full scale, where the amplitude reading goes wrong. Off = the "
            "signal is used exactly as it arrives.")
        self.act_agc.toggled.connect(self._set_agc)
        vm.addAction(self.act_agc)
        vm.addSeparator()
        thm = vm.addMenu("Theme")
        self._theme_group = QtGui.QActionGroup(self)
        for label, mode in (("Dark", "dark"), ("Light", "light"), ("Follow system", "system")):
            a = QtGui.QAction(label, self, checkable=True)
            a.setChecked(_T.MODE == mode)
            a.triggered.connect(lambda _=False, m=mode: self._set_theme(m))
            self._theme_group.addAction(a)
            thm.addAction(a)

        tm = mb.addMenu("&Tools")
        mic_act = QtGui.QAction("Microphone response calibration...", self)
        mic_act.setToolTip(
            "Play a sine sweep and measure where the pickup is deaf. Captures the "
            "whole chain -- speaker, room, mic -- so it is advisory; mainly useful "
            "for choosing the filter band.")
        mic_act.triggered.connect(self._mic_response_cal)
        tm.addAction(mic_act)
        xchk_act = QtGui.QAction("Cross-check against a hardware timegrapher...", self)
        xchk_act.setToolTip(
            "Enter what a Witschi / Weishi machine reads for the same watch and compare. "
            "Persistent disagreement points at the lift angle (amplitude) or the "
            "sample-clock calibration (rate).")
        xchk_act.triggered.connect(self._cross_check)
        tm.addAction(xchk_act)
        tm.addSeparator()
        pp_act = QtGui.QAction("Phone Portal (server, QR code)...", self)
        pp_act.triggered.connect(lambda: self._goto_page(4))
        tm.addAction(pp_act)

    def _ensure_net_server(self, pinned=True):
        """Bring the phone pickup server up if it is not already. Returns it."""
        from . import netmic
        nr = getattr(self, "_net_recorder", None)
        if nr is None or not nr.running:
            sr = int(self.cmb_sr.currentText())
            nr = netmic.NetworkRecorder(
                samplerate=sr, buffer_seconds=90.0,
                port=self._settings_get("phone_port", 8477))
            nr.start()
            self._net_recorder = nr
            got = getattr(nr, "port", 0)
            if got and got != self._settings_get("phone_port", 8477):
                self._settings_set("phone_port", got)
            self._publish_phone_watches()
        if pinned:
            self._net_server_pinned = True
        self._refresh_phone_page()
        return nr

    def _toggle_phone_server(self, on):
        if on:
            self._ensure_net_server(pinned=True)
        else:
            self._net_server_pinned = False
            nr = getattr(self, "_net_recorder", None)
            if nr is not None and nr is self.recorder:
                self._net_kill_pending = True
                self.status.showMessage(
                    "Phone pickup server will stop when the current run ends.", 6000)
            elif nr is not None:
                self._stop_net_server()
        self._refresh_phone_page()

    def _stop_net_server(self):
        nr = getattr(self, "_net_recorder", None)
        if nr is None:
            return
        try:
            nr.stop()
        except Exception:
            pass
        self._net_recorder = None
        self._net_kill_pending = False
        self._phone_run = False
        self._phone_pending = None
        self._refresh_phone_page()

    def _refresh_phone_page(self):
        if not hasattr(self, "btn_phone_server"):
            return
        nr = getattr(self, "_net_recorder", None)
        up = nr is not None and nr.running
        self.btn_phone_server.blockSignals(True)
        self.btn_phone_server.setChecked(up)
        self.btn_phone_server.setText("Stop phone server" if up else "Start phone server")
        self.btn_phone_server.blockSignals(False)
        url = nr.url if up else ""
        self.lbl_phone_url.setText(url or "server not running")
        if up and url != getattr(self, "_phone_qr_url", None):
            self._phone_qr_url = url
            pm = _qr_pixmap(url)
            if pm is not None:
                self.lbl_phone_qr.setPixmap(pm)
        if not up:
            self.lbl_phone_qr.clear()
            self._phone_qr_url = None
        if up:
            phone = nr.connected
            self.lbl_phone_conn.setText(
                "a phone is connected and streaming" if phone
                else "waiting for a phone to open the URL")
            self.lbl_phone_conn.setStyleSheet(
                f"color:{'#57d38c' if phone else '#8a94a4'};")
        else:
            self.lbl_phone_conn.setText("")

    def _maybe_stop_net_server(self):
        """Stop the phone server unless it is still wanted -- pinned, the NET
        input is still selected, or a run is in progress on it."""
        nr = getattr(self, "_net_recorder", None)
        if nr is None or nr is self.recorder:
            return
        if getattr(self, "_net_kill_pending", False):
            self._stop_net_server()
            return
        if self.cmb_dev.currentData() == "NET" or getattr(self, "_net_server_pinned", False):
            return
        self._stop_net_server()

    def _mic_response_cal(self):
        if not audio.HAVE_SD:
            QtWidgets.QMessageBox.warning(self, "Microphone response",
                                          "Audio backend not available.")
            return
        key = self._pickup_key()
        if not key:
            QtWidgets.QMessageBox.information(
                self, "Microphone response", "Select a real input device first.")
            return
        was_listening = self.recorder is not None
        if was_listening:
            if QtWidgets.QMessageBox.question(
                    self, "Microphone response",
                    "This needs exclusive use of the input. Stop listening and run "
                    "the sweep?") != QtWidgets.QMessageBox.Yes:
                return
            self._suppress_finish = True
            self._toggle_listen(False)
            self._suppress_finish = False
        dlg = MicCalDialog(int(self.cmb_sr.currentText()),
                           self.cmb_dev.currentData(), parent=self)
        if dlg.exec() == QtWidgets.QDialog.Accepted and dlg.curve is not None:
            import json
            self._pickup_profiles = self._load_profiles()
            self._pickup_profiles.setdefault(key, {})["mic_response"] = [
                [round(f, 1), round(d, 2)] for f, d in zip(*dlg.curve)]
            try:
                with open(self._profiles_path(), "w", encoding="utf-8") as fh:
                    json.dump(self._pickup_profiles, fh, indent=2)
                self.status.showMessage(f"Saved microphone response for '{key}'.", 5000)
            except OSError as e:
                QtWidgets.QMessageBox.warning(self, "Microphone response", str(e))

    def _install_collection_undo(self):
        real_save = self.collection.save

        def save_with_undo():
            try:
                if os.path.exists(self.collection.path):
                    with open(self.collection.path, "rb") as fh:
                        self._undo_stack.append(fh.read())
                    del self._undo_stack[:-25]
                if hasattr(self, "act_undo"):
                    self._refresh_undo_action()
            except OSError:
                pass
            real_save()

        self.collection.save = save_with_undo

    def _refresh_undo_action(self):
        n = len(self._undo_stack)
        self.act_undo.setEnabled(n > 0)
        self.act_undo.setText(f"Undo collection change ({n})" if n else "Undo collection change")

    def _undo_collection(self):
        if not self._undo_stack:
            return
        snap = self._undo_stack.pop()
        try:
            with open(self.collection.path, "wb") as fh:
                fh.write(snap)
        except OSError as e:
            QtWidgets.QMessageBox.warning(self, "Undo", str(e))
            return
        self.collection.load()
        self._refresh_watches()
        self._publish_phone_watches()
        self._refresh_undo_action()
        self.status.showMessage("Reverted the last collection change", 5000)

    def _apply_preset(self, name):
        presets = {
            "Quick check":       dict(win=8, runlen=0, settle=False, auto=False,
                                      pos="Dial up", page=0),
            "Timed 30 s":        dict(win=15, runlen=30, settle=True, auto=False,
                                      pos="Dial up", page=0),
            "Full 6-position":   dict(win=12, runlen=20, settle=True, auto=True,
                                      pos="Dial up", page=0, tab="Positions"),
            "Power reserve":     dict(win=15, runlen=0, settle=False, auto=False,
                                      page=0, tab="Power reserve", res_int=300, res_h=48),
            "Vintage / low beat": dict(win=25, runlen=40, settle=True, auto=False,
                                       pos="Dial up", page=0),
        }
        p = presets.get(name)
        if not p:
            return
        self._applying_preset = True
        self.spn_win.setValue(p["win"])
        self.spn_runlen.setValue(p["runlen"])
        self.chk_settle.setChecked(p["settle"])
        self.chk_auto.setChecked(p["auto"])
        if "pos" in p:
            i = self.cmb_pos.findText(p["pos"])
            if i >= 0:
                self.cmb_pos.setCurrentIndex(i)
        if "res_int" in p:
            self.spn_res_int.setValue(p["res_int"])
            self.spn_res_hours.setValue(p["res_h"])
        if "page" in p:
            self._goto_page(p["page"])
        if "tab" in p and hasattr(self, "tabs"):
            for k in range(self.tabs.count()):
                if self.tabs.tabText(k) == p["tab"]:
                    self.tabs.setCurrentIndex(k)
                    break
        self._applying_preset = False
        self.status.showMessage(f"Preset: {name}", 4000)

    def _preset_to_custom(self):
        if not getattr(self, "_applying_preset", False) \
                and hasattr(self, "cmb_preset") and self.cmb_preset.currentText() != "Custom":
            self.cmb_preset.blockSignals(True)
            self.cmb_preset.setCurrentText("Custom")
            self.cmb_preset.blockSignals(False)

    # ---------------------------------------------------- phone remote control
    def _phone_watch_payload(self):
        return [{"id": w.id, "label": w.label} for w in self.collection.sorted_watches()]

    def _publish_phone_watches(self):
        nr = getattr(self, "_net_recorder", None)
        if nr is not None:
            import json
            try:
                nr.watches_json = json.dumps(self._phone_watch_payload())
            except Exception:
                pass

    def _phone_state(self):
        def num(v):
            return None if v is None or v != v else round(float(v), 3)
        m = self.last
        good = m is not None and m.ok
        wlabel = ""
        if hasattr(self, "cmb_watch") and self.cmb_watch.currentData():
            wlabel = self.cmb_watch.currentText()
        run_active = self._run_t0 is not None
        st = {
            "device_is_net": self.cmb_dev.currentData() == "NET",
            "listening": self.recorder is not None,
            "watch": wlabel,
            "elapsed": round(time.time() - self._listen_t0, 1) if self._listen_t0 else 0.0,
            "settling": bool(getattr(self, "_settle_pending", False)),
            "run_len": float(self._run_len) if run_active else 0.0,
            "run_elapsed": round(time.time() - self._run_t0, 1) if run_active else 0.0,
            "have_reading": bool(good and m.rate == m.rate),
            "last_save": getattr(self, "_phone_last_save", ""),
            "pending": getattr(self, "_phone_pending", None),
        }
        if good:
            st["rate"] = num(m.rate)
            st["amplitude"] = num(m.amplitude)
            st["beat_error"] = num(m.beat_error)
            st["bph"] = m.detected_bph
            st["quality"] = num(m.quality)
        return st

    def _handle_phone_cmd(self, cmd):
        c = (cmd or {}).get("cmd")
        if c == "select":
            i = self.cmb_watch.findData(cmd.get("id") or None)
            if i >= 0:
                self.cmb_watch.setCurrentIndex(i)
        elif c == "start":
            self._phone_last_save = ""
            self._phone_pending = None
            self._phone_pending_m = None
            if self.recorder is not None:
                return
            if self.cmb_dev.currentData() != "NET":
                # The phone drove this, so make the phone the pickup.
                j = self.cmb_dev.findData("NET")
                if j >= 0:
                    self.cmb_dev.setCurrentIndex(j)
            if self.cmb_dev.currentData() == "NET":
                dur = cmd.get("duration")
                if isinstance(dur, (int, float)) and 0 <= dur <= 7200:
                    self.spn_runlen.blockSignals(True)
                    self.spn_runlen.setValue(int(dur))
                    self.spn_runlen.blockSignals(False)
                self._phone_starting = True
                self.btn_go.setChecked(True)
        elif c == "stop":
            if self.recorder is not None:
                self.btn_go.setChecked(False)
        elif c == "save":
            self._phone_save()
        elif c == "save_pending":
            self._phone_save_pending()
        elif c == "discard":
            self._phone_pending = None
            self._phone_pending_m = None
            self._phone_run = False
            self._phone_last_save = "discarded"

    def _phone_finish(self, summary, m, ok=True):
        """A phone-started run ended -- stash the outcome for the phone to decide."""
        have = bool(ok and m is not None and m.ok and m.rate == m.rate)
        self._phone_pending = {"summary": summary, "have": have}
        self._phone_pending_m = m if have else None
        self._phone_last_save = ""

    def _phone_save_pending(self):
        m = self._phone_pending_m
        wid = self.cmb_watch.currentData() if hasattr(self, "cmb_watch") else None
        w = self.collection.watches.get(wid) if wid else None
        if not w:
            self._phone_last_save = "pick a watch first"
            return
        if m is None or not m.ok or m.rate != m.rate:
            self._phone_last_save = "nothing usable to save"
            self._phone_pending = None
            return
        readings = list(self.readings) or [advisor.Reading(
            self.cmb_pos.currentText(), m.rate, m.amplitude, m.beat_error,
            self.cmb_wind.currentText())]
        c = self._current_caliber()
        rec = coll.record_from_readings(
            readings, c.key if c else w.caliber_key, float(self.spn_lift.value()),
            notes="Saved from phone")
        w.history.append(rec)
        self.collection.save()
        self._end_session()
        self._refresh_watches(w.id)
        self._phone_pending = None
        self._phone_pending_m = None
        self._phone_run = False
        self._phone_last_save = f"saved to {w.label} ({len(w.history)} runs)"

    def _phone_save(self):
        wid = self.cmb_watch.currentData() if hasattr(self, "cmb_watch") else None
        w = self.collection.watches.get(wid) if wid else None
        m = getattr(self, "_last_good", None) or self.last
        if not w:
            self._phone_last_save = "pick a watch first"
            return
        if m is None or not m.ok or m.rate != m.rate:
            self._phone_last_save = "no steady reading to save yet"
            return
        rd = [advisor.Reading(self.cmb_pos.currentText(), m.rate, m.amplitude,
                              m.beat_error, self.cmb_wind.currentText())]
        c = self._current_caliber()
        rec = coll.record_from_readings(
            rd, c.key if c else w.caliber_key, float(self.spn_lift.value()),
            notes="Saved from phone", service_event=False)
        w.history.append(rec)
        self.collection.save()
        self._refresh_watches(w.id)
        self._phone_last_save = f"saved to {w.label} ({len(w.history)} runs)"

    def _settings_get(self, key, default=None):
        import json
        try:
            with open(os.path.join(APP_DIR, "settings.json"), encoding="utf-8") as fh:
                return json.load(fh).get(key, default)
        except (OSError, ValueError):
            return default

    def _settings_set(self, key, value):
        import json
        p = os.path.join(APP_DIR, "settings.json")
        try:
            data = {}
            if os.path.exists(p):
                with open(p, encoding="utf-8") as fh:
                    data = json.load(fh)
            data[key] = value
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
        except OSError:
            pass

    def _set_theme(self, mode):
        _T.save_mode(mode)
        if mode != _T.MODE:
            QtWidgets.QMessageBox.information(
                self, "Theme",
                f"Theme set to {mode}. Restart WatchGrapher to apply it.")

    def _set_agc(self, on):
        if self.recorder is not None:
            self.recorder.agc_enabled = bool(on)
        self._clip_seen_frac = 0.0

    def _cross_check(self):
        c = self._current_caliber()
        dlg = CrossCheckDialog(
            self._last_good if getattr(self, "_last_good", None) is not None else self.last,
            c, parent=self)
        dlg.exec()

    def _pin_reference(self):
        m = self._last_good if getattr(self, "_last_good", None) is not None else self.last
        if self._ref_m is not None and (m is None or not m.ok):
            self._ref_m = None
        elif m is not None and m.ok:
            self._ref_m = m
        self._draw_ref_trace()
        if self._ref_m is None:
            self.status.showMessage("Trace reference cleared", 4000)
        else:
            self.status.showMessage(
                f"Pinned reference: {self._ref_m.rate:+.1f} s/d, "
                f"{self._ref_m.amplitude:.0f} deg, {self._ref_m.beat_error:.2f} ms", 6000)

    def _draw_ref_trace(self):
        m = self._ref_m
        if m is None:
            self.s_tick_ref.setData([], [])
            self.s_tock_ref.setData([], [])
            return
        xt, yt, xk, yk = trace_points(m, m.nominal_bph or m.detected_bph,
                                      float(self.spn_trace.value()))
        self.s_tick_ref.setData(xt, yt)
        self.s_tock_ref.setData(xk, yk)

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
                ("CHRONO", "Chronograph accuracy: stopwatch comparison and the chrono load test."),
                ("PHONE PORTAL", "Run and connect the phone pickup / remote: QR code, URL, "
                 "server controls."),
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

    # ============================================================ phone portal
    def _build_phone_page(self):
        page = QtWidgets.QWidget()
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        outer = QtWidgets.QVBoxLayout(page)
        outer.setContentsMargins(30, 24, 30, 24)
        outer.setSpacing(14)

        outer.addWidget(QtWidgets.QLabel(
            "<b>Phone Portal.</b> Run the little web server a phone connects to. "
            "Open the URL below on a phone on the same Wi-Fi to use its microphone "
            "as the pickup and drive a test remotely -- pick a watch, choose a "
            "duration, start, save."))

        srv = QtWidgets.QHBoxLayout()
        self.btn_phone_server = QtWidgets.QPushButton("Start phone server")
        self.btn_phone_server.setCheckable(True)
        self.btn_phone_server.setMinimumHeight(38)
        self.btn_phone_server.setStyleSheet(
            f"QPushButton{{background:{ACCENT};color:#08101c;font-weight:bold;"
            "padding:8px 22px;border-radius:6px;}"
            "QPushButton:checked{background:#ff5d5d;color:#fff;}")
        self.btn_phone_server.toggled.connect(self._toggle_phone_server)
        srv.addWidget(self.btn_phone_server, 0)
        self.chk_phone_autostart = QtWidgets.QCheckBox("Start automatically when WatchGrapher opens")
        self.chk_phone_autostart.setChecked(bool(self._settings_get("phone_autostart", False)))
        self.chk_phone_autostart.toggled.connect(
            lambda on: self._settings_set("phone_autostart", bool(on)))
        srv.addWidget(self.chk_phone_autostart, 1)
        outer.addLayout(srv)

        mid = QtWidgets.QHBoxLayout()
        self.lbl_phone_qr = QtWidgets.QLabel()
        self.lbl_phone_qr.setFixedSize(230, 230)
        self.lbl_phone_qr.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_phone_qr.setStyleSheet(
            "background:#ffffff;border:1px solid #2a323e;border-radius:8px;color:#5a6472;")
        self.lbl_phone_qr.setText("QR code appears\nwhen the server runs")
        mid.addWidget(self.lbl_phone_qr, 0)

        urlbox = QtWidgets.QVBoxLayout()
        urlbox.addStretch(1)
        urlbox.addWidget(QtWidgets.QLabel("Scan the code, or type this on the phone:"))
        self.lbl_phone_url = QtWidgets.QLineEdit("server not running")
        self.lbl_phone_url.setReadOnly(True)
        f = self.lbl_phone_url.font()
        f.setPointSize(14)
        f.setBold(True)
        self.lbl_phone_url.setFont(f)
        urlbox.addWidget(self.lbl_phone_url)
        cprow = QtWidgets.QHBoxLayout()
        b_copy = QtWidgets.QPushButton("Copy URL")
        b_copy.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(
            self.lbl_phone_url.text()))
        b_open = QtWidgets.QPushButton("Open here")
        b_open.setToolTip("Open the portal in this computer's browser to check it works.")
        b_open.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(
            QtCore.QUrl(self.lbl_phone_url.text())) if self.lbl_phone_url.text().startswith("http")
            else None)
        cprow.addWidget(b_copy)
        cprow.addWidget(b_open)
        cprow.addStretch(1)
        urlbox.addLayout(cprow)
        self.lbl_phone_conn = QtWidgets.QLabel("")
        self.lbl_phone_conn.setStyleSheet("color:#8a94a4;")
        urlbox.addWidget(self.lbl_phone_conn)
        urlbox.addStretch(1)
        mid.addLayout(urlbox, 1)
        outer.addLayout(mid)

        from . import netmic
        info = QtWidgets.QTextBrowser()
        info.setOpenExternalLinks(True)
        crypto = ("HTTPS is available." if netmic.HAVE_CRYPTO else
                  "<span style='color:#ff5d5d'>The 'cryptography' package is not "
                  "installed, so the server can only use plain HTTP -- phone browsers "
                  "block the microphone on HTTP. Run <code>pip install cryptography</code> "
                  "(or re-run run.bat) and restart.</span>")
        rtc = ("WebRTC transport available." if netmic.HAVE_AIORTC else
               "WebRTC is unavailable ('aiortc' not installed); PCM still works.")
        info.setHtml(
            "<div style='font-family:Segoe UI;font-size:12px;color:#c8d0dc'>"
            "<p>The phone and this computer must be on the <b>same Wi-Fi</b>; a guest "
            "network that isolates clients will not work. The phone shows a one-time "
            "\"connection is not private\" warning for the local self-signed certificate "
            "-- tap through it.</p>"
            f"<p>{crypto}<br>{rtc}</p>"
            "<p>The server binds only to this computer's LAN address, never all "
            "interfaces. Your watch list, the live readings and every remote command "
            "require a random token that is embedded in the page, so a scanner that "
            "just hits the port gets nothing. The server only ever <i>receives</i> "
            "audio and runs nothing from the page. It holds the phone screen awake "
            "while a test runs.</p>"
            "</div>")
        info.setMaximumHeight(200)
        outer.addWidget(info)
        outer.addStretch(1)

        self._phone_qr_url = None
        return scroll

    # ============================================================== sync page
    def _build_chrono_page(self):
        page = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(page)
        outer.setContentsMargins(28, 22, 28, 22)
        outer.setSpacing(16)

        outer.addWidget(QtWidgets.QLabel(
            "<b>Chronograph accuracy.</b> Two checks a bench timegrapher cannot do "
            "on its own: how well the running chronograph keeps time against a "
            "reference, and what running it costs in amplitude."))

        # -- stopwatch comparison --
        g1 = QtWidgets.QGroupBox("Stopwatch comparison")
        v1 = QtWidgets.QVBoxLayout(g1)
        self.lbl_chrono = QtWidgets.QLabel("0:00.00")
        f = self.lbl_chrono.font()
        f.setPointSize(48)
        f.setFamily("Consolas")
        f.setBold(True)
        self.lbl_chrono.setFont(f)
        self.lbl_chrono.setAlignment(QtCore.Qt.AlignCenter)
        v1.addWidget(self.lbl_chrono)
        v1.addWidget(QtWidgets.QLabel(
            "Press Start here and start your watch's chronograph at the same instant. "
            "Let it run at least a few minutes -- longer is better. Press Stop on both "
            "together, read the watch, and enter it below."))
        cb = QtWidgets.QHBoxLayout()
        self.btn_chrono_go = QtWidgets.QPushButton("Start")
        self.btn_chrono_go.setStyleSheet(
            f"QPushButton{{background:{ACCENT};color:#08101c;font-weight:bold;"
            "padding:8px 22px;border-radius:6px;}")
        self.btn_chrono_go.clicked.connect(self._chrono_toggle)
        self.btn_chrono_reset = QtWidgets.QPushButton("Reset")
        self.btn_chrono_reset.clicked.connect(self._chrono_reset)
        cb.addWidget(self.btn_chrono_go)
        cb.addWidget(self.btn_chrono_reset)
        cb.addStretch(1)
        v1.addLayout(cb)

        er = QtWidgets.QFormLayout()
        self.e_chrono_read = QtWidgets.QLineEdit()
        self.e_chrono_read.setPlaceholderText("mm:ss.ss  or  ss.ss  as the chrono reads")
        b_chrono_cmp = QtWidgets.QPushButton("Compare")
        b_chrono_cmp.clicked.connect(self._chrono_compare)
        crow = QtWidgets.QHBoxLayout()
        crow.addWidget(self.e_chrono_read, 1)
        crow.addWidget(b_chrono_cmp, 0)
        cw = QtWidgets.QWidget()
        cw.setLayout(crow)
        er.addRow("Chronograph reads", cw)
        v1.addLayout(er)
        self.lbl_chrono_result = QtWidgets.QLabel("")
        self.lbl_chrono_result.setWordWrap(True)
        self.lbl_chrono_result.setStyleSheet("color:#c8d0dc;font-size:13px;")
        v1.addWidget(self.lbl_chrono_result)
        outer.addWidget(g1)

        # -- chrono load test --
        g2 = QtWidgets.QGroupBox("Chronograph load test (A/B)")
        v2 = QtWidgets.QVBoxLayout(g2)
        v2.addWidget(QtWidgets.QLabel(
            "On the Measure tab, get a steady reading with the chronograph STOPPED and "
            "capture it here, then start the chronograph, let it settle, and capture "
            "again. A 20-40 deg amplitude drop is normal on a 7750; much more than that, "
            "or a big rate shift, points at the chronograph coupling (the oscillating "
            "pinion or vertical clutch) dragging."))
        lb = QtWidgets.QHBoxLayout()
        b_a = QtWidgets.QPushButton("Capture: chrono stopped")
        b_a.clicked.connect(lambda: self._chrono_ab("stopped"))
        b_b = QtWidgets.QPushButton("Capture: chrono running")
        b_b.clicked.connect(lambda: self._chrono_ab("running"))
        lb.addWidget(b_a)
        lb.addWidget(b_b)
        lb.addStretch(1)
        v2.addLayout(lb)
        self.lbl_chrono_ab = QtWidgets.QLabel("No captures yet.")
        self.lbl_chrono_ab.setWordWrap(True)
        self.lbl_chrono_ab.setStyleSheet("color:#c8d0dc;font-size:13px;")
        v2.addWidget(self.lbl_chrono_ab)
        outer.addWidget(g2)
        outer.addStretch(1)

        self._chrono_t0 = None
        self._chrono_elapsed = 0.0
        self._chrono_ab_data = {}
        self._chrono_timer = QtCore.QTimer(self)
        self._chrono_timer.setInterval(50)
        self._chrono_timer.timeout.connect(self._chrono_tick)
        return page

    def _chrono_fmt(self, s):
        m, s = divmod(s, 60.0)
        return f"{int(m)}:{s:05.2f}"

    def _chrono_tick(self):
        if self._chrono_t0 is not None:
            self.lbl_chrono.setText(
                self._chrono_fmt(self._chrono_elapsed + time.monotonic() - self._chrono_t0))

    def _chrono_toggle(self):
        if self._chrono_t0 is None:
            self._chrono_t0 = time.monotonic()
            self._chrono_timer.start()
            self.btn_chrono_go.setText("Stop")
        else:
            self._chrono_elapsed += time.monotonic() - self._chrono_t0
            self._chrono_t0 = None
            self._chrono_timer.stop()
            self.btn_chrono_go.setText("Start")
            self.lbl_chrono.setText(self._chrono_fmt(self._chrono_elapsed))

    def _chrono_reset(self):
        self._chrono_t0 = None
        self._chrono_elapsed = 0.0
        self._chrono_timer.stop()
        self.btn_chrono_go.setText("Start")
        self.lbl_chrono.setText("0:00.00")
        self.lbl_chrono_result.setText("")

    def _chrono_compare(self):
        ref = self._chrono_elapsed + (
            time.monotonic() - self._chrono_t0 if self._chrono_t0 is not None else 0.0)
        if ref < 5:
            self.lbl_chrono_result.setText("Run the comparison for longer than a few seconds.")
            return
        txt = self.e_chrono_read.text().strip().replace(" ", "")
        try:
            if ":" in txt:
                mm, ss = txt.split(":")
                watch = float(mm) * 60 + float(ss)
            else:
                watch = float(txt)
        except ValueError:
            self.lbl_chrono_result.setText("Enter the chrono reading as mm:ss.ss or ss.ss.")
            return
        err = watch - ref
        spd = err / ref * 86400.0
        ppm = err / ref * 1e6
        self.lbl_chrono_result.setText(
            f"Over {self._chrono_fmt(ref)} the chronograph is {err:+.2f} s "
            f"({spd:+.1f} s/day, {ppm:+.0f} ppm). "
            + ("That is the movement's own rate -- regulate the watch, not the "
               "chronograph." if abs(spd) > 3 else
               "Within a few s/day, which is as good as the balance is regulated. "
               "The chronograph mechanism itself adds no error.")
            + " Your reaction time on the two stop presses is worth a few tenths of "
              "a second, so a long run dilutes it -- do not read too much into a "
              "short one.")

    def _chrono_ab(self, which):
        m = self._last_good if getattr(self, "_last_good", None) is not None else self.last
        if m is None or not m.ok:
            self.lbl_chrono_ab.setText("No steady reading on the Measure tab to capture.")
            return
        self._chrono_ab_data[which] = (m.rate, m.amplitude, m.beat_error)
        a = self._chrono_ab_data.get("stopped")
        b = self._chrono_ab_data.get("running")
        lines = []
        if a:
            lines.append(f"Stopped: {a[0]:+.1f} s/d, {a[1]:.0f} deg, {a[2]:.2f} ms")
        if b:
            lines.append(f"Running: {b[0]:+.1f} s/d, {b[1]:.0f} deg, {b[2]:.2f} ms")
        if a and b:
            dr, da = b[0] - a[0], b[1] - a[1]
            lines.append("")
            lines.append(f"Chronograph load: {da:+.0f} deg amplitude, {dr:+.1f} s/d rate.")
            if da < -55:
                lines.append("That is a heavy drop -- check the oscillating pinion / "
                             "vertical clutch engagement and lubrication, and the "
                             "chronograph bridge screws.")
            elif da < -15:
                lines.append("Normal load for a coupled chronograph.")
            else:
                lines.append("Very light load -- either a well-set vertical clutch or "
                             "the chronograph is not actually engaging.")
            if abs(dr) > 8:
                lines.append(f"The {dr:+.1f} s/d rate shift is larger than expected; "
                             f"re-regulating with the chrono in its normal running "
                             f"state may be the pragmatic call for a daily-wear piece.")
        self.lbl_chrono_ab.setText("\n".join(lines))

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

        lrow = QtWidgets.QHBoxLayout()
        self.cmb_wear_watch = QtWidgets.QComboBox()
        b_wear_log = QtWidgets.QPushButton("Log this drift to the watch")
        b_wear_log.clicked.connect(self._log_wear_check)
        lrow.addWidget(self.cmb_wear_watch, 1)
        lrow.addWidget(b_wear_log, 0)
        side.addLayout(lrow)

        line2 = QtWidgets.QFrame()
        line2.setFrameShape(QtWidgets.QFrame.HLine)
        line2.setStyleSheet("color:#2a323e;")
        side.addWidget(line2)

        _sc = QtWidgets.QLabel("Sample-clock calibration")
        _sc.setStyleSheet("color:#4da3ff;font-weight:bold;")
        side.addWidget(_sc)
        self.lbl_clock = QtWidgets.QLabel("")
        self.lbl_clock.setWordWrap(True)
        self.lbl_clock.setStyleSheet("color:#c8d0dc;font-size:12px;")
        side.addWidget(self.lbl_clock)

        crow = QtWidgets.QHBoxLayout()
        self.spn_cal_min = QtWidgets.QSpinBox()
        self.spn_cal_min.setRange(3, 180)
        self.spn_cal_min.setValue(20)
        self.spn_cal_min.setSuffix(" min")
        self.btn_clock_cal = QtWidgets.QPushButton("Calibrate")
        self.btn_clock_cal.clicked.connect(self._clock_cal_toggle)
        crow.addWidget(self.spn_cal_min, 1)
        crow.addWidget(self.btn_clock_cal, 0)
        side.addLayout(crow)

        mrow = QtWidgets.QHBoxLayout()
        self.spn_clock_ppm = QtWidgets.QDoubleSpinBox()
        self.spn_clock_ppm.setRange(-2000.0, 2000.0)
        self.spn_clock_ppm.setDecimals(1)
        self.spn_clock_ppm.setSuffix(" ppm (manual)")
        b_ppm = QtWidgets.QPushButton("Set")
        b_ppm.clicked.connect(lambda: self._store_clock_ppm(self.spn_clock_ppm.value(), "manual"))
        b_ppm_clr = QtWidgets.QPushButton("Clear")
        b_ppm_clr.clicked.connect(lambda: self._store_clock_ppm(None, ""))
        mrow.addWidget(self.spn_clock_ppm, 1)
        mrow.addWidget(b_ppm, 0)
        mrow.addWidget(b_ppm_clr, 0)
        side.addLayout(mrow)

        rrow = QtWidgets.QHBoxLayout()
        self.spn_ref_app = QtWidgets.QDoubleSpinBox()
        self.spn_ref_app.setRange(-99.0, 99.0)
        self.spn_ref_app.setDecimals(1)
        self.spn_ref_app.setPrefix("app ")
        self.spn_ref_app.setSuffix(" s/d")
        self.spn_ref_true = QtWidgets.QDoubleSpinBox()
        self.spn_ref_true.setRange(-99.0, 99.0)
        self.spn_ref_true.setDecimals(1)
        self.spn_ref_true.setPrefix("true ")
        self.spn_ref_true.setSuffix(" s/d")
        b_ref = QtWidgets.QPushButton("From reference")
        b_ref.setToolTip(
            "Measure a watch whose real rate you know (from a hardware timegrapher).\n"
            "Enter what this app reads and what it should read; the difference is\n"
            "the sound card's clock error.")
        b_ref.clicked.connect(self._clock_from_reference)
        rrow.addWidget(self.spn_ref_app, 1)
        rrow.addWidget(self.spn_ref_true, 1)
        rrow.addWidget(b_ref, 0)
        side.addLayout(rrow)

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
        if idx == 4 and hasattr(self, "_refresh_phone_page"):
            self._refresh_phone_page()
        # The Sync clock only needs its 30 fps repaint while it is on screen.
        if not hasattr(self, "_sync_tmr"):
            return
        if idx == 2:
            self._sync_tick()
            self._sync_tmr.start()
            self._refresh_clock_label()
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
        self._last_wear_drift = (
            datetime.fromtimestamp(self._watch_set_ref).isoformat(timespec="seconds"),
            datetime.fromtimestamp(now).isoformat(timespec="seconds"), float(drift))
        self.lbl_watch_drift.setText(
            f"Set {ref0:%H:%M:%S}, {elapsed / 3600:.1f} h ago. Watch is {drift:+.0f} s "
            f"versus true time now -> {rate:+.1f} s/day.")

    def _log_wear_check(self):
        d = getattr(self, "_last_wear_drift", None)
        if d is None:
            self.lbl_watch_drift.setText(
                "Work out a drift first: mark when the watch was set, then read it.")
            return
        wid = self.cmb_wear_watch.currentData()
        w = self.collection.watches.get(wid) if wid else None
        if not w:
            QtWidgets.QMessageBox.information(self, "Wrist rate",
                                             "Add a watch to the collection first.")
            return
        set_iso, when_iso, off = d
        w.wear_checks.append({"when": when_iso, "set_when": set_iso,
                              "off_seconds": round(off, 1), "note": ""})
        self.collection.save()
        self._refresh_watches(w.id)
        self.status.showMessage(f"Logged wrist-rate check to {w.label}", 5000)

    # ---------------------------------------------------- sample-clock calibration
    # A cheap USB sound card's sample clock is typically 20-100 ppm off nominal.
    # 50 ppm is 4.3 s/day of systematic error on every rate reading. This measures
    # the true rate against NTP and stores a per-device correction; only the rate
    # output is affected (amplitude and beat error are ratios within one capture).
    PPM_TO_SPD = 86400.0 / 1e6         # additive s/day per +1 ppm of clock error

    def _clock_key(self):
        if self.cmb_dev.currentData() in ("SIM", "NET"):
            return None
        return (self.cmb_dev.currentText() or "").strip() or None

    def _device_clock_ppm(self, key=None):
        key = key or self._clock_key()
        if not key:
            return None
        prof = self._load_profiles().get(key) or {}
        v = prof.get("clock_ppm")
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def _store_clock_ppm(self, ppm, source):
        key = self._clock_key()
        if not key:
            QtWidgets.QMessageBox.information(
                self, "Sample clock", "Select a real input device first.")
            return
        import json
        self._pickup_profiles = self._load_profiles()
        entry = self._pickup_profiles.setdefault(key, {})
        if ppm is None:
            entry.pop("clock_ppm", None)
            entry.pop("clock_ppm_source", None)
            entry.pop("clock_ppm_date", None)
        else:
            entry["clock_ppm"] = round(float(ppm), 2)
            entry["clock_ppm_source"] = source
            entry["clock_ppm_date"] = datetime.now().isoformat(timespec="seconds")
        try:
            with open(self._profiles_path(), "w", encoding="utf-8") as fh:
                json.dump(self._pickup_profiles, fh, indent=2)
        except OSError as e:
            QtWidgets.QMessageBox.warning(self, "Sample clock", str(e))
            return
        self._refresh_clock_label()
        self.status.showMessage(
            f"Clock correction {'cleared' if ppm is None else f'{ppm:+.1f} ppm'} for '{key}'.",
            6000)

    def _refresh_clock_label(self):
        if not hasattr(self, "lbl_clock"):
            return
        key = self._clock_key()
        if not key:
            self.lbl_clock.setText("Simulated device -- no clock to calibrate.")
            return
        prof = self._load_profiles().get(key) or {}
        ppm = prof.get("clock_ppm")
        if ppm is None:
            self.lbl_clock.setText(
                f"{key}: not calibrated. Rate readings carry the sound card's own "
                f"clock error (often 2-4 s/day, worse on cheap interfaces). Start "
                f"listening on this device, then Calibrate.")
        else:
            src = prof.get("clock_ppm_source", "")
            when = self._ago(prof.get("clock_ppm_date"))
            stale = self._cal_days_old(prof.get("clock_ppm_date"))
            txt = (f"{key}: {float(ppm):+.1f} ppm ({src or 'saved'}"
                   + (f", calibrated {when}" if when else "") + "). "
                   f"Rate readings are corrected by "
                   f"{float(ppm) * self.PPM_TO_SPD:+.2f} s/day.")
            if stale is not None and stale > 120:
                txt += (" This calibration is getting old -- redo it if you have moved "
                        "the setup or the room temperature has changed with the season.")
            self.lbl_clock.setText(txt)

    @staticmethod
    def _cal_days_old(iso):
        try:
            return (datetime.now() - datetime.fromisoformat(iso)).days
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _ago(iso):
        d = MainWindow._cal_days_old(iso)
        if d is None:
            return ""
        if d <= 0:
            return "today"
        if d == 1:
            return "yesterday"
        if d < 14:
            return f"{d} days ago"
        if d < 60:
            return f"{d // 7} weeks ago"
        if d < 730:
            return f"{max(1, d // 30)} months ago"
        return f"{d // 365} years ago"

    def _nudge_stale_calibration(self):
        """One status-bar line when a listen starts on a stale/uncalibrated device."""
        key = self._clock_key()
        if not key:
            return
        prof = self._load_profiles().get(key) or {}
        if prof.get("clock_ppm") is None:
            return
        days = self._cal_days_old(prof.get("clock_ppm_date"))
        if days is not None and days > 120:
            self.status.showMessage(
                f"Sample-clock calibration for '{key}' is {self._ago(prof.get('clock_ppm_date'))}"
                f" -- consider redoing it on the Sync tab if conditions have changed.", 10000)

    def _rate_correction(self):
        """Additive s/day applied to a live rate reading for the current device."""
        rec = getattr(self, "recorder", None)
        if rec is None or isinstance(rec, audio.SimulatedRecorder):
            return 0.0
        ppm = self._device_clock_ppm()
        return (ppm * self.PPM_TO_SPD) if ppm is not None else 0.0

    def _clock_from_reference(self):
        # app reads A on a watch whose true rate is T -> the app is fast by (A - T),
        # which is the clock error expressed in s/day.
        err_spd = self.spn_ref_app.value() - self.spn_ref_true.value()
        ppm = err_spd / self.PPM_TO_SPD
        self._store_clock_ppm(ppm, "reference watch")

    def _clock_cal_toggle(self):
        if getattr(self, "_clock_cal_thread", None) is not None:
            self._clock_cal_stop("cancelled")
            return
        if self.recorder is None or isinstance(self.recorder, audio.SimulatedRecorder) \
                or not hasattr(self.recorder, "frames"):
            QtWidgets.QMessageBox.information(
                self, "Sample clock",
                "Start listening on the real input device first (Measure tab -> Start, "
                "open-ended is fine -- a watch does not need to be on the pickup).")
            return
        key = self._clock_key()
        if not key:
            return
        self._clock_cal_key = key
        self._clock_cal_seg = []
        self._clock_cal_done_segs = []
        self._clock_cal_ovf0 = getattr(self.recorder, "overflows", 0)
        self._clock_cal_rec = self.recorder     # the stream currently being counted
        self._clock_cal_breaks = 0
        self._clock_cal_end = time.monotonic() + self.spn_cal_min.value() * 60.0
        self.btn_clock_cal.setText("Stop")
        self.lbl_clock.setText("Calibrating... contacting NTP.")

        host = "pool.ntp.org"
        self._clock_cal_thread = QtCore.QThread(self)
        self._clock_cal_worker = _ClockCalWorker(host, interval_s=25.0)
        self._clock_cal_worker.moveToThread(self._clock_cal_thread)
        self._clock_cal_thread.started.connect(self._clock_cal_worker.run)
        self._clock_cal_worker.point.connect(self._on_clock_point)
        self._clock_cal_worker.finished.connect(self._clock_cal_thread.quit)
        self._clock_cal_thread.start()

    def _on_clock_point(self, true_epoch, roundtrip, err):
        if getattr(self, "_clock_cal_thread", None) is None:
            return
        # The count is only meaningful while the same stream keeps running
        # unbroken. If the user pressed Stop there is nothing to measure.
        if self.recorder is None or isinstance(self.recorder, audio.SimulatedRecorder) \
                or not hasattr(self.recorder, "frames"):
            self._clock_cal_stop("stopped")
            return
        # The stall-recovery watchdog rebuilding the stream is by design. It
        # resets the frame counter, so it breaks continuity with the points
        # collected so far -- but only at that instant. Close the current
        # segment, open a fresh one on the new stream, and carry on. The final
        # fit is done per segment and the slopes combined, so nothing is
        # thrown away and no line is ever fitted across the gap. A buffer
        # overflow drops samples the same way and is handled the same.
        broke = (self.recorder is not self._clock_cal_rec
                 or getattr(self.recorder, "overflows", 0) != self._clock_cal_ovf0)
        if broke:
            if len(self._clock_cal_seg) >= 4:
                self._clock_cal_done_segs.append(self._clock_cal_seg)
            self._clock_cal_seg = []
            self._clock_cal_rec = self.recorder
            self._clock_cal_ovf0 = getattr(self.recorder, "overflows", 0)
            self._clock_cal_breaks += 1
            self._clock_cal_end += 45.0     # recoup the fixes lost around the gap
            kept = len(self._clock_cal_done_segs)
            self.lbl_clock.setText(
                f"Calibrating '{self._clock_cal_key}': audio stream restarted, "
                f"continuing on the new stream ({kept} segment{'s' if kept != 1 else ''} "
                f"kept).")
            return
        if err:
            self.lbl_clock.setText(f"Calibrating... NTP error: {err}")
        else:
            # Pull frames back to the instant the NTP fix refers to.
            now = time.time()
            frames = self.recorder.frames - (now - true_epoch) * self.recorder.samplerate
            self._clock_cal_seg.append((true_epoch, float(frames)))
            n = sum(len(s) for s in self._clock_cal_done_segs) + len(self._clock_cal_seg)
            left = max(0.0, (self._clock_cal_end - time.monotonic()) / 60.0)
            extra = (f", {self._clock_cal_breaks} restart"
                     f"{'s' if self._clock_cal_breaks != 1 else ''}"
                     if self._clock_cal_breaks else "")
            self.lbl_clock.setText(
                f"Calibrating '{self._clock_cal_key}': {n} fixes, "
                f"~{left:.0f} min left{extra}.")
        if time.monotonic() >= self._clock_cal_end:
            self._clock_cal_stop("done")

    def _clock_cal_stop(self, reason):
        w = getattr(self, "_clock_cal_worker", None)
        if w is not None:
            w.stop()
        th = getattr(self, "_clock_cal_thread", None)
        if th is not None:
            th.quit()
            th.wait(3000)
        self._clock_cal_worker = None
        self._clock_cal_thread = None
        self._clock_cal_rec = None
        self.btn_clock_cal.setText("Calibrate")

        segs = [s for s in (list(getattr(self, "_clock_cal_done_segs", [])) +
                            [getattr(self, "_clock_cal_seg", [])]) if len(s) >= 4]
        if reason == "stopped":
            self._refresh_clock_label()
            self.status.showMessage(
                "Sample-clock calibration stopped -- listening ended before it finished.",
                8000)
            return

        nominal = float(self.recorder.samplerate) if self.recorder is not None else 48000.0
        # Fit each unbroken segment on its own, then combine the per-segment
        # sample-rate estimates weighted by how tightly each was pinned down.
        est, wt, tot_pts, tot_span = [], [], 0, 0.0
        for s in segs:
            a = np.array(s, dtype=float)
            t = a[:, 0] - a[0, 0]
            if t[-1] < 150.0:            # a sub-2.5-min segment gives a mushy slope
                continue
            fr = a[:, 1] - a[0, 1]
            (slope, _c), cov = np.polyfit(t, fr, 1, cov=True)
            var = float(cov[0, 0]) / (nominal ** 2) * 1e12       # ppm^2
            if not np.isfinite(var) or var <= 0:
                var = 25.0
            est.append((slope / nominal - 1.0) * 1e6)
            wt.append(1.0 / var)
            tot_pts += len(s)
            tot_span += float(t[-1])

        if reason == "cancelled" or not est or tot_pts < 6 or tot_span < 300.0:
            self._refresh_clock_label()
            if reason != "cancelled":
                QtWidgets.QMessageBox.information(
                    self, "Sample clock",
                    "Not enough clean NTP fixes to calibrate -- check the connection, "
                    "and if the audio stream keeps restarting try a wired input or "
                    "disable USB power management for the device.")
            return

        W = float(sum(wt))
        ppm = float(sum(p * w for p, w in zip(est, wt)) / W)
        ppm_sd = float(np.sqrt(1.0 / W))
        span_min = tot_span / 60.0
        nseg = len(est)

        seg_note = (f" across {nseg} segments ({self._clock_cal_breaks} stream "
                    f"restart{'s' if self._clock_cal_breaks != 1 else ''})"
                    if nseg > 1 else "")
        ans = QtWidgets.QMessageBox.question(
            self, "Sample clock calibrated",
            f"Over {span_min:.0f} minutes and {tot_pts} NTP fixes{seg_note}, this device's "
            f"sample clock measures {ppm:+.1f} ppm ({ppm * self.PPM_TO_SPD:+.2f} s/day), "
            f"+/-{ppm_sd:.1f} ppm.\n\n"
            f"Save it as the rate correction for '{self._clock_cal_key}'?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.Yes)
        if ans == QtWidgets.QMessageBox.Yes:
            self._store_clock_ppm(ppm, f"NTP, {span_min:.0f} min")
        else:
            self._refresh_clock_label()

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

        wtabs = QtWidgets.QTabWidget()

        # -- timing tab --
        timing = QtWidgets.QWidget()
        tl = QtWidgets.QVBoxLayout(timing)
        TR_RATE_C, TR_DELTA_C, TR_AMP_C = "#4da3ff", "#ff9d4d", "#57d38c"
        self.p_trend = pg.PlotWidget(title="Performance over time")
        tpi = self.p_trend.getPlotItem()
        tpi.showGrid(x=True, y=True, alpha=0.25)
        tpi.addLegend(offset=(-10, 10))
        tpi.setAxisItems({"bottom": pg.DateAxisItem(orientation="bottom")})
        # Left axis carries the two s/day series (rate + positional delta);
        # coloured to the mean-rate line, which is the one people read.
        tla = tpi.getAxis("left")
        tla.setLabel("rate / delta", units="s/d", color=TR_RATE_C)
        tla.setPen(TR_RATE_C)
        tla.setTextPen(TR_RATE_C)
        self.c_tr_rate = tpi.plot(pen=pg.mkPen(TR_RATE_C, width=2), symbol="o",
                                  symbolSize=7, symbolBrush=TR_RATE_C,
                                  name="mean rate (s/d)")
        self.c_tr_delta = tpi.plot(pen=pg.mkPen(TR_DELTA_C, width=2), symbol="o",
                                   symbolSize=7, symbolBrush=TR_DELTA_C,
                                   name="positional delta (s/d)")

        # Amplitude on a linked right-hand scale, coloured to its own line.
        self._tr_amp_vb = pg.ViewBox()
        tpi.showAxis("right")
        tpi.scene().addItem(self._tr_amp_vb)
        tra = tpi.getAxis("right")
        tra.linkToView(self._tr_amp_vb)
        tra.setLabel("peak amplitude", units="deg", color=TR_AMP_C)
        tra.setPen(TR_AMP_C)
        tra.setTextPen(TR_AMP_C)
        self._tr_amp_vb.setXLink(tpi)
        self.c_tr_amp = pg.PlotDataItem(pen=pg.mkPen(TR_AMP_C, width=2), symbol="o",
                                        symbolSize=7, symbolBrush=TR_AMP_C)
        self._tr_amp_vb.addItem(self.c_tr_amp)
        tpi.legend.addItem(self.c_tr_amp, "peak amplitude (deg)")

        def _sync_tr_amp_vb():
            self._tr_amp_vb.setGeometry(tpi.getViewBox().sceneBoundingRect())
            self._tr_amp_vb.linkedViewChanged(tpi.getViewBox(), self._tr_amp_vb.XAxis)
        tpi.getViewBox().sigResized.connect(_sync_tr_amp_vb)
        _sync_tr_amp_vb()
        tl.addWidget(self.p_trend, 3)

        self.tbl_hist = QtWidgets.QTableWidget(0, 7)
        self.tbl_hist.setHorizontalHeaderLabels(
            ["Date", "Mean rate", "Delta", "Amp max", "Amp min", "Beat err", "Notes"])
        self.tbl_hist.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.tbl_hist.verticalHeader().setVisible(False)
        tl.addWidget(self.tbl_hist, 2)
        hb = QtWidgets.QHBoxLayout()
        for label, slot in (("Delete selected run", self._hist_delete),
                            ("Export history CSV", self._hist_export)):
            b = QtWidgets.QPushButton(label)
            b.clicked.connect(slot)
            hb.addWidget(b)
        hb.addStretch(1)
        tl.addLayout(hb)
        wtabs.addTab(timing, "Timing history")

        # -- service tab --
        svc = QtWidgets.QWidget()
        svl = QtWidgets.QVBoxLayout(svc)
        self.lbl_svc_summary = QtWidgets.QLabel("")
        self.lbl_svc_summary.setStyleSheet("color:#c8d0dc;")
        svl.addWidget(self.lbl_svc_summary)
        self.tbl_svc = QtWidgets.QTableWidget(0, 6)
        self.tbl_svc.setHorizontalHeaderLabels(
            ["Date", "Type", "By", "Cost", "Docs", "Notes"])
        self.tbl_svc.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.tbl_svc.verticalHeader().setVisible(False)
        self.tbl_svc.itemDoubleClicked.connect(lambda _=None: self._service_edit())
        svl.addWidget(self.tbl_svc, 1)
        sb = QtWidgets.QHBoxLayout()
        for label, slot in (("Add service", self._service_add),
                            ("Edit", self._service_edit),
                            ("Delete", self._service_delete),
                            ("Open document", self._service_open_doc),
                            ("Service checklist...", self._service_checklist),
                            ("Before / after report...", self._before_after_report)):
            b = QtWidgets.QPushButton(label)
            b.clicked.connect(slot)
            sb.addWidget(b)
        sb.addStretch(1)
        svl.addLayout(sb)
        wtabs.addTab(svc, "Service log")

        # -- power reserve tab --
        pr = QtWidgets.QWidget()
        prl = QtWidgets.QVBoxLayout(pr)
        prl.addWidget(QtWidgets.QLabel(
            "Power-reserve runs filed from the Measure tab. Double-click a row to "
            "reopen it on the Power reserve tab."))
        self.tbl_res_hist = QtWidgets.QTableWidget(0, 6)
        self.tbl_res_hist.setHorizontalHeaderLabels(
            ["Date", "Hours", "Amp start -> end", "To 220 deg", "Isochronism", "Notes"])
        self.tbl_res_hist.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.tbl_res_hist.verticalHeader().setVisible(False)
        self.tbl_res_hist.itemDoubleClicked.connect(lambda _=None: self._reserve_reopen())
        prl.addWidget(self.tbl_res_hist, 1)
        rrb = QtWidgets.QHBoxLayout()
        for label, slot in (("Reopen on Power reserve tab", self._reserve_reopen),
                            ("Delete", self._reserve_hist_delete),
                            ("Export CSV", self._reserve_hist_export)):
            b = QtWidgets.QPushButton(label)
            b.clicked.connect(slot)
            rrb.addWidget(b)
        rrb.addStretch(1)
        prl.addLayout(rrb)
        wtabs.addTab(pr, "Power reserve")

        # -- wrist rate tab --
        wr = QtWidgets.QWidget()
        wrl = QtWidgets.QVBoxLayout(wr)
        wrl.addWidget(QtWidgets.QLabel(
            "How the watch actually keeps time on the wrist -- logged from the Sync "
            "tab's watch-drift tool. The dashed line is the bench mean rate for "
            "comparison."))
        self.p_wear = pg.PlotWidget(title="Wrist rate over time")
        self.p_wear.setLabel("left", "rate", units="s/d")
        self.p_wear.showGrid(x=True, y=True, alpha=0.25)
        self.p_wear.setAxisItems({"bottom": pg.DateAxisItem(orientation="bottom")})
        self.c_wear = self.p_wear.plot(pen=pg.mkPen("#57d38c", width=2), symbol="o",
                                       symbolSize=7, symbolBrush="#57d38c")
        self.l_wear_bench = pg.InfiniteLine(angle=0, pen=pg.mkPen(
            "#8a94a4", width=1, style=QtCore.Qt.DashLine))
        self.p_wear.addItem(self.l_wear_bench)
        wrl.addWidget(self.p_wear, 2)
        self.tbl_wear = QtWidgets.QTableWidget(0, 4)
        self.tbl_wear.setHorizontalHeaderLabels(
            ["Checked", "Set to true", "Off by (s)", "Real rate s/d"])
        self.tbl_wear.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.tbl_wear.verticalHeader().setVisible(False)
        wrl.addWidget(self.tbl_wear, 1)
        wearb = QtWidgets.QHBoxLayout()
        b_wear_del = QtWidgets.QPushButton("Delete selected")
        b_wear_del.clicked.connect(self._wear_delete)
        wearb.addWidget(b_wear_del)
        wearb.addStretch(1)
        wrl.addLayout(wearb)
        wtabs.addTab(wr, "Wrist rate")

        # -- documents vault tab --
        dv = QtWidgets.QWidget()
        dvl = QtWidgets.QVBoxLayout(dv)
        dvl.addWidget(QtWidgets.QLabel(
            "Warranty cards, receipts, box-and-papers photos, manuals, valuations, "
            "provenance. Files are copied into the collection so the original can move."))
        self.tbl_docs = QtWidgets.QTableWidget(0, 4)
        self.tbl_docs.setHorizontalHeaderLabels(["Kind", "Name", "Added", "Note"])
        self.tbl_docs.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.tbl_docs.verticalHeader().setVisible(False)
        self.tbl_docs.itemDoubleClicked.connect(lambda _=None: self._doc_open())
        dvl.addWidget(self.tbl_docs, 1)
        dvb = QtWidgets.QHBoxLayout()
        for label, slot in (("Add document...", self._doc_add),
                            ("Open", self._doc_open),
                            ("Delete", self._doc_delete)):
            b = QtWidgets.QPushButton(label)
            b.clicked.connect(slot)
            dvb.addWidget(b)
        dvb.addStretch(1)
        dvl.addLayout(dvb)
        wtabs.addTab(dv, "Documents")

        right.addWidget(wtabs, 5)

        hb2 = QtWidgets.QHBoxLayout()
        self.btn_wreport = QtWidgets.QPushButton("Print / save watch report")
        self.btn_wreport.setMinimumHeight(32)
        self.btn_wreport.setStyleSheet(
            "QPushButton{background:#57d38c;color:#08101c;font-weight:bold;"
            "padding:8px 18px;border-radius:6px;}")
        self.btn_wreport.clicked.connect(self._watch_report)
        hb2.addWidget(self.btn_wreport)
        self.btn_portfolio = QtWidgets.QPushButton("Portfolio report (all watches)")
        self.btn_portfolio.setMinimumHeight(32)
        self.btn_portfolio.clicked.connect(self._portfolio_report)
        hb2.addWidget(self.btn_portfolio)
        self.btn_yearreview = QtWidgets.QPushButton("Year in review...")
        self.btn_yearreview.setMinimumHeight(32)
        self.btn_yearreview.clicked.connect(self._year_review)
        hb2.addWidget(self.btn_yearreview)
        hb2.addStretch(1)
        right.addLayout(hb2)
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

        # The session-preset selector lives in section 3 (Test conditions).
        self.cmb_preset = QtWidgets.QComboBox()
        self.cmb_preset.addItems(["Custom", "Quick check", "Timed 30 s", "Full 6-position",
                                  "Power reserve", "Vintage / low beat"])
        self.cmb_preset.setToolTip(
            "One-click setups: analysis window, timed-run length, settle, position and "
            "which tab to work in. Pick Custom to leave your own settings alone.")
        self.cmb_preset.currentTextChanged.connect(self._apply_preset)

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
        cal_row = QtWidgets.QWidget()
        cal_lay = QtWidgets.QHBoxLayout(cal_row)
        cal_lay.setContentsMargins(0, 0, 0, 0)
        cal_lay.setSpacing(6)
        cal_lay.addWidget(self.cmb_cal, 1)
        self.btn_whatsnormal = QtWidgets.QPushButton("Movement info")
        self.btn_whatsnormal.setToolTip(
            "Everything about the selected caliber: regulating hardware, lift-angle "
            "source, expected beat rate, amplitude and positional delta, service "
            "interval, known weak points and equivalent movements.")
        self.btn_whatsnormal.clicked.connect(self._whats_normal)
        cal_lay.addWidget(self.btn_whatsnormal, 0)
        g2.addRow("Caliber", cal_row)
        g2.addRow("Lift / bph", lb_row)
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

        g3.addRow("Preset", self.cmb_preset)
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
        for _w in (self.spn_win, self.spn_runlen):
            _w.valueChanged.connect(self._preset_to_custom)
        for _w in (self.chk_settle, self.chk_auto):
            _w.toggled.connect(self._preset_to_custom)
        self.cmb_pos.currentIndexChanged.connect(self._preset_to_custom)

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
        dev = self.cmb_dev.currentData()
        # The phone server outlives runs, and a device change, if it was pinned
        # (Tools -> Phone pickup server / autostart). Otherwise it goes away when
        # the phone input is no longer selected.
        self._maybe_stop_net_server()
        is_sim = dev == "SIM"
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
        self._refresh_clock_label()

    # ------------------------------------------------------------ pickup profiles
    def _pickup_key(self):
        if self.cmb_dev.currentData() in ("SIM", "NET"):
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
        if not p or "band_lo" not in p:
            return
        for spn, k in ((self.spn_lo, "band_lo"), (self.spn_hi, "band_hi"),
                       (self.spn_env, "env_win_ms"), (self.spn_thr, "sub_threshold")):
            if k in p:
                spn.blockSignals(True)
                spn.setValue(p[k])
                spn.blockSignals(False)
        self._push_cfg()
        msg = f"Loaded saved filter settings for '{key}'."
        mr = p.get("mic_response")
        if mr and len(mr) > 8:
            arr = np.asarray(mr, dtype=float)
            usable = arr[arr[:, 1] > -10.0]
            if usable.size and usable[-1, 0] < float(self.spn_hi.value()) - 500:
                msg += (f" Its measured response is down past {usable[-1, 0]:.0f} Hz -- "
                        f"consider lowering the high band there.")
        self.status.showMessage(msg, 6000)

    def _save_pickup_profile(self):
        import json
        key = self._pickup_key()
        if not key:
            QtWidgets.QMessageBox.information(
                self, "Pickup profile", "Select a real input device first.")
            return
        self._pickup_profiles = self._load_profiles()
        self._pickup_profiles.setdefault(key, {}).update({
            "band_lo": int(self.spn_lo.value()), "band_hi": int(self.spn_hi.value()),
            "env_win_ms": float(self.spn_env.value()),
            "sub_threshold": float(self.spn_thr.value())})
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
        self.r_rate.setToolTip(
            "<b>Rate</b> -- seconds the watch gains (+) or loses (-) per day, from the "
            "slope of the beat times against a least-squares line. Target 0 to +-5 s/d, "
            "+-10 acceptable, chase it beyond +-20. A 20 s window gives roughly +-0.2 s/d "
            "of confidence; a 60 s timed run about +-0.02. The clock correction from the "
            "Sync tab, if set, is already folded in.")
        self.r_amp.setToolTip(
            "<b>Amplitude</b> -- peak swing of the balance from rest, in degrees, from "
            "the interval between the unlock and drop noises and the caliber's lift "
            "angle. 270-310 is healthy at full wind for a modern caliber; under 220 "
            "points at power delivery or the escapement; over 330 is approaching "
            "rebanking. A wrong lift angle shifts this about 5 deg per degree of error.")
        self.r_be.setToolTip(
            "<b>Beat error</b> -- how unevenly the tick and the tock are spaced, in "
            "milliseconds. Under 0.3 is the target, under 0.5 fine. A large beat error "
            "costs amplitude and makes the watch hard to regulate. Fixed at the stud "
            "carrier or the hairspring collet depending on the caliber's hardware.")
        self.r_bph.setToolTip(
            "<b>Beat rate</b> -- beats per hour the audio actually measured, snapped to "
            "the nearest standard frequency. If this disagrees with the selected "
            "caliber the rate figure is meaningless -- either the caliber is wrong or "
            "the pickup is mistracking on noise.")
        for r in (self.r_rate, self.r_amp, self.r_be, self.r_bph):
            row.addWidget(r)
        lay.addLayout(row)

        plots = QtWidgets.QSplitter(QtCore.Qt.Horizontal)

        self.p_trace = pg.PlotWidget(title="Trace  --  slope is rate, gap between lines is beat error")
        self.p_trace.setLabel("bottom", "deviation", units="ms")
        self.p_trace.setLabel("left", "elapsed", units="s")
        self.p_trace.showGrid(x=True, y=True, alpha=0.25)
        self.p_trace.invertY(True)
        self.p_trace.setToolTip(
            "Two dot lines, tick and tock. A straight line sloping down-right means "
            "gaining; the vertical gap between the lines is the beat error. Thick or "
            "fuzzy lines mean the escapement is not repeating cleanly or the pickup is "
            "noisy; wandering, non-straight lines point at a real fault -- a bent pivot, "
            "a hairspring catching, dirt in the train. A faint second pair is a pinned "
            "reference (Ctrl+P).")
        self.s_tick_ref = pg.ScatterPlotItem(size=4, brush=pg.mkBrush((90, 163, 255, 70)), pen=None)
        self.s_tock_ref = pg.ScatterPlotItem(size=4, brush=pg.mkBrush((255, 157, 77, 70)), pen=None)
        self.s_tick = pg.ScatterPlotItem(size=4, brush=pg.mkBrush(TICK_C), pen=None)
        self.s_tock = pg.ScatterPlotItem(size=4, brush=pg.mkBrush(TOCK_C), pen=None)
        for it in (self.s_tick_ref, self.s_tock_ref, self.s_tick, self.s_tock):
            self.p_trace.addItem(it)
        plots.addWidget(self.p_trace)
        self._ref_m = None

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
        self.p_wave.setToolTip(
            "The beat waveform. In Average view the two dashed markers are the unlock "
            "and drop noises the amplitude calculation uses -- if they are not sitting "
            "on obvious peaks, the amplitude number is wrong and the sub-noise "
            "threshold needs adjusting. Live beat shows one raw beat; Mic shows the "
            "unprocessed pickup signal for judging coupling.")
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

        RATE_C = ACCENT          # blue -- left axis
        AMP_C = "#ffb648"        # amber -- right axis
        self.p_hist = pg.PlotWidget(title="Rate history  --  rate (blue, left) and amplitude (amber, right)")
        pi = self.p_hist.getPlotItem()
        pi.setLabel("bottom", "run time", units="min")
        pi.showGrid(x=True, y=True, alpha=0.2)
        la = pi.getAxis("left")
        la.setLabel("rate", units="s/day", color=RATE_C)
        la.setPen(RATE_C)
        la.setTextPen(RATE_C)
        self.c_hist = pi.plot(pen=pg.mkPen(RATE_C, width=2))

        # Amplitude rides a second view box sharing the x axis, drawn against a
        # right-hand scale coloured to match its own line.
        self._amp_vb = pg.ViewBox()
        pi.showAxis("right")
        pi.scene().addItem(self._amp_vb)
        ra = pi.getAxis("right")
        ra.linkToView(self._amp_vb)
        ra.setLabel("amplitude", units="deg", color=AMP_C)
        ra.setPen(AMP_C)
        ra.setTextPen(AMP_C)
        self._amp_vb.setXLink(pi)
        self.c_amp_hist = pg.PlotCurveItem(pen=pg.mkPen(AMP_C, width=2))
        self._amp_vb.addItem(self.c_amp_hist)

        def _sync_amp_vb():
            self._amp_vb.setGeometry(pi.getViewBox().sceneBoundingRect())
            self._amp_vb.linkedViewChanged(pi.getViewBox(), self._amp_vb.XAxis)
        pi.getViewBox().sigResized.connect(_sync_amp_vb)
        _sync_amp_vb()
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
        abrow = QtWidgets.QHBoxLayout()
        b = QtWidgets.QPushButton("Analyze and advise")
        b.setStyleSheet(
            f"QPushButton{{background:{ACCENT};color:#08101c;font-weight:bold;padding:8px;border-radius:6px;}}")
        b.clicked.connect(self._advise)
        abrow.addWidget(b, 1)
        b_gr = QtWidgets.QPushButton("Guided regulation...")
        b_gr.setStyleSheet(
            "QPushButton{background:#2a323e;color:#e8eef7;padding:8px;border-radius:6px;}")
        b_gr.clicked.connect(self._guided_regulation)
        abrow.addWidget(b_gr)
        b_cert = QtWidgets.QPushButton("Timing certificate...")
        b_cert.setStyleSheet(
            "QPushButton{background:#2a323e;color:#e8eef7;padding:8px;border-radius:6px;}")
        b_cert.clicked.connect(self._timing_certificate)
        abrow.addWidget(b_cert)
        al.addLayout(abrow)
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
        self._demag_delta = None

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
        self.chk_res_save = QtWidgets.QCheckBox("Save to the selected watch")
        self.chk_res_save.setChecked(True)
        self.chk_res_save.setToolTip(
            "When the run ends, file it in the history of the watch chosen in the\n"
            "Measure tab's Watch dropdown. Uncheck for a one-off run.")
        for wdg in (self.btn_res, self.spn_res_int, self.spn_res_hours,
                    b_res_exp, b_res_clr, self.chk_res_save):
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
        self.s_iso_out = pg.ScatterPlotItem(size=7, symbol="x", pen=pg.mkPen("#5a6472", width=1),
                                            brush=None)
        self.c_iso_fit = self.p_iso.plot(pen=pg.mkPen("#e8eef7", width=1, style=QtCore.Qt.DashLine))
        self.p_iso.addItem(self.s_iso)
        self.p_iso.addItem(self.s_iso_out)
        iso.addWidget(self.p_iso)
        self.txt_iso = QtWidgets.QTextBrowser()
        self.txt_iso.setMaximumWidth(360)
        self.txt_iso.setHtml("<p style='color:#8a94a4;font-family:Segoe UI'>"
                             "Run a power-reserve log. Once amplitude has fallen far enough "
                             "to see a spread, the isochronism slope, the beat-error / "
                             "amplitude link and the projected runway to 220 deg appear here.</p>")
        iso.addWidget(self.txt_iso)
        iso.setSizes([620, 360])
        isobar = QtWidgets.QHBoxLayout()
        self.chk_iso_nl = QtWidgets.QCheckBox("Non-linear (quadratic) isochronism fit")
        self.chk_iso_nl.setToolTip(
            "Fit rate-vs-amplitude with a curve instead of a straight line. Real\n"
            "isochronism error is rarely linear -- the curve shows the amplitude of\n"
            "least rate sensitivity, the best place to sit the balance when regulating.")
        self.chk_iso_nl.toggled.connect(self._update_iso)
        isobar.addWidget(self.chk_iso_nl)
        isobar.addStretch(1)
        isow = QtWidgets.QWidget()
        isov = QtWidgets.QVBoxLayout(isow)
        isov.setContentsMargins(0, 0, 0, 0)
        isov.addWidget(iso, 1)
        isov.addLayout(isobar)
        rl.addWidget(isow, 3)
        tabs.addTab(rw, "Power reserve")

        # ---- diagnostics ----
        dw = QtWidgets.QWidget()
        dl = QtWidgets.QVBoxLayout(dw)
        self.diag_tabs = QtWidgets.QTabWidget()
        dl.addWidget(self.diag_tabs)

        sig_w = QtWidgets.QWidget()
        sig_l = QtWidgets.QVBoxLayout(sig_w)
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
        sig_l.addLayout(live, 1)
        self.diag_tabs.addTab(sig_w, "Live signal")

        # -- stability sub-tab --
        stab_w = QtWidgets.QWidget()
        stab_l = QtWidgets.QVBoxLayout(stab_w)
        self.p_allan = pg.PlotWidget(
            title="Rate stability (Allan deviation) -- scatter left after averaging for tau")
        self.p_allan.setLabel("bottom", "averaging time tau", units="s")
        self.p_allan.setLabel("left", "rate deviation", units="s/d")
        self.p_allan.showGrid(x=True, y=True, alpha=0.25)
        self.p_allan.setLogMode(x=True, y=True)
        self.c_allan = self.p_allan.plot(
            pen=pg.mkPen("#4da3ff", width=2), symbol="o", symbolSize=6, symbolBrush="#4da3ff")
        self.c_allan_ref = self.p_allan.plot(
            pen=pg.mkPen("#5a6472", width=1, style=QtCore.Qt.DashLine))
        stab_l.addWidget(self.p_allan, 3)
        self.lbl_allan = QtWidgets.QLabel(
            "Listen for a minute or more, or run a power-reserve log, and the rate's "
            "stability curve builds here.")
        self.lbl_allan.setWordWrap(True)
        self.lbl_allan.setStyleSheet("color:#8a94a4;")
        stab_l.addWidget(self.lbl_allan, 1)

        self.p_inst = pg.PlotWidget(
            title="Instantaneous rate -- beat-by-beat, ~1 s smoothed; faint traces are the "
                  "last few windows")
        self.p_inst.setLabel("bottom", "time within window", units="s")
        self.p_inst.setLabel("left", "rate", units="s/d")
        self.p_inst.showGrid(x=True, y=True, alpha=0.25)
        self._inst_ghosts = []
        for age in range(1, 7):                       # oldest first, faintest
            alpha = int(28 + 12 * age)
            c = self.p_inst.plot(pen=pg.mkPen((90, 163, 255, alpha), width=1))
            self._inst_ghosts.append(c)
        self.c_inst = self.p_inst.plot(pen=pg.mkPen("#4da3ff", width=2))
        self.l_inst_mean = pg.InfiniteLine(
            angle=0, pen=pg.mkPen("#e8eef7", width=1, style=QtCore.Qt.DashLine))
        self.p_inst.addItem(self.l_inst_mean)
        stab_l.addWidget(self.p_inst, 3)
        self.diag_tabs.addTab(stab_w, "Stability")

        # -- faults sub-tab --
        flt_w = QtWidgets.QWidget()
        flt_l = QtWidgets.QVBoxLayout(flt_w)
        self.p_fault = pg.PlotWidget(title="Timing residual spectrum -- peaks are repeating faults")
        self.p_fault.setLabel("bottom", "period", units="beats")
        self.p_fault.setLabel("left", "swing", units="ms")
        self.p_fault.showGrid(x=True, y=True, alpha=0.25)
        self.p_fault.setLogMode(x=True, y=False)
        self.c_fault = self.p_fault.plot(pen=pg.mkPen("#4da3ff", width=1.5))
        flt_l.addWidget(self.p_fault, 2)
        self.txt_fault = QtWidgets.QTextBrowser()
        flt_l.addWidget(self.txt_fault, 2)
        b_fault = QtWidgets.QPushButton("Scan for periodic faults")
        b_fault.setStyleSheet(
            f"QPushButton{{background:{ACCENT};color:#08101c;font-weight:bold;"
            "padding:8px;border-radius:6px;}")
        b_fault.clicked.connect(self._scan_faults)
        flt_l.addWidget(b_fault)

        self.txt_sig = QtWidgets.QTextBrowser()
        self.txt_sig.setHtml(
            "<p style='color:#8a94a4;font-family:Segoe UI'>Press below to weigh the "
            "current reading, the captured positions, the last fault scan and any "
            "demagnetiser A/B against the known fault signatures.</p>")
        flt_l.addWidget(self.txt_sig, 2)
        b_sig = QtWidgets.QPushButton("Match fault signatures")
        b_sig.setStyleSheet(
            "QPushButton{background:#2a323e;color:#e8eef7;padding:8px;border-radius:6px;}")
        b_sig.clicked.connect(self._match_signatures)
        flt_l.addWidget(b_sig)
        self.diag_tabs.addTab(flt_w, "Faults")
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

        self.cmb_dev.addItem("-- Phone / browser pickup (over Wi-Fi) --", "NET")
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
            self._phone_run = self._phone_starting
            self._phone_starting = False
            if self._phone_run:
                # A phone-started run must not inherit positions captured in an
                # earlier desktop session -- that is how one run's data ends up
                # filed against several watches.
                self._end_session()
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
                elif dev == "NET":
                    nr = getattr(self, "_net_recorder", None)
                    pinned = self._net_server_pinned
                    # Only rebuild a sample-rate-mismatched server if it is NOT
                    # the pinned Phone Portal server -- tearing that down drifts
                    # the port and breaks the phone's URL mid-session.
                    if (nr is not None and nr.running
                            and nr.samplerate != sr and not pinned):
                        nr.stop()
                        self._net_recorder = nr = None
                    self._net_fresh = nr is None or not nr.running
                    nr = self._ensure_net_server(pinned=pinned)
                    if nr.samplerate != sr:
                        self.status.showMessage(
                            f"Phone pickup is running at {nr.samplerate} Hz -- "
                            f"it keeps that rate while it is the input.", 8000)
                    if not self._net_fresh:
                        nr.clear()
                        nr.peak = 0.0
                        nr.gain = 1.0
                    self._phone_last_save = ""
                    self.recorder = nr
                else:
                    self.recorder = audio.Recorder(
                        device=dev, samplerate=sr, buffer_seconds=buf)
                if dev != "NET":
                    self.recorder.start()
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Audio error", str(e))
                self.recorder = None
                return
            self.recorder.agc_enabled = self.act_agc.isChecked()
            self._clip_count0 = 0
            if dev == "NET":
                got_port = getattr(self.recorder, "port", 0)
                if got_port and got_port != self._settings_get("phone_port", 8477):
                    self._settings_set("phone_port", got_port)
                self._refresh_phone_page()
                if (self._net_fresh and not self._phone_run
                        and self.stack.currentIndex() != 4):
                    self._goto_page(4)          # show the QR / URL
                    self.status.showMessage(
                        "Phone pickup server started -- scan the QR on your phone.", 9000)
                elif self._net_fresh and self._phone_run:
                    self.status.showMessage(
                        "Phone pickup server started for a remote run.", 9000)
                elif not self._net_fresh:
                    self.status.showMessage(
                        f"Phone pickup running at {getattr(self.recorder, 'url', '')}", 9000)
            note = getattr(self.recorder, "opened_note", "")
            if note:
                self.status.showMessage(note, 12000)
            else:
                self._nudge_stale_calibration()
            self._pending_buffer = 0
            self.worker.recorder = self.recorder
            self._rate_hist = []
            self._allan_hist = []
            self._amp_hist = []
            self._be_hist = []
            self._listen_t0 = time.time()
            self._rate_last_update = None
            self._cap_frames = None
            self._stream_restarts = 0
            self.c_hist.setData([], [])
            self.c_amp_hist.setData([], [])
            self.c_be_hist.setData([], [])
            self._inst_hist = []
            self.c_inst.setData([], [])
            for c in self._inst_ghosts:
                c.setData([], [])
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
            if self.btn_res.isChecked() and not self._closing:
                # A power-reserve log only means anything while the app is
                # listening. Stopping the stream ends it too -- finalising and
                # filing whatever was captured, rather than leaving it frozen.
                self.btn_res.setChecked(False)   # -> _toggle_reserve(False)
            self.worker.recorder = None
            # A result may already be in flight from the worker thread. Clearing
            # the session clock stops it appending a stale point to the history
            # plots (all of which gate on _listen_t0).
            self._listen_t0 = None
            if self.recorder:
                if getattr(self.recorder, "is_recording", False):
                    p = self.recorder.stop_recording()
                    if p:
                        self.status.showMessage(f"WAV saved: {os.path.basename(p)}", 8000)
                # The phone server is kept alive across runs so the URL and the
                # phone's connection stay put -- it is torn down on app close or
                # when the input device is changed.
                if self.recorder is not getattr(self, "_net_recorder", None):
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
            if not self._closing:
                self._maybe_stop_net_server()

    def _reading_summary(self, m):
        if m is None or not m.ok:
            return "No usable reading."
        amp = "n/a" if m.amplitude != m.amplitude else f"{m.amplitude:.0f} deg"
        be = "n/a" if m.beat_error != m.beat_error else f"{m.beat_error:.2f} ms"
        s = (f"Rate  {m.rate:+.1f} s/day\nAmplitude  {amp}\nBeat error  {be}\n"
             f"Beat rate  {m.detected_bph} bph\n\nmatch {m.quality:.2f}, "
             f"{3 + m.extra_peaks:.1f} noises/beat")
        if m.nominal_bph and m.detected_bph != m.nominal_bph:
            s += "\n\nBeat rate does not match the caliber -- the rate figure is not valid."
        elif m.quality < 0.6:
            s += "\n\nLow template match -- treat these numbers with caution."
        return s

    def _offer_after_listening(self):
        """Same four options after a continuous session, if there is anything to file."""
        m = self.last
        have_reading = m is not None and m.ok and m.rate == m.rate
        if getattr(self, "_phone_run", False):
            if not have_reading and not self.readings:
                self._phone_finish("Run finished -- no steady reading was captured.",
                                   None, ok=False)
            else:
                self._phone_finish(self._reading_summary(m), m)
            return
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
            msg = (f"{'Stopped' if stopped_early else 'Completed'} after {elapsed:.0f} s, "
                   f"but the capture could not be analysed.\n\n"
                   + (m.message if m else "Not enough usable audio."))
            if getattr(self, "_phone_run", False):
                self._suppress_finish = True
                self._toggle_listen(False)
                self._suppress_finish = False
                self._set_go(False)
                self._phone_finish(msg, None, ok=False)
            else:
                QtWidgets.QMessageBox.warning(self, "Run finished", msg)
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
        elif m.quality < 0.6:
            lines.append(f"\nLow template match ({m.quality:.2f}) -- the beats are not "
                         f"repeating cleanly. Treat these numbers with caution before "
                         f"filing them.")

        if getattr(self, "_phone_run", False):
            self._phone_finish("\n".join(lines), m)
            return
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
        nr = getattr(self, "_net_recorder", None)
        if nr is not None:
            import json
            for cmd in nr.drain_commands():
                try:
                    self._handle_phone_cmd(cmd)
                except Exception:
                    pass
            try:
                nr.state_json = json.dumps(self._phone_state())
            except Exception:
                pass
        if self.stack.currentIndex() == 4 and \
                time.monotonic() - getattr(self, "_phone_page_t", 0) > 1.0:
            self._phone_page_t = time.monotonic()
            self._refresh_phone_page()

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

            clips = getattr(self.recorder, "clips", 0)
            new_clips = clips - getattr(self, "_clip_count0", clips)
            self._clip_count0 = clips
            gain = getattr(self.recorder, "gain", 1.0)
            if hasattr(self, "lbl_live"):
                if self.act_agc.isChecked() and new_clips > 0:
                    self.lbl_live.setText(
                        "input is CLIPPING -- lower the level in Windows sound settings "
                        "or move the pickup back; amplitude will read wrong")
                    self.lbl_live.setStyleSheet("color:#ff5d5d;font-size:12px;")
                elif self.act_agc.isChecked() and p > 0.005 and p < 0.03 and gain > 8:
                    self.lbl_live.setText(
                        f"input very quiet (auto-gain at {gain:.0f}x) -- raise the level "
                        f"for a cleaner reading")
                    self.lbl_live.setStyleSheet("color:#ffb648;font-size:12px;")

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
        if rec is None or self.cmb_dev.currentData() in ("SIM", "NET") or self._run_t0 is not None:
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
        if dev in ("SIM", "NET") or self.recorder is None:
            return
        sr = self.recorder.samplerate
        buf = getattr(self.recorder, "buffer_seconds", self.recorder.n / sr)
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

        if m.inst_rate.size >= 4:
            t = np.asarray(m.inst_rate_t, dtype=float)
            y = np.asarray(m.inst_rate, dtype=float)
            ghosts = getattr(self, "_inst_hist", [])          # previous traces, old -> new
            n = len(self._inst_ghosts)
            for i, c in enumerate(self._inst_ghosts):
                j = i - (n - len(ghosts))                     # right-align newest to last slot
                if 0 <= j < len(ghosts):
                    c.setData(ghosts[j][0], ghosts[j][1])
                else:
                    c.setData([], [])
            self.c_inst.setData(t, y)
            self.l_inst_mean.setPos(float(np.nanmean(y)))
            self._inst_hist = (ghosts + [(t, y)])[-n:]

        if time.monotonic() - getattr(self, "_allan_last", 0.0) > 5.0:
            self._allan_last = time.monotonic()
            self._update_allan()

    def _update_allan(self):
        """Rate stability curve from whatever rate history is on hand."""
        src = self._allan_hist if len(self._allan_hist) >= 32 else []
        if not src:
            self.c_allan.setData([], [])
            self.c_allan_ref.setData([], [])
            return
        a = np.asarray(src, dtype=float)
        tau, dev = allan_deviation(a[:, 0], a[:, 1])
        if tau.size < 3:
            self.c_allan.setData([], [])
            self.c_allan_ref.setData([], [])
            self.lbl_allan.setText(
                "Not enough steady readings yet -- keep listening (about a minute "
                "clean) and the curve will fill in.")
            return
        self.c_allan.setData(tau, dev)
        # White-FM reference: tau**-0.5 anchored at the first point.
        ref = dev[0] * np.sqrt(tau[0] / tau)
        self.c_allan_ref.setData(tau, ref)

        i_min = int(np.argmin(dev))
        floor, floor_tau = float(dev[i_min]), float(tau[i_min])
        tail_falling = dev[-1] < dev[max(0, len(dev) - 2)] * 0.85
        if i_min >= len(dev) - 2 and tail_falling:
            self.lbl_allan.setText(
                f"Still white-noise limited at tau = {tau[-1]:.0f} s (down to "
                f"+/-{dev[-1]:.2f} s/d). The reading is averaging down as expected -- "
                f"a longer capture will tighten it further. Dashed line is the ideal "
                f"tau**-0.5 slope.")
        else:
            self.lbl_allan.setText(
                f"Rate stops averaging down past about tau = {floor_tau:.0f} s, where "
                f"it floors at +/-{floor:.2f} s/d. That is the watch itself wandering, "
                f"not measurement noise: expect the rate to move by roughly that much "
                f"between captures however carefully it is regulated. Dashed line is the "
                f"tau**-0.5 slope a noise-limited reading would follow.")

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

        corr = self._rate_correction()
        if corr and m.rate == m.rate:
            m.rate += corr        # propagates to the readout, history, reserve, reports

        mismatch = m.nominal_bph is not None and m.detected_bph != m.nominal_bph
        # A reading is only worth showing as a number if the beats matched their
        # own template, the beat rate agrees with the caliber, and we are not
        # resolving the room. Otherwise hold the last good numbers, greyed --
        # a stale-but-real figure beats a fresh garbage one.
        trustworthy = (m.quality >= 0.6 and not mismatch and m.rate == m.rate
                       and m.extra_peaks <= 1.5)
        # Holding the readouts on a weak signal is opt-in (View menu, off by
        # default). When off, the current number is always shown.
        hold = (not trustworthy) and self.act_gate.isChecked()

        # The trace, waveform and diagnostics always update -- you want to see
        # the mess to understand why the numbers are being withheld.
        xt, yt, xk, yk = trace_points(m, m.nominal_bph or m.detected_bph,
                                      float(self.spn_trace.value()))
        self.s_tick.setData(xt, yt)
        self.s_tock.setData(xk, yk)
        if self._ref_m is not None:
            self._draw_ref_trace()
        half = self.spn_trace.value() / 2
        self.p_trace.setXRange(-half, half, padding=0.02)
        if self._wave_mode != "Mic":
            self._render_wave(m)
        self._update_diag(m)

        if not hold:
            if trustworthy:
                self._last_good = m
            good = m.quality > 0.8
            self.r_rate.set(f"{m.rate:+.1f}", "#e8eef7" if abs(m.rate) < 15 else "#ffb648")
            unit = "seconds / day"
            if m.rate_ci == m.rate_ci:
                unit += f"   ±{m.rate_ci:.1f} (95%)"
            if corr:
                unit += f"   [clock {corr:+.1f}]"
            self.r_rate.u.setText(unit)
            self.r_amp.set("--" if m.amplitude != m.amplitude else f"{m.amplitude:.0f}",
                           "#ff5d5d" if m.amplitude > 330 else
                           ("#ffb648" if m.amplitude < 220 else "#e8eef7"))
            if m.amplitude == m.amplitude and m.amplitude >= 355:
                self.r_amp.u.setText("REBANKING -- balance is knocking; reduce mainspring power")
            elif m.amplitude == m.amplitude and m.amplitude > 330:
                self.r_amp.u.setText("degrees -- approaching the knocking region")
            else:
                self.r_amp.u.setText("degrees")
            self.r_be.set("--" if m.beat_error != m.beat_error else f"{m.beat_error:.2f}",
                          "#ff5d5d" if m.beat_error > 1.2 else
                          ("#ffb648" if m.beat_error > 0.6 else "#57d38c"))
            self.r_be.u.setText("milliseconds")
            self.r_bph.set(str(m.detected_bph),
                           "#ff5d5d" if mismatch else ("#e8eef7" if good else "#ffb648"))

            if self._ref_m is not None:
                r = self._ref_m
                if m.rate == m.rate and r.rate == r.rate:
                    self.r_rate.u.setText(self.r_rate.u.text() +
                                          f"   vs ref {m.rate - r.rate:+.1f}")
                if m.amplitude == m.amplitude and r.amplitude == r.amplitude:
                    self.r_amp.u.setText(self.r_amp.u.text() +
                                         f"   vs ref {m.amplitude - r.amplitude:+.0f}")
                if m.beat_error == m.beat_error and r.beat_error == r.beat_error:
                    self.r_be.u.setText(self.r_be.u.text() +
                                        f"   vs ref {m.beat_error - r.beat_error:+.2f}")

            if m.rate == m.rate and self._listen_t0 is not None:
                el = time.time() - self._listen_t0
                self._rate_hist.append((el, float(m.rate)))
                # Allan deviation needs an evenly-sampled series -- feed it the
                # raw (undecimated) rate points, not the age-thinned plot data,
                # or its long-tau end is computed over interpolated gaps.
                self._allan_hist.append((el, float(m.rate)))
                if len(self._allan_hist) > 4000:
                    self._allan_hist = self._allan_hist[-4000:]
                self._rate_hist = self._decimate_rate_hist(self._rate_hist)
                a = np.asarray(self._rate_hist, dtype=float)
                self.c_hist.setData(a[:, 0] / 60.0, a[:, 1])
                self._rate_last_update = time.monotonic()
                if m.amplitude == m.amplitude:
                    self._amp_hist.append((el, float(m.amplitude)))
                    self._amp_hist = self._decimate_rate_hist(self._amp_hist)
                    ah = np.asarray(self._amp_hist, dtype=float)
                    self.c_amp_hist.setData(ah[:, 0] / 60.0, ah[:, 1])
        else:
            why = ("beat rate does not match the caliber" if mismatch else
                   f"template match only {m.quality:.2f}" if m.quality < 0.6 else
                   f"{3 + m.extra_peaks:.1f} noises per beat -- hearing the room"
                   if m.extra_peaks > 1.5 else "no stable rate")
            for rw in (self.r_rate, self.r_amp, self.r_be, self.r_bph):
                rw.v.setStyleSheet("color:#5a6472;border:none;")
            self.r_rate.u.setText(f"held -- {why}")

        self._update_regulation(m)
        self._check_stable(m)
        self._log_reserve(None if hold else m)

        if hasattr(self, "lbl_live"):
            if hold:
                self.lbl_live.setText(f"reading held: {why}")
                self.lbl_live.setStyleSheet("color:#ffb648;font-size:12px;")
            else:
                warn = mismatch or m.extra_peaks > 1.0 or m.quality < 0.6
                self.lbl_live.setText(
                    f"{3 + m.extra_peaks:.1f} noises/beat  |  match {m.quality:.2f}")
                self.lbl_live.setStyleSheet(
                    f"color:{'#ffb648' if warn else '#5a6472'};font-size:12px;")

        bits = [f"{m.beats} beats", f"SNR {m.snr_db:.0f} dB", f"match {m.quality:.2f}"]
        if hold:
            bits.insert(0, f"HELD ({why})")
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
            self._reserve = []
            w = self._current_watch()
            self._res_watch_id = (w.id if (w and self.chk_res_save.isChecked()) else None)
            self.btn_res.setText("Stop power reserve log")
            hrs = self.spn_res_hours.value()
            dest = (f"When it ends it will be filed to {w.label}'s history.\n\n"
                    if self._res_watch_id else
                    ("No watch is selected in the Measure tab, so this run will not be "
                     "saved to a watch -- pick one there first if you want that.\n\n"
                     if self.chk_res_save.isChecked() else ""))
            QtWidgets.QMessageBox.information(
                self, "Power reserve started",
                (f"Sampling every {self.spn_res_int.value()} s"
                 + (f" until {hrs:g} hours have elapsed.\n\n" if hrs else
                    ", until you press stop.\n\n")
                 + dest
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
        held = " (waiting for a trustworthy reading)" if m is None else ""
        # End on the target even between scheduled samples, so a 48 h run does
        # not overshoot by one interval; but only on a trustworthy final point.
        if target_h and el >= target_h * 3600.0 and m is not None:
            self._reserve.append((el, m.rate, m.amplitude, m.beat_error))
            self._redraw_reserve()
            self._reserve_finished(stopped_early=False)
            self.btn_res.setChecked(False)
            return
        if el < self._res_next or m is None:
            # Keep the label moving between samples -- and while a bad signal is
            # holding the reading, so a working run does not look crashed.
            self.lbl_res.setText(
                f"{len(self._reserve)} samples | {el/3600:.2f} h elapsed | "
                f"next in {max(0, self._res_next - el):.0f} s"
                + (f" | {max(0.0, target_h - el/3600):.2f} h remaining" if target_h else "")
                + held)
            return
        # Check the target here as well as at sample time, or a 48 hour run
        # with a 5 minute interval could overshoot by five minutes.
        self._res_next = el + float(self.spn_res_int.value())
        self._reserve.append((el, m.rate, m.amplitude, m.beat_error))
        self._redraw_reserve()
        if target_h and el >= target_h * 3600.0:
            self._reserve_finished(stopped_early=False)
            self.btn_res.setChecked(False)

    def _reserve_finished(self, stopped_early: bool):
        if getattr(self, "_res_done", False):
            return                      # already finalised (e.g. reached target, then Stop)
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
        st = reserve_analytics(
            self._reserve,
            iso_model="quadratic" if self.chk_iso_nl.isChecked() else "linear")
        lines.extend(st.verdict)

        saved_to = self._save_reserve_to_watch(st, stopped_early)
        if saved_to:
            lines.append(f"Filed to {saved_to}'s history.")
        lines.append("\nExport CSV keeps the raw samples; the Isochronism panel below "
                     "keeps the rate-vs-amplitude plot.")
        self.lbl_res.setText(lines[0])
        self._update_iso()
        QtWidgets.QApplication.beep()
        QtWidgets.QMessageBox.information(self, "Power reserve run finished",
                                          "\n\n".join(lines))

    def _save_reserve_to_watch(self, st, stopped_early):
        wid = getattr(self, "_res_watch_id", None)
        self._res_watch_id = None
        if not wid or len(self._reserve) < 3:
            return None
        w = self.collection.watches.get(wid)
        if not w:
            return None
        c = self._current_caliber()
        rec = coll.ReserveRecord(
            when=datetime.now().isoformat(timespec="seconds"),
            caliber_key=(c.key if c else w.caliber_key),
            lift_angle=float(self.spn_lift.value()),
            interval_s=int(self.spn_res_int.value()),
            stopped_early=bool(stopped_early),
            hours=float(st.hours),
            samples=[[round(el, 1), r, a, b] for el, r, a, b in self._reserve],
            amp_first=float(st.amp_first), amp_last=float(st.amp_last),
            hours_to_220=float(st.hours_to_220), hours_to_200=float(st.hours_to_200),
            iso_slope=float(st.iso_slope), iso_span=float(st.iso_span),
            be_slope=float(st.be_slope))
        w.reserves.append(rec)
        self.collection.save()
        self._refresh_watches(w.id)
        return w.label

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
            self.s_iso_out.setData([], [])
            self.c_iso_fit.setData([], [])
            self.txt_iso.setHtml("<p style='color:#8a94a4;font-family:Segoe UI'>"
                                 "Run a power-reserve log; the isochronism analysis appears "
                                 "here once amplitude has fallen far enough to see a spread.</p>")
            return
        model = "quadratic" if self.chk_iso_nl.isChecked() else "linear"
        st = reserve_analytics(self._reserve, iso_model=model)
        self._postwind_kick = (st.kick_deg_per_h if st.kick_deg_per_h == st.kick_deg_per_h
                               else None)
        a = np.array(self._reserve, dtype=float) if self._reserve else np.zeros((0, 4))
        if st.iso_coef:
            # Once the fit exists, colour the points by whether it used them.
            self.s_iso.setData(list(st.iso_in[0]), list(st.iso_in[1]))
            self.s_iso_out.setData(list(st.iso_out[0]), list(st.iso_out[1]))
            xin = np.asarray(st.iso_in[0], dtype=float)
            xs = np.linspace(xin.min(), xin.max(), 80 if len(st.iso_coef) > 2 else 2)
            self.c_iso_fit.setData(xs, np.polyval(st.iso_coef, xs))
        else:
            if a.shape[0]:
                m = np.isfinite(a[:, 2]) & np.isfinite(a[:, 1])
                self.s_iso.setData(a[m, 2], a[m, 1])
            else:
                self.s_iso.setData([], [])
            self.s_iso_out.setData([], [])
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
        self._demag_delta = (float(dr), float(da))
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
        train = getattr(c, "train", None) if c else None
        bph = m.nominal_bph or m.detected_bph
        rep = faults.analyze_periodicity(m.index, m.resid, bph, escape_teeth=teeth,
                                         train=train)
        self._fault_report = rep
        if rep.freqs is not None and rep.power is not None and rep.freqs.size:
            o = np.argsort(rep.freqs)
            self.c_fault.setData(rep.freqs[o], rep.power[o])

        for ln in getattr(self, "_fault_markers", []):
            self.p_fault.removeItem(ln)
        self._fault_markers = []
        for pb, label, in_horizon in rep.markers:
            pen = pg.mkPen("#5a6472" if in_horizon else "#3a424e", width=1,
                           style=QtCore.Qt.DashLine)
            ln = pg.InfiniteLine(pos=np.log10(pb), angle=90, pen=pen, movable=False,
                                 label=label + ("" if in_horizon else " (beyond capture)"),
                                 labelOpts={"color": "#8a94a4", "position": 0.9})
            self.p_fault.addItem(ln)
            self._fault_markers.append(ln)

        html = [f"<div style='font-family:Segoe UI,sans-serif;color:#c8d0dc'>"]
        gears = (", ".join(f"{k} {v:g}s" for k, v in train.items()) if train
                 else "fourth wheel assumed at 60 s, third wheel estimated")
        html.append(f"<p style='color:#8a94a4'>Escape wheel assumed to have {teeth} teeth "
                    f"(one revolution = {2*teeth} beats, {2*teeth*3600.0/bph:.2f} s). "
                    f"Train: {gears}. Dashed lines mark where each part's fault would "
                    f"land.</p>")
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

    def _match_signatures(self):
        from . import signatures as sigs
        m = self.last
        if m is None or not m.ok:
            self.txt_sig.setPlainText("Need a valid current reading first.")
            return
        c = self._current_caliber()
        ctx = sigs.SymptomContext(
            rate=m.rate, amplitude=m.amplitude, beat_error=m.beat_error,
            amplitude_spread=m.amplitude_spread, extra_peaks=m.extra_peaks,
            quality=m.quality, snr_db=m.snr_db,
            amp_full_wind=getattr(c, "amp_full_wind", (250.0, 315.0)) if c else (250.0, 315.0),
            bph=m.nominal_bph or m.detected_bph,
            positions={r.position: (r.rate, r.amplitude, r.beat_error)
                       for r in self.readings if r.position},
            demag_delta=self._demag_delta)
        rep = getattr(self, "_fault_report", None)
        if rep is not None and rep.periods:
            ctx.periodic = [(p.component, p.amplitude_ms, p.snr) for p in rep.periods]
        if self._reserve:
            st = reserve_analytics(self._reserve)
            ctx.iso_span = st.iso_span
        kick = getattr(self, "_postwind_kick", None)
        if kick is not None:
            ctx.kick_deg_per_h = kick

        matches = sigs.match(ctx)
        html = ["<div style='font-family:Segoe UI,sans-serif;color:#c8d0dc;font-size:12px'>"]
        if not matches:
            html.append("<p>Nothing lines up with a stored fault signature. The reading "
                        "looks unremarkable, or there is not enough of it -- capture the "
                        "six positions and run a fault scan for a fuller picture.</p>")
        for s in matches:
            pct = int(round(s.confidence * 100))
            bar = int(round(s.confidence * 120))
            col = "#ff5d5d" if s.confidence > 0.65 else "#ffb648" if s.confidence > 0.4 else "#7fb2ff"
            html.append(
                f"<p style='margin:10px 0 2px'><b style='color:{col}'>{s.name}</b> "
                f"<span style='color:#8a94a4'>&nbsp;{pct}% match</span><br>"
                f"<span style='display:inline-block;height:4px;width:{bar}px;"
                f"background:{col}'></span><br>"
                f"<span style='color:#b6bfcc'>{s.why}.</span><br>"
                f"<span style='color:#8a94a4'>Check: {s.check}</span></p>")
        html.append("<p style='color:#5a6472;margin-top:12px'>Signatures are weighted "
                    "guesses from the numbers, not a diagnosis. Confirm by eye before "
                    "you touch anything.</p></div>")
        self.txt_sig.setHtml("".join(html))

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

    def _whats_normal(self):
        c = self._current_caliber()
        if not c:
            QtWidgets.QMessageBox.information(
                self, "Movement info", "Pick a specific caliber first.")
            return
        from .calibers import whats_normal
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle(f"Movement info -- {c.label}")
        dlg.setMinimumSize(480, 460)
        lay = QtWidgets.QVBoxLayout(dlg)
        tb = QtWidgets.QTextBrowser()
        tb.setMarkdown(whats_normal(c))
        lay.addWidget(tb)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        bb.rejected.connect(dlg.reject)
        bb.accepted.connect(dlg.accept)
        lay.addWidget(bb)
        dlg.exec()

    def _caliber_changed(self):
        c = self._current_caliber()
        if not c:
            return  # a group header, not a caliber
        self.spn_lift.setValue(c.lift_angle)
        i = self.cmb_bph.findData(c.bph) if c.bph else 0
        self.cmb_bph.setCurrentIndex(i if i >= 0 else 0)
        reg = advisor.REGULATOR_LABELS.get(c.regulator, c.regulator).split(".")[0]
        self.btn_whatsnormal.setToolTip(
            f"{c.label}: {reg}. Press for the full caliber reference -- lift-angle "
            f"source, expected figures, service interval, weak points, equivalents.")
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
        if hasattr(self, "cmb_wear_watch"):
            keep = self.cmb_wear_watch.currentData()
            self.cmb_wear_watch.blockSignals(True)
            self.cmb_wear_watch.clear()
            for w in self.collection.sorted_watches():
                self.cmb_wear_watch.addItem(w.label, w.id)
            j = self.cmb_wear_watch.findData(select_id or keep)
            self.cmb_wear_watch.setCurrentIndex(max(0, j))
            self.cmb_wear_watch.blockSignals(False)
        if hasattr(self, "lbl_now"):
            self.lbl_now.setText(
                f"Testing:  {self.cmb_watch.currentText()}"
                if self.cmb_watch.currentData() else "No watch selected")

    def _list_thumb(self, path, px, fallback=None):
        """
        A fixed px-by-px thumbnail: the photo scaled to fit without distortion
        and centred on a white square, so the list reads as an even column
        whatever shape the source images are. With no photo, `fallback` (a
        Watch) gets a schematic illustration; failing that, a plain white
        square so the text still lines up.
        """
        dpr = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0
        dpr = dpr or 1.0
        _schematic_pixmap._dpr = dpr
        n = max(1, int(round(px * dpr)))
        canvas = QtGui.QPixmap(n, n)
        canvas.setDevicePixelRatio(dpr)
        canvas.fill(QtGui.QColor("white"))
        src = QtGui.QPixmap(path) if path else QtGui.QPixmap()
        if src.isNull() and fallback is not None:
            src = _schematic_pixmap(fallback, px) or QtGui.QPixmap()
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
            it.setIcon(QtGui.QIcon(self._list_thumb(
                self.collection.photo_path(w), px, fallback=w)))
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
        self._publish_phone_watches()

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
        thumb = self._list_thumb(p, 148, fallback=w)
        if not thumb.isNull():
            self.lbl_wphoto.setPixmap(thumb)
            self.lbl_wphoto.setToolTip("" if p else "Schematic -- add a photo with Edit")
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

        self._fill_service_table(w)
        self._fill_reserve_table(w)
        self._fill_wear_table(w)
        self._fill_docs_table(w)

    def _fill_docs_table(self, w):
        self._docs_watch_id = w.id
        self.tbl_docs.setRowCount(0)
        for d in sorted(w.documents, key=lambda x: x.get("added", ""), reverse=True):
            r = self.tbl_docs.rowCount()
            self.tbl_docs.insertRow(r)
            for cix, v in enumerate([d.get("kind", ""), d.get("name", d.get("file", "")),
                                     d.get("added", "")[:10], d.get("note", "")]):
                self.tbl_docs.setItem(r, cix, QtWidgets.QTableWidgetItem(str(v)))

    def _doc_add(self):
        w = self._current_watch()
        if not w:
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Add document", "",
            "Documents & images (*.pdf *.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff "
            "*.txt *.md *.doc *.docx)")
        if not path:
            return
        kind, ok = QtWidgets.QInputDialog.getItem(
            self, "Document kind", "What is this?", coll.DOCUMENT_KINDS, 0, False)
        if not ok:
            return
        note, _ = QtWidgets.QInputDialog.getText(self, "Note", "Optional note:")
        try:
            stored = self.collection.store_document(w.id, path)
        except OSError as e:
            QtWidgets.QMessageBox.warning(self, "Documents", f"Could not copy: {e}")
            return
        w.documents.append({
            "file": stored, "kind": kind, "name": os.path.basename(path),
            "added": datetime.now().isoformat(timespec="seconds"), "note": note.strip()})
        self.collection.save()
        self._fill_docs_table(w)

    def _doc_selected(self):
        wid = getattr(self, "_docs_watch_id", None)
        w = self.collection.watches.get(wid) if wid else None
        r = self.tbl_docs.currentRow()
        if not w or r < 0:
            return None, None
        docs = sorted(w.documents, key=lambda x: x.get("added", ""), reverse=True)
        return (w, docs[r]) if r < len(docs) else (w, None)

    # Types we are happy to hand straight to the OS viewer. Anything else
    # (.doc/.docx/.md/...) opens in an app that can run macros or scripts, so
    # confirm first -- the file may have come from a repair shop by email.
    _SAFE_DOC_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp",
                     ".tif", ".tiff", ".gif", ".txt"}

    def _open_document(self, path):
        if not path or not os.path.exists(path):
            QtWidgets.QMessageBox.warning(self, "Documents", "That file is missing.")
            return
        ext = os.path.splitext(path)[1].lower()
        if ext not in self._SAFE_DOC_EXT:
            if QtWidgets.QMessageBox.question(
                    self, "Open document",
                    f"Open {os.path.basename(path)} in its default application? "
                    f"Only do this if you trust where the file came from."
                    ) != QtWidgets.QMessageBox.Yes:
                return
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))

    def _doc_open(self):
        _w, d = self._doc_selected()
        if not d:
            return
        self._open_document(self.collection.document_path(d.get("file", "")))

    def _doc_delete(self):
        w, d = self._doc_selected()
        if not w or not d:
            return
        try:
            p = self.collection.document_path(d.get("file", ""))
            if p:
                os.remove(p)
        except OSError:
            pass
        w.documents = [x for x in w.documents if x is not d]
        self.collection.save()
        self._fill_docs_table(w)

    def _fill_wear_table(self, w):
        self.tbl_wear.setRowCount(0)
        self._wear_watch_id = w.id
        checks = sorted(w.wear_checks, key=lambda c: c.get("when", ""), reverse=True)
        for c in checks:
            r = self.tbl_wear.rowCount()
            self.tbl_wear.insertRow(r)
            try:
                days = ((datetime.fromisoformat(c["when"]) -
                         datetime.fromisoformat(c["set_when"])).total_seconds() / 86400.0)
                rate = f"{c['off_seconds'] / days:+.1f}" if days > 0.02 else "--"
            except Exception:
                rate = "--"
            for cix, v in enumerate([c.get("when", "")[:16].replace("T", " "),
                                     c.get("set_when", "")[:16].replace("T", " "),
                                     f"{c.get('off_seconds', 0):+.0f}", rate]):
                self.tbl_wear.setItem(r, cix, QtWidgets.QTableWidgetItem(str(v)))
        series = coll.wear_rate_series(w)
        if series:
            self.c_wear.setData([d.timestamp() for d, _ in series], [r for _, r in series])
        else:
            self.c_wear.setData([], [])
        bench = [h.mean_rate for h in w.history if h.mean_rate == h.mean_rate]
        if bench:
            self.l_wear_bench.setPos(float(np.mean(bench)))
            self.l_wear_bench.setVisible(True)
        else:
            self.l_wear_bench.setVisible(False)

    def _wear_delete(self):
        wid = getattr(self, "_wear_watch_id", None)
        w = self.collection.watches.get(wid) if wid else None
        r = self.tbl_wear.currentRow()
        if not w or r < 0:
            return
        checks = sorted(w.wear_checks, key=lambda c: c.get("when", ""), reverse=True)
        if r >= len(checks):
            return
        target = checks[r]
        w.wear_checks = [c for c in w.wear_checks if c is not target]
        self.collection.save()
        self._fill_wear_table(w)

    def _fill_reserve_table(self, w):
        self.tbl_res_hist.setRowCount(0)
        for rec in sorted(w.reserves, key=lambda x: x.when, reverse=True):
            r = self.tbl_res_hist.rowCount()
            self.tbl_res_hist.insertRow(r)
            amp = (f"{rec.amp_first:.0f} -> {rec.amp_last:.0f}"
                   if rec.amp_first == rec.amp_first else "--")
            to220 = f"{rec.hours_to_220:.0f} h" if rec.hours_to_220 == rec.hours_to_220 else "--"
            if rec.iso_span == rec.iso_span:
                mag = abs(rec.iso_span)
                iso = (f"{'good' if mag < 4 else 'fair' if mag < 12 else 'poor'} "
                       f"({rec.iso_span:+.1f} s/d)")
            else:
                iso = "--"
            note = rec.notes or ("stopped early" if rec.stopped_early else "")
            for cix, v in enumerate([rec.when[:16].replace("T", " "),
                                     f"{rec.hours:.1f}" if rec.hours == rec.hours else "--",
                                     amp, to220, iso, note]):
                self.tbl_res_hist.setItem(r, cix, QtWidgets.QTableWidgetItem(str(v)))

    def _current_reserve(self):
        w = self._current_watch()
        if not w or not w.reserves:
            return None, None
        ordered = sorted(w.reserves, key=lambda x: x.when, reverse=True)
        r = self.tbl_res_hist.currentRow()
        return w, (ordered[r] if 0 <= r < len(ordered) else None)

    def _reserve_reopen(self):
        w, rec = self._current_reserve()
        if not rec:
            return
        self._reserve = [tuple(row) for row in rec.samples]
        self._res_t0 = None
        self._res_done = True
        self._redraw_reserve()
        self._update_iso()
        self.lbl_res.setText(f"{w.label}: reserve run of {rec.when[:10]} "
                             f"({len(rec.samples)} samples over {rec.hours:.1f} h)")
        self._goto_page(0)
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i) == "Power reserve":
                self.tabs.setCurrentIndex(i)

    def _reserve_hist_delete(self):
        w, rec = self._current_reserve()
        if not rec:
            return
        if QtWidgets.QMessageBox.question(
                self, "Delete reserve run",
                f"Delete the power-reserve run from {rec.when[:10]}?"
                ) != QtWidgets.QMessageBox.Yes:
            return
        w.reserves.remove(rec)
        self.collection.save()
        self._refresh_watches(w.id)

    def _reserve_hist_export(self):
        w, rec = self._current_reserve()
        if not rec:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export reserve run",
            os.path.join(REPORT_DIR, f"reserve_{w.id}_{rec.when[:10]}.csv"), "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as fh:
            cw = csv.writer(fh)
            cw.writerow(["elapsed_s", "elapsed_h", "rate_spd", "amplitude_deg", "beat_error_ms"])
            for el, rt, am, be in rec.samples:
                cw.writerow([f"{el:.1f}", f"{el/3600:.4f}", f"{rt:.2f}", f"{am:.1f}", f"{be:.3f}"])
        self.status.showMessage(f"Wrote {path}", 5000)

    def _fill_service_table(self, w):
        self.tbl_svc.setRowCount(0)
        for s in sorted(w.services, key=lambda x: x.when, reverse=True):
            r = self.tbl_svc.rowCount()
            self.tbl_svc.insertRow(r)
            cost = f"{s.cost} {s.currency}" if s.cost else ""
            note = s.notes.replace("\n", " ")
            wr = s.wr_summary
            if wr:
                note = f"[WR: {wr}] {note}".strip()
            cells = [s.when, s.kind, s.performed_by, cost,
                     str(len(s.documents)) if s.documents else "", note]
            for cix, v in enumerate(cells):
                self.tbl_svc.setItem(r, cix, QtWidgets.QTableWidgetItem(v))
        totals = w.total_service_cost()
        tot = ", ".join(f"{v:.0f} {k}" for k, v in totals.items())
        n = len(w.services)
        due = w.service_due() or "service timing unknown"
        self.lbl_svc_summary.setText(
            f"{n} service{'s' if n != 1 else ''} logged"
            + (f"  |  total {tot}" if tot else "")
            + f"  |  {due}")

    def _service_add(self):
        w = self._current_watch()
        if not w:
            return
        dlg = ServiceEditor(None, self)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        rec, new_srcs, _removed = dlg.result()
        for src in new_srcs:
            try:
                rec.documents.append(self.collection.store_document(w.id, src))
            except OSError as e:
                QtWidgets.QMessageBox.warning(self, "Service entry", f"Could not copy {src}: {e}")
        w.services.append(rec)
        w.last_service = w.effective_last_service
        self.collection.save()
        self._refresh_watches(w.id)

    def _before_after_report(self):
        w, s = self._current_service()
        if not w:
            w = self._current_watch()
        if not w or not w.services:
            QtWidgets.QMessageBox.information(
                self, "Before / after", "Select a watch with a logged service.")
            return
        if s is None:
            s = sorted(w.services, key=lambda x: x.when, reverse=True)[0]
        sd = s.date
        if sd is None:
            QtWidgets.QMessageBox.information(self, "Before / after",
                                             "That service entry has no usable date.")
            return
        runs = [h for h in w.history if h.date is not None]
        before = max((h for h in runs if h.date < sd), key=lambda h: h.date, default=None)
        after = min((h for h in runs if h.date >= sd and h.service_event),
                    key=lambda h: h.date, default=None) \
            or min((h for h in runs if h.date >= sd), key=lambda h: h.date, default=None)
        if before is None and after is None:
            QtWidgets.QMessageBox.information(
                self, "Before / after",
                "No timing runs saved around that service date. Save a run before and "
                "after a service (mark the after run post-service) to compare them.")
            return
        stem = "".join(ch if ch.isalnum() else "_" for ch in w.label)[:40]
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save before/after report",
            os.path.join(REPORT_DIR, f"beforeafter_{stem}_{s.when}.html"), "HTML (*.html)")
        if not path:
            return
        from .calibers import CALIBERS
        out = reportmod.build_before_after(
            path, watch=w, service=s, before=before, after=after,
            caliber=CALIBERS.get(w.caliber_key))
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(out))
        self.status.showMessage(f"Wrote {out}", 8000)

    def _service_checklist(self):
        w = self._current_watch()
        if not w:
            QtWidgets.QMessageBox.information(self, "Service checklist",
                                             "Select a watch first.")
            return
        from . import service_templates as stmpl
        from .calibers import CALIBERS
        c = CALIBERS.get(w.caliber_key)
        tmpl = stmpl.for_caliber(w.caliber_key or "", getattr(c, "group", "") if c else "")
        dlg = ServiceChecklistDialog(tmpl, caliber_label=(c.label if c else w.label), parent=self)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        import tempfile
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        safe = "".join(ch if ch.isalnum() else "_" for ch in (c.key if c else w.id))[:40]
        tmp = os.path.join(tempfile.gettempdir(), f"checklist_{safe}_{stamp}.md")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(dlg.markdown())
        rec = coll.ServiceRecord(
            when=datetime.now().strftime("%Y-%m-%d"), kind="Full service",
            notes="Service checklist attached.")
        try:
            rec.documents.append(self.collection.store_document(w.id, tmp))
        except OSError as e:
            QtWidgets.QMessageBox.warning(self, "Service checklist", f"Could not save: {e}")
            return
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        w.services.append(rec)
        w.last_service = w.effective_last_service
        self.collection.save()
        self._refresh_watches(w.id)
        self.status.showMessage("Checklist filed as a new service entry", 5000)

    def _current_service(self):
        w = self._current_watch()
        if not w or not w.services:
            return None, None
        r = self.tbl_svc.currentRow()
        ordered = sorted(w.services, key=lambda x: x.when, reverse=True)
        if 0 <= r < len(ordered):
            return w, ordered[r]
        return w, None

    def _service_edit(self):
        w, s = self._current_service()
        if not s:
            return
        dlg = ServiceEditor(s, self)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        rec, new_srcs, removed = dlg.result()
        for name in removed:
            try:
                os.remove(os.path.join(self.collection.docs, name))
            except OSError:
                pass
        for src in new_srcs:
            try:
                rec.documents.append(self.collection.store_document(w.id, src))
            except OSError:
                pass
        idx = w.services.index(s)
        w.services[idx] = rec
        w.last_service = w.effective_last_service
        self.collection.save()
        self._refresh_watches(w.id)

    def _service_delete(self):
        w, s = self._current_service()
        if not s:
            return
        if QtWidgets.QMessageBox.question(
                self, "Delete service entry",
                f"Delete the {s.kind} on {s.when}? Its {len(s.documents)} attached "
                f"document(s) will also be removed.") != QtWidgets.QMessageBox.Yes:
            return
        for name in s.documents:
            try:
                os.remove(os.path.join(self.collection.docs, name))
            except OSError:
                pass
        w.services.remove(s)
        w.last_service = w.effective_last_service
        self.collection.save()
        self._refresh_watches(w.id)

    def _service_open_doc(self):
        w, s = self._current_service()
        if not s:
            return
        if not s.documents:
            QtWidgets.QMessageBox.information(self, "Documents", "This entry has no attachments.")
            return
        name = s.documents[0]
        if len(s.documents) > 1:
            name, ok = QtWidgets.QInputDialog.getItem(
                self, "Open document", "Attachment:", s.documents, 0, False)
            if not ok:
                return
        self._open_document(self.collection.document_path(name))

    def _portfolio_report(self):
        if not self.collection.watches:
            QtWidgets.QMessageBox.information(self, "Portfolio report", "Add a watch first.")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Portfolio report",
            os.path.join(REPORT_DIR, f"portfolio_{datetime.now():%Y%m%d}.html"),
            "HTML (*.html)")
        if not path:
            return
        out = reportmod.build_portfolio(path, self.collection)
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(out))
        self.status.showMessage(f"Wrote {out}", 8000)

    def _year_review(self):
        if not self.collection.watches:
            QtWidgets.QMessageBox.information(self, "Year in review", "Add a watch first.")
            return
        yr, ok = QtWidgets.QInputDialog.getInt(
            self, "Year in review", "Year:", datetime.now().year, 1970, 2100)
        if not ok:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Year in review",
            os.path.join(REPORT_DIR, f"review_{yr}.html"), "HTML (*.html)")
        if not path:
            return
        out = reportmod.build_year_review(path, self.collection, yr,
                                          owner=getattr(self.collection, "owner", ""))
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(out))
        self.status.showMessage(f"Wrote {out}", 8000)

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
            photo_path=self.collection.photo_path(w),
            doc_dir=self.collection.docs)
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

    def _guided_regulation(self):
        c = self._current_caliber()
        dlg = RegulationWizard(
            lambda: (self._last_good if getattr(self, "_last_good", None) is not None
                     else self.last),
            c, parent=self)
        dlg.exec()

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

    def _timing_certificate(self):
        c = self._current_caliber()
        readings = list(self.readings)
        if not readings and self.last and self.last.ok:
            readings = [advisor.Reading(self.cmb_pos.currentText(), self.last.rate,
                                        self.last.amplitude, self.last.beat_error,
                                        self.cmb_wind.currentText())]
        if not readings:
            QtWidgets.QMessageBox.information(
                self, "Timing certificate",
                "Capture at least one position (six for a real certificate) first.")
            return
        gkey, gpassed, grows = self._current_grade(readings)
        w = self._current_watch()
        label, ok = QtWidgets.QInputDialog.getText(
            self, "Timing certificate", "Watch label:",
            text=(w.label if w else (c.label if c else "")))
        if not ok:
            return
        tech, _ = QtWidgets.QInputDialog.getText(self, "Timing certificate", "Tested by:")
        stem = "".join(ch if ch.isalnum() else "_" for ch in (label or "certificate"))[:40]
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save certificate",
            os.path.join(REPORT_DIR, f"cert_{stem}_{datetime.now():%Y%m%d}.html"),
            "HTML (*.html)")
        if not path:
            return
        out = reportmod.build_certificate(
            path, caliber=c, readings=readings,
            grade={"standard": gkey, "passed": gpassed, "rows": grows},
            watch_label=label, serial=(w.serial if w else ""),
            technician=tech, owner=getattr(self.collection, "owner", ""))
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(out))
        self.status.showMessage(f"Wrote {out} -- print to PDF from the browser", 9000)

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

    def changeEvent(self, e):
        # In "system" theme mode the palette is resolved at startup. If the OS
        # flips light/dark while running, we can't recolour live, but we can say
        # so rather than leaving a now-mismatched theme silently in place.
        if (e.type() == QtCore.QEvent.ApplicationPaletteChange
                and _T.MODE == "system" and not getattr(self, "_closing", False)
                and hasattr(self, "status")):
            if _T._system_is_light() != _T.IS_LIGHT:
                self.status.showMessage(
                    "System switched theme -- restart WatchGrapher to match.", 0)
        super().changeEvent(e)

    def closeEvent(self, e):
        self._closing = True
        w = getattr(self, "_clock_cal_worker", None)
        if w is not None:
            w.stop()
        th = getattr(self, "_clock_cal_thread", None)
        if th is not None:
            th.quit()
            th.wait(2000)
        try:
            self.worker.stop()
            self.thread.quit()
            self.thread.wait(1500)
        except Exception:
            pass
        if self.recorder and self.recorder is not self._net_recorder:
            self.recorder.stop()
        if self._net_recorder is not None:
            try:
                self._net_recorder.stop()
            except Exception:
                pass
            self._net_recorder = None
        super().closeEvent(e)


def _install_theme(app):
    """Re-colour every inline stylesheet by mapping the dark palette to the
    active one. Identity in dark mode, so it costs nothing there."""
    subs = {_T.DARK[k]: _T.P[k] for k in _T.DARK if _T.DARK[k] != _T.P[k]}
    _orig = getattr(QtWidgets.QWidget.setStyleSheet, "_wg_orig",
                    QtWidgets.QWidget.setStyleSheet)
    if subs:
        # Match each dark hex only as a whole colour token -- not as a prefix of
        # a longer hex -- so a gradient stop or an id selector can't be mangled.
        import re as _re
        rx = _re.compile("(" + "|".join(_re.escape(h) for h in subs) +
                         r")(?![0-9a-fA-F])")

        def _themed(self, s):
            _orig(self, rx.sub(lambda m: subs[m.group(1)], s) if s else s)
        _themed._wg_orig = _orig
        QtWidgets.QWidget.setStyleSheet = _themed
    else:
        QtWidgets.QWidget.setStyleSheet = _orig      # light->dark: undo any patch

    try:
        pg.setConfigOption("background", _T.get("PLOT_BG"))
        pg.setConfigOption("foreground", _T.get("PLOT_FG"))
    except Exception:
        pass

    app.setStyle("Fusion")
    P = _T.P
    pal = QtGui.QPalette()
    pal.setColor(QtGui.QPalette.Window, QtGui.QColor(P["BG"]))
    pal.setColor(QtGui.QPalette.Base, QtGui.QColor(P["PANEL"]))
    pal.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor(P["PANEL2"]))
    pal.setColor(QtGui.QPalette.Text, QtGui.QColor(P["INK"]))
    pal.setColor(QtGui.QPalette.WindowText, QtGui.QColor(P["INK"]))
    pal.setColor(QtGui.QPalette.Button, QtGui.QColor(P["PANEL2"]))
    pal.setColor(QtGui.QPalette.ButtonText, QtGui.QColor(P["INK"]))
    pal.setColor(QtGui.QPalette.ToolTipBase, QtGui.QColor(P["PANEL"]))
    pal.setColor(QtGui.QPalette.ToolTipText, QtGui.QColor(P["INK"]))
    pal.setColor(QtGui.QPalette.Highlight, QtGui.QColor(P["ACCENT"]))
    pal.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor(P["ON_ACCENT"]))
    app.setPalette(pal)


def main():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    _install_theme(app)
    w = MainWindow()
    w.show()
    app.exec()
