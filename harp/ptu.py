"""PTU-Dateien (PicoQuant Unified TTTR) lesen, dekodieren und schreiben.

Grundlage: PicoQuant File-Format-Demos (Read_PTU.py) und die Format-Doku
"TTTR Record Format" (apx_tttrrecords.html) aus dem HydraHarp-500-Softwarepaket v1.0.0.0
sowie https://github.com/PicoQuant/PicoQuant-Time-Tagged-File-Format-Demos.

Unterstützte Record-Typen:
  PicoHarp 300 T2/T3, HydraHarp V1/V2 T2/T3, TimeHarp 260 N/P T2/T3,
  Generic T2/T3 (MultiHarp, PicoHarp 330; vermutlich auch HydraHarp 500 [unsicher]).

Kanalnummern werden in diesem Modul einheitlich 1-basiert ausgegeben (1 = erster
Detektoreingang); 0 steht für Sync-Ereignisse (nur T2).
"""
from __future__ import annotations

import datetime as _dt
import struct
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np

# ---------------------------------------------------------------- Tag-Typen
TY_EMPTY8 = 0xFFFF0008
TY_BOOL8 = 0x00000008
TY_INT8 = 0x10000008
TY_BITSET64 = 0x11000008
TY_COLOR8 = 0x12000008
TY_FLOAT8 = 0x20000008
TY_TDATETIME = 0x21000008
TY_FLOAT8ARRAY = 0x2001FFFF
TY_ANSISTRING = 0x4001FFFF
TY_WIDESTRING = 0x4002FFFF
TY_BINARYBLOB = 0xFFFFFFFF

# ---------------------------------------------------------------- Record-Typen
RT_PICOHARP300_T3 = 0x00010303
RT_PICOHARP300_T2 = 0x00010203
RT_HYDRAHARP_T3 = 0x00010304      # HydraHarp V1
RT_HYDRAHARP_T2 = 0x00010204
RT_HYDRAHARP2_T3 = 0x01010304     # HydraHarp V2
RT_HYDRAHARP2_T2 = 0x01010204
RT_TIMEHARP260N_T3 = 0x00010305
RT_TIMEHARP260N_T2 = 0x00010205
RT_TIMEHARP260P_T3 = 0x00010306
RT_TIMEHARP260P_T2 = 0x00010206
RT_GENERIC_T3 = 0x00010307        # MultiHarp, PicoHarp 330, Generic
RT_GENERIC_T2 = 0x00010207

RECORD_TYPES: dict[int, tuple[str, str, str]] = {
    # rectype: (name, mode, family)   family: 'pt' = PicoHarp300, 'h1' = HydraHarp V1, 'h2' = alle neueren
    RT_PICOHARP300_T3: ("PicoHarp 300 T3", "T3", "pt"),
    RT_PICOHARP300_T2: ("PicoHarp 300 T2", "T2", "pt"),
    RT_HYDRAHARP_T3: ("HydraHarp V1 T3", "T3", "h1"),
    RT_HYDRAHARP_T2: ("HydraHarp V1 T2", "T2", "h1"),
    RT_HYDRAHARP2_T3: ("HydraHarp V2 T3", "T3", "h2"),
    RT_HYDRAHARP2_T2: ("HydraHarp V2 T2", "T2", "h2"),
    RT_TIMEHARP260N_T3: ("TimeHarp 260N T3", "T3", "h2"),
    RT_TIMEHARP260N_T2: ("TimeHarp 260N T2", "T2", "h2"),
    RT_TIMEHARP260P_T3: ("TimeHarp 260P T3", "T3", "h2"),
    RT_TIMEHARP260P_T2: ("TimeHarp 260P T2", "T2", "h2"),
    RT_GENERIC_T3: ("Generic T3 (MultiHarp/PicoHarp 330/HydraHarp 500?)", "T3", "h2"),
    RT_GENERIC_T2: ("Generic T2 (MultiHarp/PicoHarp 330/HydraHarp 500?)", "T2", "h2"),
}

T3_WRAP_H = 1024
T2_WRAP_H1 = 33552000
T2_WRAP_H2 = 33554432
T3_WRAP_PT = 65536
T2_WRAP_PT = 210698240


class PtuError(ValueError):
    """Fehler beim Lesen einer PTU-Datei."""


@dataclass
class PtuHeader:
    version: str
    tags: dict[str, Any]
    data_offset: int
    path: Path | None = None

    def get(self, name: str, default: Any = None) -> Any:
        return self.tags.get(name, default)

    @property
    def record_type(self) -> int:
        return int(self.tags["TTResultFormat_TTTRRecType"]) & 0xFFFFFFFF

    @property
    def record_type_name(self) -> str:
        return RECORD_TYPES.get(self.record_type, ("unbekannt", "", ""))[0]

    @property
    def mode(self) -> str:
        return RECORD_TYPES[self.record_type][1]

    @property
    def family(self) -> str:
        return RECORD_TYPES[self.record_type][2]

    @property
    def num_records(self) -> int:
        return int(self.tags["TTResult_NumberOfRecords"])

    @property
    def global_resolution(self) -> float:
        """Sekunden pro Makrozeit-Einheit (T3: Sync-Periode, T2: Zeittakt)."""
        return float(self.tags["MeasDesc_GlobalResolution"])

    @property
    def resolution(self) -> float:
        """Sekunden pro dtime-Einheit (T3) bzw. Zeitauflösung (T2)."""
        return float(self.tags.get("MeasDesc_Resolution", self.global_resolution))

    @property
    def num_channels(self) -> int:
        n = self.tags.get("HW_InpChannels")
        if n is None:
            return 4
        n = int(n)
        # HW_InpChannels zählt bei manchen Geräten den Sync mit [unsicher, geräteabhängig]
        return max(1, n - 1) if self.tags.get("HW_InpChannelsInclSync", False) else n

    @property
    def sync_rate(self) -> float:
        r = self.tags.get("TTResult_SyncRate")
        if r:
            return float(r)
        if self.mode == "T3" and self.global_resolution > 0:
            return 1.0 / self.global_resolution
        return 0.0

    @property
    def acquisition_time_s(self) -> float:
        v = self.tags.get("MeasDesc_AcquisitionTime")
        return float(v) / 1000.0 if v is not None else 0.0


def _read_exact(f: BinaryIO, n: int) -> bytes:
    b = f.read(n)
    if len(b) != n:
        raise PtuError("Datei endet unerwartet im Header")
    return b


def read_header(path: str | Path) -> PtuHeader:
    """Liest den Tag-Header einer PTU-Datei (Magic 'PQTTTR')."""
    path = Path(path)
    with open(path, "rb") as f:
        magic = _read_exact(f, 8).rstrip(b"\0").decode("ascii", "replace")
        if magic != "PQTTTR":
            raise PtuError(f"Keine PTU-Datei (Magic '{magic}')")
        version = _read_exact(f, 8).rstrip(b"\0").decode("ascii", "replace")
        tags: dict[str, Any] = {}
        while True:
            ident = _read_exact(f, 32).rstrip(b"\0").decode("ascii", "replace")
            idx, typ = struct.unpack("<iI", _read_exact(f, 8))
            name = f"{ident}({idx})" if idx > -1 else ident
            raw = _read_exact(f, 8)
            if typ == TY_EMPTY8:
                val: Any = None
            elif typ == TY_BOOL8:
                val = struct.unpack("<q", raw)[0] != 0
            elif typ in (TY_INT8, TY_BITSET64, TY_COLOR8):
                val = struct.unpack("<q", raw)[0]
            elif typ == TY_FLOAT8:
                val = struct.unpack("<d", raw)[0]
            elif typ == TY_TDATETIME:
                days = struct.unpack("<d", raw)[0]
                val = (_dt.datetime(1899, 12, 30, tzinfo=_dt.timezone.utc)
                       + _dt.timedelta(days=days)).isoformat()
            elif typ == TY_FLOAT8ARRAY:
                n = struct.unpack("<q", raw)[0]
                val = list(struct.unpack(f"<{n // 8}d", _read_exact(f, n)))
            elif typ == TY_ANSISTRING:
                n = struct.unpack("<q", raw)[0]
                b = _read_exact(f, n).split(b"\0", 1)[0]
                try:
                    val = b.decode("utf-8")
                except UnicodeDecodeError:
                    val = b.decode("latin1", "ignore")
            elif typ == TY_WIDESTRING:
                n = struct.unpack("<q", raw)[0]
                val = _read_exact(f, n).decode("utf-16le", "ignore").split("\0", 1)[0]
            elif typ == TY_BINARYBLOB:
                n = struct.unpack("<q", raw)[0]
                f.seek(n, 1)
                val = f"<binary {n} bytes>"
            else:
                raise PtuError(f"Unbekannter Tag-Typ 0x{typ:08X} bei '{name}'")
            tags[name] = val
            if ident == "Header_End":
                break
        offset = f.tell()
    hdr = PtuHeader(version=version, tags=tags, data_offset=offset, path=path)
    for req in ("TTResultFormat_TTTRRecType", "TTResult_NumberOfRecords", "MeasDesc_GlobalResolution"):
        if req not in tags:
            raise PtuError(f"Pflicht-Tag fehlt: {req}")
    if hdr.record_type not in RECORD_TYPES:
        raise PtuError(f"Nicht unterstützter Record-Typ 0x{hdr.record_type:08X}")
    return hdr


def open_records(hdr: PtuHeader) -> np.ndarray:
    """Records als speichergemapptes uint32-Array (ohne alles in den RAM zu laden)."""
    assert hdr.path is not None
    size = hdr.path.stat().st_size - hdr.data_offset
    n = min(hdr.num_records, size // 4)
    if n <= 0:
        return np.zeros(0, dtype="<u4")
    return np.memmap(hdr.path, dtype="<u4", mode="r", offset=hdr.data_offset, shape=(n,))


# ---------------------------------------------------------------- Dekodierung
@dataclass
class Events:
    """Dekodierte Photonen-Ereignisse eines Blocks (Marker werden gezählt, nicht gespeichert).

    time:    absolute Makrozeit in Einheiten von MeasDesc_GlobalResolution (T3: Sync-Nummer)
    channel: 1-basiert, 0 = Sync (nur T2)
    dtime:   Mikrozeit (nur T3), in Einheiten von MeasDesc_Resolution
    """
    time: np.ndarray
    channel: np.ndarray
    dtime: np.ndarray
    markers: int = 0

    def __len__(self) -> int:
        return int(self.time.size)

    @staticmethod
    def empty() -> "Events":
        return Events(np.zeros(0, np.int64), np.zeros(0, np.int16), np.zeros(0, np.int32))

    def take(self, sl: slice) -> "Events":
        return Events(self.time[sl], self.channel[sl], self.dtime[sl])

    @staticmethod
    def concat(parts: list["Events"]) -> "Events":
        parts = [p for p in parts if len(p)]
        if not parts:
            return Events.empty()
        return Events(np.concatenate([p.time for p in parts]),
                      np.concatenate([p.channel for p in parts]),
                      np.concatenate([p.dtime for p in parts]),
                      sum(p.markers for p in parts))


@dataclass
class StreamDecoder:
    """Zustandsbehafteter, vektorisierter Dekoder (Overflow-Korrektur über Blockgrenzen)."""
    record_type: int
    ofl: int = 0  # akkumulierte Overflow-Korrektur (Makrozeit-Einheiten)
    mode: str = field(init=False)
    family: str = field(init=False)

    def __post_init__(self) -> None:
        if self.record_type not in RECORD_TYPES:
            raise PtuError(f"Nicht unterstützter Record-Typ 0x{self.record_type:08X}")
        _, self.mode, self.family = RECORD_TYPES[self.record_type]

    def reset(self) -> None:
        self.ofl = 0

    def decode(self, rec: np.ndarray) -> Events:
        rec = np.asarray(rec, dtype=np.uint32)
        if rec.size == 0:
            return Events.empty()
        if self.family == "pt":
            return self._decode_pt(rec)
        return self._decode_h(rec)

    def _decode_h(self, rec: np.ndarray) -> Events:
        special = (rec >> 31).astype(bool)
        ch = ((rec >> 25) & 0x3F).astype(np.int16)
        is_ofl = special & (ch == 0x3F)
        if self.mode == "T3":
            low = (rec & 0x3FF).astype(np.int64)
            wrap = T3_WRAP_H
        else:
            low = (rec & 0x1FFFFFF).astype(np.int64)
            wrap = T2_WRAP_H1 if self.family == "h1" else T2_WRAP_H2
        if self.family == "h1":
            nofl = is_ofl.astype(np.int64)
        else:
            nofl = np.where(is_ofl, np.where(low == 0, 1, low), 0)
        cum = np.cumsum(nofl * wrap) + self.ofl
        self.ofl = int(cum[-1])
        markers = int(np.count_nonzero(special & (ch >= 1) & (ch <= 15)))
        if self.mode == "T3":
            photon = ~special
            t = cum[photon] + low[photon]
            c = ch[photon] + 1
            d = ((rec[photon] >> 10) & 0x7FFF).astype(np.int32)
        else:
            sync = special & (ch == 0)
            photon = (~special) | sync
            t = cum[photon] + low[photon]
            c = np.where(sync[photon], 0, ch[photon] + 1).astype(np.int16)
            d = np.zeros(t.size, np.int32)
        return Events(t.astype(np.int64), c.astype(np.int16), d, markers)

    def _decode_pt(self, rec: np.ndarray) -> Events:
        ch = (rec >> 28).astype(np.int16)
        special = ch == 15
        if self.mode == "T3":
            dt = ((rec >> 16) & 0xFFF).astype(np.int32)
            low = (rec & 0xFFFF).astype(np.int64)
            is_ofl = special & (dt == 0)
            wrap = T3_WRAP_PT
        else:
            low = (rec & 0x0FFFFFFF).astype(np.int64)
            is_ofl = special & ((low & 0xF) == 0)
            wrap = T2_WRAP_PT
            dt = np.zeros(rec.size, np.int32)
        cum = np.cumsum(is_ofl.astype(np.int64) * wrap) + self.ofl
        self.ofl = int(cum[-1])
        markers = int(np.count_nonzero(special & ~is_ofl))
        photon = ~special
        t = cum[photon] + low[photon]
        if self.mode == "T3":
            c = ch[photon]          # PicoHarp 300 T3: Kanäle bereits 1..4
        else:
            c = ch[photon] + 1      # PicoHarp 300 T2: 0..3 -> 1..4
        return Events(t, c.astype(np.int16), dt[photon], markers)


def read_all(path: str | Path) -> tuple[PtuHeader, Events]:
    """Komplette Datei dekodieren (für Tests und kleine Dateien)."""
    hdr = read_header(path)
    dec = StreamDecoder(hdr.record_type)
    return hdr, dec.decode(np.asarray(open_records(hdr)))


# ---------------------------------------------------------------- Schreiben
def _tdatetime(d: _dt.datetime) -> float:
    epoch = _dt.datetime(1899, 12, 30, tzinfo=_dt.timezone.utc)
    return (d - epoch).total_seconds() / 86400.0


def write_ptu(path: str | Path, tags: list[tuple[str, int, Any, int]], records: np.ndarray) -> None:
    """Schreibt eine PTU-Datei.

    tags: Liste von (Name, Index(-1 = keiner), Wert, Tag-Typ). 'Header_End' wird automatisch angehängt.
    """
    out = bytearray()
    out += b"PQTTTR\0\0" + b"1.0.00\0\0"

    def tag(name: str, idx: int, typ: int, raw8: bytes, extra: bytes = b"") -> None:
        nb = name.encode("ascii")
        if len(nb) > 32:
            raise ValueError(f"Tag-Name zu lang: {name}")
        out.extend(nb.ljust(32, b"\0") + struct.pack("<iI", idx, typ) + raw8 + extra)

    for name, idx, val, typ in tags:
        if typ == TY_EMPTY8:
            tag(name, idx, typ, b"\0" * 8)
        elif typ == TY_BOOL8:
            tag(name, idx, typ, struct.pack("<q", 1 if val else 0))
        elif typ in (TY_INT8, TY_BITSET64, TY_COLOR8):
            tag(name, idx, typ, struct.pack("<q", int(val)))
        elif typ in (TY_FLOAT8, TY_TDATETIME):
            tag(name, idx, typ, struct.pack("<d", float(val)))
        elif typ == TY_ANSISTRING:
            b = str(val).encode("utf-8") + b"\0"
            b = b.ljust((len(b) + 7) // 8 * 8, b"\0")
            tag(name, idx, typ, struct.pack("<q", len(b)), b)
        elif typ == TY_WIDESTRING:
            b = (str(val) + "\0").encode("utf-16le")
            b = b.ljust((len(b) + 7) // 8 * 8, b"\0")
            tag(name, idx, typ, struct.pack("<q", len(b)), b)
        else:
            raise ValueError(f"Tag-Typ beim Schreiben nicht unterstützt: 0x{typ:08X}")
    tag("Header_End", -1, TY_EMPTY8, b"\0" * 8)
    with open(path, "wb") as f:
        f.write(out)
        f.write(np.asarray(records, dtype="<u4").tobytes())


def standard_tags(*, record_type: int, num_records: int, global_res_s: float, res_s: float,
                  mode: int, num_channels: int, acq_time_ms: int, sync_rate: float,
                  hw_type: str = "HydraHarp 500", hw_partno: str = "HH500-S (simuliert)",
                  binning: int = 0, comment: str = "", input_rates: list[float] | None = None
                  ) -> list[tuple[str, int, Any, int]]:
    """Ein plausibler Satz von Header-Tags gemäß PicoQuant Tag-Dictionary."""
    now = _dt.datetime.now(_dt.timezone.utc)
    t: list[tuple[str, int, Any, int]] = [
        ("File_GUID", -1, "{" + str(uuid.uuid4()).upper() + "}", TY_ANSISTRING),
        ("File_CreatingTime", -1, _tdatetime(now), TY_TDATETIME),
        ("File_Comment", -1, comment or "Synthetische Testdaten (ExSE Harp Mobile 1.0.0)", TY_ANSISTRING),
        ("CreatorSW_Name", -1, "HarpMobile-PTU-Generator", TY_ANSISTRING),
        ("CreatorSW_Version", -1, "1.0", TY_ANSISTRING),
        ("Measurement_Mode", -1, mode, TY_INT8),
        ("Measurement_SubMode", -1, 0, TY_INT8),
        ("MeasDesc_BinningFactor", -1, 2 ** binning, TY_INT8),
        ("MeasDesc_Offset", -1, 0, TY_INT8),
        ("MeasDesc_AcquisitionTime", -1, acq_time_ms, TY_INT8),
        ("MeasDesc_StopAt", -1, 0xFFFFFFFF, TY_INT8),
        ("MeasDesc_StopOnOvfl", -1, False, TY_BOOL8),
        ("MeasDesc_Restart", -1, False, TY_BOOL8),
        ("MeasDesc_GlobalResolution", -1, global_res_s, TY_FLOAT8),
        ("MeasDesc_Resolution", -1, res_s, TY_FLOAT8),
        ("HW_Type", -1, hw_type, TY_ANSISTRING),
        ("HW_PartNo", -1, hw_partno, TY_ANSISTRING),
        ("HW_SerialNo", -1, "SIM00001", TY_ANSISTRING),
        ("HW_Version", -1, "1.0", TY_ANSISTRING),
        ("HW_BaseResolution", -1, 1e-12, TY_FLOAT8),
        ("HW_InpChannels", -1, num_channels, TY_INT8),
        ("HWSync_Divider", -1, 1, TY_INT8),
        ("TTResult_SyncRate", -1, int(sync_rate), TY_INT8),
        ("TTResult_StopAfter", -1, acq_time_ms, TY_INT8),
        ("TTResult_StopReason", -1, 0, TY_INT8),
        ("TTResult_NumberOfRecords", -1, num_records, TY_INT8),
        ("TTResultFormat_TTTRRecType", -1, record_type, TY_INT8),
        ("TTResultFormat_BitsPerRecord", -1, 32, TY_INT8),
    ]
    for i, r in enumerate(input_rates or []):
        t.append(("TTResult_InputRate", i, int(r), TY_INT8))
    return t


def encode_t3_generic(nsync: np.ndarray, channel1: np.ndarray, dtime: np.ndarray) -> np.ndarray:
    """Kodiert (sortierte) T3-Ereignisse als HydraHarp-V2/Generic-Records inkl. Overflow-Records.

    channel1: 1-basiert. Overflow-Records tragen die Anzahl der Überläufe im nsync-Feld (1..1023).
    """
    nsync = np.asarray(nsync, np.int64)
    if nsync.size and np.any(np.diff(nsync) < 0):
        raise ValueError("nsync muss aufsteigend sortiert sein")
    hi = nsync >> 10
    prev = np.concatenate([[0], hi[:-1]])
    gaps = hi - prev
    photon = ((np.asarray(channel1, np.uint32) - 1) << 25) | (np.asarray(dtime, np.uint32) << 10) \
        | (nsync & 0x3FF).astype(np.uint32)
    out: list[np.ndarray] = []
    pos = np.flatnonzero(gaps > 0)
    start = 0
    for p in pos:
        out.append(photon[start:p])
        g = int(gaps[p])
        while g > 0:
            k = min(g, 1023)
            out.append(np.array([(1 << 31) | (0x3F << 25) | k], np.uint32))
            g -= k
        start = p
    out.append(photon[start:])
    return np.concatenate(out).astype(np.uint32) if out else np.zeros(0, np.uint32)


def encode_t2_generic(time: np.ndarray, channel: np.ndarray) -> np.ndarray:
    """Kodiert T2-Ereignisse (channel 0 = Sync, 1.. = Eingänge) als Generic-T2-Records."""
    time = np.asarray(time, np.int64)
    hi = time // T2_WRAP_H2
    lo = (time % T2_WRAP_H2).astype(np.uint32)
    prev = np.concatenate([[0], hi[:-1]])
    gaps = hi - prev
    ch = np.asarray(channel, np.int64)
    rec = np.where(ch == 0, (1 << 31) | lo, ((ch - 1).astype(np.uint32) << 25) | lo).astype(np.uint32)
    out: list[np.ndarray] = []
    start = 0
    for p in np.flatnonzero(gaps > 0):
        out.append(rec[start:p])
        g = int(gaps[p])
        while g > 0:
            k = min(g, (1 << 25) - 1)
            out.append(np.array([(1 << 31) | (0x3F << 25) | k], np.uint32))
            g -= k
        start = p
    out.append(rec[start:])
    return np.concatenate(out).astype(np.uint32)
