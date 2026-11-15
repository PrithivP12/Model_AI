from pathlib import Path
from datetime import date, datetime
import hashlib
import json
import math
import yaml


def load_config(path):
    path = Path(path).resolve()
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError('Configuration must be a mapping')
    return data, path.parent


def resolve(base, path):
    p = Path(path).expanduser()
    return p.resolve() if p.is_absolute() else (Path(base) / p).resolve()


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, value):
    def convert(x):
        from dataclasses import is_dataclass,asdict
        if is_dataclass(x):return asdict(x)
        if isinstance(x, (date, datetime, Path)):
            return str(x)
        if hasattr(x, 'item'):
            return x.item()
        raise TypeError(type(x).__name__)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False, default=convert) + '\n')


def number(value, name, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{name} must be a finite number')
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        raise ValueError(f'{name} is outside supported bounds')
    return float(value)
