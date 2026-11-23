"""Create the prototype database outside the publicly served website folder."""
import argparse
from contextlib import closing
import os
from pathlib import Path
import sqlite3

SITE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'OzYilmazClinic' / 'clinic.sqlite3'


def initialize(path):
    path = Path(path).resolve()
    if path == SITE_ROOT or SITE_ROOT in path.parents:
        raise ValueError('Database must be outside the website directory to prevent public download.')
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f'Existing database will not be overwritten: {path}')
    # Exclusive creation avoids accidentally replacing an existing file.
    with path.open('xb'):
        pass
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript((SITE_ROOT / 'database' / 'schema.sql').read_text(encoding='utf-8'))
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert not connection.execute('PRAGMA foreign_key_check').fetchall()
    return path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--path', type=Path, default=DEFAULT_PATH)
    args = parser.parse_args()
    print(initialize(args.path))
