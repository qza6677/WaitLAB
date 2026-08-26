from waitlab.updates import version_tuple


def test_semantic_version_comparison_shape():
    assert version_tuple("v0.5.10") > version_tuple("0.5.2")
