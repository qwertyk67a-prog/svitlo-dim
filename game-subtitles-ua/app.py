"""Game Subtitles UA — responsive Windows capture and bilingual speech."""
from __future__ import annotations

import asyncio
import json
import os
import queue
import sys
import tempfile
import threading
import time
from pathlib import Path
from difflib import SequenceMatcher

import cv2
import numpy as np
import pytesseract
import tkinter as tk
from PIL import Image, ImageTk
from tkinter import filedialog, ttk, messagebox

APP_TITLE = "Game Subtitles UA v0.3"
LANGS = {"Английский": "eng", "Русский": "rus", "Английский + русский": "eng+rus",
         "Украинский": "ukr", "Японский": "jpn", "Немецкий": "deu",
         "Французский": "fra", "Испанский": "spa", "Польский": "pol"}
VOICE_DEFAULTS = {
    "uk": [
        ("Polina — женский", "uk-UA-PolinaNeural", "Female"),
        ("Ostap — мужской", "uk-UA-OstapNeural", "Male"),
    ],
    "ru": [
        ("Svetlana — женский", "ru-RU-SvetlanaNeural", "Female"),
        ("Dmitry — мужской", "ru-RU-DmitryNeural", "Male"),
    ],
}
VOICE_MODES = (
    "Оба одновременно",
    "По очереди",
    "Только женский",
    "Только мужской",
)

SETTINGS_FILE = Path.home() / ".game_subtitles_ua.json"


def bundled_ocr():
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    candidates = [base / "tesseract" / "tesseract.exe",
                  Path("C:/Program Files/Tesseract-OCR/tesseract.exe"),
                  Path("C:/Program Files (x86)/Tesseract-OCR/tesseract.exe")]
    for path in candidates:
        if path.is_file():
            return path, path.parent / "tessdata"
    return None, None


def available_devices():
    try:
        from pygrabber.dshow_graph import FilterGraph
        names = FilterGraph().get_input_devices()
        if names:
            return [(f"{name} (#{idx})", idx) for idx, name in enumerate(names)]
    except Exception:
        pass
    return [(f"Видео #{idx}", idx) for idx in range(8)]


def normalize_roi(rect, width, height):
    if width <= 0 or height <= 0:
        raise ValueError("Invalid frame dimensions")
    x1, y1, x2, y2 = rect
    l, r = sorted((max(0, min(width, x1)), max(0, min(width, x2))))
    t, b = sorted((max(0, min(height, y1)), max(0, min(height, y2))))
    if r - l < 2 or b - t < 2:
        raise ValueError("Selection is too small")
    return [l / width, t / height, r / width, b / height]


def text_similar(a, b):
    a = "".join(c for c in a.casefold() if c.isalnum())
    b = "".join(c for c in b.casefold() if c.isalnum())
    return bool(a and b and SequenceMatcher(None, a, b).ratio() > 0.91)


class Application:
    def __init__(self, root):
        self.root = root
        root.title(APP_TITLE)
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        root.geometry(f"{min(1140, max(780, screen_width-70))}x"
                      f"{min(900, max(630, screen_height-100))}")
        root.minsize(min(900, screen_width), min(680, screen_height))
        try:
            self.config = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.config = {}
        self.stop_event = threading.Event()
        self.events = queue.Queue(maxsize=256)
        self.preview_lock = threading.Lock()
        self.latest_preview = None
        self.ocr_jobs = queue.Queue(maxsize=1)
        self.capture_lock = threading.Lock()
        self.capture_running = False
        self.active_source = None
        self.ocr_enabled = False
        self.capture_error = None
        self.translate_jobs = queue.Queue(maxsize=1)
        self.speech_jobs = queue.Queue(maxsize=1)
        self.speech_serial = 0
        self.capture_serial = 0
        self.voice_turn = 0
        self.closing = False
        self.roi = self.config.get("roi", [0.08, 0.68, 0.92, 0.94])
        self.frame = None
        self.image = None
        self.image_box = (0, 0, 1.0)
        self.drag = None
        self.drawing = None
        self.last_ocr = ""
        self.last_sent = ""
        self.last_ocr_at = 0.0
        self.ocr_confirmations = 0
        self.devices = [("Видео #0", 0)]
        self.monitors = [("Монитор 1", 1)]
        self.voices_by_lang = {key: list(value) for key, value in VOICE_DEFAULTS.items()}
        self.source = tk.StringVar(value=self.config.get("source", "Экран Windows"))
        if self.source.get() not in ("Экран Windows", "Карта захвата", "Видео-файл"):
            self.source.set("Экран Windows")
        self.device = tk.StringVar(value=self.config.get("device", ""))
        self.device_is_monitor = self.source.get() == "Экран Windows"
        self.file = tk.StringVar(value="")
        self.language = tk.StringVar(value=self.config.get("language", "Английский"))
        self.output_language = tk.StringVar(value=self.config.get("output_language", "Украинский"))
        if self.output_language.get() not in ("Украинский", "Русский"):
            self.output_language.set("Украинский")
        self.target_code = "ru" if self.output_language.get() == "Русский" else "uk"
        self.voice_mode = tk.StringVar(value=self.config.get("voice_mode", "Оба одновременно"))
        if self.voice_mode.get() not in VOICE_MODES:
            self.voice_mode.set("Оба одновременно")
        self.voice_female = tk.StringVar()
        self.voice_male = tk.StringVar()
        self.selected_voices = {}
        for code, defaults in VOICE_DEFAULTS.items():
            self.selected_voices[code] = {
                "Female": self.config.get(f"female_{code}", defaults[0][1]),
                "Male": self.config.get(f"male_{code}", defaults[1][1]),
            }
        self.speed = tk.IntVar(value=self.config.get("speed", 0))
        self.volume = tk.IntVar(value=self.config.get("volume", 90))
        self.subtitle = tk.StringVar(value="—")
        self.translation = tk.StringVar(value="—")
        self.status = tk.StringVar(value="Ожидание")
        self.ocr_path, self.tessdata_path = bundled_ocr()
        self._ui()
        threading.Thread(target=self._ocr_worker, daemon=True).start()
        threading.Thread(target=self._translator, daemon=True).start()
        threading.Thread(target=self._speaker, daemon=True).start()
        threading.Thread(target=self._scan_devices, daemon=True).start()
        threading.Thread(target=self._scan_voices, daemon=True).start()
        root.after(110, self._poll)
        root.protocol("WM_DELETE_WINDOW", self._close)

    def _ui(self):
        pane = ttk.Frame(self.root, padding=12)
        pane.pack(fill="both", expand=True)
        ttk.Label(pane, text="Игровые субтитры → перевод и озвучка на украинском или русском",
                  font=("Segoe UI", 15, "bold")).pack(anchor="w", pady=(0, 10))

        section = ttk.LabelFrame(pane, text="1. Источник изображения", padding=8)
        section.pack(fill="x")
        first = ttk.Frame(section)
        first.pack(fill="x", pady=3)
        ttk.Label(first, text="Источник:").pack(side="left")
        self.source_box = ttk.Combobox(first, textvariable=self.source, state="readonly", width=16,
                     values=["Экран Windows", "Карта захвата", "Видео-файл"])
        self.source_box.pack(side="left", padx=6)
        self.source_box.bind("<<ComboboxSelected>>", self._source_changed)
        ttk.Label(first, text="Устройство:").pack(side="left", padx=(12, 0))
        self.device_box = ttk.Combobox(first, textvariable=self.device, state="readonly", width=35)
        self.device_box.pack(side="left", padx=6, fill="x", expand=True)
        ttk.Button(first, text="Обновить", command=lambda: threading.Thread(
            target=self._scan_devices, daemon=True).start()).pack(side="left")
        second = ttk.Frame(section)
        second.pack(fill="x", pady=3)
        ttk.Label(second, text="Видеофайл:").pack(side="left")
        ttk.Entry(second, textvariable=self.file).pack(side="left", padx=6, fill="x", expand=True)
        ttk.Button(second, text="Выбрать", command=self._choose_file).pack(side="left")
        ttk.Label(second, text="Язык игры:").pack(side="left", padx=(10, 4))
        ttk.Combobox(second, textvariable=self.language, values=list(LANGS),
                     state="readonly", width=21).pack(side="left")

        voice_section = ttk.LabelFrame(pane, text="2. Перевод и озвучка", padding=8)
        voice_section.pack(fill="x", pady=(10, 0))
        first = ttk.Frame(voice_section)
        first.pack(fill="x", pady=(0, 6))
        ttk.Label(first, text="Язык перевода:").pack(side="left")
        out_box = ttk.Combobox(first, textvariable=self.output_language,
                               values=["Украинский", "Русский"], state="readonly", width=16)
        out_box.pack(side="left", padx=(6, 18))
        out_box.bind("<<ComboboxSelected>>", self._output_language_changed)
        ttk.Label(first, text="Режим голосов:").pack(side="left")
        ttk.Combobox(first, textvariable=self.voice_mode, values=VOICE_MODES,
                     state="readonly", width=23).pack(side="left", padx=6)

        second = ttk.Frame(voice_section)
        second.pack(fill="x", pady=(2, 5))
        ttk.Label(second, text="Женский:").pack(side="left")
        self.female_box = ttk.Combobox(second, textvariable=self.voice_female,
                                       state="readonly", width=30)
        self.female_box.pack(side="left", padx=(6, 15))
        self.female_box.bind("<<ComboboxSelected>>", self._remember_voices)
        ttk.Label(second, text="Мужской:").pack(side="left")
        self.male_box = ttk.Combobox(second, textvariable=self.voice_male,
                                     state="readonly", width=30)
        self.male_box.pack(side="left", padx=6)
        self.male_box.bind("<<ComboboxSelected>>", self._remember_voices)
        self._update_voice_options()

        third = ttk.Frame(voice_section)
        third.pack(fill="x")
        ttk.Button(third, text="Обновить голоса", command=lambda: threading.Thread(
            target=self._scan_voices, daemon=True).start()).pack(side="left")
        ttk.Button(third, text="Прослушать выбранный режим",
                   command=self._test_voice).pack(side="left", padx=8)
        ttk.Button(third, text="Остановить речь", command=self._stop_speech).pack(side="left")
        ttk.Label(third, text="Одновременно: оба голоса читают одну фразу.",
                  foreground="#666666").pack(side="left", padx=12)

        fourth = ttk.Frame(voice_section)
        fourth.pack(fill="x", pady=(7, 1))
        ttk.Label(fourth, text="Скорость:").pack(side="left")
        ttk.Scale(fourth, from_=-40, to=60, variable=self.speed,
                  length=230).pack(side="left", padx=8)
        ttk.Label(fourth, text="Громкость:").pack(side="left", padx=(18, 3))
        ttk.Scale(fourth, from_=0, to=100, variable=self.volume, length=230).pack(side="left", padx=8)

        select_controls = ttk.Frame(pane)
        select_controls.pack(fill="x", pady=(12, 5))
        ttk.Label(select_controls, text="3. Выбор области субтитров",
                  font=("Segoe UI", 11, "bold")).pack(side="left")
        ttk.Button(select_controls, text="ВЫБРАТЬ ОБЛАСТЬ НА ЭКРАНЕ",
                   command=self._select_screen_area).pack(side="left", padx=14)
        ttk.Label(select_controls, text="Карта захвата: выделите рамку на видео ниже.",
                  foreground="#666666").pack(side="left")
        self.canvas = tk.Canvas(pane, bg="#111725", highlightthickness=0, height=360, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_text(400, 160, text="Выберите источник и нажмите ПРЕДПРОСМОТР",
                                fill="#dddddd", tags="placeholder", font=("Segoe UI", 14))
        self.canvas.bind("<ButtonPress-1>", self._down)
        self.canvas.bind("<B1-Motion>", self._move)
        self.canvas.bind("<ButtonRelease-1>", self._up)
        controls = ttk.Frame(pane)
        controls.pack(fill="x", pady=(10, 6))
        self.preview_button = ttk.Button(controls, text="ПРЕДПРОСМОТР", command=self._preview)
        self.preview_button.pack(side="left", padx=(0, 8))
        self.start_button = ttk.Button(controls, text="НАЧАТЬ ПЕРЕВОД", command=self._start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(controls, text="ОСТАНОВИТЬ", command=self._stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        ttk.Label(controls, textvariable=self.status).pack(side="left", padx=12)

        out = ttk.LabelFrame(pane, text="Субтитры", padding=8)
        out.pack(fill="x")
        ttk.Label(out, text="Оригинал:").pack(anchor="w")
        ttk.Label(out, textvariable=self.subtitle, wraplength=900).pack(anchor="w")
        self.translation_title = ttk.Label(out, text="Перевод:")
        self.translation_title.pack(anchor="w", pady=(8, 0))
        self._update_translation_title()
        ttk.Label(out, textvariable=self.translation, wraplength=900,
                  font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(pane, text="Для перевода и озвучки нужен интернет. Текст передаётся внешним сервисам.",
                  foreground="#666666").pack(anchor="w", pady=8)

    def _choose_file(self):
        filename = filedialog.askopenfilename(filetypes=[("Видео", "*.mp4 *.avi *.mkv *.mov"), ("Все", "*.*")])
        if filename:
            self.file.set(filename)
            self.source.set("Видео-файл")
            self._source_changed()

    def _scan_devices(self):
        cameras = available_devices()
        monitors = []
        try:
            import mss
            with mss.mss() as screen:
                for i, monitor in enumerate(screen.monitors[1:], 1):
                    monitors.append((f"Монитор {i} — {monitor['width']}×{monitor['height']} "
                                     f"({monitor['left']},{monitor['top']})", i))
        except Exception as exc:
            self._event("status", f"Не удалось определить мониторы: {exc}")
        self._event("devices", (cameras, monitors))

    def _source_changed(self, _event=None):
        if self.capture_running:
            self._stop()
        self._show_source_devices()
        self._save()

    def _show_source_devices(self):
        is_screen = self.source.get() == "Экран Windows"
        items = self.monitors if is_screen else self.devices
        old = self.device.get() if self.device_is_monitor == is_screen else ""
        labels = [name for name, idx in items]
        self.device_box["values"] = labels
        self.device.set(old if old in labels else (labels[0] if labels else ""))
        self.device_is_monitor = is_screen

    def _event(self, kind, data):
        try:
            self.events.put_nowait((kind, data))
        except queue.Full:
            # Frames never enter this queue. Keep UI memory usage bounded.
            pass

    def _put_latest_preview(self, frame, session):
        with self.preview_lock:
            self.latest_preview = (session, frame)

    def _select_screen_area(self):
        if self.source.get() != "Экран Windows":
            messagebox.showinfo("Выбор области", "Выберите источник «Экран Windows»."
                                " Для карты захвата выделите область на видеопредпросмотре.")
            return
        monitor_id = next((idx for name, idx in self.monitors if name == self.device.get()), 1)
        self.root.withdraw()
        try:
            # Hide the main window before taking the frozen screenshot for selection.
            self.root.update()
            import mss
            with mss.mss() as screen:
                if monitor_id >= len(screen.monitors):
                    raise RuntimeError("Монитор больше не доступен. Обновите устройства.")
                monitor = dict(screen.monitors[monitor_id])
                shot = np.array(screen.grab(monitor))
            screenshot = Image.fromarray(cv2.cvtColor(shot, cv2.COLOR_BGRA2RGB))
            w, h = screenshot.size
            overlay = tk.Toplevel(self.root)
            overlay.withdraw()
            overlay.overrideredirect(True)
            overlay.geometry(f"{w}x{h}{monitor['left']:+d}{monitor['top']:+d}")
            overlay.attributes("-topmost", True)
            canvas = tk.Canvas(overlay, width=w, height=h, highlightthickness=0,
                               bg="black", cursor="crosshair")
            canvas.pack(fill="both", expand=True)
            photo = ImageTk.PhotoImage(screenshot, master=overlay)
            canvas.image = photo
            canvas.create_image(0, 0, anchor="nw", image=photo)
            canvas.create_rectangle(8, 8, min(w-8, 800), 62,
                                    fill="#111725", outline="#ffffff")
            canvas.create_text(20, 36, anchor="w", fill="#ffffff",
                               font=("Segoe UI", 15, "bold"),
                               text="Выделите субтитры мышью. ESC — отмена.")
            drag = [None]

            def close_overlay():
                overlay.destroy()
                if not self.closing:
                    self.root.deiconify()
                    self.root.lift()

            def on_down(event):
                drag[0] = (event.x, event.y)
                canvas.delete("selected-area")

            def on_move(event):
                if drag[0] is not None:
                    canvas.delete("selected-area")
                    canvas.create_rectangle(*drag[0], event.x, event.y,
                                            outline="#00ff99", width=4, tags="selected-area")

            def on_up(event):
                if drag[0] is None:
                    return
                x1, y1 = drag[0]
                x2, y2 = event.x, event.y
                drag[0] = None
                if abs(x2 - x1) >= 12 and abs(y2 - y1) >= 12:
                    self.roi = normalize_roi((x1, y1, x2, y2), w, h)
                    self._reset_ocr_state()
                    self._save()
                    self.status.set("Область выбрана. Нажмите «НАЧАТЬ ПЕРЕВОД».")
                close_overlay()

            canvas.bind("<ButtonPress-1>", on_down)
            canvas.bind("<B1-Motion>", on_move)
            canvas.bind("<ButtonRelease-1>", on_up)
            overlay.bind("<Escape>", lambda _event: close_overlay())
            overlay.deiconify()
            overlay.lift()
            overlay.focus_force()
        except Exception as exc:
            self.root.deiconify()
            self.status.set(f"Ошибка выбора области: {exc}")

    def _scan_voices(self):
        try:
            import edge_tts
            items = asyncio.run(edge_tts.list_voices())
            voices = {code: list(defaults) for code, defaults in VOICE_DEFAULTS.items()}
            for item in items:
                locale = item.get("Locale", "").lower()
                code = {"uk-ua": "uk", "ru-ru": "ru"}.get(locale)
                gender = item.get("Gender")
                name = item.get("ShortName")
                if code and name and gender in ("Female", "Male"):
                    if name not in [entry[1] for entry in voices[code]]:
                        label = name.split("-", 2)[-1] + (" — женский" if gender == "Female" else " — мужской")
                        voices[code].append((label, name, gender))
            for code in voices:
                voices[code].sort(key=lambda item: (0 if item[2] == "Female" else 1, item[0]))
            self.events.put(("voices", voices))
        except Exception as exc:
            self.events.put(("status", f"Список голосов недоступен: {exc}. Доступны стандартные голоса."))

    def _language_code(self):
        return "ru" if self.output_language.get() == "Русский" else "uk"

    def _update_translation_title(self):
        if hasattr(self, "translation_title"):
            name = "Русский" if self.target_code == "ru" else "Украинский"
            self.translation_title.configure(text=f"{name} перевод:")

    def _output_language_changed(self, _event=None):
        self._remember_voices()
        self.target_code = self._language_code()
        self._update_voice_options()
        self._update_translation_title()
        self.translation.set("—")
        self._stop_speech()

    def _update_voice_options(self):
        code = self.target_code
        entries = self.voices_by_lang[code]
        for gender, widget, variable in (
            ("Female", self.female_box, self.voice_female),
            ("Male", self.male_box, self.voice_male),
        ):
            available = [(label, identifier) for label, identifier, g in entries if g == gender]
            widget["values"] = [label for label, identifier in available]
            voice_id = self.selected_voices[code][gender]
            selected = next((label for label, identifier in available if identifier == voice_id), None)
            if selected is None:
                selected, voice_id = available[0]
                self.selected_voices[code][gender] = voice_id
            variable.set(selected)

    def _remember_voices(self, _event=None):
        code = self.target_code
        for gender, variable in (("Female", self.voice_female), ("Male", self.voice_male)):
            label = variable.get()
            voice_id = next((identifier for item_label, identifier, item_gender
                             in self.voices_by_lang[code]
                             if item_gender == gender and item_label == label), None)
            if voice_id:
                self.selected_voices[code][gender] = voice_id

    def _selected_voice_ids(self, mode):
        self._remember_voices()
        options = self.selected_voices[self.target_code]
        female = options["Female"]
        male = options["Male"]
        if mode == "Только женский":
            return (female,)
        if mode == "Только мужской":
            return (male,)
        if mode == "По очереди":
            voice = female if self.voice_turn % 2 == 0 else male
            self.voice_turn += 1
            return (voice,)
        return (female, male)

    def _queue_speech(self, text, is_test=False):
        voices = self._selected_voice_ids(self.voice_mode.get())
        self.speech_serial += 1
        self._submit_latest(self.speech_jobs, (
            self.speech_serial, text, voices, self.speed.get(),
            self.volume.get(), is_test
        ))

    def _save(self):
        try:
            SETTINGS_FILE.write_text(json.dumps({"roi": self.roi, "source": self.source.get(),
                "output_language": self.output_language.get(),
                "voice_mode": self.voice_mode.get(),
                "female_uk": self.selected_voices["uk"]["Female"],
                "male_uk": self.selected_voices["uk"]["Male"],
                "female_ru": self.selected_voices["ru"]["Female"],
                "male_ru": self.selected_voices["ru"]["Male"],
                "speed": self.speed.get(),
                "volume": self.volume.get(), "language": self.language.get(),
                "device": self.device.get()},
                ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _down(self, event):
        if self.frame is None:
            self.status.set("Сначала нажмите «ПРЕДПРОСМОТР».")
            return
        self.drag = (event.x, event.y)
        self.canvas.delete("selection-drag")

    def _move(self, event):
        if self.drag is not None:
            self.canvas.delete("selection-drag")
            self.canvas.create_rectangle(*self.drag, event.x, event.y,
                                         outline="#ffff22", width=3, tags="selection-drag")

    def _up(self, event):
        if self.drag is None or self.frame is None:
            return
        x1, y1 = self.drag
        x2, y2 = event.x, event.y
        self.drag = None
        self.canvas.delete("selection-drag")
        if abs(x2-x1) < 10 or abs(y2-y1) < 10:
            return
        ox, oy, scale = self.image_box
        h, w = self.frame.shape[:2]
        try:
            self.roi = normalize_roi(((x1-ox)/scale, (y1-oy)/scale,
                                      (x2-ox)/scale, (y2-oy)/scale), w, h)
        except ValueError:
            self.status.set("Область слишком мала или вне изображения.")
            return
        self._reset_ocr_state()
        self.status.set("Область субтитров выбрана.")
        self._save()
        self._render()

    def _reset_ocr_state(self):
        self.last_ocr = ""
        self.last_sent = ""
        self.ocr_confirmations = 0
        self.last_ocr_at = 0
        self._submit_latest(self.ocr_jobs, None)

    def _capture_options(self):
        source = self.source.get()
        selected = self.device.get()
        items = self.monitors if source == "Экран Windows" else self.devices
        if source != "Видео-файл" and not items:
            raise RuntimeError("Устройство не найдено. Нажмите «Обновить».")
        if source != "Видео-файл" and selected not in [name for name, idx in items]:
            raise RuntimeError("Выберите доступное устройство.")
        idx = next((value for name, value in items if name == selected), 0)
        filename = self.file.get() if source == "Видео-файл" else ""
        if source == "Видео-файл" and not Path(filename).is_file():
            raise RuntimeError("Выберите существующий видеофайл.")
        language = LANGS.get(self.language.get(), "eng")
        return (source, idx, filename, language)

    def _preview(self):
        try:
            arguments = self._capture_options()
        except Exception as exc:
            self.status.set(str(exc))
            return
        if self.capture_running and self.active_source == arguments:
            self.status.set("Предпросмотр уже работает.")
            return
        if self.capture_running:
            self._stop()
        self.stop_event.clear()
        self.capture_serial += 1
        session = self.capture_serial
        self.capture_running = True
        self.active_source = arguments
        self.ocr_enabled = False
        self.capture_error = None
        with self.preview_lock:
            self.latest_preview = None
        self.frame = None
        self._reset_ocr_state()
        self.status.set("Подключение к источнику изображения…")
        self.stop_button["state"] = "normal"
        self.start_button["state"] = "normal"
        self._save()
        threading.Thread(target=self._capture,
                         args=(*arguments, session), daemon=True,
                         name="VideoCapture").start()

    def _start(self):
        if not self.ocr_path or not self.ocr_path.is_file():
            self.status.set("OCR не найден. Сборка приложения повреждена.")
            return
        try:
            arguments = self._capture_options()
        except Exception as exc:
            self.status.set(str(exc))
            return
        if not self.capture_running or self.active_source != arguments:
            self._preview()
        if not self.capture_running:
            return
        self.ocr_enabled = True
        self._reset_ocr_state()
        self.start_button["state"] = "disabled"
        self.status.set("Перевод запущен. Распознавание текста каждые 0,6 секунды.")

    def _stop(self):
        self.stop_event.set()
        self.capture_serial += 1
        self.capture_running = False
        self.ocr_enabled = False
        self.active_source = None
        self._stop_speech()
        with self.preview_lock:
            self.latest_preview = None
        self.start_button["state"] = "normal"
        self.stop_button["state"] = "disabled"
        self.status.set("Остановлено")

    def _stop_speech(self):
        self.speech_serial += 1

    def _submit_latest(self, q, item):
        try:
            while True:
                q.get_nowait()
        except queue.Empty:
            pass
        try:
            q.put_nowait(item)
        except queue.Full:
            pass

    def _capture(self, source, index, filename, language, session):
        capture = None
        screen = None
        frame_count = 0
        last_preview = 0
        last_ocr = 0
        error = None
        try:
            # Serializes opening/closing hardware devices on rapid start-stop.
            with self.capture_lock:
                if session != self.capture_serial or self.stop_event.is_set():
                    return
                try:
                    if source == "Экран Windows":
                        import mss
                        screen = mss.mss()
                        if index < 1 or index >= len(screen.monitors):
                            raise RuntimeError("Монитор не найден. Обновите список.")
                        region = dict(screen.monitors[index])
                    elif source == "Видео-файл":
                        capture = cv2.VideoCapture(filename)
                    else:
                        capture = cv2.VideoCapture(index, cv2.CAP_DSHOW)
                        if not capture.isOpened():
                            capture.release()
                            capture = cv2.VideoCapture(index, cv2.CAP_MSMF)
                        if capture.isOpened():
                            capture.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                    if capture is not None and not capture.isOpened():
                        raise RuntimeError("Источник не открылся. Проверьте HDMI, USB и доступ приложения к камере.")
                    self._event("status", "Источник открыт. Выделите область субтитров.")
                    while not self.stop_event.is_set() and session == self.capture_serial:
                        if screen is not None:
                            frame = cv2.cvtColor(np.asarray(screen.grab(region)), cv2.COLOR_BGRA2BGR)
                            ok = True
                        else:
                            ok, frame = capture.read()
                        if not ok or frame is None:
                            raise RuntimeError("Источник не выдаёт кадры или видео закончилось.")
                        now = time.monotonic()
                        frame_count += 1
                        if now - last_preview >= 0.15:
                            self._put_latest_preview(frame, session)
                            last_preview = now
                        if self.ocr_enabled and now - last_ocr >= 0.60:
                            self._submit_latest(self.ocr_jobs, (
                                session, frame.copy(), language, tuple(self.roi),
                                self.target_code
                            ))
                            last_ocr = now
                        if source == "Экран Windows":
                            time.sleep(0.08)
                        elif source == "Видео-файл":
                            fps = capture.get(cv2.CAP_PROP_FPS)
                            time.sleep(min(0.06, max(0.01, 1.0 / fps if fps > 0 else 0.03)))
                finally:
                    if capture is not None:
                        capture.release()
                    if screen is not None:
                        screen.close()
        except Exception as exc:
            error = str(exc)
            self._event("capture_error", (session, error))
        finally:
            self._event("finished", session)

    def _ocr_worker(self):
        blank_at = None
        while not self.closing:
            item = self.ocr_jobs.get()
            if item is None:
                blank_at = None
                continue
            session, frame, language, roi, target = item
            if session != self.capture_serial or not self.ocr_enabled:
                continue
            try:
                h, w = frame.shape[:2]
                l, t, r, b = roi
                crop = frame[int(t*h):max(int(t*h)+1,int(b*h)),
                             int(l*w):max(int(l*w)+1,int(r*w))]
                if crop.size == 0:
                    continue
                gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
                # Limit OCR image scale to avoid exhausting RAM on 4K screens.
                scale = min(2.0, max(1.0, 1800.0/max(gray.shape[:2])))
                if scale > 1:
                    gray = cv2.resize(gray, None, fx=scale, fy=scale,
                                      interpolation=cv2.INTER_CUBIC)
                gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8)).apply(gray)
                pytesseract.pytesseract.tesseract_cmd = str(self.ocr_path)
                args = "--psm 6"
                if self.tessdata_path and self.tessdata_path.is_dir():
                    args += f' --tessdata-dir "{self.tessdata_path}"'
                raw = pytesseract.image_to_string(gray, lang=language, config=args,
                                                   timeout=4.0)
                if session != self.capture_serial or not self.ocr_enabled:
                    continue
                line = " ".join(raw.split()).strip(" -_|•")
                now = time.monotonic()
                if len(line) < 2 or len(line) > 350:
                    if blank_at is None:
                        blank_at = now
                    if now - blank_at > 1.2:
                        self.last_sent = ""
                    self.ocr_confirmations = 0
                else:
                    blank_at = None
                    if text_similar(line, self.last_ocr):
                        self.ocr_confirmations += 1
                    else:
                        self.ocr_confirmations = 1
                    self.last_ocr = line
                    if self.ocr_confirmations >= 2 and not text_similar(line, self.last_sent):
                        self.last_sent = line
                        self._event("original", (session, line))
                        self._submit_latest(self.translate_jobs, (session, line, target))
            except RuntimeError as exc:
                self._event("status", f"Ошибка OCR: {exc}")
            except Exception as exc:
                self._event("status", f"Ошибка распознавания: {exc}")

    def _translator(self):
        from deep_translator import GoogleTranslator
        while True:
            session, text, target = self.translate_jobs.get()
            if session != self.capture_serial or self.stop_event.is_set():
                continue
            try:
                result = GoogleTranslator(source="auto", target=target).translate(text)
                if result and session == self.capture_serial and not self.stop_event.is_set():
                    self._event("translation", (session, target, result))
            except Exception as exc:
                self._event("status", f"Ошибка перевода: {exc}")

    def _speaker(self):
        """Use separate mixer channels for two truly simultaneous TTS voices."""
        import edge_tts
        import pygame
        mixer_ready = False
        while True:
            serial, text, voices, speed, volume, is_test = self.speech_jobs.get()
            paths = []
            channels = []
            sounds = []
            try:
                if serial != self.speech_serial:
                    continue
                if not mixer_ready:
                    pygame.mixer.init(frequency=24000, channels=2, buffer=1024)
                    pygame.mixer.set_num_channels(8)
                    mixer_ready = True
                for _ in voices:
                    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as handle:
                        paths.append(handle.name)

                async def synthesize_all():
                    await asyncio.gather(*(
                        edge_tts.Communicate(text, voice,
                            rate=f"{max(-50, min(75, int(speed))):+d}%").save(path)
                        for voice, path in zip(voices, paths)
                    ))
                asyncio.run(synthesize_all())
                if serial != self.speech_serial or (self.stop_event.is_set() and not is_test):
                    continue
                sounds = [pygame.mixer.Sound(path) for path in paths]
                mix_volume = max(0.0, min(1.0, volume/100.0))
                for idx, sound in enumerate(sounds):
                    channel = pygame.mixer.Channel(idx)
                    # Two voices share the output; reduce summed amplitude.
                    channel.set_volume(mix_volume * (0.65 if len(sounds) == 2 else 1.0))
                    channel.play(sound)
                    channels.append(channel)
                while any(channel.get_busy() for channel in channels):
                    if (serial != self.speech_serial or self.closing
                            or (self.stop_event.is_set() and not is_test)):
                        break
                    time.sleep(0.05)
            except Exception as exc:
                self._event("status", f"Ошибка озвучки: {exc}")
            finally:
                for channel in channels:
                    channel.stop()
                sounds.clear()
                for path in paths:
                    try:
                        os.unlink(path)
                    except OSError:
                        pass

    def _test_voice(self):
        text = ("Привет! Это проверка женского и мужского голоса."
                if self.target_code == "ru"
                else "Вітаю! Це перевірка жіночого та чоловічого голосу.")
        self._queue_speech(text, is_test=True)

    def _render(self):
        if self.frame is None:
            return
        h, w = self.frame.shape[:2]
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        if cw < 10 or ch < 10:
            return
        scale = min(cw/w, ch/h)
        dw, dh = max(1, int(w*scale)), max(1, int(h*scale))
        ox, oy = (cw-dw)//2, (ch-dh)//2
        rgb = cv2.cvtColor(cv2.resize(self.frame, (dw, dh)), cv2.COLOR_BGR2RGB)
        self.image = ImageTk.PhotoImage(Image.fromarray(rgb))
        self.canvas.delete("video")
        self.canvas.create_image(ox, oy, anchor="nw", image=self.image, tags="video")
        self.canvas.tag_lower("video")
        self.canvas.delete("roi")
        l, t, r, b = self.roi
        self.canvas.create_rectangle(ox+l*dw, oy+t*dh, ox+r*dw, oy+b*dh,
                                     outline="#11eba9", width=3, tags="roi")
        if self.drawing is not None:
            self.canvas.create_rectangle(*self.drawing, outline="yellow", width=2, tags="roi")
        self.canvas.delete("placeholder")
        self.image_box = (ox, oy, scale)

    def _poll(self):
        if self.closing:
            return
        # Get only the most recent frame. Never accumulate full-resolution frames.
        with self.preview_lock:
            latest = self.latest_preview
            self.latest_preview = None
        if latest is not None:
            session, frame = latest
            if session == self.capture_serial:
                self.frame = frame
                self._render()
        try:
            for _ in range(32):
                kind, data = self.events.get_nowait()
                if kind == "devices":
                    cameras, monitors = data
                    self.devices = cameras
                    self.monitors = monitors
                    self._show_source_devices()
                elif kind == "voices":
                    self._remember_voices()
                    self.voices_by_lang = data
                    self._update_voice_options()
                elif kind == "original":
                    session, line = data
                    if session == self.capture_serial:
                        self.subtitle.set(line)
                        self.translation.set("Перевод…")
                elif kind == "translation":
                    session, language, text = data
                    if session == self.capture_serial and language == self.target_code:
                        self.translation.set(text)
                        self._queue_speech(text)
                elif kind == "status":
                    self.status.set(data)
                elif kind == "capture_error":
                    session, msg = data
                    if session == self.capture_serial:
                        self.capture_error = msg
                elif kind == "finished":
                    if data == self.capture_serial:
                        error = self.capture_error
                        self._stop()
                        if error:
                            self.status.set(f"Ошибка захвата: {error}")
        except queue.Empty:
            pass
        self.root.after(110, self._poll)

    def _close(self):
        self.closing = True
        self.stop_event.set()
        self._stop_speech()
        self._save()
        self.root.destroy()


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (OSError, AttributeError):
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except (OSError, AttributeError):
                pass
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    Application(root)
    root.mainloop()
