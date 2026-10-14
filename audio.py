"""Conservative detection and lossless trimming of trailing MP3 silence."""

from __future__ import annotations

from array import array
from dataclasses import dataclass
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid


SAMPLE_RATE = 16_000
CHANNELS = 2
WINDOW_SECONDS = 0.05
MIN_SILENCE_SECONDS = 1.0
SAFETY_SECONDS = 0.25
PROCESSED_FOLDER_NAME = "Áudios codificados"


class Cancelled(Exception):
    """The current batch was cancelled by the user."""


@dataclass(frozen=True)
class Analysis:
    path: Path
    duration: float
    cut_at: float | None
    trailing_silence: float
    threshold_db: int
    size: int
    modified_ns: int

    @property
    def removed(self) -> float:
        return max(0.0, self.duration - self.cut_at) if self.cut_at is not None else 0.0


def find_ffmpeg() -> str:
    local_packages = Path(__file__).resolve().parent / ".vendor"
    if local_packages.is_dir() and str(local_packages) not in sys.path:
        sys.path.insert(0, str(local_packages))
    try:
        import imageio_ffmpeg

        executable = imageio_ffmpeg.get_ffmpeg_exe()
        if Path(executable).is_file():
            return executable
    except (ImportError, RuntimeError, OSError):
        pass
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    raise RuntimeError(
        "FFmpeg não encontrado. Execute: python -m pip install imageio-ffmpeg"
    )


def decide_cut(
    levels: list[float],
    window_seconds: float = WINDOW_SECONDS,
    threshold_db: int = -60,
    duration: float | None = None,
) -> tuple[float | None, float]:
    """Return a safe cut position and the measured final quiet interval.

    Only a continuous quiet interval at the very end qualifies. A short safety
    margin is retained after the last block with sound.
    """
    if duration is None:
        duration = len(levels) * window_seconds
    if not levels or duration <= 0:
        return None, 0.0

    threshold = 10 ** (threshold_db / 20)
    last_loud = next((i for i in range(len(levels) - 1, -1, -1)
                      if levels[i] >= threshold), None)
    if last_loud is None:
        return None, duration  # Never turn an entirely quiet file into an empty one.

    last_loud_end = min(duration, (last_loud + 1) * window_seconds)
    trailing = max(0.0, duration - last_loud_end)
    if trailing < MIN_SILENCE_SECONDS + SAFETY_SECONDS:
        return None, trailing

    cut_at = last_loud_end + SAFETY_SECONDS
    if cut_at <= 0 or cut_at >= duration:
        return None, trailing
    return cut_at, trailing


def analyze_mp3(
    path: Path,
    threshold_db: int = -60,
    cancel: threading.Event | None = None,
    ffmpeg: str | None = None,
) -> Analysis:
    path = Path(path).resolve()
    stat = path.stat()
    executable = ffmpeg or find_ffmpeg()
    command = [
        executable, "-nostdin", "-hide_banner", "-loglevel", "error",
        "-i", str(path), "-map", "0:a:0", "-vn", "-ac", str(CHANNELS),
        "-ar", str(SAMPLE_RATE), "-f", "s16le", "-",
    ]
    bytes_per_window = int(SAMPLE_RATE * WINDOW_SECONDS) * CHANNELS * 2
    levels: list[float] = []
    sample_count = 0
    with tempfile.TemporaryFile() as error_file:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=error_file)
        assert process.stdout is not None
        try:
            while True:
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                chunk = process.stdout.read(bytes_per_window)
                if not chunk:
                    break
                samples = array("h")
                samples.frombytes(chunk)
                if sys.byteorder != "little":
                    samples.byteswap()
                if not samples:
                    break
                rms = math.sqrt(sum(value * value for value in samples) / len(samples)) / 32768
                levels.append(rms)
                sample_count += len(samples)
            code = process.wait()
            if code != 0:
                error_file.seek(0)
                error = error_file.read().decode("utf-8", errors="replace").strip()
                raise RuntimeError(error or "Não foi possível decodificar o MP3.")
        except BaseException:
            if process.poll() is None:
                process.terminate()
                process.wait()
            raise
        finally:
            process.stdout.close()

    if sample_count == 0:
        raise RuntimeError("O arquivo não contém áudio decodificável.")
    duration = sample_count / (SAMPLE_RATE * CHANNELS)
    cut_at, trailing = decide_cut(levels, duration=duration, threshold_db=threshold_db)
    return Analysis(path, duration, cut_at, trailing, threshold_db, stat.st_size, stat.st_mtime_ns)


def processed_directory(base_dir: Path) -> Path:
    """Put all results in one clearly named folder under the selected base."""
    base_dir = Path(base_dir).expanduser().resolve()
    if base_dir.name.casefold() == PROCESSED_FOLDER_NAME.casefold():
        return base_dir
    return base_dir / PROCESSED_FOLDER_NAME


def unique_destination(source: Path, output_dir: Path, suffix: str = " - sem silencio") -> Path:
    base = source.stem + suffix
    candidate = output_dir / (base + ".mp3")
    number = 2
    while candidate.exists():
        candidate = output_dir / f"{base} ({number}).mp3"
        number += 1
    return candidate


def export_cut(
    analysis: Analysis,
    output_dir: Path,
    cancel: threading.Event | None = None,
    ffmpeg: str | None = None,
) -> Path:
    if analysis.cut_at is None:
        raise ValueError("Este arquivo não tem silêncio final suficiente para cortar.")
    _check_source(analysis)
    if cancel is not None and cancel.is_set():
        raise Cancelled()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = unique_destination(analysis.path, output_dir)
    temporary = output_dir / f".{uuid.uuid4().hex}.partial.mp3"
    executable = ffmpeg or find_ffmpeg()
    command = [
        executable, "-nostdin", "-hide_banner", "-loglevel", "error", "-n",
        "-i", str(analysis.path), "-map", "0:a:0", "-map_metadata", "0",
        "-t", f"{analysis.cut_at:.6f}", "-c:a", "copy", "-id3v2_version", "3",
        str(temporary),
    ]
    with tempfile.TemporaryFile() as error_file:
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=error_file)
        try:
            while process.poll() is None:
                if cancel is not None and cancel.wait(0.1):
                    process.terminate()
                    process.wait()
                    raise Cancelled()
                if cancel is None:
                    try:
                        process.wait(timeout=0.1)
                    except subprocess.TimeoutExpired:
                        pass
            if process.returncode != 0:
                error_file.seek(0)
                error = error_file.read().decode("utf-8", errors="replace").strip()
                raise RuntimeError(error or "Não foi possível salvar o MP3.")
            # Hard linking publishes the complete file without replacing an existing one.
            destination = unique_destination(analysis.path, output_dir)
            os.link(temporary, destination)
            temporary.unlink()
            return destination
        except BaseException:
            if temporary.exists():
                temporary.unlink()
            raise


def _check_source(analysis: Analysis) -> None:
    current = analysis.path.stat()
    if (current.st_size, current.st_mtime_ns) != (analysis.size, analysis.modified_ns):
        raise RuntimeError("O arquivo original mudou. Analise-o novamente antes de salvar.")


def export_unchanged(
    analysis: Analysis,
    output_dir: Path,
    cancel: threading.Event | None = None,
) -> Path:
    """Copy a file that needs no trimming into the processed collection."""
    if analysis.cut_at is not None:
        raise ValueError("Este arquivo tem um corte identificado.")
    _check_source(analysis)
    if cancel is not None and cancel.is_set():
        raise Cancelled()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    temporary = output_dir / f".{uuid.uuid4().hex}.partial.mp3"
    try:
        with analysis.path.open("rb") as source, temporary.open("xb") as target:
            while chunk := source.read(1024 * 1024):
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                target.write(chunk)
        shutil.copystat(analysis.path, temporary)
        _check_source(analysis)
        destination = unique_destination(analysis.path, output_dir, " - sem corte")
        os.link(temporary, destination)
        temporary.unlink()
        return destination
    except BaseException:
        if temporary.exists():
            temporary.unlink()
        raise
