import sqlite3
conn = sqlite3.connect(r'D:\Clock_in_Out\clock_in_out.db')
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
print('Tables:', cur.fetchall())
cur.execute('SELECT id, name, is_admin, active FROM employees')
print('Employees:', cur.fetchall())
cur.execute('SELECT id, employee_id, date, clock_in, clock_out, shift_type FROM shifts')
print('Shifts:', cur.fetchall())
conn.close()

