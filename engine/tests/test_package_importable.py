import atlas_engine


def test_version_is_a_string():
    assert isinstance(atlas_engine.__version__, str)
    assert atlas_engine.__version__ != ""
