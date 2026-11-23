"""Apply non-destructive appointment reservation upgrades to existing databases."""
from pathlib import Path


def upgrade(db):
    version = db.execute('PRAGMA user_version').fetchone()[0]
    if version >= 2:
        return
    if version != 1:
        raise ValueError('Unsupported database schema version')
    # Reuse the current index and trigger definitions, without rebuilding data tables.
    schema = Path(__file__).with_name('schema.sql').read_text(encoding='utf-8')
    definitions = schema[schema.index('CREATE INDEX appointments_calendar'):schema.index('PRAGMA user_version')]
    db.executescript('''BEGIN IMMEDIATE;
        DROP TRIGGER IF EXISTS appointment_overlap_insert;
        DROP TRIGGER IF EXISTS appointment_overlap_update;
        DROP INDEX IF EXISTS appointments_calendar;
        DROP INDEX IF EXISTS appointments_inbox;
        DROP INDEX IF EXISTS closures_calendar;
        ''' + definitions + 'PRAGMA user_version = 2; COMMIT;')
