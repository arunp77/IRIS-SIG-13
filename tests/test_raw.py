"""Packing, unpacking and verifying the raw portal answers."""

import io
import json
import tarfile

from harvester.raw import pack, unpack
from harvester.source import DETAIL_URL, SEARCH_URL


def make_cache(folder):
    folder.mkdir()
    answers = {
        "a.json": {"url": f"{SEARCH_URL}?q=x", "fetched_at": 1793000000.0, "body": '{"success": true}'},
        "b.json": {"url": f"{DETAIL_URL}?id=1", "fetched_at": 1793000100.0, "body": "<html>Zuständige</html>"},
    }
    for name, entry in answers.items():
        (folder / name).write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")


def test_pack_then_unpack_gives_the_same_files_and_verifies_them(tmp_path):
    make_cache(tmp_path / "cache")
    raw = tmp_path / "raw"
    assert pack(tmp_path / "cache", raw) == 2
    assert [p.name for p in raw.glob("*.tar.gz")] == ["portal_answers_2026-10-26.tar.gz"]
    manifest = (raw / "MANIFEST.csv").read_text(encoding="utf-8").splitlines()
    assert manifest[0] == "file,kind,url,fetched_at_utc,bytes,body_sha256" and len(manifest) == 3
    assert ",search," in manifest[1] and ",detail," in manifest[2]

    count, problems = unpack(raw)
    assert (count, problems) == (2, [])
    assert json.loads((raw / "b.json").read_text(encoding="utf-8"))["body"] == "<html>Zuständige</html>"


def replace_archive(raw, members):
    """Overwrite the archive with {name: bytes}, as a damaged or tampered archive would look."""
    with tarfile.open(next(raw.glob("*.tar.gz")), "w:gz") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))


def test_a_changed_answer_is_reported(tmp_path):
    make_cache(tmp_path / "cache")
    raw = tmp_path / "raw"
    pack(tmp_path / "cache", raw)
    tampered = json.loads((tmp_path / "cache" / "a.json").read_text(encoding="utf-8"))
    tampered["body"] = '{"success": false}'
    replace_archive(
        raw, {"a.json": json.dumps(tampered).encode(), "b.json": (tmp_path / "cache" / "b.json").read_bytes()}
    )
    assert unpack(raw)[1] == ["a.json: does not match the manifest"]


def test_the_archive_cannot_write_outside_the_raw_folder(tmp_path):
    make_cache(tmp_path / "cache")
    raw = tmp_path / "raw"
    pack(tmp_path / "cache", raw)
    replace_archive(raw, {"../escaped.json": b"{}"})
    unpack(raw)
    assert not (tmp_path / "escaped.json").exists()


def test_unpack_without_an_archive_says_so(tmp_path):
    assert unpack(tmp_path) == (0, [f"no portal_answers_*.tar.gz in {tmp_path}"])
