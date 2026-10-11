from clinicdesk.store import export_filename


def test_json_export_name():
    assert export_filename("p1") == "p1.json"


def test_export_suffix_validation():
    import pytest
    with pytest.raises(ValueError):
        export_filename("p1", "exe")
