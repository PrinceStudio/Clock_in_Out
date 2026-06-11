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
from app.models import Employee, Site, Shift, Schedule
from app.schemas import (
    LoginRequest, ClockActionRequest,
    EmployeeCreate, EmployeeUpdate, SiteCreate, SiteUpdate,
    ShiftCreate, ShiftUpdate, ScheduleCreate,
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


def require_admin(current_user=Depends(get_current_user)):
    if not current_user["is_admin"]:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


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

    today = date.today()
    schedule = db.query(Schedule).filter(
        Schedule.employee_id == emp.id,
        Schedule.date == today
    ).first()
    site_name = None
    if schedule:
        site = db.query(Site).filter(Site.id == schedule.site_id).first()
        if site:
            site_name = site.name

    return {
        "id": emp.id,
        "name": emp.name,
        "is_admin": emp.is_admin,
        "today_site": site_name,
    }


@app.get("/api/employee/active-shift")
def active_shift(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    today = date.today()
    shift = db.query(Shift).filter(
        Shift.employee_id == current_user["employee_id"],
        Shift.date == today,
        Shift.clock_out.is_(None),
    ).first()
    if not shift:
        return {"active": False}
    site_name = None
    if shift.site_id:
        site = db.query(Site).filter(Site.id == shift.site_id).first()
        if site:
            site_name = site.name
    return {
        "active": True,
        "shift_id": shift.id,
        "clock_in": format_12hour(shift.clock_in),
        "clock_in_datetime": shift.clock_in.isoformat(),
        "site": site_name,
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

    emp = db.query(Employee).filter(Employee.id == current_user["employee_id"]).first()
    schedule = db.query(Schedule).filter(
        Schedule.employee_id == emp.id,
        Schedule.date == d
    ).first()

    shift = Shift(
        employee_id=current_user["employee_id"],
        site_id=schedule.site_id if schedule else None,
        clock_in=clock_in_dt,
        date=d,
    )
    db.add(shift)
    db.commit()
    db.refresh(shift)

    return {
        "id": shift.id,
        "clock_in": format_12hour(shift.clock_in),
        "date": shift.date.isoformat(),
        "message": "Shift started successfully",
    }


@app.post("/api/employee/clock-out")
def clock_out(
    req: ClockActionRequest,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        d = date.fromisoformat(req.date)
        clock_out_dt = parse_12hour_time(req.time, d)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid date or time format")

    shift = db.query(Shift).filter(
        Shift.employee_id == current_user["employee_id"],
        Shift.clock_out.is_(None),
    ).first()
    if not shift:
        raise HTTPException(status_code=400, detail="No active shift found")

    if clock_out_dt <= shift.clock_in:
        clock_out_dt += timedelta(days=1)

    shift.clock_out = clock_out_dt
    db.commit()
    db.refresh(shift)

    hours = (shift.clock_out - shift.clock_in).total_seconds() / 3600

    return {
        "id": shift.id,
        "clock_in": format_12hour(shift.clock_in),
        "clock_out": format_12hour(shift.clock_out),
        "date": shift.date.isoformat(),
        "hours": round(hours, 2),
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
                total += (s.clock_out - s.clock_in).total_seconds() / 3600
        result[day_name] = round(total, 2)

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
        hours_by_week[week_num] += (s.clock_out - s.clock_in).total_seconds() / 3600

    result = []
    for w in range(1, 6):
        result.append(round(hours_by_week.get(w, 0.0), 2))

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
        hours = None
        if s.clock_out:
            hours = round((s.clock_out - s.clock_in).total_seconds() / 3600, 2)
        site_name = None
        if s.site_id:
            site = db.query(Site).filter(Site.id == s.site_id).first()
            if site:
                site_name = site.name
        result.append({
            "id": s.id,
            "date": s.date.isoformat(),
            "clock_in": format_12hour(s.clock_in),
            "clock_out": format_12hour(s.clock_out) if s.clock_out else None,
            "hours": hours,
            "site": site_name,
        })
    return result


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
            (s.clock_out - s.clock_in).total_seconds() / 3600 for s in w_shifts
        )
        weekly_data[emp.name] = round(w_total, 2)

        m_shifts = emp_shifts.filter(
            Shift.date >= first_day, Shift.date <= last_day
        ).all()
        m_total = sum(
            (s.clock_out - s.clock_in).total_seconds() / 3600 for s in m_shifts
        )
        monthly_data[emp.name] = round(m_total, 2)

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


# ─── Admin: Sites ───

@app.get("/api/admin/sites")
def admin_sites(current_user=Depends(require_admin), db: Session = Depends(get_db)):
    sites = db.query(Site).order_by(Site.id).all()
    return [
        {"id": s.id, "name": s.name, "active": s.active} for s in sites
    ]


@app.post("/api/admin/sites")
def admin_create_site(
    req: SiteCreate,
    current_user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    existing = db.query(Site).filter(Site.name == req.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="Site already exists")
    site = Site(name=req.name, active=True)
    db.add(site)
    db.commit()
    db.refresh(site)
    return {"id": site.id, "name": site.name, "message": "Site created"}


@app.put("/api/admin/sites/{site_id}")
def admin_update_site(
    site_id: int,
    req: SiteUpdate,
    current_user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    site = db.query(Site).filter(Site.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    site.name = req.name
    db.commit()
    return {"id": site.id, "name": site.name, "message": "Site updated"}


@app.delete("/api/admin/sites/{site_id}")
def admin_delete_site(
    site_id: int,
    current_user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    site = db.query(Site).filter(Site.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    db.delete(site)
    db.commit()
    return {"message": "Site deleted"}


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
        hours = None
        if s.clock_out:
            hours = round((s.clock_out - s.clock_in).total_seconds() / 3600, 2)
        emp = db.query(Employee).filter(Employee.id == s.employee_id).first()
        site_name = None
        if s.site_id:
            site = db.query(Site).filter(Site.id == s.site_id).first()
            if site:
                site_name = site.name
        result.append({
            "id": s.id,
            "employee_id": s.employee_id,
            "employee_name": emp.name if emp else "Unknown",
            "site": site_name,
            "date": s.date.isoformat(),
            "clock_in": format_12hour(s.clock_in),
            "clock_out": format_12hour(s.clock_out) if s.clock_out else None,
            "hours": hours,
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

    shift = Shift(
        employee_id=req.employee_id,
        site_id=req.site_id,
        clock_in=ci_dt,
        clock_out=co_dt,
        date=ci_date,
    )
    db.add(shift)
    db.commit()
    db.refresh(shift)

    hours = None
    if co_dt:
        hours = round((co_dt - ci_dt).total_seconds() / 3600, 2)

    return {
        "id": shift.id,
        "clock_in": format_12hour(shift.clock_in),
        "clock_out": format_12hour(shift.clock_out) if shift.clock_out else None,
        "hours": hours,
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

    if req.clock_in_date and req.clock_in_time:
        try:
            ci_date = date.fromisoformat(req.clock_in_date)
            shift.clock_in = parse_12hour_time(req.clock_in_time, ci_date)
            shift.date = ci_date
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid clock-in date or time")

    if req.clock_out_date and req.clock_out_time:
        try:
            co_date = date.fromisoformat(req.clock_out_date)
            shift.clock_out = parse_12hour_time(req.clock_out_time, co_date)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid clock-out date or time")
    elif req.clock_out_date == "" or req.clock_out_time == "":
        shift.clock_out = None

    db.commit()
    db.refresh(shift)

    hours = None
    if shift.clock_out:
        hours = round((shift.clock_out - shift.clock_in).total_seconds() / 3600, 2)

    return {
        "id": shift.id,
        "clock_in": format_12hour(shift.clock_in),
        "clock_out": format_12hour(shift.clock_out) if shift.clock_out else None,
        "hours": hours,
        "message": "Shift updated",
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


# ─── Admin: Schedules ───

@app.get("/api/admin/schedules")
def admin_schedules(
    date_str: Optional[str] = Query(None),
    current_user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    q = db.query(Schedule)
    if date_str:
        q = q.filter(Schedule.date == date.fromisoformat(date_str))
    schedules = q.order_by(Schedule.date).all()

    result = []
    for s in schedules:
        emp = db.query(Employee).filter(Employee.id == s.employee_id).first()
        site = db.query(Site).filter(Site.id == s.site_id).first()
        result.append({
            "id": s.id,
            "employee_id": s.employee_id,
            "employee_name": emp.name if emp else "Unknown",
            "site_id": s.site_id,
            "site_name": site.name if site else "Unknown",
            "date": s.date.isoformat(),
        })
    return result


@app.post("/api/admin/schedules")
def admin_create_schedule(
    req: ScheduleCreate,
    current_user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    try:
        sched_date = date.fromisoformat(req.date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")

    existing = db.query(Schedule).filter(
        Schedule.employee_id == req.employee_id,
        Schedule.date == sched_date,
    ).first()
    if existing:
        existing.site_id = req.site_id
        db.commit()
        return {"id": existing.id, "message": "Schedule updated"}

    sched = Schedule(
        employee_id=req.employee_id,
        site_id=req.site_id,
        date=sched_date,
    )
    db.add(sched)
    db.commit()
    db.refresh(sched)
    return {"id": sched.id, "message": "Schedule created"}


@app.delete("/api/admin/schedules/{schedule_id}")
def admin_delete_schedule(
    schedule_id: int,
    current_user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    sched = db.query(Schedule).filter(Schedule.id == schedule_id).first()
    if not sched:
        raise HTTPException(status_code=404, detail="Schedule not found")
    db.delete(sched)
    db.commit()
    return {"message": "Schedule deleted"}
