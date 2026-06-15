from datetime import datetime, date, timedelta
from app.database import SessionLocal, engine, Base
from app.models import Employee, Shift
from app.auth import hash_password


def seed_database():
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        if db.query(Employee).count() > 0:
            return

        admin = Employee(
            name="Admin",
            password_hash=hash_password("admin123"),
            is_admin=True,
            active=True,
        )
        emp1 = Employee(
            name="John Doe",
            password_hash=hash_password("john2024"),
            is_admin=False,
            active=True,
        )
        emp2 = Employee(
            name="Jane Smith",
            password_hash=hash_password("jane2024"),
            is_admin=False,
            active=True,
        )
        emp3 = Employee(
            name="Bob Wilson",
            password_hash=hash_password("bob2024"),
            is_admin=False,
            active=True,
        )
        db.add_all([admin, emp1, emp2, emp3])
        db.commit()

        # Refresh to get IDs
        db.refresh(emp1)
        db.refresh(emp2)
        db.refresh(emp3)

        # Seed mock shifts for the last 14 days
        today = date.today()
        shifts = []

        # Helper to classify shift type
        def get_shift_type(dt: datetime) -> str:
            hour = dt.hour
            if 6 <= hour < 18:
                return "Day"
            return "Night"

        # John Doe (Employee 1) shifts
        for i in range(12):
            shift_date = today - timedelta(days=i)
            # Alternate day and night shifts
            if i % 2 == 0:
                # Day Shift
                ci = datetime(shift_date.year, shift_date.month, shift_date.day, 8, 0)
                co = datetime(shift_date.year, shift_date.month, shift_date.day, 16, 30) # 8.5 hours
            else:
                # Night Shift
                ci = datetime(shift_date.year, shift_date.month, shift_date.day, 20, 0)
                # Ends next day
                end_date = shift_date + timedelta(days=1)
                co = datetime(end_date.year, end_date.month, end_date.day, 4, 0) # 8.0 hours

            shifts.append(Shift(
                employee_id=emp1.id,
                clock_in=ci,
                clock_out=co,
                date=shift_date,
                shift_type=get_shift_type(ci)
            ))

        # Jane Smith (Employee 2) shifts - Mostly Day shifts
        for i in range(10):
            shift_date = today - timedelta(days=i)
            ci = datetime(shift_date.year, shift_date.month, shift_date.day, 9, 0)
            co = datetime(shift_date.year, shift_date.month, shift_date.day, 17, 0) # 8 hours
            shifts.append(Shift(
                employee_id=emp2.id,
                clock_in=ci,
                clock_out=co,
                date=shift_date,
                shift_type=get_shift_type(ci)
            ))

        # Bob Wilson (Employee 3) shifts - Mostly Night shifts
        for i in range(8):
            shift_date = today - timedelta(days=i)
            ci = datetime(shift_date.year, shift_date.month, shift_date.day, 22, 0)
            end_date = shift_date + timedelta(days=1)
            co = datetime(end_date.year, end_date.month, end_date.day, 6, 0) # 8 hours
            shifts.append(Shift(
                employee_id=emp3.id,
                clock_in=ci,
                clock_out=co,
                date=shift_date,
                shift_type=get_shift_type(ci)
            ))

        db.add_all(shifts)
        db.commit()
    finally:
        db.close()
