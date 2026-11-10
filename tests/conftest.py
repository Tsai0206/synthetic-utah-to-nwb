import pytest
from pynwb import NWBHDF5IO

from synthetic_utah_to_nwb import make_session, session_to_nwb


@pytest.fixture(scope="session")
def session_dir(tmp_path_factory):
    return make_session(tmp_path_factory.mktemp("client") / "S01", ports=("A", "B"), block_s=3.0)


@pytest.fixture(scope="session")
def nwbfile_path(session_dir, tmp_path_factory):
    return session_to_nwb(session_dir, tmp_path_factory.mktemp("nwb") / "S01.nwb")


@pytest.fixture(scope="session")
def nwbfile(nwbfile_path):
    io = NWBHDF5IO(nwbfile_path, "r")
    yield io.read()
    io.close()
