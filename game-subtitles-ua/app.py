"""Game Subtitles UA — Windows OCR with Ukrainian/Russian dual-voice speech."""
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

APP_TITLE = "Game Subtitles UA"
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


def text_similar(a, b):
    a = "".join(c for c in a.casefold() if c.isalnum())
    b = "".join(c for c in b.casefold() if c.isalnum())
    return bool(a and b and SequenceMatcher(None, a, b).ratio() > 0.91)


class Application:
    def __init__(self, root):
        self.root = root
        root.title(APP_TITLE)
        root.geometry("1000x850")
        root.minsize(850, 690)
        try:
            self.config = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.config = {}
        self.stop_event = threading.Event()
        self.events = queue.Queue()
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
        self.voices_by_lang = {key: list(value) for key, value in VOICE_DEFAULTS.items()}
        self.source = tk.StringVar(value=self.config.get("source", "Карта захвата"))
        self.device = tk.StringVar(value="Видео #0")
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
        threading.Thread(target=self._translator, daemon=True).start()
        threading.Thread(target=self._speaker, daemon=True).start()
        threading.Thread(target=self._scan_devices, daemon=True).start()
        threading.Thread(target=self._scan_voices, daemon=True).start()
        root.after(90, self._poll)
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
        ttk.Combobox(first, textvariable=self.source, state="readonly", width=16,
                     values=["Карта захвата", "Экран Windows", "Видео-файл"]).pack(side="left", padx=6)
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

        ttk.Label(pane, text="3. Выделите мышью область субтитров на видео",
                  font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(12, 5))
        self.canvas = tk.Canvas(pane, bg="#111725", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_text(400, 160, text="После запуска здесь появится видео",
                                fill="#dddddd", tags="placeholder", font=("Segoe UI", 14))
        self.canvas.bind("<ButtonPress-1>", self._down)
        self.canvas.bind("<B1-Motion>", self._move)
        self.canvas.bind("<ButtonRelease-1>", self._up)
        controls = ttk.Frame(pane)
        controls.pack(fill="x", pady=(10, 6))
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

    def _scan_devices(self):
        self.events.put(("devices", available_devices()))

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
                "volume": self.volume.get(), "language": self.language.get()},
                ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _down(self, event):
        self.drag = (event.x, event.y)

    def _move(self, event):
        if self.drag is not None:
            self.drawing = (*self.drag, event.x, event.y)

    def _up(self, event):
        if self.drag is None or self.frame is None:
            return
        x1, y1 = self.drag
        x2, y2 = event.x, event.y
        self.drag, self.drawing = None, None
        if abs(x2-x1) < 10 or abs(y2-y1) < 10:
            return
        ox, oy, scale = self.image_box
        h, w = self.frame.shape[:2]
        xlo, xhi = sorted([(x1-ox)/(w*scale), (x2-ox)/(w*scale)])
        ylo, yhi = sorted([(y1-oy)/(h*scale), (y2-oy)/(h*scale)])
        self.roi = [max(0, min(1, xlo)), max(0, min(1, ylo)),
                    max(0, min(1, xhi)), max(0, min(1, yhi))]
        if self.roi[2] <= self.roi[0] or self.roi[3] <= self.roi[1]:
            self.roi = [0.08, 0.68, 0.92, 0.94]
        self.last_ocr, self.last_sent, self.ocr_confirmations = "", "", 0
        self._save()

    def _start(self):
        if not self.ocr_path or not self.ocr_path.is_file():
            messagebox.showerror("Tesseract", "OCR не найден. Переустановите приложение с Tesseract.")
            return
        if self.source.get() == "Видео-файл" and not Path(self.file.get()).is_file():
            messagebox.showerror("Видео", "Выберите существующий видеофайл")
            return
        self.stop_event.clear()
        self.capture_serial += 1
        session = self.capture_serial
        self.voice_turn = 0
        self.last_ocr, self.last_sent, self.ocr_confirmations = "", "", 0
        self.start_button["state"] = "disabled"
        self.stop_button["state"] = "normal"
        self._save()
        choice = next((i for name, i in self.devices if name == self.device.get()), 0)
        arguments = (self.source.get(), choice, self.file.get(), LANGS[self.language.get()])
        threading.Thread(target=self._capture, args=(*arguments, session), daemon=True).start()

    def _stop(self):
        self.stop_event.set()
        self.capture_serial += 1
        self._stop_speech()
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
        q.put_nowait(item)

    def _capture(self, source, index, filename, language, session):
        capture = None
        screen = None
        try:
            if source == "Экран Windows":
                import mss
                screen = mss.mss()
            elif source == "Видео-файл":
                capture = cv2.VideoCapture(filename)
            else:
                capture = cv2.VideoCapture(index, cv2.CAP_DSHOW)
                if not capture.isOpened():
                    capture.release()
                    capture = cv2.VideoCapture(index, cv2.CAP_MSMF)
            if capture is not None and not capture.isOpened():
                raise RuntimeError("Не удалось открыть источник. Закройте OBS и проверьте карту захвата.")
            self.events.put(("status", "Захват работает. Выделите область субтитров."))
            last_check = 0
            blank_at = None
            while not self.stop_event.is_set() and session == self.capture_serial:
                if screen is not None:
                    frame = cv2.cvtColor(np.array(screen.grab(screen.monitors[1])), cv2.COLOR_BGRA2BGR)
                    ok = True
                else:
                    ok, frame = capture.read()
                if not ok:
                    break
                self.events.put(("frame", frame))
                now = time.monotonic()
                if now-last_check >= 0.5:
                    last_check = now
                    h, w = frame.shape[:2]
                    l, t, r, b = self.roi
                    crop = frame[int(t*h):max(int(t*h)+1,int(b*h)),
                                 int(l*w):max(int(l*w)+1,int(r*w))]
                    if crop.size == 0:
                        continue
                    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
                    gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
                    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8)).apply(gray)
                    pytesseract.pytesseract.tesseract_cmd = str(self.ocr_path)
                    args = "--psm 6"
                    if self.tessdata_path and self.tessdata_path.is_dir():
                        args += f' --tessdata-dir "{self.tessdata_path}"'
                    raw = pytesseract.image_to_string(gray, lang=language, config=args)
                    line = " ".join(raw.split()).strip(" -_|•")
                    if len(line) < 2 or len(line) > 350:
                        if blank_at is None:
                            blank_at = now
                        if now-blank_at > 1.2:
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
                            self.events.put(("original", line))
                            self._submit_latest(self.translate_jobs, (session, line, self.target_code))
                if screen is not None:
                    time.sleep(0.04)
        except Exception as exc:
            self.events.put(("status", f"Ошибка захвата/OCR: {exc}"))
        finally:
            if capture is not None:
                capture.release()
            if screen is not None:
                screen.close()
            self.events.put(("finished", session))

    def _translator(self):
        from deep_translator import GoogleTranslator
        while True:
            session, text, target = self.translate_jobs.get()
            if session != self.capture_serial or self.stop_event.is_set():
                continue
            try:
                result = GoogleTranslator(source="auto", target=target).translate(text)
                if result and session == self.capture_serial and not self.stop_event.is_set():
                    self.events.put(("translation", (session, target, result)))
            except Exception as exc:
                self.events.put(("status", f"Ошибка перевода: {exc}"))

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
                self.events.put(("status", f"Ошибка озвучки: {exc}"))
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
        redraw = False
        try:
            for _ in range(100):
                kind, data = self.events.get_nowait()
                if kind == "frame":
                    self.frame = data
                    redraw = True
                elif kind == "devices":
                    self.devices = data
                    self.device_box["values"] = [name for name, _ in data]
                    if self.device.get() not in [name for name, _ in data]:
                        self.device.set(data[0][0])
                elif kind == "voices":
                    self._remember_voices()
                    self.voices_by_lang = data
                    self._update_voice_options()
                elif kind == "original":
                    self.subtitle.set(data)
                    self.translation.set("Перевод…")
                elif kind == "translation":
                    session, language, text = data
                    if session == self.capture_serial and language == self.target_code:
                        self.translation.set(text)
                        self._queue_speech(text)
                elif kind == "status":
                    self.status.set(data)
                elif kind == "finished":
                    if data == self.capture_serial:
                        self._stop()
        except queue.Empty:
            pass
        if redraw:
            self._render()
        self.root.after(120, self._poll)

    def _close(self):
        self.closing = True
        self.stop_event.set()
        self._stop_speech()
        self._save()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    Application(root)
    root.mainloop()
