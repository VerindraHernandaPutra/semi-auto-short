import shutil

import pytest

from shorts_engine.config import get_config
from shorts_engine.pipeline import music as music_module
from shorts_engine.pipeline.audio import measure_loudness, probe_audio
from shorts_engine.pipeline.branding import build_branding_ass, create_branding
from shorts_engine.pipeline.music import pick_track, prepare_music
from shorts_engine.pipeline.render import join_shots, mix_audio

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


@pytest.fixture
def config():
    cfg = get_config()
    saved = {key: dict(cfg.settings[key]) for key in ("branding", "music")}
    cfg.settings["branding"]["channel_name"] = "@TestChannel"
    yield cfg
    for key, value in saved.items():
        cfg.settings[key] = value


def events(ass, layer):
    return [line for line in ass.splitlines() if line.startswith(f"Dialogue: {layer},")]


def test_title_watermark_and_popup_are_drawn_when_a_channel_name_is_set(config):
    ass = build_branding_ass("Jupiter Is Shrinking", 33.0, config)

    assert "Jupiter Is Shrinking" in events(ass, 3)[0]
    assert "@TestChannel" in events(ass, 4)[0]  # watermark for the whole video
    assert events(ass, 4)[0].split(",")[2] == "0:00:33.00"
    assert len(events(ass, 5)) == 1 and "\\p1" in events(ass, 5)[0]  # white card
    assert "@TestChannel" in events(ass, 6)[0]  # its text


def test_without_a_channel_name_only_the_title_card_remains(config):
    config.settings["branding"]["channel_name"] = ""
    ass = build_branding_ass("A Title", 30.0, config)

    assert events(ass, 3) and not events(ass, 4) and not events(ass, 5)


def test_a_popup_too_close_to_the_end_is_skipped_and_times_can_repeat(config):
    config.settings["branding"]["popup_at_s"] = [3.0, 12.0, 32.8]
    ass = build_branding_ass("T", 33.0, config)

    assert len(events(ass, 5)) == 2


def test_nothing_to_show_means_no_file_and_a_stale_one_is_removed(config, tmp_path):
    config.settings["branding"]["channel_name"] = ""
    config.settings["branding"]["title_card"] = False
    (tmp_path / "subtitles").mkdir()
    stale = tmp_path / "subtitles" / "branding.ass"
    stale.write_text("old")

    assert create_branding(tmp_path, "T", 30.0, config) is None and not stale.exists()


def test_braces_in_a_name_cannot_break_the_subtitle_file(config):
    config.settings["branding"]["channel_name"] = "{\\fs500}Evil"

    ass = build_branding_ass("T", 30.0, config)

    assert "{\\fs500}" not in ass


def test_crossfades_start_where_each_shot_ends_and_cuts_use_concat():
    graph, label = join_shots([3.0, 4.0, 5.0], 0.25)

    assert label == "joined"
    assert "xfade=transition=fade:duration=0.25:offset=3.000[x1]" in graph
    assert "[x1][2:v]xfade=transition=fade:duration=0.25:offset=7.000[joined]" in graph
    assert join_shots([3.0, 4.0], 0)[0] == "[0:v][1:v]concat=n=2:v=1:a=0[joined]"
    assert join_shots([3.0], 0.25)[0] == "[0:v]null[joined]"


def test_music_ducks_under_the_voice_unless_switched_off(config):
    assert "sidechaincompress" in mix_audio(2, config, 1.2, 30.0, True)
    config.settings["music"]["duck"] = False
    plain = mix_audio(2, config, 1.2, 30.0, True)

    assert "sidechaincompress" not in plain and "alimiter=limit=0.8:level=0" in plain


def test_the_ending_is_silence_after_the_voice_and_everything_fades_out(config):
    graph = mix_audio(2, config, 1.2, 30.0, True)

    assert "apad=pad_dur=1.200" in graph and "afade=t=out:st=29.200:d=0.8" in graph
    bare = mix_audio(2, config, 1.2, 30.0, False)
    assert bare.startswith("[2:a]apad=pad_dur=1.200") and bare.endswith("[aout]")
    assert "amix" not in bare


def test_the_same_job_always_gets_the_same_track(tmp_path):
    tracks = [tmp_path / "a.mp3", tmp_path / "b.mp3", tmp_path / "c.mp3"]

    assert pick_track("job_1", tracks) == pick_track("job_1", tracks)
    assert {pick_track(f"job_{i}", tracks) for i in range(30)} == set(tracks)
    assert pick_track("job_1", []) is None


@needs_ffmpeg
def test_ambient_music_is_stereo_48k_as_long_as_asked_and_quiet(config, tmp_path):
    config.settings["music"]["mode"] = "ambient"

    out = prepare_music("job_m", tmp_path, 6.0, config)

    info = probe_audio(out)
    assert (info.sample_rate, info.channels) == (48000, 2)
    assert info.duration_s == pytest.approx(6.0, abs=0.2)
    assert measure_loudness(out)["lufs"] < -10


@needs_ffmpeg
def test_music_can_be_off_and_an_empty_tracks_folder_falls_back_to_the_pad(
    config, tmp_path, monkeypatch
):
    config.settings["music"]["mode"] = "off"
    assert prepare_music("job_m", tmp_path, 4.0, config) is None

    config.settings["music"]["mode"] = "tracks"
    monkeypatch.setattr(music_module, "TRACKS_DIR", tmp_path / "no_tracks")
    assert prepare_music("job_m", tmp_path, 4.0, config).is_file()
