"""Game Subtitles UA — screen, USB capture and video subtitle translation with Ukrainian speech."""
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
FALLBACK_VOICES = [("Polina (женский)", "uk-UA-PolinaNeural"),
                   ("Ostap (мужской)", "uk-UA-OstapNeural")]
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
        root.geometry("1000x800")
        root.minsize(770, 650)
        try:
            self.config = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.config = {}
        self.stop_event = threading.Event()
        self.events = queue.Queue()
        self.translate_jobs = queue.Queue(maxsize=1)
        self.speech_jobs = queue.Queue(maxsize=1)
        self.speech_serial = 0
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
        self.voices = list(FALLBACK_VOICES)
        self.source = tk.StringVar(value=self.config.get("source", "Карта захвата"))
        self.device = tk.StringVar(value="Видео #0")
        self.file = tk.StringVar(value="")
        self.language = tk.StringVar(value=self.config.get("language", "Английский"))
        self.voice = tk.StringVar(value=self.config.get("voice", FALLBACK_VOICES[0][0]))
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
        ttk.Label(pane, text="Игровые субтитры → украинский перевод и озвучка",
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

        voice_section = ttk.LabelFrame(pane, text="2. Украинская озвучка", padding=8)
        voice_section.pack(fill="x", pady=(10, 0))
        first = ttk.Frame(voice_section)
        first.pack(fill="x")
        ttk.Label(first, text="Голос:").pack(side="left")
        self.voice_box = ttk.Combobox(first, textvariable=self.voice, width=34, state="readonly")
        self.voice_box.pack(side="left", padx=6)
        self.voice_box["values"] = [v[0] for v in self.voices]
        ttk.Button(first, text="Обновить голоса", command=lambda: threading.Thread(
            target=self._scan_voices, daemon=True).start()).pack(side="left")
        ttk.Button(first, text="Прослушать", command=self._test_voice).pack(side="left", padx=5)
        ttk.Button(first, text="Остановить речь", command=self._stop_speech).pack(side="left")
        second = ttk.Frame(voice_section)
        second.pack(fill="x", pady=(10, 3))
        ttk.Label(second, text="Скорость:").pack(side="left")
        ttk.Scale(second, from_=-40, to=60, variable=self.speed,
                  length=230).pack(side="left", padx=8)
        ttk.Label(second, text="Громкость:").pack(side="left", padx=(18, 3))
        ttk.Scale(second, from_=0, to=100, variable=self.volume, length=230).pack(side="left", padx=8)

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
        ttk.Label(out, text="Украинский перевод:").pack(anchor="w", pady=(8, 0))
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
            voices = []
            for item in items:
                if item.get("Locale", "").lower() == "uk-ua" and item.get("ShortName"):
                    name = item["ShortName"]
                    voices.append((name + (" — женский" if item.get("Gender") == "Female" else " — мужской"), name))
            if voices:
                self.events.put(("voices", sorted(voices)))
        except Exception as exc:
            self.events.put(("status", f"Список голосов недоступен: {exc}"))

    def _save(self):
        try:
            SETTINGS_FILE.write_text(json.dumps({"roi": self.roi, "source": self.source.get(),
                "voice": self.voice.get(), "speed": self.speed.get(),
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
        self.last_ocr, self.last_sent, self.ocr_confirmations = "", "", 0
        self.start_button["state"] = "disabled"
        self.stop_button["state"] = "normal"
        self._save()
        choice = next((i for name, i in self.devices if name == self.device.get()), 0)
        arguments = (self.source.get(), choice, self.file.get(), LANGS[self.language.get()])
        threading.Thread(target=self._capture, args=arguments, daemon=True).start()

    def _stop(self):
        self.stop_event.set()
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

    def _capture(self, source, index, filename, language):
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
            while not self.stop_event.is_set():
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
                            self._submit_latest(self.translate_jobs, line)
                if screen is not None:
                    time.sleep(0.04)
        except Exception as exc:
            self.events.put(("status", f"Ошибка захвата/OCR: {exc}"))
        finally:
            if capture is not None:
                capture.release()
            if screen is not None:
                screen.close()
            self.events.put(("finished", None))

    def _translator(self):
        from deep_translator import GoogleTranslator
        while True:
            text = self.translate_jobs.get()
            if self.stop_event.is_set():
                continue
            try:
                result = GoogleTranslator(source="auto", target="uk").translate(text)
                if result:
                    self.events.put(("translation", result))
                    self.speech_serial += 1
                    self._submit_latest(self.speech_jobs,
                                        (self.speech_serial, result, self.voice.get(), self.speed.get()))
            except Exception as exc:
                self.events.put(("status", f"Ошибка перевода: {exc}"))

    def _speaker(self):
        import edge_tts
        import pygame
        mixer_ready = False
        while True:
            serial, text, label, speed = self.speech_jobs.get()
            name = next((n for display, n in self.voices if display == label),
                        FALLBACK_VOICES[0][1])
            temp_path = None
            try:
                if not mixer_ready:
                    pygame.mixer.init()
                    mixer_ready = True
                with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                    temp_path = f.name
                asyncio.run(edge_tts.Communicate(text, name, rate=f"{max(-50, min(75, int(speed))):+d}%").save(temp_path))
                if serial != self.speech_serial:
                    continue
                pygame.mixer.music.load(temp_path)
                pygame.mixer.music.set_volume(self.volume.get()/100)
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy():
                    if serial != self.speech_serial or self.stop_event.is_set():
                        pygame.mixer.music.stop()
                        break
                    time.sleep(0.08)
                pygame.mixer.music.unload()
            except Exception as exc:
                self.events.put(("status", f"Ошибка озвучки: {exc}"))
            finally:
                if temp_path:
                    try:
                        os.unlink(temp_path)
                    except OSError:
                        pass

    def _test_voice(self):
        self.speech_serial += 1
        self._submit_latest(self.speech_jobs, (
            self.speech_serial, "Вітаю! Це український голос у вашій грі.",
            self.voice.get(), self.speed.get()))

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
                    current_name = self.voice.get()
                    self.voices = data
                    self.voice_box["values"] = [name for name, _ in data]
                    if current_name not in [name for name, _ in data]:
                        saved_id = next((key for label, key in FALLBACK_VOICES if label == current_name), None)
                        self.voice.set(next((label for label, key in data if key == saved_id), data[0][0]))
                elif kind == "original":
                    self.subtitle.set(data)
                    self.translation.set("Перевод…")
                elif kind == "translation":
                    self.translation.set(data)
                elif kind == "status":
                    self.status.set(data)
                elif kind == "finished":
                    self._stop()
        except queue.Empty:
            pass
        if redraw:
            self._render()
        self.root.after(120, self._poll)

    def _close(self):
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
