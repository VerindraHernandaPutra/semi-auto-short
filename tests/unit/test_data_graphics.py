import json

from shorts_engine.config import get_config
from shorts_engine.pipeline.data_graphics import create_graphics, find_comparisons

PARTS = [
    "Jupiter is shrinking.",
    "Its length has diminished from 24,200 miles in 1879 to around 8,700 miles now.",
    "Its extent was 40 degrees in the late 19th century. By 2016, it had contracted to 14 degrees.",
    "It has been shrinking for a century, over the past 50 years.",
    "Roots extend no more than 500 kilometers below the cloud tops.",
]


def test_two_amounts_in_one_unit_become_a_before_and_after_with_labels():
    found = find_comparisons(PARTS)

    assert [c.part_index for c in found] == [1, 2]
    first = found[0]
    assert (first.before.text, first.before.label) == ("24,200 miles", "1879")
    assert (first.after.text, first.after.label) == ("8,700 miles", "Now")
    second = found[1]
    assert (second.before.label, second.after.label) == ("Late 19th century", "2016")


def test_a_single_amount_or_mixed_units_make_no_graphic():
    parts = [
        "It spans 500 kilometers.",
        "It is 40 degrees wide and 500 kilometers deep.",
        "It lasted 50 years.",
    ]

    assert find_comparisons(parts) == []


def test_only_numbers_from_the_script_are_ever_shown():
    [found] = find_comparisons(["It fell from 10 miles to 4 miles."])

    assert (found.before.value, found.after.value) == (10.0, 4.0)
    assert (found.before.text, found.after.text) == ("10 miles", "4 miles")


def heard_for(parts):
    words = [w for p in parts for w in p.split()]
    return [{"word": w, "start": i * 0.4, "end": i * 0.4 + 0.3} for i, w in enumerate(words)]


def make_job(tmp_path, parts):
    (tmp_path / "audio").mkdir()
    (tmp_path / "audio" / "alignment.json").write_text(json.dumps(heard_for(parts)))


def test_bars_start_when_their_number_is_spoken_and_the_second_is_shorter(tmp_path):
    make_job(tmp_path, PARTS)

    path = create_graphics(tmp_path, PARTS, get_config())
    text = path.read_text(encoding="utf-8")

    assert path.name == "graphics.ass" and "24,200 miles" in text and "8,700 miles" in text
    bars = [line for line in text.splitlines() if line.startswith("Dialogue: 1,")]
    assert len(bars) == 4
    first_bar_start = bars[0].split(",")[1]
    second_bar_start = bars[1].split(",")[1]
    assert second_bar_start > first_bar_start  # 8,700 is spoken after 24,200
    lengths = [int(line.split("l ")[1].split()[0]) for line in bars[:2]]
    assert lengths[0] == 560 and lengths[1] == round(560 * 8700 / 24200)


def test_no_comparison_means_no_file_and_a_stale_one_is_removed(tmp_path):
    parts = ["Jupiter is big.", "It spins fast."]
    make_job(tmp_path, parts)
    (tmp_path / "subtitles").mkdir()
    stale = tmp_path / "subtitles" / "graphics.ass"
    stale.write_text("old")

    assert create_graphics(tmp_path, parts, get_config()) is None
    assert not stale.exists()


def test_graphics_can_be_switched_off(tmp_path):
    make_job(tmp_path, PARTS)
    config = get_config()
    config.settings["graphics"] = {**config.settings["graphics"], "enabled": False}

    assert create_graphics(tmp_path, PARTS, config) is None
    config.settings["graphics"]["enabled"] = True
