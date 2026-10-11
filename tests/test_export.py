from clinicdesk.store import export_filename


def test_json_export_name():
    assert export_filename("p1") == "p1.json"
