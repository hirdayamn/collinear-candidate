import sqlite3

conn = sqlite3.connect('/var/log/services/audit.db')
conn.execute('PRAGMA journal_mode=WAL;')
conn.execute('CREATE TABLE IF NOT EXISTS audit_log (id INTEGER PRIMARY KEY, metric_name TEXT, status TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP);')
for i in range(1, 51):
    conn.execute('INSERT OR IGNORE INTO audit_log (id, metric_name, status) VALUES (?, ?, ?)', (i, f'metric_{i}', 'PROCESSED'))
conn.commit()
conn.close()
print("Database initialized successfully.")