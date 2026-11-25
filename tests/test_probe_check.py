from pxlsync.check import CheckRule, check
from pxlsync.codecs import BUILTIN_AUDIO, BUILTIN_VIDEO, find_preset
from pxlsync.probe import find_ffprobe, probe, scan_media


def preset(label, video=True):
    return find_preset(BUILTIN_VIDEO if video else BUILTIN_AUDIO, label)


def test_probe_reads_codecs_and_profiles(media_dir):
    ff = find_ffprobe()
    hq = probe(media_dir / "prores_hq.mov", ff)
    assert (hq.video_codec, hq.video_profile, hq.resolution) == ("prores", "HQ", "256x144")
    assert hq.fps_text == "23.976"
    assert hq.audio_codec == "pcm_s24le"
    dnx = probe(media_dir / "dnxhr_hq.mxf", ff)
    assert (dnx.video_codec, dnx.video_profile) == ("dnxhd", "DNXHR HQ")
    assert probe(media_dir / "broken.mov", ff).error


def test_check_against_presets(media_dir):
    ff = find_ffprobe()
    infos = {p.name: probe(p, ff) for p in scan_media(media_dir)}
    rule = CheckRule(video=preset("ProRes 422 HQ"), audio=preset("PCM 24-bit", video=False))
    results = {n: check(i, rule) for n, i in infos.items()}
    assert results["prores_hq.mov"].ok
    assert "expected ProRes 422 HQ" in results["prores_lt.mov"].reasons[0]
    assert not results["h264.mp4"].ok and len(results["h264.mp4"].reasons) == 2
    assert not results["broken.mov"].ok
    # wav has no video stream: video rule does not apply, audio rule does (16-bit != 24-bit)
    assert results["vo.wav"].reasons == ["audio is pcm_s16le, expected PCM 24-bit"]

    any_prores = CheckRule(video=preset("Apple ProRes (any)"))
    assert check(infos["prores_lt.mov"], any_prores).ok
    assert check(infos["dnxhr_hq.mxf"], CheckRule(video=preset("DNxHR HQ"))).ok
