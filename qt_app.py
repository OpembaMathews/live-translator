"""Live Translator with the Qt caption window.

Same application as translation.py: the same audio capture, the same Whisper
recognition, the same local and cloud translation engines. Only the window is
different. QtTranslator inherits FloatingTranslator and replaces the Tk half -
the window, the painting, the input handling and the settings surface - while
the capture and recognition half is used exactly as it stands.

Run:  pythonw qt_app.py        (or: python qt_app.py, to see tracebacks)
"""
import sys
import traceback

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication, QMenu

import translation as core
from translation import (
    ENGINE, DEFAULT_INPUT, LANGUAGES, LANG_NAMES, RESPONSE_PRESETS,
    SENSITIVITY_PRESETS, WHISPER_MODELS, CAPTURE_MODES, log,
)
from qt_ui import CaptionWindow, SIZE_PRESETS


MENU_QSS = """
QMenu {
    background-color: #142130;
    color: #DCE8F4;
    border: 1px solid #2A3E52;
    border-radius: 10px;
    padding: 6px;
}
QMenu::item {
    padding: 7px 26px 7px 24px;
    border-radius: 6px;
}
QMenu::item:selected { background-color: #22405E; color: #FFFFFF; }
QMenu::item:disabled { color: #6E8CAB; }
QMenu::separator { height: 1px; background: #2A3E52; margin: 6px 10px; }
QMenu::indicator { width: 14px; }
"""


class Bridge(QObject):
    """Carries a callable from a worker thread onto the Qt thread.

    The capture and recognition threads were written against Tk's after(),
    which is safe to call from anywhere. Qt is not, so every one of those
    calls comes through this queued signal instead.
    """

    call = Signal(object)

    def __init__(self):
        super().__init__()
        self.call.connect(self._run, Qt.ConnectionType.QueuedConnection)

    @Slot(object)
    def _run(self, fn):
        try:
            fn()
        except Exception:
            log("UI callback failed:\n" + traceback.format_exc())


class QtTranslator(core.FloatingTranslator):
    """The app, wearing the Qt window."""

    def __init__(self, engine_name=ENGINE, input_mode=DEFAULT_INPUT):
        super().__init__(engine_name=engine_name, input_mode=input_mode)

    # -- the window --------------------------------------------------------
    def build_ui(self):
        self.bridge = Bridge()
        # font_for() picks a face per message; Qt resolves families by name
        self.cjk_font = "Microsoft JhengHei UI"
        self.latin_font = "Segoe UI"
        self.font_family = self.latin_font

        self.win = CaptionWindow(controller=self)
        # init_state() already put the app in its starting state; the window
        # should open showing that rather than blank for the ten seconds the
        # engine and the speech model take to load.
        self.win.set_lines(self._notice, "")
        self.win.set_busy(self._busy)
        self.win.set_listening(False)
        self.win.show()

        # The gauge and the bars need a repaint even when no text changed.
        self._pump = QTimer(self.win)
        self._pump.timeout.connect(self._pump_level)
        self._pump.start(33)

    def _pump_level(self):
        """Carry what the audio thread writes into the view.

        set_level() is inherited and fires for every captured chunk, and the
        capture loop flips _listening on its own thread rather than through a
        setter, so both are read here instead of pushed. It is already the
        meter's tick, so the extra check costs nothing.
        """
        scale = max(core.WAVE_SCALE_FLOOR, self._level_scale)
        self.win.set_level(min(1.0, self._level_raw / scale))
        if self.win._listening != self._listening:
            self.win.set_listening(self._listening)

    def run(self):
        QApplication.instance().exec()

    # -- what the view calls back into -------------------------------------
    def open_settings(self, global_pos=None):
        menu = self.build_menu()
        if global_pos is None:
            menu.exec()
        else:
            menu.exec(global_pos.toPoint())

    def set_size(self, name):
        self.win.set_size(name)
        self._size_name = name
        log(f"size -> {name}")

    def close(self):
        self.closing = True
        try:
            self._pump.stop()
        except Exception:
            pass
        self.win.close()
        QApplication.instance().quit()

    # -- UI methods the engine half drives ---------------------------------
    def on_ui(self, fn):
        if not self.closing:
            self.bridge.call.emit(fn)

    def redraw(self):
        if not self.closing:
            self.win.update()

    def update_text(self, text):
        # One-line updates (status before the first translation) go on the
        # heard line, with the translated line cleared.
        self._text = text
        self.win.set_lines(text, "")

    def show_stream(self, heard, translated, pending=""):
        """A partial caption while the speaker is still talking."""
        self._has_translation = True
        self._notice = ""
        self._busy = False
        self._text = translated
        self.win.set_busy(False)
        self.win.set_notice("")
        self.win.set_lines(heard, translated, pending)

    def show_pair(self, heard, translated):
        self._has_translation = True
        self._notice = ""
        self._busy = False
        self._text = translated
        self.win.set_busy(False)
        self.win.set_notice("")
        self.win.set_lines(heard, translated, "")

    def set_notice(self, text):
        self._notice = text
        self.win.set_notice(text)

    def set_input_mode(self, mode):
        self.input_mode = mode
        self.last_direction = None
        log(f"input mode -> {mode}")

    def set_target_mode(self, mode):
        self.target_mode = mode
        log(f"target mode -> {mode}")

    def set_opacity(self, value):
        self.opacity = max(0.35, min(1.0, value))
        self.win.setWindowOpacity(self.opacity)

    def toggle_listening(self):
        super().toggle_listening()
        self.win.set_listening(not self.paused)

    def toggle_source(self):
        super().toggle_source()
        self.win.set_device_kind(self.device_kind)

    def select_device(self, index, label, kind="mic"):
        super().select_device(index, label, kind)
        self.win.set_device_kind(kind)

    def show_status(self, text, busy=None):
        """Status and warnings, placed the way this window can carry them.

        The Tk panel blanked the caption while busy and drew a sweeping line
        in its place. Here the TRANSLATING pill says "working", so the message
        itself can stay on the line and be read.
        """
        if busy is None:
            busy = text.rstrip().endswith("...")

        def apply():
            self.win.set_busy(busy)
            if self._has_translation:
                # Something worth protecting is on screen: drop to the pill
                self._notice = text
                self.win.set_notice(text)
            else:
                self._busy = busy
                self._text = text
                self.win.set_notice("")
                self.win.set_lines(text, "")

        self.on_ui(apply)

    # -- settings ----------------------------------------------------------
    def build_menu(self):
        m = QMenu(self.win)
        m.setStyleSheet(MENU_QSS)
        m.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

        self._device_menu(m.addMenu("Audio input"))
        self._lang_menu(m.addMenu("Speaking"), "input")
        self._lang_menu(m.addMenu("Show me"), "target")
        m.addSeparator()
        self._choice_menu(m.addMenu("Captions"), CAPTURE_MODES,
                          self.capture_mode, self.set_capture_mode)
        self._choice_menu(m.addMenu("Response"), RESPONSE_PRESETS,
                          self.response, self.set_response)
        self._choice_menu(m.addMenu("Sensitivity"), SENSITIVITY_PRESETS,
                          self.sensitivity, self.set_sensitivity)
        self._speech_menu(m.addMenu("Speech engine"))
        m.addSeparator()
        self._choice_menu(m.addMenu("Size"), SIZE_PRESETS,
                          self._size_name, self.set_size)
        self._opacity_menu(m.addMenu("Opacity"))
        m.addSeparator()
        act = m.addAction("Quit")
        act.triggered.connect(self.close)
        return m

    @staticmethod
    def _checkable(menu, label, on, handler):
        act = menu.addAction(label)
        act.setCheckable(True)
        act.setChecked(on)
        act.triggered.connect(lambda _checked=False, h=handler: h())
        return act

    def _device_menu(self, menu):
        mics = list(core.list_input_devices())
        loops = list(core.list_loopback_devices())
        if not mics and not loops:
            menu.addAction("No input devices found").setEnabled(False)
            return
        for group, items in (("Microphones", mics), ("Playback", loops)):
            if not items:
                continue
            head = menu.addAction(group)
            head.setEnabled(False)
            for d in items:
                self._checkable(
                    menu, d["label"], d["label"] == self.device_label,
                    lambda d=d: self.select_device(d["index"], d["label"],
                                                   d.get("kind", "mic")))
            menu.addSeparator()

    def _lang_menu(self, menu, which):
        current = self.input_mode if which == "input" else self.target_mode
        setter = self.set_input_mode if which == "input" else self.set_target_mode
        self._checkable(menu, "Auto detect" if which == "input"
                        else "The other language",
                        current == "auto", lambda: setter("auto"))
        menu.addSeparator()
        for code in LANGUAGES:
            self._checkable(menu, LANG_NAMES[code], current == code,
                            lambda c=code: setter(c))

    def _choice_menu(self, menu, options, current, setter):
        for name in options:
            self._checkable(menu, name, name == current,
                            lambda n=name: setter(n))

    def _speech_menu(self, menu):
        for choice in WHISPER_MODELS:
            self._checkable(menu, f"Local - {choice}",
                            self.stt_kind == "local"
                            and self.whisper_choice == choice,
                            lambda c=choice: self.set_whisper_model(c))
        menu.addSeparator()
        self._checkable(menu, "Online (Google)", self.stt_kind == "google",
                        lambda: self.set_stt("google"))

    def _opacity_menu(self, menu):
        for pct in (100, 90, 80, 70, 60, 50):
            self._checkable(menu, f"{pct}%",
                            abs(self.opacity - pct / 100) < 0.01,
                            lambda v=pct / 100: self.set_opacity(v))


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    translator = QtTranslator()
    core.apply_startup_options(translator, sys.argv)
    translator.run()


if __name__ == "__main__":
    main()
