import re
from datetime import datetime, date, timedelta
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db, Base, engine
from app.models import Employee, Shift
from app.schemas import (
    LoginRequest, ClockActionRequest,
    EmployeeCreate, EmployeeUpdate,
    ShiftCreate, ShiftUpdate, EmployeeShiftCreate,
)
from app.auth import (
    verify_password, hash_password, create_access_token, get_current_user,
)
from app.seed import seed_database

app = FastAPI(title="Clock In / Out System")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static", html=True), name="static")


@app.get("/")
def redirect_to_static():
    return RedirectResponse(url="/static/index.html")


@app.on_event("startup")
def startup():
    seed_database()


def parse_12hour_time(time_str: str, date_obj: date) -> datetime:
    time_str = time_str.strip().upper()
    match = re.match(r'(\d{1,2}):(\d{2})\s*(AM|PM)', time_str)
    if not match:
        raise ValueError(f"Invalid time format: {time_str}")
    hour = int(match.group(1))
    minute = int(match.group(2))
    period = match.group(3)
    if period == "PM" and hour != 12:
        hour += 12
    if period == "AM" and hour == 12:
        hour = 0
    return datetime(date_obj.year, date_obj.month, date_obj.day, hour, minute)


def format_12hour(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


def get_shift_type(dt: datetime) -> str:
    # Day shift is starting between 6:00 AM (inclusive) and 6:00 PM (exclusive)
    hour = dt.hour
    if 6 <= hour < 18:
        return "Day"
    return "Night"


def calculate_shift_hours(clock_in: datetime, clock_out: Optional[datetime]) -> Optional[float]:
    if not clock_out:
        return None
    diff_hours = (clock_out - clock_in).total_seconds() / 3600
    import math
    return math.floor(diff_hours * 2 + 0.5) / 2



def require_admin(current_user=Depends(get_current_user)):
    if not current_user["is_admin"]:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


def is_passcode_taken(password: str, db: Session, exclude_employee_id: Optional[int] = None) -> bool:
    employees = db.query(Employee).filter(Employee.active == True)
    if exclude_employee_id is not None:
        employees = employees.filter(Employee.id != exclude_employee_id)
    for emp in employees.all():
        try:
            if verify_password(password, emp.password_hash):
                return True
        except Exception:
            continue
    return False


# ─── Auth ───

@app.post("/api/auth/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    employees = db.query(Employee).filter(Employee.active == True).all()
    for emp in employees:
        if verify_password(req.password, emp.password_hash):
            token = create_access_token({
                "employee_id": emp.id,
                "is_admin": emp.is_admin,
            })
            return {
                "access_token": token,
                "employee_id": emp.id,
                "name": emp.name,
                "is_admin": emp.is_admin,
            }
    raise HTTPException(status_code=401, detail="Invalid password")


# ─── Employee ───

@app.get("/api/employee/me")
def employee_me(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    emp = db.query(Employee).filter(Employee.id == current_user["employee_id"]).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    return {
        "id": emp.id,
        "name": emp.name,
        "is_admin": emp.is_admin,
    }


@app.get("/api/employee/active-shift")
def active_shift(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    shift = db.query(Shift).filter(
        Shift.employee_id == current_user["employee_id"],
        Shift.clock_out.is_(None),
    ).first()
    if not shift:
        return {"active": False}
    return {
        "active": True,
        "shift_id": shift.id,
        "clock_in": format_12hour(shift.clock_in),
        "clock_in_datetime": shift.clock_in.isoformat(),
        "date": shift.date.isoformat(),
        "shift_type": shift.shift_type or get_shift_type(shift.clock_in),
    }


@app.post("/api/employee/clock-in")
def clock_in(
    req: ClockActionRequest,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        d = date.fromisoformat(req.date)
        clock_in_dt = parse_12hour_time(req.time, d)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid date or time format")

    existing = db.query(Shift).filter(
        Shift.employee_id == current_user["employee_id"],
        Shift.clock_out.is_(None),
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Already have an active shift")

    shift = Shift(
        employee_id=current_user["employee_id"],
        clock_in=clock_in_dt,
        date=d,
        shift_type=get_shift_type(clock_in_dt),
    )
    db.add(shift)
    db.commit()
    db.refresh(shift)

    return {
        "id": shift.id,
        "clock_in": format_12hour(shift.clock_in),
        "date": shift.date.isoformat(),
        "shift_type": shift.shift_type,
        "message": "Shift started successfully",
    }


@app.post("/api/employee/clock-out")
def clock_out(
    req: ClockActionRequest,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    shift = db.query(Shift).filter(
        Shift.employee_id == current_user["employee_id"],
        Shift.clock_out.is_(None),
    ).first()
    if not shift:
        raise HTTPException(status_code=400, detail="No active shift found")

    try:
        clock_out_dt = parse_12hour_time(req.time, shift.date)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid date or time format")

    if clock_out_dt < shift.clock_in:
        clock_out_dt += timedelta(days=1)
    elif clock_out_dt == shift.clock_in:
        raise HTTPException(status_code=400, detail="Clock-out time must be after clock-in time")

    shift.clock_out = clock_out_dt
    db.commit()
    db.refresh(shift)

    hours = calculate_shift_hours(shift.clock_in, shift.clock_out)

    return {
        "id": shift.id,
        "clock_in": format_12hour(shift.clock_in),
        "clock_out": format_12hour(shift.clock_out),
        "date": shift.date.isoformat(),
        "hours": hours,
        "shift_type": shift.shift_type,
        "message": "Shift ended successfully",
    }


@app.get("/api/employee/hours/week")
def employee_weekly_hours(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)

    shifts = db.query(Shift).filter(
        Shift.employee_id == current_user["employee_id"],
        Shift.date >= monday,
        Shift.date <= sunday,
        Shift.clock_out.isnot(None),
    ).all()

    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    result = {}
    for i, day_name in enumerate(days):
        day_date = monday + timedelta(days=i)
        total = 0.0
        for s in shifts:
            if s.date == day_date:
                total += calculate_shift_hours(s.clock_in, s.clock_out) or 0.0
        result[day_name] = total

    return result


@app.get("/api/employee/hours/month")
def employee_monthly_hours(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    today = date.today()
    first_day = today.replace(day=1)
    if today.month == 12:
        last_day = today.replace(day=31)
    else:
        last_day = (today.replace(month=today.month + 1, day=1) - timedelta(days=1))

    shifts = db.query(Shift).filter(
        Shift.employee_id == current_user["employee_id"],
        Shift.date >= first_day,
        Shift.date <= last_day,
        Shift.clock_out.isnot(None),
    ).all()

    hours_by_week = {}
    for s in shifts:
        week_num = (s.date.day - 1) // 7 + 1
        if week_num not in hours_by_week:
            hours_by_week[week_num] = 0.0
        hours_by_week[week_num] += calculate_shift_hours(s.clock_in, s.clock_out) or 0.0

    result = []
    for w in range(1, 6):
        result.append(hours_by_week.get(w, 0.0))

    return {"labels": ["Week 1", "Week 2", "Week 3", "Week 4", "Week 5"], "data": result}


@app.get("/api/employee/shifts")
def employee_shifts(
    limit: int = Query(10, ge=1, le=100),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    shifts = (
        db.query(Shift)
        .filter(Shift.employee_id == current_user["employee_id"])
        .order_by(Shift.date.desc(), Shift.clock_in.desc())
        .limit(limit)
        .all()
    )
    result = []
    for s in shifts:
        hours = calculate_shift_hours(s.clock_in, s.clock_out)
        result.append({
            "id": s.id,
            "date": s.date.isoformat(),
            "clock_in": format_12hour(s.clock_in),
            "clock_out": format_12hour(s.clock_out) if s.clock_out else None,
            "hours": hours,
            "shift_type": s.shift_type or get_shift_type(s.clock_in),
        })
    return result


@app.post("/api/employee/shifts")
def employee_create_shift(
    req: EmployeeShiftCreate,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        ci_date = date.fromisoformat(req.clock_in_date)
        ci_dt = parse_12hour_time(req.clock_in_time, ci_date)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid clock-in date or time")

    co_dt = None
    if req.clock_out_date and req.clock_out_time:
        try:
            co_date = date.fromisoformat(req.clock_out_date)
            co_dt = parse_12hour_time(req.clock_out_time, co_date)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid clock-out date or time")
        if co_dt <= ci_dt:
            raise HTTPException(status_code=400, detail="Clock-out time must be after clock-in time")

    # Check for active shift
    active_shift = db.query(Shift).filter(
        Shift.employee_id == current_user["employee_id"],
        Shift.clock_out.is_(None),
    ).first()

    if active_shift:
        if co_dt:
            active_shift.clock_in = ci_dt
            active_shift.clock_out = co_dt
            active_shift.date = ci_date
            active_shift.shift_type = req.shift_type or get_shift_type(ci_dt)
            db.commit()
            db.refresh(active_shift)
            hours = calculate_shift_hours(ci_dt, co_dt)
            return {
                "id": active_shift.id,
                "clock_in": format_12hour(active_shift.clock_in),
                "clock_out": format_12hour(active_shift.clock_out),
                "hours": hours,
                "shift_type": active_shift.shift_type,
                "message": "Active shift updated and completed",
            }
        else:
            raise HTTPException(status_code=400, detail="Already have an active shift. Please specify a clock-out time to complete it.")

    # Create new shift
    shift = Shift(
        employee_id=current_user["employee_id"],
        clock_in=ci_dt,
        clock_out=co_dt,
        date=ci_date,
        shift_type=req.shift_type or get_shift_type(ci_dt),
    )
    db.add(shift)
    db.commit()
    db.refresh(shift)

    hours = calculate_shift_hours(ci_dt, co_dt)

    return {
        "id": shift.id,
        "clock_in": format_12hour(shift.clock_in),
        "clock_out": format_12hour(shift.clock_out) if shift.clock_out else None,
        "hours": hours,
        "shift_type": shift.shift_type,
        "message": "Shift logged successfully",
    }


@app.get("/api/employee/reports")
def employee_reports(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(Shift).filter(Shift.employee_id == current_user["employee_id"])
    if date_from:
        q = q.filter(Shift.date >= date.fromisoformat(date_from))
    if date_to:
        q = q.filter(Shift.date <= date.fromisoformat(date_to))

    shifts = q.order_by(Shift.date.desc(), Shift.clock_in.desc()).all()

    total_hours = 0.0
    day_hours = 0.0
    night_hours = 0.0

    detailed_shifts = []
    for s in shifts:
        hours = 0.0
        if s.clock_out:
            hours = calculate_shift_hours(s.clock_in, s.clock_out) or 0.0
            total_hours += hours
            if s.shift_type == "Day":
                day_hours += hours
            else:
                night_hours += hours

        detailed_shifts.append({
            "id": s.id,
            "date": s.date.isoformat(),
            "clock_in": format_12hour(s.clock_in),
            "clock_out": format_12hour(s.clock_out) if s.clock_out else None,
            "hours": hours if s.clock_out else None,
            "shift_type": s.shift_type or get_shift_type(s.clock_in),
        })

    return {
        "shifts": detailed_shifts,
        "summary": {
            "total_hours": round(total_hours, 2),
            "day_hours": round(day_hours, 2),
            "night_hours": round(night_hours, 2),
            "total_shifts": len(shifts),
        }
    }


# ─── Admin: Dashboard ───

@app.get("/api/admin/dashboard")
def admin_dashboard(current_user=Depends(require_admin), db: Session = Depends(get_db)):
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    first_day = today.replace(day=1)
    if today.month == 12:
        last_day = today.replace(day=31)
    else:
        last_day = (today.replace(month=today.month + 1, day=1) - timedelta(days=1))

    employees = db.query(Employee).filter(
        Employee.active == True, Employee.is_admin == False
    ).all()

    weekly_data = {}
    monthly_data = {}

    for emp in employees:
        emp_shifts = db.query(Shift).filter(
            Shift.employee_id == emp.id,
            Shift.clock_out.isnot(None),
        )

        w_shifts = emp_shifts.filter(
            Shift.date >= monday, Shift.date <= monday + timedelta(days=6)
        ).all()
        w_total = sum(
            calculate_shift_hours(s.clock_in, s.clock_out) or 0.0 for s in w_shifts
        )
        weekly_data[emp.name] = w_total

        m_shifts = emp_shifts.filter(
            Shift.date >= first_day, Shift.date <= last_day
        ).all()
        m_total = sum(
            calculate_shift_hours(s.clock_in, s.clock_out) or 0.0 for s in m_shifts
        )
        monthly_data[emp.name] = m_total

    active_count = (
        db.query(Shift)
        .filter(Shift.clock_out.is_(None), Shift.date == today)
        .count()
    )

    return {
        "weekly_hours": weekly_data,
        "monthly_hours": monthly_data,
        "active_shifts": active_count,
        "total_employees": len(employees),
    }


@app.get("/api/admin/directory")
def admin_directory(current_user=Depends(require_admin), db: Session = Depends(get_db)):
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    first_day = today.replace(day=1)
    if today.month == 12:
        last_day = today.replace(day=31)
    else:
        last_day = (today.replace(month=today.month + 1, day=1) - timedelta(days=1))

    employees = db.query(Employee).filter(
        Employee.active == True, Employee.is_admin == False
    ).all()

    result = []
    for emp in employees:
        completed = db.query(Shift).filter(
            Shift.employee_id == emp.id,
            Shift.clock_out.isnot(None),
        )

        w_shifts = completed.filter(
            Shift.date >= monday, Shift.date <= monday + timedelta(days=6)
        ).all()
        w_total = sum(
            calculate_shift_hours(s.clock_in, s.clock_out) or 0.0 for s in w_shifts
        )

        m_shifts = completed.filter(
            Shift.date >= first_day, Shift.date <= last_day
        ).all()
        m_total = sum(
            calculate_shift_hours(s.clock_in, s.clock_out) or 0.0 for s in m_shifts
        )
        days_worked = len(set(s.date for s in m_shifts))

        active_now = db.query(Shift).filter(
            Shift.employee_id == emp.id,
            Shift.clock_out.is_(None),
        ).first() is not None

        result.append({
            "id": emp.id,
            "name": emp.name,
            "weekly_hours": round(w_total, 2),
            "monthly_hours": round(m_total, 2),
            "days_worked": days_worked,
            "active_now": active_now,
        })

    return result


@app.get("/api/admin/hours/monthly-history")
def admin_monthly_history(current_user=Depends(require_admin), db: Session = Depends(get_db)):
    today = date.today()
    months = []
    for i in range(5, -1, -1):
        m = today.month - i
        y = today.year
        while m < 1:
            m += 12
            y -= 1
        while m > 12:
            m -= 12
            y += 1
        first = date(y, m, 1)
        if m == 12:
            last = date(y, 12, 31)
        else:
            last = date(y, m + 1, 1) - timedelta(days=1)

        shifts = db.query(Shift).filter(
            Shift.clock_out.isnot(None),
            Shift.date >= first,
            Shift.date <= last,
        ).all()

        total = sum(calculate_shift_hours(s.clock_in, s.clock_out) or 0.0 for s in shifts)

        months.append({
            "month": first.strftime("%b"),
            "hours": total,
        })
    return months


# ─── Admin: Employees ───

@app.get("/api/admin/employees")
def admin_employees(current_user=Depends(require_admin), db: Session = Depends(get_db)):
    employees = db.query(Employee).order_by(Employee.id).all()
    return [
        {
            "id": e.id,
            "name": e.name,
            "is_admin": e.is_admin,
            "active": e.active,
            "has_password": True,
        }
        for e in employees
    ]


@app.post("/api/admin/employees")
def admin_create_employee(
    req: EmployeeCreate,
    current_user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    existing = db.query(Employee).filter(Employee.name == req.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="Employee name already exists")
    if is_passcode_taken(req.password, db):
        raise HTTPException(status_code=400, detail="Passcode is already in use by another active employee")
    emp = Employee(
        name=req.name,
        password_hash=hash_password(req.password),
        is_admin=False,
        active=True,
    )
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return {"id": emp.id, "name": emp.name, "message": "Employee created"}


@app.put("/api/admin/employees/{employee_id}")
def admin_update_employee(
    employee_id: int,
    req: EmployeeUpdate,
    current_user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    if req.name is not None:
        emp.name = req.name
    if req.password is not None:
        if is_passcode_taken(req.password, db, exclude_employee_id=employee_id):
            raise HTTPException(status_code=400, detail="Passcode is already in use by another active employee")
        emp.password_hash = hash_password(req.password)
    db.commit()
    return {"id": emp.id, "name": emp.name, "message": "Employee updated"}


@app.delete("/api/admin/employees/{employee_id}")
def admin_delete_employee(
    employee_id: int,
    current_user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    if emp.is_admin:
        raise HTTPException(status_code=400, detail="Cannot delete admin")
    db.delete(emp)
    db.commit()
    return {"message": "Employee deleted"}


# Sites functionality removed


# ─── Admin: Shifts ───

@app.get("/api/admin/shifts")
def admin_shifts(
    employee_id: Optional[int] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    current_user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    q = db.query(Shift).join(Employee, Shift.employee_id == Employee.id)
    if employee_id:
        q = q.filter(Shift.employee_id == employee_id)
    if date_from:
        q = q.filter(Shift.date >= date.fromisoformat(date_from))
    if date_to:
        q = q.filter(Shift.date <= date.fromisoformat(date_to))
    shifts = q.order_by(Shift.date.desc(), Shift.clock_in.desc()).all()

    result = []
    for s in shifts:
        hours = calculate_shift_hours(s.clock_in, s.clock_out)
        emp = db.query(Employee).filter(Employee.id == s.employee_id).first()
        result.append({
            "id": s.id,
            "employee_id": s.employee_id,
            "employee_name": emp.name if emp else "Unknown",
            "date": s.date.isoformat(),
            "clock_in": format_12hour(s.clock_in),
            "clock_out": format_12hour(s.clock_out) if s.clock_out else None,
            "hours": hours,
            "shift_type": s.shift_type or get_shift_type(s.clock_in),
        })
    return result


@app.post("/api/admin/shifts")
def admin_create_shift(
    req: ShiftCreate,
    current_user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    try:
        ci_date = date.fromisoformat(req.clock_in_date)
        ci_dt = parse_12hour_time(req.clock_in_time, ci_date)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid clock-in date or time")

    co_dt = None
    if req.clock_out_date and req.clock_out_time:
        try:
            co_date = date.fromisoformat(req.clock_out_date)
            co_dt = parse_12hour_time(req.clock_out_time, co_date)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid clock-out date or time")
        if co_dt <= ci_dt:
            raise HTTPException(status_code=400, detail="Clock-out time must be after clock-in time")

    shift = Shift(
        employee_id=req.employee_id,
        clock_in=ci_dt,
        clock_out=co_dt,
        date=ci_date,
        shift_type=req.shift_type or get_shift_type(ci_dt),
    )
    db.add(shift)
    db.commit()
    db.refresh(shift)

    hours = calculate_shift_hours(ci_dt, co_dt)

    return {
        "id": shift.id,
        "clock_in": format_12hour(shift.clock_in),
        "clock_out": format_12hour(shift.clock_out) if shift.clock_out else None,
        "hours": hours,
        "shift_type": shift.shift_type,
        "message": "Shift created",
    }


@app.put("/api/admin/shifts/{shift_id}")
def admin_update_shift(
    shift_id: int,
    req: ShiftUpdate,
    current_user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    shift = db.query(Shift).filter(Shift.id == shift_id).first()
    if not shift:
        raise HTTPException(status_code=404, detail="Shift not found")

    temp_ci = shift.clock_in
    temp_co = shift.clock_out
    temp_date = shift.date

    if req.clock_in_date and req.clock_in_time:
        try:
            ci_date = date.fromisoformat(req.clock_in_date)
            temp_ci = parse_12hour_time(req.clock_in_time, ci_date)
            temp_date = ci_date
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid clock-in date or time")

    if req.clock_out_date and req.clock_out_time:
        try:
            co_date = date.fromisoformat(req.clock_out_date)
            temp_co = parse_12hour_time(req.clock_out_time, co_date)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid clock-out date or time")
    elif req.clock_out_date == "" or req.clock_out_time == "":
        temp_co = None

    if temp_co and temp_co <= temp_ci:
        raise HTTPException(status_code=400, detail="Clock-out time must be after clock-in time")

    shift.clock_in = temp_ci
    shift.clock_out = temp_co
    shift.date = temp_date
    shift.shift_type = req.shift_type or get_shift_type(temp_ci)

    db.commit()
    db.refresh(shift)

    hours = calculate_shift_hours(temp_ci, temp_co)

    return {
        "id": shift.id,
        "clock_in": format_12hour(shift.clock_in),
        "clock_out": format_12hour(shift.clock_out) if shift.clock_out else None,
        "hours": hours,
        "shift_type": shift.shift_type,
        "message": "Shift updated",
    }


@app.get("/api/admin/reports")
def admin_reports(
    employee_id: Optional[int] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    current_user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    q = db.query(Shift).join(Employee, Shift.employee_id == Employee.id)
    if employee_id:
        q = q.filter(Shift.employee_id == employee_id)
    if date_from:
        q = q.filter(Shift.date >= date.fromisoformat(date_from))
    if date_to:
        q = q.filter(Shift.date <= date.fromisoformat(date_to))

    shifts = q.order_by(Shift.date.desc(), Shift.clock_in.desc()).all()

    total_hours = 0.0
    day_hours = 0.0
    night_hours = 0.0

    detailed_shifts = []
    for s in shifts:
        hours = 0.0
        if s.clock_out:
            hours = calculate_shift_hours(s.clock_in, s.clock_out) or 0.0
            total_hours += hours
            if s.shift_type == "Day":
                day_hours += hours
            else:
                night_hours += hours

        emp = db.query(Employee).filter(Employee.id == s.employee_id).first()
        detailed_shifts.append({
            "id": s.id,
            "employee_id": s.employee_id,
            "employee_name": emp.name if emp else "Unknown",
            "date": s.date.isoformat(),
            "clock_in": format_12hour(s.clock_in),
            "clock_out": format_12hour(s.clock_out) if s.clock_out else None,
            "hours": hours if s.clock_out else None,
            "shift_type": s.shift_type or get_shift_type(s.clock_in),
        })

    return {
        "shifts": detailed_shifts,
        "summary": {
            "total_hours": round(total_hours, 2),
            "day_hours": round(day_hours, 2),
            "night_hours": round(night_hours, 2),
            "total_shifts": len(shifts),
        }
    }


@app.delete("/api/admin/shifts/{shift_id}")
def admin_delete_shift(
    shift_id: int,
    current_user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    shift = db.query(Shift).filter(Shift.id == shift_id).first()
    if not shift:
        raise HTTPException(status_code=404, detail="Shift not found")
    db.delete(shift)
    db.commit()
    return {"message": "Shift deleted"}
