from pathlib import Path
import math
import struct
import subprocess
import tempfile
import unittest
import wave

from audio import (
    PROCESSED_FOLDER_NAME, analyze_mp3, decide_cut, export_cut,
    export_unchanged, find_ffmpeg, processed_directory,
)


class CutDecisionTests(unittest.TestCase):
    def test_uses_one_named_output_folder(self):
        base = Path("testes")
        folder = processed_directory(base)
        self.assertEqual(folder.name, PROCESSED_FOLDER_NAME)
        self.assertEqual(processed_directory(folder), folder)

    def test_trims_only_long_final_silence(self):
        cut, silence = decide_cut([0.2] * 40 + [0.0] * 40, duration=4.0)
        self.assertAlmostEqual(cut, 2.25)
        self.assertAlmostEqual(silence, 2.0)

    def test_retains_short_pause_and_faint_final_sound(self):
        self.assertIsNone(decide_cut([0.2] * 40 + [0.0] * 20, duration=3.0)[0])
        self.assertIsNone(decide_cut([0.2] * 40 + [0.0] * 30 + [0.003] * 10,
                                      duration=4.0)[0])

    def test_all_silent_file_is_not_emptied(self):
        self.assertIsNone(decide_cut([0.0] * 100, duration=5.0)[0])


class Mp3IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.ffmpeg = find_ffmpeg()
        except RuntimeError as exc:
            raise unittest.SkipTest(str(exc)) from exc

    def make_mp3(self, directory: Path, name: str, sounding: float, total: float) -> Path:
        wav = directory / (name + ".wav")
        rate = 16_000
        with wave.open(str(wav), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(rate)
            chunk = bytearray()
            for index in range(int(total * rate)):
                value = int(9000 * math.sin(2 * math.pi * 440 * index / rate)) if index < sounding * rate else 0
                chunk.extend(struct.pack("<h", value))
            writer.writeframes(chunk)
        mp3 = directory / (name + ".mp3")
        subprocess.run([self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-i",
                        str(wav), "-c:a", "libmp3lame", "-q:a", "3", str(mp3)], check=True)
        return mp3

    def test_exports_only_trailing_silence_and_keeps_original(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source = self.make_mp3(folder, "ending", 2.0, 5.0)
            original_size = source.stat().st_size
            analysis = analyze_mp3(source, ffmpeg=self.ffmpeg)
            self.assertIsNotNone(analysis.cut_at)
            self.assertGreater(analysis.removed, 2.4)
            self.assertLess(analysis.removed, 3.2)
            destination = export_cut(analysis, folder / "output", ffmpeg=self.ffmpeg)
            self.assertTrue(destination.is_file())
            self.assertEqual(source.stat().st_size, original_size)
            result = analyze_mp3(destination, ffmpeg=self.ffmpeg)
            self.assertGreater(result.duration, 2.0)
            self.assertLess(result.duration, 2.5)
            second = export_cut(analysis, folder / "output", ffmpeg=self.ffmpeg)
            self.assertNotEqual(destination, second)

    def test_does_not_cut_music_through_end(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source = self.make_mp3(folder, "no_silence", 3.0, 3.0)
            analysis = analyze_mp3(source, ffmpeg=self.ffmpeg)
            self.assertIsNone(analysis.cut_at)
            destination = export_unchanged(analysis, processed_directory(folder))
            self.assertEqual(destination.read_bytes(), source.read_bytes())
            self.assertEqual(destination.parent.name, PROCESSED_FOLDER_NAME)


if __name__ == "__main__":
    unittest.main()
