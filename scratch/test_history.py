from app.database import SessionLocal
from app.models import Shift
from sqlalchemy import func
from datetime import date, timedelta

db = SessionLocal()
try:
    first = date(2026, 6, 1)
    last = date(2026, 6, 30)

    print("Querying shifts between", first, "and", last)
    
    total = db.query(func.sum(
        (Shift.clock_out - Shift.clock_in) / 3600
    )).filter(
        Shift.clock_out.isnot(None),
        Shift.date >= first,
        Shift.date <= last,
    ).scalar()
    print("Result:", total)
    print("Result type:", type(total))
except Exception as e:
    import traceback
    traceback.print_exc()
finally:
    db.close()
