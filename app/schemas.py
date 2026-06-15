from pydantic import BaseModel
from typing import Optional, List
from datetime import date


class LoginRequest(BaseModel):
    password: str


class LoginResponse(BaseModel):
    access_token: str
    employee_id: int
    name: str
    is_admin: bool


class ClockActionRequest(BaseModel):
    date: str
    time: str


class EmployeeCreate(BaseModel):
    name: str
    password: str


class EmployeeUpdate(BaseModel):
    name: Optional[str] = None
    password: Optional[str] = None


class ShiftCreate(BaseModel):
    employee_id: int
    clock_in_date: str
    clock_in_time: str
    clock_out_date: Optional[str] = None
    clock_out_time: Optional[str] = None
    shift_type: Optional[str] = None


class EmployeeShiftCreate(BaseModel):
    clock_in_date: str
    clock_in_time: str
    clock_out_date: Optional[str] = None
    clock_out_time: Optional[str] = None
    shift_type: Optional[str] = None


class ShiftUpdate(BaseModel):
    clock_in_date: Optional[str] = None
    clock_in_time: Optional[str] = None
    clock_out_date: Optional[str] = None
    clock_out_time: Optional[str] = None
    shift_type: Optional[str] = None


