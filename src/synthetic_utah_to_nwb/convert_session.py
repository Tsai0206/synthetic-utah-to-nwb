import argparse
import json
from datetime import datetime
from pathlib import Path

from neuroconv import ConverterPipe
from neuroconv.utils import dict_deep_update, load_dict_from_file

from .interfaces import GaitInterface, UtahBlockRecordingInterface
from .synthetic import PORT_OFFSET, make_session


def session_to_nwb(session_dir, nwbfile_path, overwrite=False, stub_test=False):
    session_dir = Path(session_dir)
    info = json.loads((session_dir / "session.json").read_text())
    channel_map = session_dir / "channel_map.csv"

    interfaces = {}
    for i, block in enumerate(info["blocks"], start=1):
        port = block.get("port")
        if port not in PORT_OFFSET:
            raise ValueError(
                f"{session_dir.name}/{block['file']}: port is {port!r}. Ports A and B put the same 128 headstage "
                "channels on different electrodes, so the conversion refuses to guess. Fix session.json."
            )
        interface = UtahBlockRecordingInterface(
            file_path=session_dir / block["file"],
            channel_map_path=channel_map,
            port=port,
            sampling_frequency=info["sampling_frequency_hz"],
            uv_per_bit=info["uv_per_bit"],
            metadata_key=f"ElectricalSeriesBlock{i:03d}",
        )
        interface.set_aligned_starting_time(block["start_s"])
        interfaces[f"Block{i:03d}"] = interface
    interfaces["Gait"] = GaitInterface(
        kinematics_path=session_dir / "kinematics.csv", gait_cycles_path=session_dir / "gait_cycles.csv"
    )

    converter = ConverterPipe(data_interfaces=interfaces)
    metadata = dict_deep_update(converter.get_metadata(), load_dict_from_file(Path(__file__).parent / "metadata.yaml"))
    metadata["NWBFile"]["session_start_time"] = datetime.fromisoformat(info["session_start_time"])
    metadata["NWBFile"]["session_id"] = info["session_id"]
    metadata["Subject"]["subject_id"] = info["subject_id"]

    options = {name: dict(stub_test=stub_test) for name in interfaces if name.startswith("Block")}
    converter.run_conversion(
        nwbfile_path=nwbfile_path, metadata=metadata, overwrite=overwrite, conversion_options=options
    )
    return Path(nwbfile_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simulate one client session and convert it to NWB.")
    parser.add_argument("--out", default="demo_data", help="folder for the simulated session and the .nwb")
    parser.add_argument("--block-seconds", type=float, default=10.0)
    args = parser.parse_args()

    session = make_session(Path(args.out) / "S01", block_s=args.block_seconds)
    path = session_to_nwb(session, Path(args.out) / "sub-M01_ses-S01_ecephys+behavior.nwb", overwrite=True)
    print(f"wrote {path}")
