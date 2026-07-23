from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_start_waits_for_android_boot_before_starting_iris() -> None:
    script = (ROOT / "scripts/start_kakaotalk_readonly.sh").read_text(encoding="utf-8")

    boot_check = script.index("getprop sys.boot_completed")
    iris_start = script.index("party.qwer.iris.Main")

    assert boot_check < iris_start
