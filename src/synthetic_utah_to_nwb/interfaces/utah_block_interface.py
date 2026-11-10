from typing import Literal

import numpy as np
import pandas as pd
from neuroconv.datainterfaces.ecephys.baserecordingextractorinterface import BaseRecordingExtractorInterface
from pydantic import FilePath, validate_call
from spikeinterface.core import BinaryRecordingExtractor

from ..synthetic import CHANNELS_PER_PORT, ELECTRODES_PER_ARRAY, PORT_OFFSET


def load_channel_map(path):
    wiring = pd.read_csv(path)
    missing = {"nsp_channel", "array", "electrode"} - set(wiring.columns)
    if missing:
        raise ValueError(f"{path}: missing columns {sorted(missing)}")

    problems = []
    expected = set(range(1, 2 * CHANNELS_PER_PORT + 1))
    nsp = wiring["nsp_channel"]
    if nsp.duplicated().any():
        problems.append(f"duplicated nsp_channel {sorted(nsp[nsp.duplicated()].unique().tolist())}")
    if set(nsp) != expected:
        problems.append(
            f"nsp_channel must cover 1-{2 * CHANNELS_PER_PORT} exactly; "
            f"missing {sorted(expected - set(nsp))[:10]}, unexpected {sorted(set(nsp) - expected)[:10]}"
        )
    if wiring.duplicated(["array", "electrode"]).any():
        problems.append("two NSP channels wired to the same (array, electrode)")
    if not wiring["electrode"].between(1, ELECTRODES_PER_ARRAY).all():
        problems.append(f"electrode outside 1-{ELECTRODES_PER_ARRAY}")
    if problems:
        raise ValueError(f"{path}: " + "; ".join(problems))
    return wiring.set_index("nsp_channel")


class UtahBlockRecordingInterface(BaseRecordingExtractorInterface):
    """One recording block: a flat int16 file from a single 128-channel headstage port.

    ``port`` has no default: A and B put the same 128 channels on different electrodes.
    """

    @classmethod
    def get_extractor_class(cls):
        return BinaryRecordingExtractor

    @validate_call
    def __init__(
        self,
        file_path: FilePath,
        channel_map_path: FilePath,
        port: Literal["A", "B"],
        sampling_frequency: float = 30_000.0,
        uv_per_bit: float = 0.25,
        verbose: bool = False,
        *,
        metadata_key: str = "ElectricalSeries",
    ):
        super().__init__(
            file_path=file_path,
            channel_map_path=channel_map_path,
            port=port,
            sampling_frequency=sampling_frequency,
            uv_per_bit=uv_per_bit,
            verbose=verbose,
            metadata_key=metadata_key,
        )

    def _initialize_extractor(self, interface_kwargs):
        self.extractor_kwargs = dict(interface_kwargs)
        recording = BinaryRecordingExtractor(
            file_paths=[interface_kwargs["file_path"]],
            sampling_frequency=interface_kwargs["sampling_frequency"],
            dtype="int16",
            num_channels=CHANNELS_PER_PORT,
            gain_to_uV=interface_kwargs["uv_per_bit"],
            offset_to_uV=0.0,
        )
        port = interface_kwargs["port"]
        nsp = np.arange(1, CHANNELS_PER_PORT + 1) + PORT_OFFSET[port]  # (128,)
        wired = load_channel_map(interface_kwargs["channel_map_path"]).loc[nsp]

        # group_name + electrode_name + channel_name is the key NeuroConv uses to reuse electrode rows,
        # so two blocks on the same port share rows instead of duplicating them
        recording.set_property("group_name", ("array" + wired["array"].astype(str)).to_numpy())
        recording.set_property("electrode_name", wired["electrode"].map("elec{:02d}".format).to_numpy())
        recording.set_property("channel_name", np.char.mod("nsp%03d", nsp))
        recording.set_property("nsp_channel", nsp)
        recording.set_property("port", np.full(CHANNELS_PER_PORT, port))
        return recording

    def set_aligned_starting_time(self, aligned_starting_time):
        # the base class materializes a timestamp vector and re-derives the rate from it, which turns
        # 30000.0 into 30000.000000000004; shifting t_start keeps the series on its regular clock
        self.recording_extractor.shift_times(aligned_starting_time)

    def get_metadata(self):
        metadata = super().get_metadata()
        port = self.source_data["port"]
        first = PORT_OFFSET[port] + 1
        metadata["Ecephys"]["ElectricalSeries"][self.metadata_key].update(
            name=self.metadata_key,
            description=(
                f"Wideband voltage from one 128-channel headstage on port {port} "
                f"(NSP channels {first}-{first + CHANNELS_PER_PORT - 1}). Stored as int16 counts; "
                "multiply by conversion for volts."
            ),
        )
        return metadata

    def add_to_nwbfile(self, nwbfile, metadata=None, **conversion_options):
        # location lives once, per array, in metadata; copy it onto each channel so the electrodes table agrees
        groups = (metadata or {}).get("Ecephys", {}).get("ElectrodeGroups", {})
        location = {g["name"]: g["location"] for g in groups.values()}
        names = self.recording_extractor.get_property("group_name")
        unknown = sorted(set(names) - set(location))
        if unknown:
            raise ValueError(f"metadata['Ecephys']['ElectrodeGroups'] has no location for {unknown}")
        self.recording_extractor.set_property("brain_area", np.array([location[n] for n in names]))
        super().add_to_nwbfile(nwbfile=nwbfile, metadata=metadata, **conversion_options)
