import pytest
from clinicdesk.eval import load_task


def test_task_ids_cannot_escape_directory():
    with pytest.raises(ValueError):
        load_task("../outside")
