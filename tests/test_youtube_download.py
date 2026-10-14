import threading
from pathlib import Path
from types import SimpleNamespace
import unittest

from youtube_download import (
    DownloadError, Video, _choose_tool, _safe_name, _tool_arguments,
    _unwrap_result, expand_links, parse_links,
)


class YoutubeImportTests(unittest.TestCase):
    def test_parses_video_playlist_and_deduplicates(self):
        links = parse_links("\n".join([
            "https://youtu.be/dQw4w9WgXcQ",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://music.youtube.com/playlist?list=PLW_omwEM1aic5BpJJUEyteXnpbaMdT3MJ",
        ]))
        self.assertEqual(links, [
            ("video", "dQw4w9WgXcQ"),
            ("playlist", "PLW_omwEM1aic5BpJJUEyteXnpbaMdT3MJ"),
        ])

    def test_rejects_non_youtube_hosts(self):
        with self.assertRaises(DownloadError):
            parse_links("https://youtube.com.evil.example/watch?v=dQw4w9WgXcQ")

    def test_single_video_needs_no_playlist_dependency(self):
        self.assertEqual(expand_links([("video", "dQw4w9WgXcQ")], threading.Event()),
                         [Video("dQw4w9WgXcQ", "dQw4w9WgXcQ")])

    def test_mcp_tool_and_result_contract(self):
        tool = SimpleNamespace(name="download_mp3", description="Download YouTube MP3",
                               inputSchema={"properties": {"video_url": {"type": "string"}},
                                            "required": ["video_url"]})
        self.assertIs(_choose_tool([tool]), tool)
        self.assertEqual(_tool_arguments(tool, Video("dQw4w9WgXcQ", "Title")),
                         {"video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})
        result = SimpleNamespace(isError=False, structuredContent={"data": {
            "download_url": "https://example.com/audio.mp3", "title": "Song"}}, content=[])
        self.assertEqual(_unwrap_result(result), ("https://example.com/audio.mp3", None, "Song"))

    def test_filename_cannot_escape_download_folder(self):
        name = _safe_name("../Band: Song?", "dQw4w9WgXcQ")
        self.assertEqual(Path(name).name, name)
        self.assertTrue(name.endswith("[dQw4w9WgXcQ].mp3"))


if __name__ == "__main__":
    unittest.main()
