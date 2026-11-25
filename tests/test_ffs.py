import xml.etree.ElementTree as ET
from pathlib import Path

from pxlsync.ffs import MODES, build_batch

MIRROR, UPDATE = list(MODES)


def test_batch_has_pair_mode_and_excludes(tmp_path):
    src = tmp_path / "src"
    xml = build_batch(src, Path("/mnt/nas/job"), UPDATE, [src / "A" / "bad.mov"], sep="/")
    root = ET.fromstring(xml)
    assert root.get("XmlType") == "BATCH"
    assert root.find("FolderPairs/Pair/Left").text == str(src)
    assert root.find("Synchronize/Differences").get("RightOnly") == "none"
    items = [i.text for i in root.findall("Filter/Exclude/Item")]
    assert "/A/bad.mov" in items and "*/.DS_Store" in items


def test_windows_separators(tmp_path):
    src = tmp_path / "src"
    xml = build_batch(src, Path("/x"), MIRROR, [src / "A" / "bad.mov"], sep="\\")
    assert "<Item>\\A\\bad.mov</Item>" in xml


def test_template_keeps_its_settings(tmp_path):
    tpl = tmp_path / "studio.ffs_batch"
    tpl.write_text(
        '<?xml version="1.0" encoding="utf-8"?><FreeFileSync XmlType="BATCH" XmlFormat="20">'
        '<Compare><Variant>Content</Variant></Compare>'
        '<FolderPairs><Pair><Left>old</Left><Right>old</Right></Pair></FolderPairs></FreeFileSync>'
    )
    root = ET.fromstring(build_batch(Path("/a"), Path("/b"), MIRROR, template=tpl))
    assert root.find("Compare/Variant").text == "Content"
    assert [p.find("Left").text for p in root.findall("FolderPairs/Pair")] == ["/a"]
