import json
import shutil

import numpy as np
import pandas as pd
import pytest
from nwbinspector import Importance, inspect_nwbfile
from pynwb import NWBHDF5IO

from synthetic_utah_to_nwb import make_session, session_to_nwb
from synthetic_utah_to_nwb.interfaces import UtahBlockRecordingInterface, load_channel_map
from synthetic_utah_to_nwb.synthetic import CHANNELS_PER_PORT, PORT_OFFSET


def _blocks(session_dir):
    return json.loads((session_dir / "session.json").read_text())["blocks"]


def test_raw_counts_roundtrip(session_dir, nwbfile):
    for i, block in enumerate(_blocks(session_dir), start=1):
        raw = np.fromfile(session_dir / block["file"], dtype="<i2").reshape(-1, CHANNELS_PER_PORT)  # (n, 128)
        series = nwbfile.acquisition[f"ElectricalSeriesBlock{i:03d}"]
        np.testing.assert_array_equal(series.data[:], raw)
        assert series.conversion == pytest.approx(0.25e-6)
        assert series.rate == 30_000.0
        assert series.starting_time == block["start_s"]


def test_block_electrodes_follow_port(session_dir, nwbfile):
    wiring = load_channel_map(session_dir / "channel_map.csv")
    table = nwbfile.electrodes.to_dataframe()
    assert len(table) == 2 * CHANNELS_PER_PORT

    for i, block in enumerate(_blocks(session_dir), start=1):
        rows = table.iloc[nwbfile.acquisition[f"ElectricalSeriesBlock{i:03d}"].electrodes.data[:]]
        nsp = np.arange(1, CHANNELS_PER_PORT + 1) + PORT_OFFSET[block["port"]]
        np.testing.assert_array_equal(rows["nsp_channel"], nsp)
        assert (rows["port"] == block["port"]).all()
        expected = wiring.loc[nsp]
        np.testing.assert_array_equal(rows["group_name"], "array" + expected["array"].astype(str))
        np.testing.assert_array_equal(rows["electrode_name"], expected["electrode"].map("elec{:02d}".format))


def test_location_per_array(nwbfile):
    table = nwbfile.electrodes.to_dataframe()
    per_group = table.groupby("group_name")["location"].unique()
    assert per_group.map(len).eq(1).all()
    assert all(table["location"] == [g.location for g in table["group"]])


def test_same_port_shares_electrodes(tmp_path):
    session = make_session(tmp_path / "S02", ports=("A", "A"), block_s=2.5)
    with NWBHDF5IO(session_to_nwb(session, tmp_path / "S02.nwb"), "r") as io:
        nwb = io.read()
        assert len(nwb.electrodes) == CHANNELS_PER_PORT
        np.testing.assert_array_equal(
            nwb.acquisition["ElectricalSeriesBlock001"].electrodes.data[:],
            nwb.acquisition["ElectricalSeriesBlock002"].electrodes.data[:],
        )


@pytest.mark.parametrize("port", [None, "", "C"])
def test_invalid_port_raises(session_dir, tmp_path, port):
    broken = shutil.copytree(session_dir, tmp_path / "S01")
    info = json.loads((broken / "session.json").read_text())
    if port is None:
        del info["blocks"][1]["port"]
    else:
        info["blocks"][1]["port"] = port
    (broken / "session.json").write_text(json.dumps(info))

    with pytest.raises(ValueError, match="refuses to guess"):
        session_to_nwb(broken, tmp_path / "never_written.nwb")
    assert not (tmp_path / "never_written.nwb").exists()


def test_port_is_required(session_dir):
    with pytest.raises(ValueError):
        UtahBlockRecordingInterface(
            file_path=session_dir / "block001.dat", channel_map_path=session_dir / "channel_map.csv"
        )


def test_duplicate_nsp_channel_raises(session_dir, tmp_path):
    wiring = pd.read_csv(session_dir / "channel_map.csv")
    wiring.loc[5, "nsp_channel"] = wiring.loc[4, "nsp_channel"]
    wiring.to_csv(tmp_path / "channel_map.csv", index=False)
    with pytest.raises(ValueError, match="duplicated nsp_channel"):
        load_channel_map(tmp_path / "channel_map.csv")


def test_marker_dropouts_stay_nan(session_dir, nwbfile):
    kin = pd.read_csv(session_dir / "kinematics.csv")
    angles = nwbfile.processing["behavior"]["BehavioralTimeSeries"]["joint_angles"]
    stored = angles.data[:]  # (n_samples, n_joints)
    assert stored.shape == (len(kin), 2)
    np.testing.assert_array_equal(np.isnan(stored), kin[["hip_deg", "shoulder_deg"]].isna().to_numpy())
    assert angles.rate == pytest.approx(1000.0)


def test_gait_cycles_roundtrip(session_dir, nwbfile):
    cycles = pd.read_csv(session_dir / "gait_cycles.csv")
    stored = nwbfile.intervals["gait_cycles"].to_dataframe()
    np.testing.assert_allclose(stored["start_time"], cycles["start_s"])
    np.testing.assert_allclose(stored["stop_time"], cycles["stop_s"])
    assert stored["condition"].tolist() == cycles["condition"].tolist()
    assert stored["exclude"].astype(bool).tolist() == cycles["exclude"].tolist()


def test_nwbinspector(nwbfile_path):
    # threshold includes BEST_PRACTICE_SUGGESTION
    messages = list(
        inspect_nwbfile(nwbfile_path=nwbfile_path, importance_threshold=Importance.BEST_PRACTICE_SUGGESTION)
    )
    assert messages == [], "\n".join(f"{m.importance.name} {m.check_function_name}: {m.message}" for m in messages)
