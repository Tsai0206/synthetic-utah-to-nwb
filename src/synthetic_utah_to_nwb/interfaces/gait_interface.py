import numpy as np
import pandas as pd
from neuroconv.basedatainterface import BaseDataInterface
from neuroconv.tools.nwb_helpers import get_module
from pydantic import FilePath, validate_call
from pynwb.behavior import BehavioralTimeSeries
from pynwb.base import TimeSeries
from pynwb.epoch import TimeIntervals


class GaitInterface(BaseDataInterface):
    """Joint-angle kinematics and the lab's gait-cycle annotations.

    Marker dropouts are written as NaN; the series description says how many samples are missing.
    """

    keywords = ("behavior", "locomotion", "gait")

    @validate_call
    def __init__(self, kinematics_path: FilePath, gait_cycles_path: FilePath, verbose: bool = False):
        super().__init__(kinematics_path=kinematics_path, gait_cycles_path=gait_cycles_path, verbose=verbose)

    def add_to_nwbfile(self, nwbfile, metadata=None):
        kin = pd.read_csv(self.source_data["kinematics_path"])
        t = kin.pop("time_s").to_numpy()
        dt = np.diff(t)
        if not np.allclose(dt, dt[0], rtol=1e-3):
            raise ValueError(
                f"{self.source_data['kinematics_path']}: samples are not evenly spaced "
                f"(dt ranges {dt.min():.6f}-{dt.max():.6f} s)"
            )
        angles = kin.to_numpy()  # (n_samples, n_joints)
        n_missing = np.isnan(angles).sum(axis=0)

        joint_angles = TimeSeries(
            name="joint_angles",
            data=angles,
            unit="degrees",
            starting_time=float(t[0]),
            rate=float(1.0 / dt.mean()),
            description=(
                "Joint angles from marker-based motion capture. Columns: "
                + ", ".join(f"{c} ({m} NaN samples from marker dropout)" for c, m in zip(kin.columns, n_missing))
                + ". Gaps are left as NaN."
            ),
        )
        behavior = get_module(nwbfile, "behavior", "Processed behavioral data.")
        behavior.add(BehavioralTimeSeries(name="BehavioralTimeSeries", time_series=[joint_angles]))

        cycles = pd.read_csv(self.source_data["gait_cycles_path"])
        bad = np.flatnonzero(cycles["stop_s"].to_numpy() <= cycles["start_s"].to_numpy())
        if bad.size:
            raise ValueError(f"{self.source_data['gait_cycles_path']}: rows {bad.tolist()} end before they start")

        intervals = TimeIntervals(
            name="gait_cycles",
            description="One row per step cycle, from one forelimb peak to the next, as annotated by the lab.",
        )
        intervals.add_column("condition", "Locomotion condition of the block the cycle belongs to.")
        intervals.add_column("exclude", "True if the lab flagged the cycle as unusable during manual curation.")
        for row in cycles.itertuples(index=False):
            intervals.add_interval(
                start_time=row.start_s, stop_time=row.stop_s, condition=row.condition, exclude=bool(row.exclude)
            )
        nwbfile.add_time_intervals(intervals)
