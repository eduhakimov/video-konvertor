"""Asosiy oyna: konvertatsiya navbati, birlashtirish, media ma'lumoti."""
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSlider,
                               QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from uzkit.i18n import register, tr
from uzkit.widgets import (Card, DropArea, PathPicker, Worker, error, header, human_size, info, open_path,
                           primary_button, run_worker, warn)
from uzkit.window import BaseMainWindow

from . import ffbin
from . import presets as P
from .media import MediaInfo, fmt_duration, parse_time, probe
from .runner import Cancelled, FFmpegRun, remove_partial, run_with_fallbacks
from .strings import STRINGS

register(STRINGS)

MEDIA_EXT = ["mp4", "mkv", "mov", "avi", "webm", "m4v", "wmv", "flv", "mpg", "mpeg", "ts", "mts", "m2ts", "3gp",
             "mp3", "wav", "m4a", "aac", "flac", "ogg", "opus", "wma", "gif"]
MEDIA_FILTER = "Media (" + " ".join(f"*.{e}" for e in MEDIA_EXT) + ");;All (*.*)"
SCREENSHOT = "--screenshot" in sys.argv


def cmdline(args: List[str]) -> str:
    return subprocess.list2cmdline(args)


def split_cmdline(text: str) -> List[str]:
    """Windows qoidalari bo'yicha buyruq qatorini bo'laklarga ajratadi."""
    text = " ".join(text.split("\n")).strip()
    try:
        import ctypes
        from ctypes import wintypes
        CommandLineToArgvW = ctypes.windll.shell32.CommandLineToArgvW
        CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
        CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
        n = ctypes.c_int()
        argv = CommandLineToArgvW(text, ctypes.byref(n))
        res = [argv[i] for i in range(n.value)]
        ctypes.windll.kernel32.LocalFree(argv)
        return res
    except Exception:
        import shlex
        return shlex.split(text, posix=False)


@dataclass
class QueueItem:
    path: str
    info: Optional[MediaInfo] = None
    status: str = "st_probing"
    progress: float = 0.0
    out: str = ""
    message: str = ""
    custom_cmd: Optional[List[str]] = None


# ======================================================================== ishchi oqim
class QueueThread(QThread):
    row_progress = Signal(int, float)
    row_state = Signal(int, str, str)        # qator, holat kaliti, xabar/chiqish yo'li
    finished_all = Signal(int, int)

    def __init__(self, items: List[QueueItem], rows: List[int], job: P.Job, out_dir: str):
        super().__init__()
        self.items, self.rows, self.job, self.out_dir = items, rows, job, out_dir
        self.runner = FFmpegRun()

    def cancel(self):
        self.runner.cancel()

    def run(self):
        ok = bad = 0
        job = self.job
        if job.encoder == "auto":
            job.encoder = ffbin.best_h264()
        for row in self.rows:
            it = self.items[row]
            if self.runner.cancelled:
                break
            self.row_state.emit(row, "st_running", "")
            out = P.output_path(job, it.path, self.out_dir)
            try:
                if it.custom_cmd:
                    cmds = [it.custom_cmd]
                else:
                    cmds = P.build_commands(job, it.path, it.info, out, ffbin.ffmpeg())
                if job.preset == "frames":
                    os.makedirs(out, exist_ok=True)
                dur = P.effective_duration(job, it.info) if it.info else 0
                err = run_with_fallbacks(cmds, out, dur, self.runner,
                                         lambda pct, r=row: self.row_progress.emit(r, pct))
            except Cancelled:
                remove_partial(out)
                self.row_state.emit(row, "st_cancelled", "")
                break
            except ValueError as ex:
                key = {"no_video": "err_no_video", "no_audio": "err_no_audio"}.get(str(ex), "st_error")
                self.row_state.emit(row, "st_error", tr(key))
                bad += 1
                continue
            except Exception as ex:  # noqa
                self.row_state.emit(row, "st_error", str(ex))
                bad += 1
                continue
            if err:
                remove_partial(out)
                self.row_state.emit(row, "st_error", err.splitlines()[-1] if err else "")
                bad += 1
            else:
                self.row_state.emit(row, "st_done", out)
                ok += 1
        self.finished_all.emit(ok, bad)


# ======================================================================== buyruq oynasi
class CommandDialog(QDialog):
    def __init__(self, cmd: List[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("cmd_title"))
        self.resize(760, 360)
        self.result_cmd: Optional[List[str]] = None
        self.reset = False
        l = QVBoxLayout(self)
        hint = QLabel(tr("cmd_hint"))
        hint.setWordWrap(True)
        hint.setObjectName("Muted")
        l.addWidget(hint)
        self.edit = QPlainTextEdit(cmdline(cmd))
        f = self.edit.font()
        f.setFamily("Consolas")
        self.edit.setFont(f)
        l.addWidget(self.edit, 1)
        row = QHBoxLayout()
        cp = QPushButton(tr("copy"))
        cp.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.edit.toPlainText()))
        rs = QPushButton(tr("cmd_reset"))
        rs.clicked.connect(self._reset)
        ap = primary_button(tr("cmd_apply"))
        ap.clicked.connect(self._apply)
        cl = QPushButton(tr("close"))
        cl.clicked.connect(self.reject)
        row.addWidget(cp)
        row.addWidget(rs)
        row.addStretch(1)
        row.addWidget(cl)
        row.addWidget(ap)
        l.addLayout(row)

    def _apply(self):
        args = split_cmdline(self.edit.toPlainText())
        if args:
            self.result_cmd = args
            self.accept()

    def _reset(self):
        self.reset = True
        self.accept()


# ======================================================================== konvertatsiya sahifasi
class ConvertPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.items: List[QueueItem] = []
        self.thread: Optional[QueueThread] = None
        self.last_out = ""
        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        lay.addWidget(header(tr("conv_title"), tr("conv_sub")))

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        # ---- chap: navbat
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(8)
        self.drop = DropArea(tr("drop_media"), MEDIA_FILTER, MEDIA_EXT)
        self.drop.setMinimumHeight(70)
        self.drop.setMaximumHeight(80)
        self.drop.files_dropped.connect(self.add_files)
        ll.addWidget(self.drop)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([tr("col_file"), tr("col_duration"), tr("col_res"),
                                              tr("col_size") + "  >  " + tr("col_est"), tr("col_status")])
        self.table.setColumnHidden(2, True)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for c, wdt in ((1, 150), (2, 10), (3, 150), (4, 100)):
            hh.setSectionResizeMode(c, QHeaderView.Interactive)
            self.table.setColumnWidth(c, wdt)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setTextElideMode(Qt.ElideMiddle)
        self.table.setWordWrap(False)
        ll.addWidget(self.table, 1)
        row = QHBoxLayout()
        b_add = QPushButton(tr("add_files"))
        b_add.clicked.connect(lambda: self.drop.mouseReleaseEvent(_FakeClick()))
        self.b_remove = QPushButton(tr("remove"))
        self.b_remove.clicked.connect(self.remove_selected)
        self.b_clear = QPushButton(tr("clear"))
        self.b_clear.clicked.connect(self.clear)
        self.b_cmd = QPushButton(tr("show_cmd"))
        self.b_cmd.clicked.connect(self.show_command)
        row.addWidget(b_add)
        row.addWidget(self.b_remove)
        row.addWidget(self.b_clear)
        row.addStretch(1)
        row.addWidget(self.b_cmd)
        ll.addLayout(row)
        split.addWidget(left)

        # ---- o'ng: sozlamalar
        card = Card(tr("settings_card"))
        self.form = QFormLayout()
        self.form.setLabelAlignment(Qt.AlignLeft)
        self.form.setHorizontalSpacing(12)
        self.form.setVerticalSpacing(9)
        self.preset = QComboBox()
        for p in P.PRESETS:
            self.preset.addItem(tr("p_" + p), p)
        self.desc = QLabel()
        self.desc.setWordWrap(True)
        self.desc.setObjectName("Muted")
        self.form.addRow(tr("preset"), self.preset)

        qrow = QHBoxLayout()
        self.quality = QSlider(Qt.Horizontal)
        self.quality.setRange(0, 100)
        self.quality_lbl = QLabel()
        self.quality_lbl.setMinimumWidth(120)
        qrow.addWidget(self.quality, 1)
        qrow.addWidget(self.quality_lbl)
        self.w_quality = _wrap(qrow)
        self.height_cb = QComboBox()
        for h in P.HEIGHTS:
            self.height_cb.addItem(tr("keep") if h == 0 else f"{h}p", h)
        self.fit = QComboBox()
        for f in P.FIT_MODES:
            self.fit.addItem(tr("fit_" + f), f)
        self.container = QComboBox()
        for c in P.CONTAINERS:
            self.container.addItem(c.upper(), c)
        self.copy = QCheckBox(tr("fast_copy"))
        self.audio_fmt = QComboBox()
        for a in P.AUDIO_FORMATS:
            self.audio_fmt.addItem({"aac": "AAC (M4A)"}.get(a, a.upper()), a)
        self.audio_kbps = QComboBox()
        for k in (96, 128, 160, 192, 256, 320):
            self.audio_kbps.addItem(f"{k} kbps", k)
        self.gif_fps = QSpinBox()
        self.gif_fps.setRange(1, 50)
        self.gif_width = QSpinBox()
        self.gif_width.setRange(64, 1920)
        self.gif_width.setSingleStep(40)
        self.rotate = QComboBox()
        for r in P.ROTATIONS:
            self.rotate.addItem(tr("rot_" + r), r)
        self.frame_every = QDoubleSpinBox()
        self.frame_every.setRange(0.04, 3600)
        self.frame_every.setDecimals(2)
        self.frame_fmt = QComboBox()
        self.frame_fmt.addItems(["jpg", "png"])
        trow = QHBoxLayout()
        self.start = QLineEdit()
        self.start.setPlaceholderText(tr("time_ph"))
        self.end = QLineEdit()
        self.end.setPlaceholderText(tr("time_ph"))
        self.start.setToolTip(tr("time_tip"))
        self.end.setToolTip(tr("time_tip"))
        trow.addWidget(self.start)
        trow.addWidget(QLabel("..."))
        trow.addWidget(self.end)
        self.w_time = _wrap(trow)
        self.encoder = QComboBox()
        self.encoder.addItem(tr("enc_auto"), "auto")
        self.out_dir = PathPicker("dir", placeholder=tr("out_same"))

        self.rows = {}
        for key, label, w in (("quality", tr("quality"), self.w_quality), ("height", tr("height"), self.height_cb),
                              ("fit", tr("fit"), self.fit), ("container", tr("format"), self.container),
                              ("copy", "", self.copy), ("audio_fmt", tr("format"), self.audio_fmt),
                              ("audio_kbps", tr("bitrate"), self.audio_kbps), ("gif_fps", tr("gif_fps"), self.gif_fps),
                              ("gif_width", tr("gif_width"), self.gif_width), ("rotate", tr("rotate"), self.rotate),
                              ("frame_every", tr("frame_every"), self.frame_every),
                              ("frame_fmt", tr("img_format"), self.frame_fmt),
                              ("time", f"{tr('start_time')} / {tr('end_time')}", self.w_time),
                              ("encoder", tr("encoder"), self.encoder), ("out", tr("out_folder"), self.out_dir)):
            self.form.addRow(label, w)
            self.rows[key] = w
        card.add(self.desc)
        card.add(self.form)
        card.lay.addStretch(1)
        for cb in card.findChildren(QComboBox):
            cb.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            cb.setMinimumContentsLength(8)
        self.desc.setMinimumWidth(10)
        self.est_lbl = QLabel()
        self.est_lbl.setObjectName("SectionTitle")
        card.add(self.est_lbl)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(card)
        scroll.setMinimumWidth(330)
        split.addWidget(scroll)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([800, 330])
        lay.addWidget(split, 1)

        # ---- pastki panel
        bottom = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(10)
        self.state_lbl = QLabel()
        self.state_lbl.setObjectName("Muted")
        col = QVBoxLayout()
        col.addWidget(self.progress)
        col.addWidget(self.state_lbl)
        bottom.addLayout(col, 1)
        self.b_open = QPushButton(tr("open_folder"))
        self.b_open.clicked.connect(self.open_out)
        self.b_cancel = QPushButton(tr("cancel"))
        self.b_cancel.setEnabled(False)
        self.b_cancel.clicked.connect(self.cancel)
        self.b_start = primary_button(tr("start"))
        self.b_start.setMinimumWidth(140)
        self.b_start.clicked.connect(self.start_queue)
        bottom.addWidget(self.b_open)
        bottom.addWidget(self.b_cancel)
        bottom.addWidget(self.b_start)
        lay.addLayout(bottom)

        self._load_job()
        for sig in (self.preset.currentIndexChanged, self.height_cb.currentIndexChanged, self.fit.currentIndexChanged,
                    self.container.currentIndexChanged, self.audio_fmt.currentIndexChanged,
                    self.audio_kbps.currentIndexChanged, self.rotate.currentIndexChanged,
                    self.frame_fmt.currentIndexChanged, self.encoder.currentIndexChanged):
            sig.connect(self.on_settings_changed)
        for sig in (self.quality.valueChanged, self.gif_fps.valueChanged, self.gif_width.valueChanged,
                    self.frame_every.valueChanged):
            sig.connect(self.on_settings_changed)
        self.copy.toggled.connect(self.on_settings_changed)
        self.start.textChanged.connect(self.on_settings_changed)
        self.end.textChanged.connect(self.on_settings_changed)
        self.on_settings_changed()
        self._detect_encoders()

    # ------------------------------------------------------------------ sozlamalar
    def _load_job(self):
        d = self.win.settings.get("job", {}) if self.win.settings else {}
        j = P.Job(**{k: v for k, v in d.items() if k in P.Job.__dataclass_fields__})
        _set_data(self.preset, j.preset)
        self.quality.setValue(j.quality)
        _set_data(self.height_cb, j.height)
        _set_data(self.fit, j.fit)
        _set_data(self.container, j.container)
        self.copy.setChecked(j.copy)
        _set_data(self.audio_fmt, j.audio_fmt)
        _set_data(self.audio_kbps, j.audio_kbps)
        self.gif_fps.setValue(j.gif_fps)
        self.gif_width.setValue(j.gif_width)
        _set_data(self.rotate, j.rotate)
        self.frame_every.setValue(j.frame_every)
        self.frame_fmt.setCurrentText(j.frame_fmt)
        self.out_dir.set_path(self.win.settings.get("out_dir", "") if self.win.settings else "")

    def current_job(self, strict: bool = False) -> P.Job:
        try:
            s, e = parse_time(self.start.text()), parse_time(self.end.text())
        except ValueError:
            if strict:
                raise
            s = e = None
        if strict and s is not None and e is not None and e <= s:
            raise ValueError("order")
        return P.Job(preset=self.preset.currentData(), quality=self.quality.value(),
                     container=self.container.currentData(), copy=self.copy.isChecked(),
                     audio_fmt=self.audio_fmt.currentData(), audio_kbps=self.audio_kbps.currentData(),
                     gif_fps=self.gif_fps.value(), gif_width=self.gif_width.value(), start=s, end=e,
                     height=self.height_cb.currentData(), rotate=self.rotate.currentData(), fit=self.fit.currentData(),
                     frame_every=self.frame_every.value(), frame_fmt=self.frame_fmt.currentText(),
                     encoder=self.encoder.currentData() or "auto")

    def on_settings_changed(self, *_):
        p = self.preset.currentData()
        self.desc.setText(tr("d_" + p))
        vis = {
            "quality": p in P.VIDEO_PRESETS or (p == "container" and not self.copy.isChecked())
            or (p == "trim" and not self.copy.isChecked()),
            "height": p in ("compress", "resize"), "fit": p in ("reels", "youtube"),
            "container": p == "container", "copy": p in ("container", "trim"),
            "audio_fmt": p == "audio", "audio_kbps": p == "audio" and self.audio_fmt.currentData() != "wav",
            "gif_fps": p == "gif", "gif_width": p == "gif", "rotate": p == "rotate",
            "frame_every": p == "frames", "frame_fmt": p == "frames", "time": True,
            "encoder": p in P.VIDEO_PRESETS or p in ("container", "trim"), "out": True,
        }
        for k, w in self.rows.items():
            self.form.setRowVisible(w, vis.get(k, True))
        q = self.quality.value()
        self.quality_lbl.setText(tr("quality_val", q=q, crf=P.quality_to_crf(q)))
        job = self.current_job()
        if self.win.settings and not SCREENSHOT:
            d = asdict(job)
            d.pop("start"), d.pop("end"), d.pop("encoder")
            self.win.settings.set("job", d)
        self.refresh_estimates()

    def refresh_estimates(self):
        job = self.current_job()
        total = 0
        for r, it in enumerate(self.items):
            est = P.estimate_size(job, it.info) if it.info else None
            if est:
                total += est
            src = human_size(it.info.size) if it.info and it.info.size else "—"
            self._set_cell(r, 3, f"{src}  >  ~{human_size(est)}" if est else src)
        self.est_lbl.setText(tr("total_est", size=human_size(total)) if total else "")

    def _detect_encoders(self):
        if SCREENSHOT:
            for e in ("h264_nvenc", "libopenh264"):
                self.encoder.addItem(e, e)
            return
        w = Worker(ffbin.available_h264)

        def done(lst):
            cur = self.win.settings.get("encoder", "auto") if self.win.settings else "auto"
            for e in lst:
                self.encoder.addItem(e, e)
            _set_data(self.encoder, cur)
            self.encoder.currentIndexChanged.connect(
                lambda *_: self.win.settings.set("encoder", self.encoder.currentData()))
        w.signals.result.connect(done)
        start_worker(w)

    # ------------------------------------------------------------------ navbat
    def add_files(self, paths: List[str]):
        new = []
        for p in paths:
            p = os.path.normpath(p)
            if any(it.path == p for it in self.items):
                continue
            self.items.append(QueueItem(p))
            r = len(self.items) - 1
            self.table.insertRow(r)
            self._render_row(r)
            new.append(r)
        if not new:
            return
        w = Worker(lambda paths: [_safe_probe(x) for x in paths], [self.items[r].path for r in new])

        def done(infos, rows=new):
            for r, inf in zip(rows, infos):
                if r >= len(self.items):
                    continue
                it = self.items[r]
                it.info = inf
                it.status = "st_waiting" if inf else "st_error"
                it.message = "" if inf else tr("err_probe")
                self._render_row(r)
            self.refresh_estimates()
        w.signals.result.connect(done)
        start_worker(w)

    def _set_cell(self, r: int, c: int, text: str, tip: str = ""):
        it = self.table.item(r, c)
        if it is None:
            it = QTableWidgetItem()
            self.table.setItem(r, c, it)
        it.setText(text)
        if tip:
            it.setToolTip(tip)
        if c in (1, 2, 3):
            it.setTextAlignment(Qt.AlignCenter)

    def _render_row(self, r: int):
        it = self.items[r]
        self._set_cell(r, 0, os.path.basename(it.path), it.path)
        inf = it.info
        dur = fmt_duration(inf.duration) if inf else "…"
        if inf and inf.has_video:
            dur += "  ·  {}×{}".format(*inf.display_size)
        self._set_cell(r, 1, dur)
        if inf and inf.has_video:
            w, h = inf.display_size
            self._set_cell(r, 2, f"{w}×{h}")
        else:
            self._set_cell(r, 2, "audio" if inf and inf.has_audio else "—")
        if not self.table.item(r, 3) or not self.table.item(r, 3).text():
            self._set_cell(r, 3, human_size(inf.size) if inf and inf.size else "—")
        bar = self.table.cellWidget(r, 4)
        if it.status == "st_running":
            if not isinstance(bar, QProgressBar):
                bar = QProgressBar()
                bar.setRange(0, 100)
                bar.setFormat("%p%")
                bar.setTextVisible(True)
                bar.setFixedHeight(18)
                self.table.setCellWidget(r, 4, bar)
            bar.setValue(int(it.progress))
            self._set_cell(r, 4, "")
        else:
            if bar is not None:
                self.table.removeCellWidget(r, 4)
            text = tr(it.status)
            if it.custom_cmd and it.status == "st_waiting":
                text = tr("st_custom")
            self._set_cell(r, 4, text, it.message or it.out)
            cell = self.table.item(r, 4)
            color = {"st_done": "#16a34a", "st_error": "#dc2626", "st_cancelled": "#d97706"}.get(it.status)
            if color:
                from PySide6.QtGui import QColor
                cell.setForeground(QColor(color))
            else:
                cell.setData(Qt.ForegroundRole, None)

    def selected_rows(self) -> List[int]:
        return sorted({i.row() for i in self.table.selectionModel().selectedRows()})

    def remove_selected(self):
        if self.thread:
            return
        for r in reversed(self.selected_rows()):
            self.table.removeRow(r)
            del self.items[r]
        self.refresh_estimates()

    def clear(self):
        if self.thread:
            return
        self.items.clear()
        self.table.setRowCount(0)
        self.refresh_estimates()

    def show_command(self):
        rows = self.selected_rows() or ([0] if len(self.items) == 1 else [])
        if not rows:
            warn(self, tr("select_row"))
            return
        r = rows[0]
        it = self.items[r]
        if not it.info:
            return
        job = self.current_job()
        if job.encoder == "auto":
            job.encoder = ffbin.best_h264()
        try:
            cmd = it.custom_cmd or P.build_commands(job, it.path, it.info, P.output_path(job, it.path,
                                                    self.out_dir.path()), ffbin.ffmpeg())[0]
        except ValueError as ex:
            warn(self, tr({"no_video": "err_no_video", "no_audio": "err_no_audio"}.get(str(ex), "st_error")))
            return
        dlg = CommandDialog(cmd, self)
        if dlg.exec():
            it.custom_cmd = None if dlg.reset else dlg.result_cmd
            self._render_row(r)

    # ------------------------------------------------------------------ ishga tushirish
    def start_queue(self):
        if self.thread:
            return
        if not ffbin.find_tool("ffmpeg.exe"):
            error(self, tr("no_ffmpeg"))
            return
        try:
            job = self.current_job(strict=True)
        except ValueError as ex:
            warn(self, tr("end_before_start") if str(ex) == "order" else tr("bad_time"))
            return
        rows = [r for r, it in enumerate(self.items) if it.info and it.status in ("st_waiting", "st_error",
                                                                                     "st_cancelled", "st_done")]
        sel = [r for r in self.selected_rows() if r in rows]
        rows = sel if len(sel) > 1 else [r for r in rows if self.items[r].status != "st_done"] or rows
        if not rows:
            warn(self, tr("queue_empty"))
            return
        out_dir = self.out_dir.path()
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        if self.win.settings:
            self.win.settings.set("out_dir", out_dir)
        for r in rows:
            self.items[r].status, self.items[r].progress, self.items[r].message = "st_waiting", 0, ""
            self._render_row(r)
        self._run_rows = rows
        self._done_count = 0
        self.thread = QueueThread(self.items, rows, job, out_dir)
        self.thread.row_progress.connect(self._on_progress)
        self.thread.row_state.connect(self._on_state)
        self.thread.finished_all.connect(self._on_finished)
        self._busy(True)
        self.thread.start()

    def _busy(self, b: bool):
        self.b_start.setEnabled(not b)
        self.b_cancel.setEnabled(b)
        self.b_remove.setEnabled(not b)
        self.b_clear.setEnabled(not b)
        self.preset.setEnabled(not b)

    def _on_progress(self, r: int, pct: float):
        it = self.items[r]
        it.progress = pct
        bar = self.table.cellWidget(r, 4)
        if isinstance(bar, QProgressBar):
            bar.setValue(int(pct))
        n = len(self._run_rows)
        self.progress.setValue(int((self._done_count + pct / 100) / n * 1000))

    def _on_state(self, r: int, status: str, msg: str):
        it = self.items[r]
        it.status = status
        if status == "st_running":
            idx = self._run_rows.index(r) + 1
            self.state_lbl.setText(tr("running_n", i=idx, n=len(self._run_rows)) + ":  " + os.path.basename(it.path))
        if status in ("st_done", "st_error"):
            self._done_count += 1
        if status == "st_done":
            it.out = msg
            self.last_out = msg
        else:
            it.message = msg
        self._render_row(r)

    def _on_finished(self, ok: int, bad: int):
        self.thread.wait()
        self.thread = None
        self._busy(False)
        self.progress.setValue(1000 if ok and not bad else self.progress.value())
        text = tr("all_done", ok=ok, bad=bad)
        self.state_lbl.setText(text)
        self.win.status(text, 10000)

    def cancel(self):
        if self.thread:
            self.thread.cancel()

    def open_out(self):
        target = self.out_dir.path()
        if not target and self.last_out:
            target = os.path.dirname(self.last_out)
        if not target and self.items:
            target = os.path.dirname(self.items[0].path)
        if target and os.path.isdir(target):
            open_path(target)

    # ------------------------------------------------------------------ demo (skrinshot uchun)
    def load_demo(self):
        demo = [("Toy_video_4K.mp4", 3840, 2160, 312.4, 1_820_000_000, "st_done"),
                ("Intervyu_kamera_A.mov", 1920, 1080, 1265.0, 2_450_000_000, "st_running"),
                ("Dron_Chorvoq.mp4", 2720, 1530, 95.3, 410_000_000, "st_waiting"),
                ("Reklama_rolik_v3.mkv", 1080, 1920, 31.0, 86_000_000, "st_waiting")]
        for name, w, h, d, size, st in demo:
            inf = MediaInfo(path=name, duration=d, size=size, bitrate=int(size * 8 / d), has_video=True, width=w,
                            height=h, fps=29.97, vcodec="h264", vbitrate=int(size * 8 / d) - 192000, has_audio=True,
                            acodec="aac", abitrate=192000, sample_rate=48000, channels=2)
            self.items.append(QueueItem("C:\\Video\\" + name, inf, st, 47 if st == "st_running" else 0))
            r = len(self.items) - 1
            self.table.insertRow(r)
            self._render_row(r)
        self.table.clearSelection()
        self.progress.setValue(380)
        self.state_lbl.setText(tr("running_n", i=2, n=4) + ":  Intervyu_kamera_A.mov")
        self.refresh_estimates()


_LIVE_WORKERS = set()


def start_worker(w: Worker) -> Worker:
    """Worker obyektini ish tugaguncha xotirada ushlab turadi (aks holda signal obyekti o'chib ketadi)."""
    _LIVE_WORKERS.add(w)
    w.signals.finished.connect(lambda: _LIVE_WORKERS.discard(w))
    return run_worker(w)


class _FakeClick:
    def button(self):
        return Qt.LeftButton


def _wrap(layout) -> QWidget:
    w = QWidget()
    layout.setContentsMargins(0, 0, 0, 0)
    w.setLayout(layout)
    return w


def _set_data(combo: QComboBox, value):
    i = combo.findData(value)
    if i >= 0:
        combo.setCurrentIndex(i)


def _safe_probe(path: str) -> Optional[MediaInfo]:
    try:
        return probe(path)
    except Exception:
        return None


# ======================================================================== birlashtirish
class MergeThread(QThread):
    progress = Signal(float)
    done = Signal(str)        # '' = ok, aks holda xato

    def __init__(self, cmds, out, duration):
        super().__init__()
        self.cmds, self.out, self.duration = cmds, out, duration
        self.runner = FFmpegRun()

    def run(self):
        try:
            err = run_with_fallbacks(self.cmds, self.out, self.duration, self.runner, self.progress.emit)
        except Cancelled:
            remove_partial(self.out)
            err = tr("st_cancelled")
        self.done.emit(err)


class MergePage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.infos: dict = {}
        self.thread: Optional[MergeThread] = None
        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        lay.addWidget(header(tr("merge_title"), tr("merge_sub")))
        self.drop = DropArea(tr("drop_media"), MEDIA_FILTER, MEDIA_EXT)
        self.drop.setMaximumHeight(80)
        self.drop.setMinimumHeight(70)
        self.drop.files_dropped.connect(self.add_files)
        lay.addWidget(self.drop)
        mid = QHBoxLayout()
        self.list = QListWidget()
        self.list.setAlternatingRowColors(True)
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.setStyleSheet("QListWidget::item { padding: 9px 6px; }")
        self.list.model().rowsMoved.connect(lambda *_: self.check())
        mid.addWidget(self.list, 1)
        side = QVBoxLayout()
        for key, fn in (("up", lambda: self.move(-1)), ("down", lambda: self.move(1)), ("remove", self.remove),
                        ("clear", self.clear)):
            b = QPushButton(tr(key))
            b.clicked.connect(fn)
            side.addWidget(b)
        side.addStretch(1)
        mid.addLayout(side)
        lay.addLayout(mid, 1)
        card = Card()
        f = QFormLayout()
        self.out = PathPicker("save", "MP4 (*.mp4);;MKV (*.mkv);;MOV (*.mov)")
        f.addRow(tr("merge_out"), self.out)
        self.fast = QCheckBox(tr("fast_copy"))
        f.addRow("", self.fast)
        self.hint = QLabel()
        self.hint.setObjectName("Muted")
        f.addRow("", self.hint)
        card.add(f)
        lay.addWidget(card)
        bottom = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(10)
        bottom.addWidget(self.progress, 1)
        self.b_cancel = QPushButton(tr("cancel"))
        self.b_cancel.setEnabled(False)
        self.b_cancel.clicked.connect(lambda: self.thread and self.thread.runner.cancel())
        self.b_go = primary_button(tr("merge_btn"))
        self.b_go.setMinimumWidth(140)
        self.b_go.clicked.connect(self.go)
        bottom.addWidget(self.b_cancel)
        bottom.addWidget(self.b_go)
        lay.addLayout(bottom)
        self.check()

    def files(self) -> List[str]:
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]

    def add_files(self, paths):
        paths = [os.path.normpath(p) for p in paths]
        w = Worker(lambda ps: [(p, _safe_probe(p)) for p in ps], paths)

        def done(res):
            for p, inf in res:
                if not inf:
                    continue
                self.infos[p] = inf
                it = QListWidgetItem(f"{os.path.basename(p)}   ·   {fmt_duration(inf.duration)}"
                                     + (f"   ·   {inf.display_size[0]}×{inf.display_size[1]}" if inf.has_video else ""))
                it.setData(Qt.UserRole, p)
                it.setToolTip(p)
                self.list.addItem(it)
            if not self.out.path() and self.list.count():
                first = self.files()[0]
                self.out.set_path(P.output_path(P.Job(preset="compress"), first).replace("_compressed", "_merged"))
            self.check()
        w.signals.result.connect(done)
        start_worker(w)

    def move(self, d):
        r = self.list.currentRow()
        if r < 0 or not (0 <= r + d < self.list.count()):
            return
        it = self.list.takeItem(r)
        self.list.insertItem(r + d, it)
        self.list.setCurrentRow(r + d)
        self.check()

    def remove(self):
        r = self.list.currentRow()
        if r >= 0:
            self.list.takeItem(r)
        self.check()

    def clear(self):
        self.list.clear()
        self.check()

    def check(self):
        infos = [self.infos.get(f) for f in self.files()]
        ok = len(infos) >= 2 and all(infos) and P.concat_compatible(infos)
        self.fast.setEnabled(ok)
        self.fast.setChecked(ok)
        self.hint.setText("" if len(infos) < 2 else tr("merge_fast_ok") if ok else tr("merge_fast_no"))

    def go(self):
        if self.thread:
            return
        files = self.files()
        if len(files) < 2:
            warn(self, tr("merge_need2"))
            return
        out = self.out.path()
        if not out:
            return
        if not Path(out).suffix:
            out += ".mp4"
        infos = [self.infos[f] for f in files]
        lst = os.path.join(tempfile.gettempdir(), "vk_concat_list.txt")
        Path(lst).write_text(P.concat_list_text(files), encoding="utf-8")
        enc = ffbin.best_h264()
        cmds = P.build_concat_commands(files, infos, out, lst, self.fast.isChecked(), enc, ffbin.ffmpeg())
        self.thread = MergeThread(cmds, out, sum(i.duration for i in infos))
        self.thread.progress.connect(lambda p: self.progress.setValue(int(p * 10)))
        self.thread.done.connect(lambda err, o=out: self._done(err, o))
        self.b_go.setEnabled(False)
        self.b_cancel.setEnabled(True)
        self.thread.start()

    def _done(self, err, out):
        self.thread.wait()
        self.thread = None
        self.b_go.setEnabled(True)
        self.b_cancel.setEnabled(False)
        if err:
            error(self, err[-600:])
        else:
            self.win.status(tr("merged_ok", path=out), 10000)
            info(self, tr("merged_ok", path=out))

    def load_demo(self):
        for name, d in (("Kirish_qismi.mp4", 12.0), ("Asosiy_sahna.mp4", 184.5), ("Yakun_titrlar.mp4", 20.2)):
            inf = MediaInfo(path=name, duration=d, has_video=True, width=1920, height=1080, vcodec="h264",
                            has_audio=True, acodec="aac")
            p = "C:\\Video\\" + name
            self.infos[p] = inf
            it = QListWidgetItem(f"{name}   ·   {fmt_duration(d)}   ·   1920×1080")
            it.setData(Qt.UserRole, p)
            self.list.addItem(it)
        self.out.set_path("C:\\Video\\Kirish_qismi_merged.mp4")
        self.check()


# ======================================================================== media ma'lumoti
class InfoPage(QWidget):
    def __init__(self, win, convert_page: ConvertPage):
        super().__init__()
        self.win, self.convert_page = win, convert_page
        self.path = ""
        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        lay.addWidget(header(tr("info_title"), tr("info_sub")))
        self.drop = DropArea(tr("drop_one"), MEDIA_FILTER, MEDIA_EXT, multiple=False)
        self.drop.setMaximumHeight(80)
        self.drop.setMinimumHeight(70)
        self.drop.files_dropped.connect(lambda ps: self.load(ps[0]))
        lay.addWidget(self.drop)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels([tr("prop"), tr("value")])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        lay.addWidget(self.table, 1)
        self.empty = QLabel(tr("info_empty"))
        self.empty.setObjectName("Muted")
        row = QHBoxLayout()
        row.addWidget(self.empty)
        row.addStretch(1)
        cp = QPushButton(tr("copy_info"))
        cp.clicked.connect(self.copy_all)
        add = primary_button(tr("use_in_queue"))
        add.clicked.connect(lambda: self.path and self.convert_page.add_files([self.path]))
        row.addWidget(cp)
        row.addWidget(add)
        lay.addLayout(row)

    def load(self, path: str):
        self.path = path
        inf = _safe_probe(path)
        if not inf:
            error(self, tr("err_probe"))
            return
        self.show_info(inf)

    def show_info(self, inf: MediaInfo):
        self.empty.setText("")
        rows = [(tr("i_path"), inf.path), (tr("i_container"), inf.container),
                (tr("i_duration"), fmt_duration(inf.duration) + f"  ({inf.duration:.2f} s)"),
                (tr("i_size"), human_size(inf.size)), (tr("i_bitrate"), f"{inf.bitrate / 1000:.0f} kbps")]
        if inf.has_video:
            w, h = inf.display_size
            rows.append((tr("i_video"), f"{inf.vcodec} · {w}×{h} · {inf.fps:.3g} fps · {inf.pix_fmt}"
                         + (f" · {inf.vbitrate / 1000:.0f} kbps" if inf.vbitrate else "")))
            if inf.rotation:
                rows.append((tr("i_rotation"), f"{inf.rotation}°"))
        if inf.has_audio:
            rows.append((tr("i_audio"), f"{inf.acodec} · {inf.sample_rate} Hz · {inf.channels} ch"
                         + (f" · {inf.abitrate / 1000:.0f} kbps" if inf.abitrate else "")))
        for s in inf.streams:
            desc = [s.get("codec_type", ""), s.get("codec_long_name") or s.get("codec_name", "")]
            lang = (s.get("tags") or {}).get("language")
            if lang:
                desc.append(lang)
            rows.append((tr("i_stream", n=s.get("index", 0)), " · ".join(x for x in desc if x)))
        self.table.setRowCount(len(rows))
        for i, (k, v) in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(k))
            self.table.setItem(i, 1, QTableWidgetItem(str(v)))

    def copy_all(self):
        lines = [f"{self.table.item(r, 0).text()}: {self.table.item(r, 1).text()}" for r in range(self.table.rowCount())]
        QGuiApplication.clipboard().setText("\n".join(lines))
        self.win.status(tr("copied"))

    def load_demo(self):
        inf = MediaInfo(path="C:\\Video\\Intervyu_kamera_A.mov", container="mov,mp4,m4a,3gp,3g2,mj2", duration=1265.0,
                        size=2_450_000_000, bitrate=15_494_000, has_video=True, width=1920, height=1080, fps=29.97,
                        vcodec="h264", vbitrate=15_300_000, pix_fmt="yuv420p", has_audio=True, acodec="aac",
                        abitrate=192000, sample_rate=48000, channels=2,
                        streams=[{"index": 0, "codec_type": "video", "codec_long_name": "H.264 / AVC / MPEG-4 AVC",
                                  "tags": {"language": "und"}},
                                 {"index": 1, "codec_type": "audio", "codec_long_name": "AAC (Advanced Audio Coding)",
                                  "tags": {"language": "uzb"}}])
        self.path = inf.path
        self.show_info(inf)


# ======================================================================== asosiy oyna
class MainWindow(BaseMainWindow):
    def __init__(self):
        super().__init__()
        self.convert = ConvertPage(self)
        self.merge = MergePage(self)
        self.info = InfoPage(self, self.convert)
        self.add_page(tr("page_convert"), self.convert)
        self.add_page(tr("page_merge"), self.merge)
        self.add_page(tr("page_info"), self.info)
        self._nav.setFixedWidth(184)
        if SCREENSHOT:
            self.convert.load_demo()
            self.merge.load_demo()
            self.info.load_demo()

    def closeEvent(self, e):
        for t in (self.convert.thread, self.merge.thread):
            if t:
                t.runner.cancel()
                t.wait(3000)
        from PySide6.QtCore import QThreadPool
        QThreadPool.globalInstance().waitForDone(8000)
        super().closeEvent(e)
