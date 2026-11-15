"""Bronze loader for Freddie Mac's loan-level files: zip -> Parquet, streamed, never unzipped.

For each zip (a sample year, or later a full-data quarter):
1. Check every file against its data contract (ingestion.data_contracts). Any error stops the load
   before anything is written.
2. Check there is room on disk for the estimated output (ingestion.disk), keeping 3 GB free.
3. Stream each file straight out of the zip into Parquet, one block at a time (ADR-001: the files
   are never extracted to disk). Columns get the contract's names and types.
4. Tie-out: rows written to Parquet must equal the rows the contract check counted; a mismatch
   deletes the output and stops (control C02).

Bronze keeps Freddie's values as they are, including "Not Available" codes such as 9999; silver
turns those into nulls. Money and rates are stored as exact decimals (not floating point), so later
sums can reconcile to the cent (the rollforward control, C05).

Output: <out_root>/freddie_<kind>/vintage=<year>/<file>.parquet. Re-running overwrites the same
files, so a load can be repeated safely.
"""

from __future__ import annotations

import os
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

from ingestion import data_contracts as dc
from ingestion import disk

# Freddie's decimals have at most 3 places (rates like 6.875; money like 250000.00). Four places and
# 18 digits leave room, and decimals are exact, unlike floating point.
DECIMAL = pa.decimal128(18, 4)
ARROW_TYPES = {"int": pa.int64(), "decimal": DECIMAL, "yyyymm": pa.string(), "str": pa.string()}
BLOCK_BYTES = 32 * 1024 * 1024  # read 32 MB of text at a time: steady memory use whatever the file size
# Parquet is much smaller than the text. Until a real ratio is measured, assume a cautious 50%.
DEFAULT_SIZE_RATIO = 0.5
YEAR = re.compile(r"(\d{4})")


@dataclass
class LoadedFile:
    member: str
    kind: str
    vintage: int
    rows: int
    text_bytes: int  # uncompressed size inside the zip
    parquet_bytes: int
    path: Path


def arrow_schema(contract: dc.Contract) -> dict[str, pa.DataType]:
    return {f.name: ARROW_TYPES[f.type] for f in contract.fields}


def vintage_of(member: str) -> int:
    match = YEAR.search(Path(member).name)
    if not match:
        raise ValueError(f"no year in file name {member!r}")
    return int(match.group(1))


def output_path(out_root: Path, kind: str, member: str) -> Path:
    name = Path(member).name.removesuffix(".txt") + ".parquet"
    return Path(out_root) / f"freddie_{kind}" / f"vintage={vintage_of(member)}" / name


def _write_member(
    zf: zipfile.ZipFile, info: zipfile.ZipInfo, contract: dc.Contract, path: Path, release: int
) -> int:
    """Stream one member of the zip into a Parquet file; returns the rows written."""
    read = pacsv.ReadOptions(column_names=contract.names, block_size=BLOCK_BYTES, encoding="latin1")
    # Pipe-delimited with no quoting: a quote character in a seller name is just a character.
    parse = pacsv.ParseOptions(delimiter=contract.delimiter, quote_char=False)
    convert = pacsv.ConvertOptions(
        column_types=arrow_schema(contract), null_values=[""], strings_can_be_null=True
    )
    lineage = {"source_file": Path(info.filename).name, "release": release}
    tmp = path.with_suffix(".parquet.tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    rows = 0
    writer: pq.ParquetWriter | None = None
    try:
        with zf.open(info) as raw:
            # pyarrow can't open an empty file; an empty member gets an empty table below
            batches = (
                pacsv.open_csv(raw, read_options=read, parse_options=parse, convert_options=convert)
                if info.file_size
                else []
            )
            for batch in batches:
                table = pa.Table.from_batches([batch])
                table = table.append_column(
                    "source_file", pa.array([lineage["source_file"]] * table.num_rows)
                )
                table = table.append_column("release", pa.array([release] * table.num_rows, type=pa.int16()))
                if writer is None:
                    writer = pq.ParquetWriter(tmp, table.schema, compression="zstd")
                writer.write_table(table)
                rows += table.num_rows
        if writer is None:  # empty file: still write an empty table with the right schema
            empty = pa.schema(
                list(arrow_schema(contract).items()) + [("source_file", pa.string()), ("release", pa.int16())]
            )
            writer = pq.ParquetWriter(tmp, empty, compression="zstd")
        writer.close()
        writer = None
        os.replace(tmp, path)  # atomic: a crash never leaves a half-written file under the real name
    finally:
        if writer is not None:
            writer.close()
        if tmp.exists():
            tmp.unlink()
    return rows


def load_zip(
    zip_path: Path,
    release: int,
    out_root: Path,
    *,
    size_ratio: float = DEFAULT_SIZE_RATIO,
    contract_dir: Path = dc.CONTRACT_DIR,
) -> list[LoadedFile]:
    """Contract-check, then load every Freddie file in one zip to bronze Parquet."""
    zip_path, out_root = Path(zip_path), Path(out_root)

    # 1. contracts first: a broken file must never reach bronze
    results = dc.check_zip(zip_path, release, contract_dir)
    dc.enforce(results)
    expected_rows = {r.file.split(":", 1)[1]: r.rows for r in results}

    with zipfile.ZipFile(zip_path) as zf:
        members = [i for i in zf.infolist() if not i.is_dir()]
        # 2. room on disk for the estimated output
        disk.check_room(out_root, int(sum(i.file_size for i in members) * size_ratio))

        loaded = []
        for info in members:
            member = Path(info.filename).name
            kind = dc.kind_for(Path(member))
            contract = dc.contract_for(kind, release, contract_dir)
            path = output_path(out_root, kind, member)
            # 3. stream into Parquet
            rows = _write_member(zf, info, contract, path, release)
            # 4. tie-out with the contract check's count
            if rows != expected_rows[member]:
                path.unlink(missing_ok=True)
                raise dc.ContractViolation(
                    f"tie-out failed for {zip_path.name}:{member}: {rows:,} rows written to Parquet, "
                    f"{expected_rows[member]:,} counted by the contract check"
                )
            loaded.append(
                LoadedFile(member, kind, vintage_of(member), rows, info.file_size, path.stat().st_size, path)
            )
    return loaded
