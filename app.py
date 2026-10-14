"""Desktop interface for reviewing and trimming trailing silence in MP3 files."""

from __future__ import annotations

import os
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


BACKGROUND = "#f3f5f7"
FOREGROUND = "#18212b"
MUTED = "#5d6975"
ACCENT = "#16705d"


def format_time(seconds: float) -> str:
    minutes, rest = divmod(max(0.0, seconds), 60)
    return f"{int(minutes):02d}:{rest:05.2f}"


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Corte de silêncio · MP3")
        self.geometry("990x620")
        self.minsize(760, 520)
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
        self.summary = tk.StringVar(value="Adicione arquivos MP3 para começar.")
        self.progress_text = tk.StringVar(value="Pronto")

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
        style.configure("TLabel", background=BACKGROUND, foreground=FOREGROUND, font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 21), foreground=FOREGROUND)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("TButton", font=("Segoe UI", 10), padding=(12, 8))
        style.configure("Accent.TButton", background=ACCENT, foreground="white")
        style.map("Accent.TButton", background=[("active", "#115744"), ("disabled", "#a8b9b2")])
        style.configure("Treeview", font=("Segoe UI", 10), rowheight=29)
        style.configure("Treeview.Heading", font=("Segoe UI Semibold", 10))

    def _layout(self) -> None:
        outer = ttk.Frame(self, padding=22)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="Corte de silêncio", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text="Analise vários MP3 e reúna as cópias em uma pasta separada. Os originais são preservados.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(2, 18))

        toolbar = ttk.Frame(outer)
        toolbar.pack(fill="x", pady=(0, 10))
        self.add_button = ttk.Button(toolbar, text="+ Adicionar MP3", command=self._add_files)
        self.add_button.pack(side="left")
        self.remove_button = ttk.Button(toolbar, text="Remover seleção", command=self._remove_selected)
        self.remove_button.pack(side="left", padx=8)
        self.clear_button = ttk.Button(toolbar, text="Limpar lista", command=self._clear)
        self.clear_button.pack(side="left")

        list_frame = ttk.Frame(outer)
        list_frame.pack(fill="both", expand=True)
        columns = ("file", "duration", "remove", "status")
        self.table = ttk.Treeview(list_frame, columns=columns, show="headings", selectmode="extended")
        for key, label, width, anchor in (
            ("file", "Arquivo", 410, "w"),
            ("duration", "Duração", 95, "center"),
            ("remove", "A cortar", 95, "center"),
            ("status", "Resultado", 275, "w"),
        ):
            self.table.heading(key, text=label)
            self.table.column(key, width=width, anchor=anchor, stretch=(key in ("file", "status")))
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        options = ttk.Frame(outer)
        options.pack(fill="x", pady=(15, 3))
        ttk.Label(options, text="Sensibilidade:").pack(side="left")
        self.sensitivity_box = ttk.Combobox(
            options,
            textvariable=self.sensitivity,
            values=("Conservador (−60 dB)", "Normal (−50 dB)", "Forte (−40 dB)"),
            state="readonly",
            width=24,
        )
        self.sensitivity_box.pack(side="left", padx=(10, 12))
        self.sensitivity_box.bind("<<ComboboxSelected>>", self._reset_analyses)
        ttk.Label(options, text="Limite mínimo: 1 s · margem preservada: 0,25 s", style="Muted.TLabel").pack(side="left")

        destination = ttk.Frame(outer)
        destination.pack(fill="x", pady=(12, 12))
        ttk.Label(destination, text="Pasta base:").pack(side="left")
        self.destination_entry = ttk.Entry(destination, textvariable=self.output_dir)
        self.destination_entry.pack(side="left", fill="x", expand=True, padx=10)
        self.folder_button = ttk.Button(destination, text="Escolher pasta", command=self._choose_folder)
        self.folder_button.pack(side="left")
        ttk.Label(outer, textvariable=self.destination_hint, style="Muted.TLabel").pack(anchor="w", pady=(0, 10))

        actions = ttk.Frame(outer)
        actions.pack(fill="x")
        self.analyze_button = ttk.Button(actions, text="1  Analisar arquivos", command=self._analyze)
        self.analyze_button.pack(side="left")
        self.save_button = ttk.Button(actions, text="2  Salvar áudios", style="Accent.TButton", command=self._save)
        self.save_button.pack(side="left", padx=10)
        self.cancel_button = ttk.Button(actions, text="Cancelar", command=self.cancel_event.set)
        self.cancel_button.pack(side="left")
        self.open_button = ttk.Button(actions, text="Abrir áudios codificados", command=self._open_folder)
        self.open_button.pack(side="right")

        ttk.Label(outer, textvariable=self.summary, style="Muted.TLabel").pack(anchor="w", pady=(14, 5))
        self.progress = ttk.Progressbar(outer, mode="determinate")
        self.progress.pack(fill="x")
        ttk.Label(outer, textvariable=self.progress_text, style="Muted.TLabel").pack(anchor="w", pady=(5, 0))
        self._update_buttons()

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
        for path in self.paths:
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
            self.table.insert("", "end", iid=str(path), values=(
                path.name,
                format_time(analysis.duration) if analysis else "—",
                format_time(analysis.removed) if analysis and analysis.cut_at is not None else "—",
                status,
            ))
        cuts = sum(a.cut_at is not None for a in self.analyses.values())
        if self.paths:
            self.summary.set(
                f"{len(self.paths)} arquivo(s) · {len(self.analyses)} analisado(s) · "
                f"{cuts} com corte. Todos os analisados serão organizados na pasta de destino."
            )
        else:
            self.summary.set("Adicione arquivos MP3 para começar.")
        self._update_buttons()

    def _update_buttons(self) -> None:
        state = "disabled" if self.busy else "normal"
        for button in (self.add_button, self.remove_button, self.clear_button, self.folder_button):
            button.configure(state=state)
        self.sensitivity_box.configure(state="disabled" if self.busy else "readonly")
        self.destination_entry.configure(state=state)
        self.analyze_button.configure(state="normal" if self.paths and not self.busy else "disabled")
        self.save_button.configure(state="normal" if not self.busy and self.analyses else "disabled")
        self.cancel_button.configure(state="normal" if self.busy else "disabled")

    def _start(self, operation: str, worker: object, count: int) -> None:
        self.busy = True
        self.cancel_event.clear()
        self.progress.configure(maximum=max(1, count), value=0)
        self.progress_text.set("Analisando..." if operation == "analyze" else "Salvando...")
        self._update_buttons()
        threading.Thread(target=worker, daemon=True).start()

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
        except queue.Empty:
            pass
        self.after(80, self._poll_events)

    def _close(self) -> None:
        self.cancel_event.set()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
