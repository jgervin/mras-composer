from main import build_playlist


def test_build_playlist_returns_sorted_mp4_urls(tmp_path):
    (tmp_path / "standard2.mp4").touch()
    (tmp_path / "standard.mp4").touch()
    (tmp_path / "readme.txt").touch()  # non-mp4 ignored
    (tmp_path / "standard10.mp4").touch()

    result = build_playlist(tmp_path, "http://localhost:8002")

    assert result == [
        "http://localhost:8002/assets/standard.mp4",
        "http://localhost:8002/assets/standard10.mp4",
        "http://localhost:8002/assets/standard2.mp4",
    ]


def test_build_playlist_empty_dir_returns_empty(tmp_path):
    assert build_playlist(tmp_path, "http://x") == []
