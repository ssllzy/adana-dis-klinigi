-- Local prototype schema. UTC timestamps are Unix seconds.
PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TABLE doctors (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 120),
    specialty TEXT,
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1))
);

CREATE TABLE services (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE CHECK(length(trim(name)) BETWEEN 1 AND 120),
    duration_minutes INTEGER NOT NULL CHECK(duration_minutes BETWEEN 5 AND 480),
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1))
);

CREATE TABLE doctor_services (
    doctor_id INTEGER NOT NULL REFERENCES doctors(id) ON DELETE RESTRICT,
    service_id INTEGER NOT NULL REFERENCES services(id) ON DELETE RESTRICT,
    PRIMARY KEY(doctor_id, service_id)
);

-- Local clinic time, Europe/Istanbul. Monday=1, Sunday=7.
-- Separate rows can represent morning and afternoon work around a break.
CREATE TABLE working_hours (
    id INTEGER PRIMARY KEY,
    doctor_id INTEGER NOT NULL REFERENCES doctors(id) ON DELETE RESTRICT,
    weekday INTEGER NOT NULL CHECK(weekday BETWEEN 1 AND 7),
    start_minute INTEGER NOT NULL CHECK(start_minute BETWEEN 0 AND 1439),
    end_minute INTEGER NOT NULL CHECK(end_minute BETWEEN 1 AND 1440),
    CHECK(end_minute > start_minute),
    UNIQUE(doctor_id, weekday, start_minute)
);

-- NULL doctor_id means the entire clinic is closed for this interval.
CREATE TABLE closures (
    id INTEGER PRIMARY KEY,
    doctor_id INTEGER REFERENCES doctors(id) ON DELETE RESTRICT,
    starts_at INTEGER NOT NULL CHECK(typeof(starts_at) = 'integer'),
    ends_at INTEGER NOT NULL CHECK(typeof(ends_at) = 'integer'),
    label TEXT,
    CHECK(ends_at > starts_at)
);

CREATE TABLE appointments (
    id INTEGER PRIMARY KEY,
    contact_name TEXT NOT NULL CHECK(length(trim(contact_name)) BETWEEN 1 AND 120),
    contact_phone TEXT NOT NULL CHECK(length(trim(contact_phone)) BETWEEN 7 AND 30),
    service_id INTEGER NOT NULL REFERENCES services(id) ON DELETE RESTRICT,
    doctor_id INTEGER REFERENCES doctors(id) ON DELETE RESTRICT,
    starts_at INTEGER CHECK(starts_at IS NULL OR typeof(starts_at) = 'integer'),
    ends_at INTEGER CHECK(ends_at IS NULL OR typeof(ends_at) = 'integer'),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'confirmed', 'cancelled', 'completed', 'no_show')),
    created_at INTEGER NOT NULL DEFAULT (unixepoch()),
    CHECK((starts_at IS NULL AND ends_at IS NULL) OR
          (starts_at IS NOT NULL AND ends_at IS NOT NULL AND ends_at > starts_at)),
    CHECK(status NOT IN ('confirmed', 'completed', 'no_show') OR
          (doctor_id IS NOT NULL AND starts_at IS NOT NULL AND ends_at IS NOT NULL)),
    FOREIGN KEY(doctor_id, service_id) REFERENCES doctor_services(doctor_id, service_id)
        ON DELETE RESTRICT
);

CREATE INDEX appointments_calendar ON appointments(doctor_id, starts_at, ends_at)
    WHERE status IN ('pending', 'confirmed');
CREATE INDEX appointments_inbox ON appointments(status, created_at);
CREATE INDEX closures_calendar ON closures(doctor_id, starts_at, ends_at);

-- Back-to-back appointments are allowed; overlapping pending or confirmed slots are not.
CREATE TRIGGER appointment_overlap_insert BEFORE INSERT ON appointments
WHEN NEW.status IN ('pending', 'confirmed')
BEGIN
    SELECT RAISE(ABORT, 'doctor_slot_conflict') WHERE EXISTS (
        SELECT 1 FROM appointments WHERE doctor_id = NEW.doctor_id
          AND status IN ('pending', 'confirmed') AND starts_at < NEW.ends_at AND ends_at > NEW.starts_at
    );
END;

CREATE TRIGGER appointment_overlap_update BEFORE UPDATE ON appointments
WHEN NEW.status IN ('pending', 'confirmed')
BEGIN
    SELECT RAISE(ABORT, 'doctor_slot_conflict') WHERE EXISTS (
        SELECT 1 FROM appointments WHERE id <> NEW.id AND doctor_id = NEW.doctor_id
          AND status IN ('pending', 'confirmed') AND starts_at < NEW.ends_at AND ends_at > NEW.starts_at
    );
END;

PRAGMA user_version = 2;
COMMIT;

