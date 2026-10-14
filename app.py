"""Desktop interface for reviewing and trimming trailing silence in MP3 files."""

from __future__ import annotations

import os
import asyncio
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from audio import (
    Analysis, Cancelled, analyze_mp3, export_cut, export_unchanged,
    find_ffmpeg, processed_directory,
)
from youtube_download import (api_key, download_videos, download_videos_locally,
                              expand_links, parse_links, DownloadError)


BACKGROUND = "#f5f7fa"
SURFACE = "#ffffff"
FOREGROUND = "#1c2938"
MUTED = "#667587"
ACCENT = "#2563eb"
BORDER = "#dde4ec"


def format_time(seconds: float) -> str:
    minutes, rest = divmod(max(0.0, seconds), 60)
    return f"{int(minutes):02d}:{rest:05.2f}"


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Minha coleção de músicas · MP3")
        self.geometry("1060x760")
        self.minsize(720, 500)
        self.configure(bg=BACKGROUND)

        self.paths: list[Path] = []
        self.analyses: dict[Path, Analysis] = {}
        self.errors: dict[Path, str] = {}
        self.saved: dict[Path, Path] = {}
        self.events: queue.Queue[tuple] = queue.Queue()
        self.cancel_event = threading.Event()
        self.busy = False
        self.output_dir = tk.StringVar(value=str(Path.cwd()))
        self.destination_hint = tk.StringVar()
        self.sensitivity = tk.StringVar(value="Conservador (−60 dB)")
        self.summary = tk.StringVar(value="Cole links do YouTube ou adicione arquivos MP3 para começar.")
        self.progress_text = tk.StringVar(value="Pronto")
        self.source_status = tk.StringVar(value="Fonte: RapidAPI · modo local automático se indisponível")
        self.failure_text = tk.StringVar()
        self.link_text: tk.Text
        self.download_failures: dict[str, str] = {}

        self._style()
        self._layout()
        self.output_dir.trace_add("write", self._update_destination_hint)
        self._update_destination_hint()
        self.after(80, self._poll_events)
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _style(self) -> None:
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("TFrame", background=BACKGROUND)
        style.configure("Card.TFrame", background=SURFACE, borderwidth=1,
                        bordercolor=BORDER, relief="solid")
        style.configure("CardInner.TFrame", background=SURFACE)
        style.configure("Footer.TFrame", background=SURFACE)
        style.configure("TLabel", background=BACKGROUND, foreground=FOREGROUND, font=("Segoe UI", 10))
        style.configure("Card.TLabel", background=SURFACE, foreground=FOREGROUND, font=("Segoe UI", 10))
        style.configure("CardMuted.TLabel", background=SURFACE, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("FooterMuted.TLabel", background=SURFACE, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("Error.TLabel", background=SURFACE, foreground="#ae3745", font=("Segoe UI", 9))
        style.configure("Eyebrow.TLabel", foreground=ACCENT, font=("Segoe UI Semibold", 9))
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 23), foreground=FOREGROUND)
        style.configure("Section.TLabel", background=SURFACE, foreground=FOREGROUND, font=("Segoe UI Semibold", 13))
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("TButton", font=("Segoe UI", 10), padding=(11, 7))
        style.configure("Accent.TButton", background=ACCENT, foreground="white",
                        bordercolor=ACCENT, padding=(15, 8))
        style.map("Accent.TButton", background=[("active", "#1d4ed8"), ("disabled", "#a8b9d9")])
        style.configure("Treeview", font=("Segoe UI", 10), rowheight=30,
                        background=SURFACE, fieldbackground=SURFACE, foreground=FOREGROUND,
                        borderwidth=0)
        style.map("Treeview", background=[("selected", "#dbeafe")],
                  foreground=[("selected", FOREGROUND)])
        style.configure("Treeview.Heading", font=("Segoe UI Semibold", 10),
                        background="#f0f4f8", foreground=FOREGROUND, padding=(6, 8))

    def _layout(self) -> None:
        shell = ttk.Frame(self)
        shell.pack(fill="both", expand=True)
        self.page_canvas = tk.Canvas(shell, bg=BACKGROUND, highlightthickness=0, bd=0)
        page_scroll = ttk.Scrollbar(shell, orient="vertical", command=self.page_canvas.yview)
        self.page_canvas.configure(yscrollcommand=page_scroll.set)
        self.page_canvas.pack(side="left", fill="both", expand=True)
        page_scroll.pack(side="right", fill="y")
        outer = ttk.Frame(self.page_canvas, padding=(24, 22, 24, 26))
        self.page_window = self.page_canvas.create_window((0, 0), window=outer, anchor="nw")
        outer.bind("<Configure>", self._update_scroll_region)
        self.page_canvas.bind("<Configure>", self._resize_page)
        self.bind_all("<MouseWheel>", self._on_mousewheel, add="+")
        self.bind_all("<Button-4>", self._on_mousewheel, add="+")
        self.bind_all("<Button-5>", self._on_mousewheel, add="+")

        ttk.Label(outer, text="SEU ESPAÇO DE ÁUDIO", style="Eyebrow.TLabel").pack(anchor="w")
        ttk.Label(outer, text="Músicas prontas para levar", style="Title.TLabel").pack(anchor="w", pady=(2, 3))
        ttk.Label(outer, text="Do YouTube ao pendrive em um só fluxo.",
                  style="Muted.TLabel").pack(anchor="w", pady=(0, 18))

        import_card = ttk.Frame(outer, style="Card.TFrame", padding=20)
        import_card.pack(fill="x", pady=(0, 14))
        ttk.Label(import_card, text="01   Adicione músicas", style="Section.TLabel").pack(anchor="w")
        ttk.Label(import_card, text="Cole links de vídeos ou playlists, um por linha.",
                  style="CardMuted.TLabel").pack(anchor="w", pady=(3, 11))
        self.link_text = tk.Text(import_card, height=3, wrap="word", font=("Segoe UI", 10),
                                 relief="solid", bd=1, highlightthickness=0, bg="#f9fbfd", fg=FOREGROUND,
                                 insertbackground=FOREGROUND, padx=10, pady=9)
        self.link_text.pack(fill="x")

        ttk.Label(import_card, text="SALVAR EM", style="CardMuted.TLabel").pack(anchor="w", pady=(16, 6))
        destination = ttk.Frame(import_card, style="CardInner.TFrame")
        destination.pack(fill="x")
        self.destination_entry = ttk.Entry(destination, textvariable=self.output_dir)
        self.destination_entry.pack(side="left", fill="x", expand=True)
        self.folder_button = ttk.Button(destination, text="Escolher pasta", command=self._choose_folder)
        self.folder_button.pack(side="left", padx=(8, 0))
        self.destination_label = ttk.Label(import_card, textvariable=self.destination_hint,
                                           style="CardMuted.TLabel", wraplength=820)
        self.destination_label.pack(anchor="w", pady=(7, 0))

        import_actions = ttk.Frame(import_card, style="CardInner.TFrame")
        import_actions.pack(fill="x", pady=(15, 0))
        self.download_button = ttk.Button(import_actions, text="Baixar e preparar músicas",
                                          style="Accent.TButton", command=self._import_youtube)
        self.download_button.pack(side="left")
        ttk.Label(import_actions, textvariable=self.source_status,
                  style="CardMuted.TLabel").pack(side="left", padx=(12, 0))

        files_card = ttk.Frame(outer, style="Card.TFrame", padding=20)
        files_card.pack(fill="x", pady=(0, 14))
        ttk.Label(files_card, text="02   Confira os arquivos", style="Section.TLabel").pack(anchor="w")
        ttk.Label(files_card, text="Os downloads aparecem aqui. Você também pode adicionar MP3 do computador.",
                  style="CardMuted.TLabel").pack(anchor="w", pady=(3, 12))
        toolbar = ttk.Frame(files_card, style="CardInner.TFrame")
        toolbar.pack(fill="x", pady=(0, 11))
        self.add_button = ttk.Button(toolbar, text="+ Adicionar MP3", command=self._add_files)
        self.add_button.pack(side="left")
        self.remove_button = ttk.Button(toolbar, text="Remover seleção", command=self._remove_selected)
        self.remove_button.pack(side="left", padx=8)
        self.clear_button = ttk.Button(toolbar, text="Limpar lista", command=self._clear)
        self.clear_button.pack(side="left")

        list_frame = ttk.Frame(files_card, style="CardInner.TFrame")
        list_frame.pack(fill="x")
        columns = ("file", "duration", "remove", "status")
        self.table = ttk.Treeview(list_frame, columns=columns, show="headings",
                                  selectmode="extended", height=8)
        for key, label, width, anchor in (
            ("file", "Arquivo", 250, "w"),
            ("duration", "Duração", 80, "center"),
            ("remove", "A cortar", 80, "center"),
            ("status", "Resultado", 180, "w"),
        ):
            self.table.heading(key, text=label)
            self.table.column(key, width=width, anchor=anchor, stretch=(key in ("file", "status")))
        table_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=table_scroll.set)
        self.table.tag_configure("alternate", background="#f8fafc")
        self.table.pack(side="left", fill="both", expand=True)
        table_scroll.pack(side="right", fill="y")
        ttk.Label(files_card, textvariable=self.summary,
                  style="CardMuted.TLabel").pack(anchor="w", pady=(10, 0))

        finish_card = ttk.Frame(outer, style="Card.TFrame", padding=20)
        finish_card.pack(fill="x")
        ttk.Label(finish_card, text="03   Ajuste o corte", style="Section.TLabel").pack(anchor="w")
        ttk.Label(finish_card, text="Para MP3 locais ou para revisar os arquivos baixados.",
                  style="CardMuted.TLabel").pack(anchor="w", pady=(3, 11))
        options = ttk.Frame(finish_card, style="CardInner.TFrame")
        options.pack(fill="x")
        ttk.Label(options, text="Sensibilidade", style="Card.TLabel").pack(side="left")
        self.sensitivity_box = ttk.Combobox(
            options,
            textvariable=self.sensitivity,
            values=("Conservador (−60 dB)", "Normal (−50 dB)", "Forte (−40 dB)"),
            state="readonly",
            width=24,
        )
        self.sensitivity_box.pack(side="left", padx=(10, 12))
        self.sensitivity_box.bind("<<ComboboxSelected>>", self._reset_analyses)
        ttk.Label(options, text="Silêncio final mínimo: 1 s", style="CardMuted.TLabel").pack(side="left")
        actions = ttk.Frame(finish_card, style="CardInner.TFrame")
        actions.pack(fill="x", pady=(15, 0))
        self.analyze_button = ttk.Button(actions, text="Analisar MP3 da lista", command=self._analyze)
        self.analyze_button.pack(side="left")
        self.save_button = ttk.Button(actions, text="Salvar para pendrive", style="Accent.TButton", command=self._save)
        self.save_button.pack(side="left", padx=10)

        footer = ttk.Frame(self, style="Footer.TFrame", padding=(24, 11))
        footer.pack(fill="x", side="bottom")
        footer_actions = ttk.Frame(footer, style="Footer.TFrame")
        footer_actions.pack(side="right")
        self.cancel_button = ttk.Button(footer_actions, text="Cancelar", command=self.cancel_event.set)
        self.cancel_button.pack(side="left", padx=(0, 8))
        self.open_button = ttk.Button(footer_actions, text="Abrir pasta pronta", command=self._open_folder)
        self.open_button.pack(side="left")
        status = ttk.Frame(footer, style="Footer.TFrame")
        status.pack(side="left", fill="x", expand=True, padx=(0, 14))
        self.progress = ttk.Progressbar(status, mode="determinate")
        self.progress.pack(fill="x")
        ttk.Label(status, textvariable=self.progress_text,
                  style="FooterMuted.TLabel", wraplength=390).pack(anchor="w", pady=(4, 0))
        ttk.Label(status, textvariable=self.failure_text, style="Error.TLabel",
                  wraplength=390).pack(anchor="w", pady=(2, 0))
        self._update_buttons()

    def _update_scroll_region(self, _event: tk.Event) -> None:
        self.page_canvas.configure(scrollregion=self.page_canvas.bbox("all"))

    def _resize_page(self, event: tk.Event) -> None:
        self.page_canvas.itemconfigure(self.page_window, width=event.width)
        self.destination_label.configure(wraplength=max(280, event.width - 110))

    def _on_mousewheel(self, event: tk.Event) -> None:
        target = self.winfo_containing(event.x_root, event.y_root)
        if target is None or target.winfo_toplevel() is not self:
            return
        if event.num == 4:
            direction = -1
        elif event.num == 5:
            direction = 1
        else:
            steps = max(1, abs(event.delta) // 120) if event.delta else 0
            direction = -steps if event.delta > 0 else steps
        if not direction:
            return
        if target in (self.table, self.link_text):
            first, last = target.yview()
            if (direction < 0 and first > 0) or (direction > 0 and last < 1):
                return
        self.page_canvas.yview_scroll(direction, "units")

    def _threshold(self) -> int:
        return {"Conservador (−60 dB)": -60, "Normal (−50 dB)": -50,
                "Forte (−40 dB)": -40}[self.sensitivity.get()]

    def _add_files(self) -> None:
        chosen = filedialog.askopenfilenames(
            title="Selecione os arquivos MP3", filetypes=[("Áudio MP3", "*.mp3"), ("Todos os arquivos", "*.*")]
        )
        for name in chosen:
            path = Path(name).resolve()
            if path.suffix.lower() == ".mp3" and path not in self.paths:
                self.paths.append(path)
        self._refresh()

    def _remove_selected(self) -> None:
        selected = {Path(item) for item in self.table.selection()}
        self.paths = [path for path in self.paths if path not in selected]
        for path in selected:
            self.analyses.pop(path, None)
            self.errors.pop(path, None)
            self.saved.pop(path, None)
        self._refresh()

    def _clear(self) -> None:
        self.paths.clear()
        self.analyses.clear()
        self.errors.clear()
        self.saved.clear()
        self._refresh()

    def _reset_analyses(self, _event: object = None) -> None:
        self.analyses.clear()
        self.errors.clear()
        self.saved.clear()
        self._refresh()

    def _choose_folder(self) -> None:
        selected = filedialog.askdirectory(title="Escolha a pasta base", initialdir=self.output_dir.get())
        if selected:
            self.output_dir.set(selected)

    def _update_destination_hint(self, *_args: object) -> None:
        base = self.output_dir.get().strip()
        self.destination_hint.set(
            "Os MP3 serão organizados em: " + str(processed_directory(Path(base)))
            if base else "Escolha uma pasta base para os MP3 processados."
        )

    def _open_folder(self) -> None:
        folder = processed_directory(Path(self.output_dir.get()))
        if not folder.is_dir():
            messagebox.showinfo("Pasta ainda não criada", "A pasta será criada quando você salvar os cortes.")
            return
        try:
            if sys.platform == "win32":
                os.startfile(str(folder))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except OSError as exc:
            messagebox.showerror("Não foi possível abrir a pasta", str(exc))

    def _refresh(self) -> None:
        for item in self.table.get_children():
            self.table.delete(item)
        for index, path in enumerate(self.paths):
            analysis = self.analyses.get(path)
            if path in self.errors:
                status = "Erro: " + self.errors[path]
            elif path in self.saved:
                status = "Salvo: " + self.saved[path].name
            elif analysis is None:
                status = "Aguardando análise"
            elif analysis.cut_at is None:
                status = "Sem corte · será copiado"
            else:
                status = "Corte em " + format_time(analysis.cut_at)
            self.table.insert("", "end", iid=str(path), tags=("alternate",) if index % 2 else (), values=(
                path.name,
                format_time(analysis.duration) if analysis else "—",
                format_time(analysis.removed) if analysis and analysis.cut_at is not None else "—",
                status,
            ))
        cuts = sum(a.cut_at is not None for a in self.analyses.values())
        if self.paths:
            self.summary.set(
                f"{len(self.paths)} arquivo(s) · {len(self.analyses)} analisado(s) · "
                f"{cuts} com corte · {len(self.saved)} pronto(s) para o pendrive."
            )
        else:
            self.summary.set("Cole links do YouTube ou adicione arquivos MP3 para começar.")
        if self.download_failures:
            self.summary.set(self.summary.get() + f" · {len(self.download_failures)} falha(s) no download")
        self._update_buttons()

    def _update_buttons(self) -> None:
        state = "disabled" if self.busy else "normal"
        for button in (self.add_button, self.remove_button, self.clear_button, self.folder_button):
            button.configure(state=state)
        self.download_button.configure(state=state)
        self.link_text.configure(state=state)
        self.sensitivity_box.configure(state="disabled" if self.busy else "readonly")
        self.destination_entry.configure(state=state)
        self.analyze_button.configure(state="normal" if self.paths and not self.busy else "disabled")
        self.save_button.configure(state="normal" if not self.busy and self.analyses else "disabled")
        self.cancel_button.configure(state="normal" if self.busy else "disabled")

    def _start(self, operation: str, worker: object, count: int) -> None:
        self.busy = True
        self.cancel_event.clear()
        self.progress.configure(maximum=max(1, count), value=0)
        self.progress_text.set({"analyze": "Analisando...", "save": "Salvando...",
                                "youtube": "Lendo links..."}[operation])
        self._update_buttons()
        threading.Thread(target=worker, daemon=True).start()

    def _import_youtube(self) -> None:
        try:
            links = parse_links(self.link_text.get("1.0", "end"))
        except DownloadError as exc:
            messagebox.showerror("Confira os links", str(exc))
            return
        key = api_key()
        output_text = self.output_dir.get().strip()
        if not output_text:
            messagebox.showerror("Escolha uma pasta", "Selecione a pasta base para os arquivos.")
            return
        try:
            ffmpeg = find_ffmpeg()
        except RuntimeError as exc:
            messagebox.showerror("FFmpeg necessário", str(exc))
            return
        base = Path(output_text).expanduser().resolve()
        downloads = base / "Downloads do YouTube"
        output = processed_directory(base)
        threshold = self._threshold()
        self.download_failures.clear()
        self.failure_text.set("")
        self.source_status.set("Conectando à RapidAPI...")

        def worker() -> None:
            try:
                videos = expand_links(links, self.cancel_event)
                if self.cancel_event.is_set():
                    raise Cancelled()
                self.events.put(("youtube_count", len(videos)))
                if not videos:
                    raise DownloadError("Nenhum vídeo disponível nos links informados.")
                callback = lambda *event: self.events.put(event)
                used_fallback = False
                try:
                    if not key:
                        raise DownloadError("Chave RapidAPI ausente.")
                    paths = asyncio.run(download_videos(videos, downloads, key,
                                                        self.cancel_event, callback))
                    self.events.put(("notice", "Fonte: RapidAPI"))
                except Exception:
                    used_fallback = True
                    self.events.put(("notice", "RapidAPI indisponível; usando download local."))
                    paths = download_videos_locally(videos, downloads, ffmpeg,
                                                    self.cancel_event, callback)
                missing = [video for video in videos if not any(
                    path.name.endswith(f"[{video.id}].mp3") for path in paths)]
                if missing and not self.cancel_event.is_set() and not used_fallback:
                    self.events.put(("notice", "Concluindo músicas pendentes por download local."))
                    paths.extend(download_videos_locally(missing, downloads, ffmpeg,
                                                         self.cancel_event, callback))
                if self.cancel_event.is_set():
                    raise Cancelled()
                if not paths:
                    raise DownloadError("Nenhum MP3 foi baixado. Confira os erros dos links e tente novamente.")
                for index, path in enumerate(paths, 1):
                    if self.cancel_event.is_set():
                        raise Cancelled()
                    try:
                        analysis = analyze_mp3(path, threshold, self.cancel_event, ffmpeg)
                        self.events.put(("analysis", path, analysis))
                        result = (export_cut(analysis, output, self.cancel_event, ffmpeg)
                                  if analysis.cut_at is not None else
                                  export_unchanged(analysis, output, self.cancel_event))
                        self.events.put(("saved", path, result))
                    except Cancelled:
                        raise
                    except (OSError, RuntimeError, ValueError) as exc:
                        self.events.put(("error", path, str(exc)))
                    self.events.put(("youtube_prepared", index, len(paths), path.name))
                self.events.put(("done", f"Concluído: {len(paths)}/{len(videos)} MP3 baixado(s). Confira a pasta para pendrive."))
            except Cancelled:
                self.events.put(("done", "Operação cancelada. Os arquivos já concluídos foram mantidos."))
            except Exception as exc:
                self.events.put(("done", "Falha ao importar do YouTube."))
                self.events.put(("fatal", str(exc)))

        self._start("youtube", worker, 1)

    def _analyze(self) -> None:
        try:
            ffmpeg = find_ffmpeg()
        except RuntimeError as exc:
            messagebox.showerror("FFmpeg necessário", str(exc))
            return
        self.analyses.clear()
        self.errors.clear()
        self.saved.clear()
        self._refresh()
        paths = self.paths.copy()
        threshold = self._threshold()

        def worker() -> None:
            done = 0
            try:
                for path in paths:
                    if self.cancel_event.is_set():
                        raise Cancelled()
                    try:
                        result = analyze_mp3(path, threshold, self.cancel_event, ffmpeg)
                        self.events.put(("analysis", path, result))
                    except Cancelled:
                        raise
                    except (OSError, RuntimeError, ValueError) as exc:
                        self.events.put(("error", path, str(exc)))
                    done += 1
                    self.events.put(("progress", done, path.name))
                self.events.put(("done", "Análise concluída."))
            except Cancelled:
                self.events.put(("done", "Análise cancelada."))

        self._start("analyze", worker, len(paths))

    def _save(self) -> None:
        output_text = self.output_dir.get().strip()
        if not output_text:
            messagebox.showerror("Escolha uma pasta", "Informe a pasta onde os MP3 cortados serão salvos.")
            return
        output = Path(output_text).expanduser()
        if not output.is_absolute():
            output = Path.cwd() / output
        output = processed_directory(output)
        to_save = [self.analyses[path] for path in self.paths if path in self.analyses]
        try:
            ffmpeg = find_ffmpeg()
        except RuntimeError as exc:
            messagebox.showerror("FFmpeg necessário", str(exc))
            return

        def worker() -> None:
            saved = 0
            try:
                for index, analysis in enumerate(to_save, 1):
                    if self.cancel_event.is_set():
                        raise Cancelled()
                    try:
                        if analysis.cut_at is not None:
                            result = export_cut(analysis, output, self.cancel_event, ffmpeg)
                        else:
                            result = export_unchanged(analysis, output, self.cancel_event)
                        saved += 1
                        self.events.put(("saved", analysis.path, result))
                    except Cancelled:
                        raise
                    except (OSError, RuntimeError, ValueError) as exc:
                        self.events.put(("error", analysis.path, str(exc)))
                    self.events.put(("progress", index, analysis.path.name))
                self.events.put(("done", f"{saved} arquivo(s) salvo(s) em {output}."))
            except Cancelled:
                self.events.put(("done", f"Salvamento cancelado. {saved} arquivo(s) já salvo(s)."))

        self._start("save", worker, len(to_save))

    def _poll_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "analysis":
                    self.analyses[event[1]] = event[2]
                    self._refresh()
                elif kind == "error":
                    self.errors[event[1]] = event[2]
                    self._refresh()
                elif kind == "saved":
                    self.errors.pop(event[1], None)
                    self.saved[event[1]] = event[2]
                    self._refresh()
                    self.progress_text.set("Salvo: " + event[2].name)
                elif kind == "progress":
                    self.progress.configure(value=event[1])
                    self.progress_text.set(f"{event[1]}/{int(self.progress['maximum'])} · {event[2]}")
                elif kind == "done":
                    self.busy = False
                    self.progress_text.set(event[1])
                    self._update_buttons()
                elif kind == "youtube_count":
                    self.progress.configure(maximum=max(1, event[1] * 2), value=0)
                    self.progress_text.set(f"{event[1]} música(s) encontrada(s). Iniciando downloads...")
                elif kind == "downloading":
                    self.progress_text.set(f"Baixando {event[1]}/{event[2]} · {event[3]}")
                elif kind == "downloaded":
                    path = event[2]
                    self.download_failures.pop(event[1].id, None)
                    remaining_failure = next(iter(self.download_failures.values()), "")
                    self.failure_text.set("Falha no download · " + remaining_failure
                                          if remaining_failure else "")
                    if path not in self.paths:
                        self.paths.append(path)
                    self._refresh()
                elif kind == "download_error":
                    self.download_failures[event[1].id] = f"{event[1].title}: {event[2]}"
                    self.failure_text.set("Falha no download · " + self.download_failures[event[1].id])
                    self.progress_text.set("Falha ao baixar: " + event[1].title)
                    self._refresh()
                elif kind == "download_progress":
                    self.progress.configure(value=event[1])
                elif kind == "youtube_prepared":
                    self.progress.configure(value=event[2] + event[1])
                    self.progress_text.set(f"Preparando {event[1]}/{event[2]} · {event[3]}")
                elif kind == "fatal":
                    messagebox.showerror("Importação não concluída", event[1])
                elif kind == "notice":
                    self.source_status.set(event[1])
        except queue.Empty:
            pass
        self.after(80, self._poll_events)

    def _close(self) -> None:
        self.cancel_event.set()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
